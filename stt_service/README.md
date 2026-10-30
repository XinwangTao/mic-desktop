# 本地转写服务（faster-whisper，GPU 可选）

MicDesktop 的 `whisper_http` 引擎后端。三个文件：

- `stt_server.py` — HTTP 服务本体（POST WAV → JSON {text}），参数全走环境变量：
  `MODEL_PATH` `DEVICE` `COMPUTE_TYPE` `CPU_THREADS` `BEAM_SIZE` `PORT` `HOST` `INITIAL_PROMPT`
- `run_native.ps1` — 原生进程启动（有 NVIDIA 显卡时用这个，24× 实时）
- `stt-service.ps1` — 启停/状态管理（start 会自动停掉占用 8390 的 Docker 容器）

脚本里写死的 `F:\cache\...` 是作者机器的路径，按需改成自己的模型与服务路径。

## 快速开始

```powershell
# 1) 依赖：Python 3.11 + pip install faster-whisper nvidia-cublas-cu12 nvidia-cudnn-cu12
#    （venv 请用系统 Python 建——Anaconda 的 python 建的 venv 里 ctranslate2 会 segfault）
# 2) 模型：faster-whisper-small（CT2 格式），放到 MODEL_PATH 指向的目录
# 3) 启动（本机自用，默认只监听 127.0.0.1）
powershell -File stt-service.ps1 start
#    供局域网里其他电脑使用：
powershell -File run_native.ps1 -BindHost 0.0.0.0    # 防火墙首次弹窗，允许即可
```

接口：`POST /stt`，body 为 16k mono 16-bit WAV，返回 `{"text": "..."}`。
MicDesktop 侧在 `config.json` 里把 `whisper_http.url` 指过来即可（首启向导的「自动探测」会找到它）。
