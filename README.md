# MicDesktop — 无线麦克风接收台

ESP32-S3 INMP441 无线麦克风的 PC 端配套软件：实时接收 UDP 音频流，实时语音转文字显示。

## 使用

1. 把 `config.json` 放在 EXE 同目录，填入讯飞凭据：

```json
{
  "udp_port": 5000,
  "stt_engine": "xfyun",
  "language": "zh_cn",
  "xfyun": { "app_id": "...", "api_key": "...", "api_secret": "..." },
  "vad": { "rms_threshold": 170, "start_ms": 120, "hangover_ms": 1000 }
}
```

2. 固件端 `AUDIO_UDP_TARGET` 指向本机 IP 与 `udp_port`。
3. 运行 `MicDesktop.exe`：顶部显示设备地址/包率/丢包/电平，正文实时滚动转写文本。

VAD 阈值按拾音距离调整：远场（跨房间）用 150–200，近场可用 300–500。

## 架构

```
UdpReceiver(AUD0解包) → VADSlicer(能量VAD切段) → STTEngine(可插拔) → UI 队列 → tkinter
```

- `micapp/stt/base.py`：引擎注册表。新引擎 = 继承 `STTEngine` + `@register`，config 里换 `stt_engine` 名字即生效。
- 已实现：`xfyun`（讯飞流式听写，每段语音一个 IAT 会话）。规划：`whisper_http`（本地 Docker whisper HTTP）。
- 线程模型：收包/VAD/STT 各自线程，UI 通过队列 + `root.after` 轮询，跨线程零共享。

## 测试

```bash
python test_headless.py        # 无界面端到端：回放录音 → 转写（需 config 填凭据）
python replay_test.py <wav> [端口]   # 把任意 16k mono WAV 以 AUD0 流回放
```

## 构建 EXE

```bash
python -m venv F:\cache\venvs\micbuild
F:\cache\venvs\micbuild\Scripts\python.exe -m pip install pyinstaller websocket-client
cd /d F:\projects\mic-desktop
F:\cache\venvs\micbuild\Scripts\python.exe -m PyInstaller --onefile --noconsole --name MicDesktop --distpath dist --workpath build --specpath build main.py
copy config.json dist\
```
