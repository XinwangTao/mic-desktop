# 原生转写服务的启停/状态：start | stop | status
# 用它替代 Docker 容器（容器是 Hyper-V 后端，用不了 GPU）。
param(
    [ValidateSet('start', 'stop', 'status')]
    [string]$Action = 'status'
)
$root = 'F:\cache\docker-stt'
$pidFile = "$root\stt-native.pid"
$logFile = "$root\stt-native.log"

function Get-Service-Process {
    if (-not (Test-Path $pidFile)) { return $null }
    $id = (Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if (-not $id) { return $null }
    return Get-Process -Id ([int]$id) -ErrorAction SilentlyContinue
}

switch ($Action) {
    'status' {
        $p = Get-Service-Process
        if ($p) { Write-Host "运行中 pid=$($p.Id) 启动于 $($p.StartTime)" } else { Write-Host "未运行" }
        try {
            $r = Invoke-RestMethod -Uri 'http://127.0.0.1:8390/stt' -Method Post -Body ([byte[]]::new(2000)) -TimeoutSec 5
            Write-Host "端口 8390 可访问：$($r.text)"
        } catch { Write-Host "端口 8390 不可访问：$($_.Exception.Message)" }
    }
    'stop' {
        $p = Get-Service-Process
        if ($p) { Stop-Process -Id $p.Id -Force; Write-Host "已停止 pid=$($p.Id)" }
        else { Write-Host "本来就没在运行" }
        Remove-Item $pidFile -ErrorAction SilentlyContinue
    }
    'start' {
        if (Get-Service-Process) { Write-Host "已经在运行"; return }
        # 容器占着同一个端口，先停掉；哪天要用回 CPU 版再 docker start
        docker stop whisper-local 2>$null | Out-Null
        $proc = Start-Process powershell.exe -ArgumentList @('-NoProfile', '-File', "$root\run_native.ps1") `
            -WindowStyle Hidden -RedirectStandardOutput $logFile -RedirectStandardError "$logFile.err" -PassThru
        $proc.Id | Set-Content $pidFile
        Write-Host "已启动 pid=$($proc.Id)，日志 $logFile"
        # 等模型加载完（GPU 上十几秒）
        for ($i = 0; $i -lt 60; $i++) {
            Start-Sleep -Seconds 1
            try {
                Invoke-RestMethod -Uri 'http://127.0.0.1:8390/stt' -Method Post -Body ([byte[]]::new(2000)) -TimeoutSec 3 | Out-Null
                Write-Host "服务就绪（$($i+1) 秒）"
                return
            } catch { }
        }
        Write-Host "60 秒内没就绪，看 $logFile"
    }
}
