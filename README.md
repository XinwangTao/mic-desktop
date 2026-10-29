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
  "vad": { "rms_threshold": 350, "start_ms": 200, "hangover_ms": 800,
           "adaptive": true, "ratio": 3.0, "floor": 120, "cap": 1200 },
  "tts_voice": "Microsoft Huihui Desktop"
}
```

2. 固件端 `AUDIO_UDP_TARGET` 指向本机 IP 与 `udp_port`。
3. 运行 `MicDesktop.exe`：顶部显示设备地址/包率/丢包/引擎/电平，正文实时滚动转写文本。

VAD 阈值默认**按环境底噪自适应**，不再是一个要人按距离调的数字：

- **有设备端 VAD 时优先用它**：固件 AFE 在包头 `flags` 里每帧给出结论——`bit1` 表示本帧带结论、
  `bit0` 表示结论为语音。两位都在时 `VADSlicer` 直接按位切句，下面这套能量阈值完全不参与。
  老固件两位恒 0 → `hint=None`，自动退回能量阈值。为什么必须两位：只给 `bit0` 的话，
  「老固件」和「AFE 判为静音」都是 0，PC 端会把老固件永远当成静音，一句都切不出来。

- 底噪取最近 `window_frames`（默认 500 帧 = 10 s）里**非语音帧**的 **P20**——说话帧只占少数，
  取均值会被自己的声音抬上去；语音帧不进窗口，所以连说 10 秒基线也不会涨。
- 阈值 = `clamp(底噪 × ratio, floor, cap)`，默认 `×3`（≈ +9.5 dB）、`[50, 1200]`。
  安静房间底噪 20 → 阈值 57~65，2–3 米的小声说话才过得去；嘈杂房间底噪 500 → 阈值封顶 1200。
  （下限原本定 120，2026-10-29 实测本机语音整句 RMS 只有 110–125，120 会把阈值压在语音线上，已下调到 50。）
- 抖动控制分两层：底噪平滑 `smooth=0.2`（只去毛刺），**阈值自身**再平滑 `thr_smooth=0.005`
  （50 Hz 下 τ≈4 s）。少了第二层，底噪在 18~25 之间漂就会把阈值放大成 55~76 的跳动。
  首次估出底噪时直接跳到位，不从固定值慢慢爬。
- 冷启动前 0.5 s 无条件采样（否则开屏就比 350 吵的房间会自锁：每帧都被当成语音，永远学不到底噪），
  之后每 2 s 强采一帧兜底。
- 界面上实时显示「底噪 / 阈值」，电平条按当前阈值归一化。
- 不想自适应：`"adaptive": false`，回到写死的 `rms_threshold`（远场跨房间 150–200，近场 300–500）。

## 朗读（TTS 下行）

底部输入框打字，回车或点「朗读」，文字用 Windows SAPI（离线）合成后经 UDP `speaker_port` 推给板子播放。

- 板子 IP 默认取**最近一次上行包的来源地址**，不用手填；没收到包时退回 `board_ip`。
- 音色由 `tts_voice` 决定：中文默认 `Microsoft Huihui Desktop`，英文可换 `Microsoft Zira Desktop`。
- 合成优先走进程内 COM（pywin32），失败自动退回 PowerShell 调 SAPI。
- **回声处理**：扬声器和麦克风在同一块板子上，朗读声会被采回来重复转写。现在有两道防线，
  由 config 的 `mic_gate` 选择：
  - `"auto"`（**代码默认值**）：**启动时问板子一句**——向 `speaker_port` 发 `STAT`（1 s 超时 ×3），
    解析回包里的 `aec=on|off`：有 AEC 就走全双工、不装门控；`aec=off`、老固件没这个字段、
    或压根没回包（板子不在线）都按**半双工**装门控。`board_ip` 为空时发广播，顺带用回包来源地址
    发现板子。整条判定只在启动时跑一次，运行期不重探、不热切换。
  - `"off"`：强制全双工，不探测不装门控（AEC 效果达标时的人工覆盖档）。
  - `"on"`：强制半双工，不探测（AEC 效果不达标时的人工覆盖档）。
  - 半双工的具体行为：朗读期间**静音麦克风**（`micapp/gate.py`）——播放中丢弃上行音频，
    播完再等 `mic_mute_tail_s`（默认 350 ms）混响尾巴，期间把进行中的语音段作废。
  - 固件侧用 esp-sr AEC 把播放声从麦克风里减掉（`rust-esp32/tarot-esp32/main/audio_aec.cc`），
    播放期间上行能量降到环境底噪附近（平均 RMS 97 vs 底噪 104）。
  AEC 残留：每句话开头约 300 ms 还在收敛，可能有一两帧能量冲高；真出现重复转写就把
  `mic_gate` 写死成 `"on"`。细节见 `rust-esp32/docs/research/2026-10-29-audio-pipeline-research.md`。

- **自动回读**（可选）：`auto_speak: true` 时，转写出一句就自动念出来，不用打字点按钮。
  默认关——会把噪声误转写也念出来。四道闸门任一拦下都只跳过这一句，不影响转写与落盘：
  有效字符 < `auto_speak_min_chars`（默认 2）不念；与最近 5 条念过的文本相同不念（挡住 AEC
  残留把刚念的话又转写回来）；连续 3 次且间隔 < 2 s 判定疑似自激，暂停并提示（需重启恢复）；
  朗读中来了新句子不打断，最多排队 1 条、再多的丢最新的。逻辑在 `micapp/readback.py`。
  图：`rust-esp32/docs/archify/mic-desktop-auto-readback_workflow.*`。

## 架构

```
UdpReceiver(AUD0解包 + 乱序重排) → [MicGate(仅半双工)] → VADSlicer(设备端VAD优先，能量阈值兜底) → STTEngine → Qt 信号 → 界面
                                                        ↑
                          输入框 ── SAPI 合成 ──→ UDP 5001 ──→ 板子扬声器
启动时 STAT 探测 ──→ 全双工(直通) / 半双工(装 MicGate)
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

```powershell
./build.ps1                       # 默认用 F:\cache\venvs\micbuild
./build.ps1 -Venv 'D:\some\venv'  # 指定别的构建环境
```

打包配方（图标、版本信息、assets）都写在 `build.ps1` 里，等价于：

```powershell
$Venv\Scripts\python.exe -m PyInstaller --noconfirm --onefile --noconsole `
  --name MicDesktop --distpath dist --workpath build --specpath build `
  --icon   'F:\projects\mic-desktop\micapp\assets\appicon.ico' `
  --version-file 'F:\projects\mic-desktop\packaging\version_info.txt' `
  --add-data 'F:\projects\mic-desktop\micapp\assets;micapp\assets' `
  --collect-data certifi --hidden-import win32com.client --hidden-import pythoncom `
  --exclude-module tkinter --exclude-module numpy --exclude-module matplotlib main.py
```

注意：

- **路径必须写绝对路径**：`--specpath build` 会让相对路径相对 `build\` 解析，
  写成 `micapp\assets` 会报 `Unable to find 'F:\projects\mic-desktop\build\micapp\assets'`。
- `build.ps1` 保持**纯 ASCII**。Windows PowerShell 5.1 在无 BOM 时按 ANSI 读 `.ps1`，中文注释会被打乱。
- **PySide6 必须钉在 6.9.1**。6.12 在本机 Python 3.11 venv 里 `import QtCore` 直接 `DLL load failed … 找不到指定的程序`（MSVC 运行库版本不匹配），且冻结后同样报错。

## 图标与版本

- 图标源文件：`micapp/assets/appicon.ico`（16/24/32/48/64/128/256 七档）与 `appicon.png`（256，界面用）。
  重新生成见仓库外的 `F:\cache\make_icon.py`：1024 超采样绘制后 LANCZOS 降采样。
- 生成 ICO 时**必须从 1024 原图调用** `save(format="ICO", sizes=[...])`；传小图进去 PIL 只会产出那一档。
- 版本号在 `micapp/assets.py` 的 `APP_VERSION`，同时要同步 `packaging/version_info.txt`（exe 属性面板显示的就是它）。
- 任务栏图标靠 `SetCurrentProcessExplicitAppUserModelID("MicDesktop.ESP32S3.WirelessMic")`，
  不加这句 Windows 会显示 python.exe 的图标。
- `dist/config.json` 不要用 `copy config.json dist\` 覆盖——源 config 是空凭据模板，覆盖会把 EXE 里的真凭据清空。手改 `dist/config.json` 即可。
