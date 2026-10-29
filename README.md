# MicDesktop — 无线麦克风接收台

ESP32-S3 INMP441 无线麦克风的 PC 端配套软件：实时接收 UDP 音频流并转成文字；也能反向把文字念给板子上的扬声器。

## 使用

1. 把 `config.json` 放在 EXE 同目录，填入讯飞凭据：

```json
{
  "udp_port": 5000,
  "speaker_port": 5001,
  "board_ip": "",
  "stt_engine": "xfyun",
  "language": "zh_cn",
  "xfyun": { "app_id": "...", "api_key": "...", "api_secret": "..." },
  "vad": { "rms_threshold": 170, "start_ms": 120, "hangover_ms": 1000 },
  "tts_voice": "Microsoft Huihui Desktop"
}
```

2. 固件端 `AUDIO_UDP_TARGET` 指向本机 IP 与 `udp_port`。
3. 运行 `MicDesktop.exe`：顶部显示设备地址/包率/丢包/引擎/电平，正文实时滚动转写文本。

VAD 阈值按拾音距离调整：远场（跨房间）用 150–200，近场可用 300–500。

## 朗读（TTS 下行）

底部输入框打字，回车或点「朗读」，文字用 Windows SAPI（离线）合成后经 UDP `speaker_port` 推给板子播放。

- 板子 IP 默认取**最近一次上行包的来源地址**，不用手填；没收到包时退回 `board_ip`。
- 音色由 `tts_voice` 决定：中文默认 `Microsoft Huihui Desktop`，英文可换 `Microsoft Zira Desktop`。
- 合成优先走进程内 COM（pywin32），失败自动退回 PowerShell 调 SAPI。
- **回声处理**：扬声器和麦克风在同一块板子上，朗读声会被采回来重复转写。现在的做法是朗读期间
  **静音麦克风**（`micapp/gate.py`）：播放中丢弃上行音频，播完再等 350 ms 混响尾巴，期间把进行中的
  语音段作废。尾巴时长可用 config 的 `mic_mute_tail_s` 调整。真正的全双工需要设备端 AEC
  （esp-sr AFE，要求 PSRAM），见 `rust-esp32/docs/research/2026-10-29-audio-pipeline-research.md`。

## 架构

```
UdpReceiver(AUD0解包 + 乱序重排) → MicGate(朗读期间静音) → VADSlicer(能量VAD切段) → STTEngine → Qt 信号 → 界面
                                                        ↑
                          输入框 ── SAPI 合成 ──→ UDP 5001 ──→ 板子扬声器
```

- `micapp/stt/base.py`：引擎注册表。新引擎 = 继承 `STTEngine` + `@register`，config 里换 `stt_engine` 名字即生效。
- **已实现引擎**：
  - `xfyun`（默认，云端）：讯飞流式听写，每段语音一个 IAT 会话，实时性好。
  - `whisper_http`（本地）：POST WAV 到本机 Docker 转写服务。启动服务：
    ```bash
    docker run -d --name whisper-local --restart unless-stopped \
      -v F:/cache/models/faster-whisper-small:/models:ro \
      -v F:/cache/docker-stt/stt_server.py:/w/stt_server.py:ro \
      -p 8390:8390 whisper-stt python /w/stt_server.py
    ```
    切换：config 里 `"stt_engine": "whisper_http"`。
- 收包端带 3 帧（60 ms）乱序重排缓冲：早到的包先缓存，缺口 60 ms 内补上就按序放行，否则记一次丢包继续播；重复包直接丢弃。丢包数按「实际播放顺序」统计，不会被乱序包带偏。
- 转写历史自动写入 `logs/transcript_YYYYMMDD.txt`；引擎错误写入 `log.txt`。
- 线程模型：收包/VAD/STT/TTS 各自线程，跨线程只 emit Qt 信号，界面更新一律在 GUI 线程槽里做。

## 界面

PySide6 无边框深色卡片：自定义标题栏（可拖动）、圆角气泡消息、电平表、引擎与设备状态胶囊。
Qt 样式集中在 `micapp/ui.py` 的 `QSS`，配色常量在同文件顶部。

## 测试

```bash
python test_headless.py        # 无界面端到端：回放录音 → 转写（需 config 填凭据）
python replay_test.py <wav> [端口]   # 把任意 16k mono WAV 以 AUD0 流回放
```

## 构建 EXE

```bash
F:\cache\venvs\micbuild\Scripts\python.exe -m pip install pyinstaller websocket-client "PySide6==6.9.1" pywin32
cd /d F:\projects\mic-desktop
F:\cache\venvs\micbuild\Scripts\python.exe -m PyInstaller --noconfirm --onefile --noconsole ^
  --name MicDesktop --distpath dist --workpath build --specpath build ^
  --collect-data certifi --hidden-import win32com.client --hidden-import pythoncom ^
  --exclude-module tkinter --exclude-module numpy --exclude-module matplotlib main.py
```

注意：

- **PySide6 必须钉在 6.9.1**。6.12 在本机 Python 3.11 venv 里 `import QtCore` 直接 `DLL load failed … 找不到指定的程序`（MSVC 运行库版本不匹配），且冻结后同样报错。
- `dist/config.json` 不要用 `copy config.json dist\` 覆盖——源 config 是空凭据模板，覆盖会把 EXE 里的真凭据清空。手改 `dist/config.json` 即可。
