import argparse
import threading
import time
import uuid
from pathlib import Path

import av
import torch
import uvicorn
from fastapi import FastAPI, File, HTTPException, UploadFile
from qwen_asr import Qwen3ASRModel


ROOT = Path(__file__).resolve().parent
MODEL_DIR = ROOT / "models-qwen" / "Qwen3-ASR-0.6B"
TEMP_DIR = ROOT / "work" / "qwen-audio"
MAX_UPLOAD_BYTES = 200 * 1024 * 1024

app = FastAPI(title="Qwen3-ASR Worker", docs_url=None, redoc_url=None)
_model = None
_model_lock = threading.Lock()


def get_model():
    global _model
    with _model_lock:
        if _model is None:
            if not MODEL_DIR.is_dir():
                raise RuntimeError(f"模型目录不存在：{MODEL_DIR}")
            if not torch.cuda.is_available():
                raise RuntimeError("CUDA 不可用，Qwen3-ASR GPU worker 无法启动")
            torch.set_float32_matmul_precision("high")
            _model = Qwen3ASRModel.from_pretrained(
                str(MODEL_DIR),
                dtype=torch.float16,
                device_map="cuda:0",
                max_inference_batch_size=1,
                max_new_tokens=512,
            )
    return _model


def audio_duration(path: Path) -> float:
    with av.open(str(path)) as container:
        if container.duration is not None:
            return float(container.duration / av.time_base)
        stream = container.streams.audio[0]
        return float(stream.duration * stream.time_base) if stream.duration is not None else 0.0


@app.get("/health")
def health():
    return {
        "ok": True,
        "loaded": _model is not None,
        "cuda": torch.cuda.is_available(),
        "device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "model": "Qwen3-ASR-0.6B",
    }


@app.post("/transcribe")
async def transcribe(file: UploadFile = File(...)):
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    await file.close()
    if not data:
        raise HTTPException(status_code=400, detail="音频文件为空")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="音频文件不能超过 200 MB")

    suffix = Path(file.filename or "audio.wav").suffix.lower()
    if suffix not in {".mp3", ".wav", ".m4a", ".webm", ".ogg", ".flac", ".mp4", ".aac"}:
        suffix = ".audio"
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    path = TEMP_DIR / f"{uuid.uuid4().hex}{suffix}"
    path.write_bytes(data)

    try:
        duration = audio_duration(path)
        started = time.perf_counter()
        model = get_model()
        loaded = time.perf_counter()
        with torch.inference_mode():
            results = model.transcribe(audio=str(path), language=None)
        finished = time.perf_counter()
        return {
            "text": results[0].text,
            "language": results[0].language,
            "duration": round(duration, 2),
            "load_seconds": round(loaded - started, 2),
            "transcribe_seconds": round(finished - loaded, 2),
            "elapsed": round(finished - started, 2),
            "peak_vram_mb": round(torch.cuda.max_memory_allocated() / 1024**2),
        }
    except torch.OutOfMemoryError as exc:
        torch.cuda.empty_cache()
        raise HTTPException(status_code=507, detail="显存不足，请关闭占用 GPU 的程序后重试") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Qwen3-ASR 推理失败：{exc}") from exc
    finally:
        path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="Qwen3-ASR 本地 GPU worker")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
