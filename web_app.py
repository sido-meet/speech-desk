import argparse
import asyncio
import gc
import io
import json
import logging
import os
import subprocess
import threading
import time
import uuid
import webbrowser
import wave
from datetime import datetime
from pathlib import Path
from typing import Optional

import av
import requests
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from vosk import KaldiRecognizer, Model, SetLogLevel

from technical_normalizer import normalize_technical_text
from vad_buffer import UtteranceDetector


logger = logging.getLogger("vosk_desk.refine")


ROOT = Path(__file__).resolve().parent
MODELS_DIR = ROOT / "models"
QWEN_MODEL_ID = "qwen3-asr-0.6b"
QWEN_MODEL_DIR = ROOT / "models-qwen" / "Qwen3-ASR-0.6B"
QWEN_PYTHON = ROOT / ".venv-qwen" / "Scripts" / "python.exe"
QWEN_WORKER_SCRIPT = ROOT / "qwen_worker.py"
QWEN_WORKER_URL = "http://127.0.0.1:8766"
STATIC_DIR = ROOT / "static"
DATA_DIR = ROOT / "data"
HISTORY_FILE = DATA_DIR / "history.json"
AUDIO_DIR = DATA_DIR / "audio"
MAX_UPLOAD_BYTES = 200 * 1024 * 1024
MAX_HISTORY = 200
MAX_STREAM_SECONDS = 60 * 60

app = FastAPI(title="Vosk Desk", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

_model_cache: dict[str, Model] = {}
_model_lock = threading.Lock()
_history_lock = threading.Lock()
SetLogLevel(-1)


def format_size(size: int) -> str:
    if size >= 1024**3:
        return f"{size / 1024**3:.1f} GB"
    if size >= 1024**2:
        return f"{size / 1024**2:.0f} MB"
    return f"{size / 1024:.0f} KB"


def directory_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def model_label(model_id: str) -> str:
    known = {
        "vosk-model-small-cn-0.22": "中文小模型 0.22",
        "vosk-model-cn-0.22": "中文高精度模型 0.22",
        "vosk-model-cn-kaldi-multicn-0.15": "中文 Kaldi Multi-CN 0.15",
        "vosk-model-small-en-us-0.15": "英文小模型 0.15",
        "vosk-model-en-us-0.22": "英文高精度模型 0.22",
        "vosk-model-en-us-0.22-lgraph": "英文增强模型 0.22 LGraph",
        "vosk-model-en-us-0.42-gigaspeech": "英文 GigaSpeech 模型 0.42",
        QWEN_MODEL_ID: "Qwen3-ASR 0.6B · 中英混合",
    }
    return known.get(model_id, model_id.replace("vosk-model-", "").replace("-", " ").title())


def installed_models() -> list[dict]:
    MODELS_DIR.mkdir(exist_ok=True)
    result = []
    for path in sorted(MODELS_DIR.iterdir()):
        if not path.is_dir() or not (path / "conf" / "model.conf").exists():
            continue
        size = directory_size(path)
        result.append(
            {
                "id": path.name,
                "name": model_label(path.name),
                "size": size,
                "size_label": format_size(size),
                "lightweight": "small" in path.name,
                "loaded": path.name in _model_cache,
            }
        )
    if (QWEN_MODEL_DIR / "model.safetensors").is_file():
        size = directory_size(QWEN_MODEL_DIR)
        result.insert(
            0,
            {
                "id": QWEN_MODEL_ID,
                "name": model_label(QWEN_MODEL_ID),
                "size": size,
                "size_label": format_size(size),
                "lightweight": False,
                "loaded": False,
                "engine": "qwen",
                "streaming": False,
            },
        )
    return result


def validate_model(model_id: str) -> None:
    if model_id == QWEN_MODEL_ID:
        if not (QWEN_MODEL_DIR / "model.safetensors").is_file():
            raise HTTPException(status_code=404, detail="Qwen3-ASR 模型尚未安装完整")
        return
    resolve_model(model_id)


def ensure_qwen_worker() -> None:
    try:
        if requests.get(f"{QWEN_WORKER_URL}/health", timeout=1).ok:
            return
    except requests.RequestException:
        pass
    if not QWEN_PYTHON.is_file():
        raise RuntimeError("Qwen3-ASR 独立运行环境不存在")

    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    subprocess.Popen(
        [str(QWEN_PYTHON), str(QWEN_WORKER_SCRIPT), "--host", "127.0.0.1", "--port", "8766"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        time.sleep(0.5)
        try:
            if requests.get(f"{QWEN_WORKER_URL}/health", timeout=1).ok:
                return
        except requests.RequestException:
            continue
    raise RuntimeError("Qwen3-ASR worker 启动超时")


def transcribe_qwen(data: bytes, filename: str, content_type: str) -> dict:
    ensure_qwen_worker()
    response = requests.post(
        f"{QWEN_WORKER_URL}/transcribe",
        files={"file": (filename, data, content_type or "application/octet-stream")},
        timeout=600,
    )
    if not response.ok:
        try:
            message = response.json().get("detail") or response.text
        except ValueError:
            message = response.text
        raise RuntimeError(message or f"Qwen3-ASR worker 返回 {response.status_code}")
    return response.json()


def build_wav_bytes(pcm: bytes, sample_rate: int) -> bytes:
    """Wrap raw Int16-LE mono PCM into a complete WAV file in memory."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return buffer.getvalue()


def refine_with_qwen(pcm: bytes, sample_rate: int) -> str:
    """Run Qwen3-ASR on a single utterance and return normalized text. Raises on failure."""
    wav_bytes = build_wav_bytes(pcm, sample_rate)
    result = transcribe_qwen(wav_bytes, "utterance.wav", "audio/wav")
    raw = result.get("text", "").strip()
    return normalize_technical_text(raw)



def resolve_model(model_id: str) -> Path:
    candidate = (MODELS_DIR / model_id).resolve()
    try:
        candidate.relative_to(MODELS_DIR.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="模型名称无效") from exc
    if not candidate.is_dir() or not (candidate / "conf" / "model.conf").exists():
        raise HTTPException(status_code=404, detail="所选模型不存在或目录不完整")
    return candidate


def get_model(model_id: str) -> Model:
    path = resolve_model(model_id)
    with _model_lock:
        if model_id not in _model_cache:
            _model_cache.clear()
            gc.collect()
            _model_cache[model_id] = Model(str(path))
        return _model_cache[model_id]


def read_history() -> list[dict]:
    with _history_lock:
        if not HISTORY_FILE.exists():
            return []
        try:
            data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError):
            return []


def write_history(records: list[dict]) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    with _history_lock:
        temp = HISTORY_FILE.with_suffix(".tmp")
        temp.write_text(json.dumps(records[:MAX_HISTORY], ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(HISTORY_FILE)


def add_history(record: dict) -> None:
    records = read_history()
    write_history([record, *records])


def remove_record_audio(record: dict) -> None:
    filename = record.get("audio_file")
    if not filename:
        return
    path = (AUDIO_DIR / filename).resolve()
    try:
        path.relative_to(AUDIO_DIR.resolve())
    except ValueError:
        return
    if path.is_file():
        path.unlink()


def transcribe_bytes(data: bytes, model_id: str) -> tuple[str, list[dict], float]:
    model = get_model(model_id)
    try:
        container = av.open(io.BytesIO(data), mode="r")
    except av.FFmpegError as exc:
        raise ValueError(f"无法解码音频：{exc}") from exc

    recognizer = KaldiRecognizer(model, 16000)
    recognizer.SetWords(True)
    resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
    segments: list[dict] = []
    total_samples = 0

    with container:
        if not container.streams.audio:
            raise ValueError("文件中没有音频流")
        stream = container.streams.audio[0]
        try:
            for frame in container.decode(stream):
                for converted in resampler.resample(frame):
                    total_samples += converted.samples
                    pcm = bytes(converted.planes[0])[: converted.samples * 2]
                    if recognizer.AcceptWaveform(pcm):
                        result = json.loads(recognizer.Result())
                        if result.get("text"):
                            segments.append(result)
            for converted in resampler.resample(None):
                total_samples += converted.samples
                pcm = bytes(converted.planes[0])[: converted.samples * 2]
                recognizer.AcceptWaveform(pcm)
        except av.FFmpegError as exc:
            raise ValueError(f"音频流损坏或格式不受支持：{exc}") from exc

    final = json.loads(recognizer.FinalResult())
    if final.get("text"):
        segments.append(final)
    text = "".join(segment.get("text", "").replace(" ", "") for segment in segments)
    return text, segments, total_samples / 16000


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health():
    models = installed_models()
    return {"ok": True, "models": len(models), "history": len(read_history())}


@app.get("/api/models")
def models():
    return installed_models()


@app.get("/api/history")
def history():
    return read_history()


@app.delete("/api/history/{record_id}")
def delete_history(record_id: str):
    records = read_history()
    deleted = next((item for item in records if item.get("id") == record_id), None)
    filtered = [item for item in records if item.get("id") != record_id]
    if len(filtered) == len(records):
        raise HTTPException(status_code=404, detail="记录不存在")
    write_history(filtered)
    remove_record_audio(deleted or {})
    return {"ok": True}


@app.delete("/api/history")
def clear_history():
    for record in read_history():
        remove_record_audio(record)
    write_history([])
    return {"ok": True}


@app.get("/api/audio/{record_id}")
def history_audio(record_id: str):
    record = next((item for item in read_history() if item.get("id") == record_id), None)
    if not record or not record.get("audio_file"):
        raise HTTPException(status_code=404, detail="音频文件不存在")
    path = (AUDIO_DIR / record["audio_file"]).resolve()
    try:
        path.relative_to(AUDIO_DIR.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="音频路径无效") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="音频文件不存在")
    return FileResponse(path, media_type=record.get("audio_type") or "application/octet-stream", filename=record["source"])


@app.post("/api/transcribe")
async def transcribe(file: UploadFile = File(...), model_id: str = Form(...)):
    validate_model(model_id)
    original_name = Path(file.filename or "录音").name
    content_type = file.content_type or "application/octet-stream"
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    await file.close()
    if not data:
        raise HTTPException(status_code=400, detail="音频文件为空")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="音频文件不能超过 200 MB")

    started = time.perf_counter()
    try:
        if model_id == QWEN_MODEL_ID:
            qwen_result = await asyncio.to_thread(transcribe_qwen, data, original_name, content_type)
            raw_text = qwen_result.get("text", "")
            text = normalize_technical_text(raw_text)
            segments = []
            duration = float(qwen_result.get("duration", 0))
            language = qwen_result.get("language")
        else:
            text, segments, duration = await asyncio.to_thread(transcribe_bytes, data, model_id)
            raw_text = text
            language = None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"识别失败：{exc}") from exc
    elapsed = time.perf_counter() - started

    record_id = uuid.uuid4().hex
    suffix = Path(original_name).suffix.lower()
    if suffix not in {".mp3", ".wav", ".m4a", ".webm", ".ogg", ".flac", ".mp4", ".aac"}:
        suffix = ".audio"
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    audio_filename = f"{record_id}{suffix}"
    (AUDIO_DIR / audio_filename).write_bytes(data)

    record = {
        "id": record_id,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": original_name,
        "model_id": model_id,
        "model_name": model_label(model_id),
        "duration": round(duration, 2),
        "elapsed": round(elapsed, 2),
        "size": len(data),
        "text": text,
        "raw_text": raw_text,
        "language": language,
        "engine": "qwen" if model_id == QWEN_MODEL_ID else "vosk",
        "segments": segments,
        "audio_file": audio_filename,
        "audio_type": content_type,
        "audio_url": f"/api/audio/{record_id}",
    }
    add_history(record)
    return record


@app.websocket("/api/stream")
async def stream_transcription(websocket: WebSocket):
    await websocket.accept()
    try:
        config = await websocket.receive_json()
        model_id = str(config.get("model_id", ""))
        sample_rate = int(config.get("sample_rate", 0))
        if sample_rate < 8000 or sample_rate > 96000:
            await websocket.send_json({"type": "error", "message": "浏览器采样率不受支持"})
            await websocket.close(code=1003)
            return
        if model_id == QWEN_MODEL_ID:
            await websocket.send_json({"type": "error", "message": "Qwen3-ASR 本机版暂不支持实时流式，请使用上传文件；实时语音请选择 Vosk"})
            await websocket.close(code=1008)
            return
        resolve_model(model_id)
        model = await asyncio.to_thread(get_model, model_id)
        recognizer = KaldiRecognizer(model, sample_rate)
        recognizer.SetWords(True)
        vad = UtteranceDetector(sample_rate=sample_rate)
        await websocket.send_json({"type": "ready", "sample_rate": sample_rate})

        started = time.perf_counter()
        pcm_chunks: list[bytes] = []
        segments: list[dict] = []
        total_samples = 0
        last_partial = ""
        display_text = ""
        refine_lock = asyncio.Lock()
        refine_task: Optional[asyncio.Task] = None
        vad_segments_at_utterance_start = 0

        def _compute_vosk_text(since_idx: int) -> str:
            return "".join(
                s.get("text", "").replace(" ", "") for s in segments[since_idx:]
            )

        def _merge_refinement(since_idx: int, qwen_text: str) -> str:
            vosk_text = _compute_vosk_text(since_idx)
            if vosk_text and display_text.endswith(vosk_text):
                return display_text[: -len(vosk_text)] + qwen_text
            return display_text + qwen_text

        async def run_refinement(pcm: bytes, since_idx: int) -> None:
            nonlocal display_text
            try:
                text = await asyncio.to_thread(refine_with_qwen, pcm, sample_rate)
            except Exception as exc:
                logger.warning("Qwen refinement failed: %s", exc)
                return
            if not text:
                return
            display_text = _merge_refinement(since_idx, text)
            try:
                await websocket.send_json({
                    "type": "refined",
                    "text": display_text + last_partial,
                })
            except RuntimeError:
                pass  # socket already closed

        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
            chunk = message.get("bytes")
            if chunk is not None:
                if len(chunk) % 2:
                    chunk = chunk[:-1]
                if not chunk:
                    continue
                total_samples += len(chunk) // 2
                if total_samples > sample_rate * MAX_STREAM_SECONDS:
                    await websocket.send_json({"type": "error", "message": "单次实时识别最长 60 分钟"})
                    return
                pcm_chunks.append(chunk)
                if recognizer.AcceptWaveform(chunk):
                    result = json.loads(recognizer.Result())
                    if result.get("text"):
                        segments.append(result)
                    finalized = _compute_vosk_text(0)
                    await websocket.send_json({
                        "type": "result",
                        "text": display_text + finalized,
                        "segment": result,
                    })
                    last_partial = ""
                else:
                    partial = json.loads(recognizer.PartialResult()).get("partial", "").replace(" ", "")
                    if partial != last_partial:
                        await websocket.send_json({
                            "type": "partial",
                            "text": display_text + _compute_vosk_text(0),
                            "partial": partial,
                        })
                        last_partial = partial

                # VAD: detect end of utterance, schedule Qwen refinement.
                completed = vad.feed(chunk)
                if completed is not None and not refine_lock.locked():
                    vad_segments_at_utterance_start = len(segments)

                    async def _job(pcm=completed, idx=vad_segments_at_utterance_start):
                        async with refine_lock:
                            await run_refinement(pcm, idx)
                    refine_task = asyncio.create_task(_job())
                continue

            text_message = message.get("text")
            if not text_message:
                continue
            try:
                event = json.loads(text_message)
            except json.JSONDecodeError:
                continue
            if event.get("event") != "stop":
                continue

            # Drain any in-flight refinement before finalizing.
            if refine_task is not None:
                try:
                    await asyncio.wait_for(refine_task, timeout=10.0)
                except (asyncio.TimeoutError, Exception):
                    pass

            # Drain remaining VAD buffer (user may have stopped mid-speech).
            trailing = vad.flush()
            if trailing is not None:
                try:
                    async with refine_lock:
                        text = await asyncio.to_thread(refine_with_qwen, trailing, sample_rate)
                        if text:
                            display_text = _merge_refinement(vad_segments_at_utterance_start, text)
                except Exception as exc:
                    logger.warning("Final Qwen refinement failed: %s", exc)

            final = json.loads(recognizer.FinalResult())
            if final.get("text"):
                segments.append(final)
            text = display_text + _compute_vosk_text(0)
            elapsed = time.perf_counter() - started
            duration = total_samples / sample_rate
            record_id = uuid.uuid4().hex
            now = datetime.now().astimezone()
            source = f"实时录音-{now.strftime('%Y%m%d-%H%M%S')}.wav"
            audio_filename = f"{record_id}.wav"
            AUDIO_DIR.mkdir(parents=True, exist_ok=True)
            with wave.open(str(AUDIO_DIR / audio_filename), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(sample_rate)
                audio.writeframes(b"".join(pcm_chunks))

            record = {
                "id": record_id,
                "created_at": now.isoformat(timespec="seconds"),
                "source": source,
                "model_id": model_id,
                "model_name": model_label(model_id),
                "duration": round(duration, 2),
                "elapsed": round(elapsed, 2),
                "size": sum(len(item) for item in pcm_chunks),
                "text": text,
                "segments": segments,
                "audio_file": audio_filename,
                "audio_type": "audio/wav",
                "audio_url": f"/api/audio/{record_id}",
            }
            add_history(record)
            await websocket.send_json({"type": "complete", "record": record})
            await websocket.close(code=1000)
            return
    except WebSocketDisconnect:
        return
    except HTTPException as exc:
        await websocket.send_json({"type": "error", "message": exc.detail})
        await websocket.close(code=1008)
    except Exception as exc:
        try:
            await websocket.send_json({"type": "error", "message": f"实时识别失败：{exc}"})
            await websocket.close(code=1011)
        except RuntimeError:
            pass


def main():
    parser = argparse.ArgumentParser(description="启动 Vosk Desk 本地网页")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(f"http://{args.host}:{args.port}")).start()
    print(f"Vosk Desk 已启动：http://{args.host}:{args.port}")
    print("按 Ctrl+C 停止服务。")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
