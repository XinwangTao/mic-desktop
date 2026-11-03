# MicDesktop — 无线麦克风接收台

ESP32-S3 INMP441 无线麦克风的 PC 端配套软件。

**最基础的用法**：对板子说话，文字直接打进你电脑上任何输入框（微信、浏览器、编辑器、终端），像打字一样。另外还带反向朗读（文字 → 板子扬声器）、全局热键、板载按键控制。

---

## 第二台电脑接入（5 分钟）

把板子拿到另一台电脑上用，前置条件只有两个：**蓝牙能连到板子**、**有一个转写引擎**。软件本身免安装——双击 `MicDesktop.exe`，首次启动会弹出向导：

1. **连接板子**：点「扫描板子」，列表里选中 `MicDesktop-XXXX`。
   扫不到时按提示排查：板子没电 / 被别的电脑连着（蓝牙单连接，原机器先断开）/ 距离太远。
2. **选转写引擎**（二选一）：
   - **本地 / 局域网服务（推荐）**：点「自动探测」。如果局域网里已经有一台机器跑着转写服务（8390 端口），会自动填上地址；没有就手填 `http://<那台机器的IP>:8390/stt`，或改用讯飞。
   - **讯飞云端**：填 APPID / APIKey / APISecret（在讯飞开放平台创建应用获取）。凭据只存在本机 `config.json`，不进 git。
3. **试说一句**：点「开始测试」，对着板子随便说句话，向导里当场看到转写文字 → 点「完成」。

之后每次启动直达主界面。没走完向导就点「跳过」也能进，底栏会一直挂着「⚙ 完成初始配置」按钮，随时点它重开向导。

### 转写服务主机（给提供引擎的那台电脑）

局域网引擎方案里，总得有一台机器跑转写服务（GPU 加速，faster-whisper）：

```powershell
# 本机自用（默认只监听 127.0.0.1，安全）
powershell -File stt_service\stt-service.ps1 start

# 供局域网里第二台电脑使用：绑 0.0.0.0，Windows 防火墙首次会弹窗，允许即可
powershell -File stt_service\run_native.ps1 -BindHost 0.0.0.0
```

服务脚本在本仓库 `stt_service/`（含依赖与模型说明）。不想跑本地服务的机器直接选讯飞云端，`config.json` 填凭据即可，不需要任何本地环境。

### 常见问题

| 现象 | 处理 |
|---|---|
| 向导扫不到板子 | 板子没电；被原电脑连着（BLE 单连接）；蓝牙适配器被关 |
| 「试说一句」转写失败 | 服务地址不通（探测/换讯飞）；录音太短（靠近板子再说一次） |
| 说话没反应但界面有字 | 语音输入开关是关的：BOOT 键长按 3 秒 / Ctrl+Alt+F9 / 底栏按钮 |
| 终端里打拼音直接出英文字母 | 输入法被切到英文档：在该窗口按一下 Shift 切回；根因与修法见「热键为什么不用 Shift」 |

---

## 跨机切换（一台麦，两台电脑，热键选打进哪台）

「第二台电脑接入」是板子换机器；这一节是**两台电脑同时在线、一个麦克风，热键在中间切**：这台电脑当转写中枢，第二台只收文字上屏，不连板子、不跑转写。

- **中枢（这台）**：`config.json` 里 `remote.host` 填第二台电脑的 IP（`remote.port` 默认 8391）。语音输入开着时，按 **F8** 在「本机 / 第二台」之间切换——本机照常上屏；切到第二台后，转写好的文字经局域网发给第二台、在第二台前台窗口上屏；第二台不可达时自动回本机，不丢字。目标状态记 `target.json`，重启恢复。
- **第二台**：跑一个无界面接收端即可（复用同一套终端护栏 / 回车清除 / 去重）：
  ```powershell
  python remote_receiver.py        # 监听 0.0.0.0:8391，收 {text,dur} 上屏
  ```
  首次运行往 `config.json` 同目录写 `state.json`（语音输入常开）。Windows 防火墙首次弹窗放行即可。

---

## 语音输入（说话即打字）

转写结果通过「剪贴板 + Ctrl+V」整句粘进当前前台窗口（不抢焦点、不经过输入法；剪贴板 0.4 秒后自动恢复）。常开模式靠三道闸兜住误触发：话长 < 0.6 s 丢弃、有效字 < 2 丢弃、2 秒内连续 3 次触发自动暂停。

- **开关**：界面底栏按钮 / `Ctrl+Alt+F9` / 板载 BOOT 键长按 3 秒。
- **暂停恢复**：`F9`（转写照常，只是不上屏）。
- **语音指令**：整句说「回车」「换行」按一次 Enter（近音词「回出」「会车」等也认，`voice_input_enter_words` 可自定义）；整句说「清除」「清空」删掉本次语音打进当前窗口的字（换窗口不删，单次上限 4000）。指令词不参与去重，连说两次「回车」就是两次。
- **终端护栏**：前台是 conhost / Windows Terminal / mintty / PuTTY 时默认不注入（免得误识别变成半条命令）；`config.json` 里 `"voice_input_allow_console": true` 可放行（含回车）。VS Code、JetBrains 内置终端类名与主窗口相同，识别不出来，会照常注入。
- **板载 BOOT 键**：短按 = 回车（不走闸门，等同亲手按键盘）；长按 3 秒 = 切换语音输入开关。状态计数在 `STAT` 回包的 `key=N/M`。
- 开关状态记在 EXE 同目录 `state.json`，重启后恢复上次状态。开启语音输入时自动回读暂停，避免板子念的话又被收回去。

### 热键为什么不用 Shift

`RegisterHotKey` 会把功能键从输入队列里吞掉。旧版用 `Shift+F9` 当硬开关，结果输入法只看到「Shift 按下 + 抬起」= 一次裸 Shift——中文输入法普遍把它当中英文切换（豆包输入法 `languageSwitchKey=shift`），于是被切到英文档，表现就是**在终端里打拼音直接出英文字母**。这个状态按窗口/线程记，软件关掉也不会自动恢复，所以看起来像「软件一开就坏、关掉也不好」。

- 2026-10-31 实测：不注册热键时发 `Shift+F9` 保持中文；`RegisterHotKey(Shift+F9)` 后同样发一遍 → 中文档 `0x0001` 变英文档 `0x0000`。
- 现在所有带修饰键的热键都用 `Ctrl+Alt`（`Ctrl+Alt+F9` 硬开关、`Ctrl+Alt+F8` 切换终端注入），单键 `F9`/`F8` 不变。
- 注入端也改成**一次 `SendInput` 下发整组 Ctrl+V**（全进或全不进），退出时把 Ctrl/Shift/Alt/Win 各抬一次，避免修饰键卡在按下状态。
- 已经被切成英文档的窗口：在该窗口按一下 `Shift` 切回；或者跑 `tools/fix_cn_input.py`（桌面快捷方式「修复中文输入」）：普通程序直接读状态并置位，终端这类 TSF 程序读豆包状态条判 `中/英`、动作后**再验证一次**。

### 「状态条显示中文，打拼音却只出字母」

这不是中英文档位问题，是**输入法被「关闭」**了（同一个开关就是 `Ctrl+空格`）。这时 `conversion` 仍是中文、豆包状态条照样显示「中」，但所有键直接透传成字母——**按 Shift 修不好，只有 `Ctrl+空格` 能打开**。

- 2026-10-31 实测：关闭后打 `nihao` 得 `nihao`；按一次 `Ctrl+空格` 后打 `nihao` 得「你好」。
- 常见成因是自己想「切到中文」而按了 `Ctrl+空格`——在现代 Windows 上它是**开关**，本来就开着的时候按下去等于关掉。
- `tools/fix_cn_input.py` 遇到状态条是「中」时会先问一句「打出来还是英文字母吗」，回答「是」才按 `Ctrl+空格`，避免把本来正常的输入法关掉。

### 一键修复：`tools/ime_guard.py`

```powershell
pythonw tools\ime_guard.py          # 常驻，空闲时阻塞在 GetMessage，CPU 0
pythonw tools\ime_guard.py --stop   # 停止
```

- 注册 **`Ctrl+Alt+I`**（被占时自动顺延 K/J/G），按下就修**当前前台窗口**，热键本身不抢焦点。
- 实现要点：`RegisterHotKey` 不走 .lnk 系统快捷键（这台机器上 Explorer 不接管，实测注册了也不生效）；追问框会抢焦点，所以动作前要用 `AttachThreadInput` + `SetForegroundWindow` + `SetFocus(焦点子窗口)` 把目标窗口切回来，否则 `Ctrl+空格` 会打进对话框。
- 日志：`tools/ime_guard.log`（启动/热键）与 `tools/fix_cn_input.log`（每次修复的窗口、权限、结论）。
- 自启：把 `pythonw ime_guard.py` 的快捷方式放进 `shell:startup` 即可（默认不装）。

---

## 连接与引擎

### 上行链路（板子 → PC）

`config.json` 的 `input_mode` 二选一，与固件编译时的 `AUDIO_LINK` 一致：

- **`"ble"`（默认推荐）**：NimBLE 外设 + Nordic UART Service，166 字节/包 = 6 字节头 + 160 字节 IMA ADPCM（4:1）。断线后按记住的地址直连，实测重连 2.8 s。**BLE 模式不做任何 UDP 探测**，按全双工走。
- **`"udp"`**：AUD0 帧走 Wi-Fi，`udp_port` 与固件 `AUDIO_UDP_TARGET` 一致。带 3 帧（60 ms）乱序重排；启动时向板子发 `STAT` 探测 AEC 能力，决定装不装半双工门控。

### 转写引擎

`stt_engine` 二选一：

- **`whisper_http`（默认）**：POST WAV 到 faster-whisper 服务。服务端（GPU，24× 实时）：
  ```powershell
  powershell -File stt_service\stt-service.ps1 start
  # 模型/精度/端口都是环境变量：DEVICE COMPUTE_TYPE BEAM_SIZE PORT INITIAL_PROMPT HOST
  ```
  模型实测结论：small fp16 是本机最优；medium / large-v3-turbo / beam5 在干净与加噪基准上都无收益，不换。
  没有 GPU 的机器可以用原 Docker CPU 容器（4~5× 实时）：
  ```bash
  docker run -d --name whisper-local --restart unless-stopped \
    -v F:/cache/models/faster-whisper-small:/models:ro \
    -v F:/cache/docker-stt/stt_server.py:/w/stt_server.py:ro \
    -p 127.0.0.1:8390:8390 whisper-stt python /w/stt_server.py
  ```
- **`xfyun`**：讯飞流式听写（云端），每段语音一个 IAT 会话。

whisper 提示词 `initial_prompt="以下是普通话的句子。"` 必须保留——不加会输出繁体并改字（「带上」→「戴上」）。

---

## 朗读（TTS 下行）

底部输入框打字回车，SAPI（离线）合成后推给板子扬声器。板子 IP 默认取最近一次上行包来源，不用手填。BLE 模式下行走同一条蓝牙链路（RX 特征）。

回声处理由 `mic_gate` 控制：`"auto"`（默认）启动时问板子 `STAT` 里的 `aec=on|off`，有 AEC 全双工、没有就朗读期间静音麦克风（半双工）；`"on"`/`"off"` 人工强制。**自动回读** `auto_speak: true` 时转写出一句自动念出来，四道闸门防自激（详见 `micapp/readback.py`）。

---

## 架构

```
BleReceiver(ADPCM解码/重排) ─┐
UdpReceiver(AUD0解包/重排)  ─┴→ [MicGate(仅UDP半双工)] → VADSlicer(设备端VAD优先) → STTEngine → Qt信号 → 界面
                                                                                ↓
                                                voice_input(闸门→SendInput逐字注入前台) / readback(自动朗读)
下行：SAPI 合成 → BLE RX特征 / UDP 5001 → 板子扬声器
```

- `micapp/stt/base.py`：引擎注册表，新引擎继承 `STTEngine` + `@register`。
- 首启向导：`micapp/setup_wizard.py`（对应 Archify 图 `rust-esp32/docs/archify/mic-desktop-first-run-setup_workflow.*`）。
- 转写历史写 `logs/transcript_YYYYMMDD.txt`，运行日志 `log.txt`（滚动）。
- 线程模型：收包/VAD/STT/TTS/热键各自线程，跨线程只 emit Qt 信号，界面更新一律在 GUI 线程槽里做。

## 测试

```bash
python test_voice_input.py     # 语音输入全链路 43 项（真窗口注入，跑之前先关掉应用）
python test_headless.py        # 无界面端到端：回放录音 → 转写
python replay_test.py <wav> [端口]   # 把 16k mono WAV 以 AUD0 流回放
```

## 构建 EXE

```powershell
./build.ps1                       # 默认用 F:\cache\venvs\micbuild
```

注意：PyInstaller 路径必须绝对（`--specpath build`）；`build.ps1` 纯 ASCII（PowerShell 5.1 无 BOM 按 ANSI 读）；**PySide6 钉 6.9.1**（6.12 在本机 `import QtCore` 直接 DLL load failed）。图标/版本细节见 git 历史，配方全在 `build.ps1`。

## 配置速查

`config.json` 与 EXE 同目录（便携式）。要点：

- `input_mode`: `ble` / `udp`；`stt_engine`: `whisper_http` / `xfyun`
- `voice_input_allow_console`: 终端里也打字（默认 false）
- `voice_input_enter_words` / `voice_input_clear_words`: 自定义语音指令词
- `auto_speak`: 自动回读；`mic_gate`: `auto`/`on`/`off`
- `vad.adaptive`: 底噪自适应阈值（默认开，`log_stats: true` 可打电平分布日志调参）
- **`dist/config.json` 不要用仓库模板覆盖**——模板凭据是空的，会清掉真凭据。
