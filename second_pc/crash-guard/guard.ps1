# PCCrashGuard - generic runaway-process watchdog for the second PC.
# Pure ASCII (PowerShell 5.1 reads BOM-less .ps1 as ANSI).
# Runs every N seconds: records heartbeat, kills processes that match danger rules,
# and under memory pressure kills the largest non-whitelisted process.
# Never kills system/whitelisted names. All actions are logged with rotation.

param(
    [int]$IntervalSec = 10,
    [string]$ConfigPath = (Join-Path $PSScriptRoot 'guard.json')
)

$Root  = Join-Path $env:LOCALAPPDATA 'PCCrashGuard'
$Log   = Join-Path $Root 'guard.log'
New-Item -ItemType Directory -Force -Path $Root | Out-Null

function Rotate-Log {
    try {
        if ((Test-Path $Log) -and ((Get-Item $Log).Length -gt 2MB)) {
            Move-Item -Force $Log (Join-Path $Root 'guard.1.log')
        }
    } catch {}
}

function Log([string]$level, [string]$msg) {
    Rotate-Log
    $line = '{0} [{1}] {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $level, $msg
    Add-Content -Path $Log -Value $line -Encoding UTF8 -ErrorAction SilentlyContinue
}

# ---------- config ----------
$defaults = @{
    memPressurePct   = 90      # physical memory used above this = pressure
    pressureKillMB   = 800     # under pressure, kill largest process above this
    heartbeatSec     = 60
    whitelist        = @('System','Idle','Registry','smss','csrss','wininit','winlogon',
                         'services','lsass','svchost','explorer','dwm','audiodg','fontdrvhost',
                         'MsMpEng','NisSrv','Taskmgr','powershell','pwsh','conhost','cmd')
    rules            = @(
        @{ name='RelayDesk'; maxRssMB=1200; maxInstances=8 },
        @{ name='MicDesktop'; maxRssMB=1500; maxInstances=4 },
        @{ name='flix';      maxRssMB=800;  maxInstances=4 },
        @{ name='node';      maxRssMB=3000; maxInstances=12 },
        @{ name='python';    maxRssMB=3000; maxInstances=10 },
        @{ name='pythonw';   maxRssMB=3000; maxInstances=10 }
    )
}
$cfg = $defaults
if (Test-Path $ConfigPath) {
    try {
        $loaded = Get-Content $ConfigPath -Raw -ErrorAction Stop | ConvertFrom-Json
        foreach ($k in $loaded.PSObject.Properties.Name) { $cfg[$k] = $loaded.$k }
    } catch { Log 'WARN' ('config parse failed, using defaults: ' + $_.Exception.Message) }
}
$white = @($cfg.whitelist | ForEach-Object { $_.ToLower() })

function Is-White([string]$n) { return ($white -contains $n.ToLower()) }
function Self-Pid { return @($PID) + @((Get-CimInstance Win32_Process -Filter "ProcessId=$PID").ParentProcessId) }

function Get-MemPct {
    try {
        $os = Get-CimInstance Win32_OperatingSystem
        $total = [double]$os.TotalVisibleMemorySize
        $free  = [double]$os.FreePhysicalMemory
        if ($total -le 0) { return 0 }
        return [math]::Round((($total - $free) / $total) * 100, 1)
    } catch { return 0 }
}

function Kill-Proc($p, [string]$reason) {
    try {
        $mb = [math]::Round($p.WorkingSet64 / 1MB, 1)
        Stop-Process -Id $p.Id -Force -ErrorAction Stop
        Log 'KILL' ("pid={0} name={1} rss={2}MB reason={3}" -f $p.Id, $p.ProcessName, $mb, $reason)
    } catch {
        Log 'WARN' ("kill failed pid={0} name={1}: {2}" -f $p.Id, $p.ProcessName, $_.Exception.Message)
    }
}

function Test-ProcExited($p) {
    try { return $p.HasExited } catch { return $true }
}

# ---------- main loop ----------
$selfPids = Self-Pid
Log 'INIT' ("guard start pid={0} interval={1}s memPressure={2}% config={3}" -f $PID, $IntervalSec, $cfg.memPressurePct, $ConfigPath)
# 启动时报告近 24h 的意外关机次数（Event 41），用于跟踪卡死频率
try {
    $since = (Get-Date).AddDays(-1)
    $n = @(Get-WinEvent -FilterHashtable @{ LogName = 'System'; Id = 41; StartTime = $since } -ErrorAction SilentlyContinue).Count
    $last = Get-WinEvent -FilterHashtable @{ LogName = 'System'; Id = 41; StartTime = $since } -MaxEvents 1 -ErrorAction SilentlyContinue
    $lt = if ($last) { $last.TimeCreated.ToString('yyyy-MM-dd HH:mm') } else { 'none' }
    Log 'BOOT' ("unexpected-shutdowns-24h={0} last={1} boot={2}" -f $n, $lt, ((Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToString('yyyy-MM-dd HH:mm')))
} catch {}
$lastBeat = [DateTime]::MinValue

while ($true) {
    try {
        $now = Get-Date
        $memPct = Get-MemPct
        $procs = @(Get-Process -ErrorAction SilentlyContinue | Where-Object { -not (Is-White $_.ProcessName) })

        # 1) per-name rules
        foreach ($rule in @($cfg.rules)) {
            $name = [string]$rule.name
            $hits = @($procs | Where-Object { $_.ProcessName -eq $name })
            if ($hits.Count -eq 0) { continue }

            # RSS cap: kill any instance above the cap
            if ($rule.maxRssMB) {
                foreach ($h in $hits) {
                    if ($selfPids -contains $h.Id) { continue }
                    if ($h.WorkingSet64 -gt ([double]$rule.maxRssMB * 1MB)) {
                        Kill-Proc $h ("{0} RSS>{1}MB" -f $name, $rule.maxRssMB)
                    }
                }
            }
            # instance cap: keep the oldest, kill the newest extras
            if ($rule.maxInstances) {
                $alive = @($hits | Where-Object { -not (Test-ProcExited $_) } |
                    Sort-Object StartTime)
                if ($alive.Count -gt [int]$rule.maxInstances) {
                    $extras = $alive | Select-Object -Last ($alive.Count - [int]$rule.maxInstances)
                    foreach ($e in $extras) {
                        if ($selfPids -contains $e.Id) { continue }
                        Kill-Proc $e ("{0} instances>{1}" -f $name, $rule.maxInstances)
                    }
                }
            }
        }

        # 2) global memory pressure
        if ($memPct -ge [double]$cfg.memPressurePct) {
            $cand = $procs | Where-Object { $_.WorkingSet64 -gt ([double]$cfg.pressureKillMB * 1MB) } |
                Sort-Object WorkingSet64 -Descending | Select-Object -First 1
            if ($cand -and -not ($selfPids -contains $cand.Id)) {
                Kill-Proc $cand ("memory pressure {0}%" -f $memPct)
            } else {
                Log 'WARN' ("memory pressure {0}% but no candidate above {1}MB" -f $memPct, $cfg.pressureKillMB)
            }
        }

        # 3) heartbeat with top memory users (post-mortem breadcrumbs)
        if (($now - $lastBeat).TotalSeconds -ge [double]$cfg.heartbeatSec) {
            $lastBeat = $now
            $top = Get-Process -ErrorAction SilentlyContinue | Where-Object { -not (Is-White $_.ProcessName) } |
                Sort-Object WorkingSet64 -Descending | Select-Object -First 5
            $parts = @($top | ForEach-Object { '{0}({1})={2}MB' -f $_.ProcessName, $_.Id, [math]::Round($_.WorkingSet64 / 1MB) })
            $diskFreeGB = try { [math]::Round((Get-PSDrive C).Free / 1GB, 1) } catch { -1 }
            Log 'BEAT' ("mem={0}% diskFreeC={1}GB top={2}" -f $memPct, $diskFreeGB, ($parts -join ' '))
        }
    } catch {
        Log 'ERROR' $_.Exception.Message
    }
    Start-Sleep -Seconds $IntervalSec
}
