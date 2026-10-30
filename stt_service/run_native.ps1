# 原生 Windows 版转写服务：用 GPU 跑 faster-whisper，接口与容器版完全一致（127.0.0.1:8390）。
# 容器是 Hyper-V 后端，没有 /dev/nvidia*，所以显卡只能用原生进程。
# 注意：venv 必须建在系统 Python 上（F:\cache\venvs\stt-gpu2）。
# 用 Anaconda 的 python 建 venv 会在加载模型时 segfault——它带的 MKL/OpenMP 与 ctranslate2 冲突。
param(
    [string]$Model = 'F:\cache\models\faster-whisper-small',
    [string]$Device = 'cuda',
    [string]$Compute = 'float16',
    [int]$Port = 8390,
    [string]$BindHost = '127.0.0.1'
)
$py = 'F:\cache\venvs\stt-gpu2\Scripts\python.exe'
$sp = & $py -c "import sysconfig;print(sysconfig.get_paths()['purelib'])"

# ctranslate2 在 Windows 上不会自己找 pip 装的 CUDA 运行库，必须手动加进 PATH
$dlls = @("$sp\nvidia\cublas\bin", "$sp\nvidia\cudnn\bin", "$sp\nvidia\cuda_nvrtc\bin")
foreach ($d in $dlls) { if (Test-Path $d) { $env:PATH = "$d;$env:PATH" } }

$env:MODEL_PATH = $Model
$env:DEVICE = $Device
$env:COMPUTE_TYPE = $Compute
$env:PORT = "$Port"
$env:HOST = $BindHost
if (-not $env:BEAM_SIZE) { $env:BEAM_SIZE = '1' }
Write-Host "启动: model=$Model device=$Device compute=$Compute port=$Port"
& $py 'F:\cache\docker-stt\stt_server.py'
