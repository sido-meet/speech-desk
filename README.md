# Windows Vosk 中文离线语音识别

当前部署使用 `vosk-model-small-cn-0.22`。模型约 42 MB，官方给出的典型运行内存约 300 MB，适合桌面端和资源受限设备。

网页会自动扫描 `models` 目录。Vosk 官方模型按语言独立，适合分别识别中文或英文，不适合一句话内频繁中英混说；混合语言和技术公式需要改用多语种模型并增加文本规范化层。

已额外部署独立的 `Qwen3-ASR-0.6B` GPU 引擎。在网页右上角选择“Qwen3-ASR 0.6B · 中英混合”可识别中英混说的上传音频，并自动规范常见编程术语、化学式与幂表达式，例如 `python` → `Python`、`H2O2` → `H₂O₂`、`x squared` → `x²`。本机 Qwen 引擎不用于实时流式输入；实时语音继续选择 Vosk。

## 网页界面（推荐）

双击 `run-web.cmd`，浏览器会自动打开：

```powershell
.\run-web.cmd
```

网页地址为 `http://127.0.0.1:8765`。支持模型切换、文件上传、WebSocket 麦克风流式识别、音频试听、结果复制/下载，以及可播放的本地历史记录。实时语音会边输入边显示识别文字，结束后保存为 WAV。模型和音频均在本机处理。

网页服务会在后台持续运行。需要停止时，双击 `stop-web.cmd`。

## 麦克风实时识别

双击 `run-microphone.cmd`，或在 PowerShell 中运行：

```powershell
.\run-microphone.cmd
```

如果默认麦克风不正确：

```powershell
.\run-microphone.cmd --list-devices
.\run-microphone.cmd --device 设备编号
```

如果显示“未检测到音频设备”，请确认麦克风已连接，并在 Windows 的“设置 → 隐私和安全性 → 麦克风”中允许桌面应用访问麦克风。

## 识别音频文件

支持 MP3、WAV、M4A 等常见音频格式，程序会自动转成 16 kHz 单声道 PCM：

```powershell
.\run-transcribe.cmd C:\path\speech.mp3
.\run-transcribe.cmd C:\path\speech.mp3 --json
```

所有识别均在本机离线完成。首次部署模型和 Python 包之后，不再需要联网。
