import argparse
import json
import queue
import sys
from pathlib import Path

import sounddevice as sd
from vosk import KaldiRecognizer, Model, SetLogLevel


ROOT = Path(__file__).resolve().parent
DEFAULT_MODEL = ROOT / "models" / "vosk-model-small-cn-0.22"


def main() -> int:
    parser = argparse.ArgumentParser(description="使用 Vosk 进行中文麦克风离线识别")
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL, help="Vosk 模型目录")
    parser.add_argument("--device", type=int, help="输入设备编号；用 --list-devices 查看")
    parser.add_argument("--list-devices", action="store_true", help="列出音频设备并退出")
    parser.add_argument("--samplerate", type=int, help="采样率；默认使用设备采样率")
    args = parser.parse_args()

    if args.list_devices:
        devices = sd.query_devices()
        print(devices if len(devices) else "未检测到音频设备。请检查麦克风连接和 Windows 麦克风权限。")
        return 0
    if not args.model.is_dir():
        parser.error(f"模型目录不存在：{args.model}")

    try:
        device_info = sd.query_devices(args.device, "input")
    except sd.PortAudioError as exc:
        parser.error(f"无法打开录音设备：{exc}。请先运行 --list-devices 检查设备。")
    samplerate = args.samplerate or int(device_info["default_samplerate"])
    audio_queue: queue.Queue[bytes] = queue.Queue()

    def callback(indata, frames, time, status):
        if status:
            print(status, file=sys.stderr)
        audio_queue.put(bytes(indata))

    SetLogLevel(-1)
    recognizer = KaldiRecognizer(Model(str(args.model)), samplerate)
    print(f"正在监听：{device_info['name']}（{samplerate} Hz）。按 Ctrl+C 停止。")

    try:
        with sd.RawInputStream(
            samplerate=samplerate,
            blocksize=8000,
            device=args.device,
            dtype="int16",
            channels=1,
            callback=callback,
        ):
            while True:
                if recognizer.AcceptWaveform(audio_queue.get()):
                    text = json.loads(recognizer.Result()).get("text", "").replace(" ", "")
                    if text:
                        print(text, flush=True)
    except KeyboardInterrupt:
        text = json.loads(recognizer.FinalResult()).get("text", "").replace(" ", "")
        if text:
            print(text)
        print("已停止。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
