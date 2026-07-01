import argparse
import json
import sys
from pathlib import Path

import av
from vosk import KaldiRecognizer, Model, SetLogLevel


ROOT = Path(__file__).resolve().parent
DEFAULT_MODEL = ROOT / "models" / "vosk-model-small-cn-0.22"


def main() -> int:
    parser = argparse.ArgumentParser(description="使用 Vosk 离线识别音频文件")
    parser.add_argument("audio", type=Path, help="MP3、WAV、M4A 等音频文件")
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL, help="Vosk 模型目录")
    parser.add_argument("--json", action="store_true", help="输出完整 JSON 结果")
    args = parser.parse_args()

    if not args.model.is_dir():
        parser.error(f"模型目录不存在：{args.model}")
    if not args.audio.is_file():
        parser.error(f"音频文件不存在：{args.audio}")

    SetLogLevel(-1)
    try:
        container = av.open(str(args.audio))
    except av.FFmpegError as exc:
        parser.error(f"无法打开音频：{exc}")

    with container:
        if not container.streams.audio:
            parser.error("文件中没有音频流")
        audio_stream = container.streams.audio[0]
        resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
        recognizer = KaldiRecognizer(Model(str(args.model)), 16000)
        recognizer.SetWords(True)
        segments: list[dict] = []
        for frame in container.decode(audio_stream):
            for converted in resampler.resample(frame):
                pcm = bytes(converted.planes[0])[: converted.samples * 2]
                if recognizer.AcceptWaveform(pcm):
                    result = json.loads(recognizer.Result())
                    if result.get("text"):
                        segments.append(result)
        for converted in resampler.resample(None):
            pcm = bytes(converted.planes[0])[: converted.samples * 2]
            recognizer.AcceptWaveform(pcm)
        final = json.loads(recognizer.FinalResult())
        if final.get("text"):
            segments.append(final)

    if args.json:
        print(json.dumps(segments, ensure_ascii=False, indent=2))
    else:
        print("".join(segment["text"].replace(" ", "") for segment in segments))
    return 0


if __name__ == "__main__":
    sys.exit(main())
