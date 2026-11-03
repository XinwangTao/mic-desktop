# Post-mortem / live diagnostic snapshot for the second PC. Pure ASCII.
# Writes %LOCALAPPDATA%\PCCrashGuard\diagnose-<timestamp>.txt and prints a summary.
# Read-only: does not change system state.

$ErrorActionPreference = 'SilentlyContinue'
$Root = Join-Path $env:LOCALAPPDATA 'PCCrashGuard'
New-Item -ItemType Directory -Force -Path $Root | Out-Null
$out = Join-Path $Root ('diagnose-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.txt')

function Section($title) { Add-Content $out ("`n===== " + $title + " =====") }
function W($s) { Add-Content $out $s }

Section 'SYSTEM'
$os = Get-CimInstance Win32_OperatingSystem
W ("host=" + $env:COMPUTERNAME + " user=" + $env:USERNAME)
W ("os=" + $os.Caption + " build=" + $os.BuildNumber + " uptime=" + ((Get-Date) - $os.LastBootUpTime))
$cs = Get-CimInstance Win32_ComputerSystem
W ("model=" + $cs.Manufacturer + " " + $cs.Model + " ramGB=" + [math]::Round($cs.TotalPhysicalMemory / 1GB, 1))
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
W ("cpu=" + $cpu.Name + " load=" + $cpu.LoadPercentage + "%")

Section 'MEMORY NOW'
$totalMB = [math]::Round($os.TotalVisibleMemorySize / 1KB)
$freeMB = [math]::Round($os.FreePhysicalMemory / 1KB)
W ("total={0}MB free={1}MB used={2}%" -f $totalMB, $freeMB, [math]::Round((($totalMB - $freeMB) / $totalMB) * 100, 1))
Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 15 ProcessName, Id,
    @{n = 'RSS_MB'; e = { [math]::Round($_.WorkingSet64 / 1MB) } },
    @{n = 'CPUs'; e = { [math]::Round($_.CPU, 1) } } | Format-Table -AutoSize | Out-String -Width 200 | ForEach-Object { W $_ }

Section 'DISK'
Get-PSDrive -PSProvider FileSystem | ForEach-Object {
    W ("drive {0}: usedGB={1} freeGB={2}" -f $_.Name, [math]::Round($_.Used / 1GB, 1), [math]::Round($_.Free / 1GB, 1))
}

Section 'EVENTLOG CRITICAL (last 14 days)'
$ids = 41, 1001, 6008, 6005, 1074, 129, 153, 157, 4101, 1000, 1002
$ev = Get-WinEvent -FilterHashtable @{ LogName = 'System'; StartTime = (Get-Date).AddDays(-14) } |
      Where-Object { $ids -contains $_.Id -or $_.ProviderName -like 'WHEA*' } |
      Select-Object -First 200
foreach ($e in $ev) {
    $msg = ($e.Message -split "`n")[0]
    W ("{0} id={1} prov={2} lvl={3} :: {4}" -f $e.TimeCreated, $e.Id, $e.ProviderName, $e.LevelDisplayName, $msg)
}
if (-not $ev) { W 'no matching events' }

Section 'BUGCHECK MINIDUMPS'
$dmp = Get-ChildItem "$env:SystemRoot\Minidump\*.dmp" -ErrorAction SilentlyContinue
if ($dmp) { $dmp | Select-Object LastWriteTime, Name, Length | Format-Table -AutoSize | Out-String | ForEach-Object { W $_ } }
else { W 'no minidumps' }

Section 'SCHEDULED TASKS (non-Microsoft)'
Get-ScheduledTask | Where-Object { $_.TaskPath -notlike '\Microsoft\*' } |
    Select-Object TaskName, State, @{n = 'Action'; e = { ($_.Actions | ForEach-Object { $_.Execute + ' ' + $_.Arguments }) -join '; ' } } |
    Format-Table -AutoSize -Wrap | Out-String -Width 220 | ForEach-Object { W $_ }

Section 'AUTOSTART'
Get-CimInstance Win32_StartupCommand | Select-Object Name, Command, Location |
    Format-Table -AutoSize -Wrap | Out-String -Width 220 | ForEach-Object { W $_ }

Section 'RELEVANT PROCESSES'
Get-CimInstance Win32_Process |
    Where-Object { $_.Name -match 'RelayDesk|MicDesktop|flix|node|python|claude' } |
    Select-Object Name, ProcessId, ParentProcessId, @{n = 'MB'; e = { [math]::Round($_.WorkingSetSize / 1MB) } }, CommandLine |
    Format-Table -AutoSize -Wrap | Out-String -Width 260 | ForEach-Object { W $_ }

Section 'GPU / DRIVERS'
Get-CimInstance Win32_VideoController | Select-Object Name, DriverVersion, DriverDate, Status |
    Format-Table -AutoSize | Out-String | ForEach-Object { W $_ }

Section 'GUARD LOG TAIL'
$glog = Join-Path $Root 'guard.log'
if (Test-Path $glog) { Get-Content $glog -Tail 40 | ForEach-Object { W $_ } } else { W 'no guard.log' }

Write-Output ("DIAGNOSE-OK file=" + $out)
