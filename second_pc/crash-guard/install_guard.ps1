# Install PCCrashGuard on the current PC (no admin required).
# Pure ASCII. Creates a per-user scheduled task at logon, hidden window,
# restart-on-failure. Logs: %LOCALAPPDATA%\PCCrashGuard\guard.log

param(
    [string]$SourceDir = $PSScriptRoot,
    [string]$TaskName = 'PCCrashGuard'
)

$ErrorActionPreference = 'Stop'
$Root = Join-Path $env:LOCALAPPDATA 'PCCrashGuard'
New-Item -ItemType Directory -Force -Path $Root | Out-Null

Copy-Item -Force (Join-Path $SourceDir 'guard.ps1') (Join-Path $Root 'guard.ps1')
Copy-Item -Force (Join-Path $SourceDir 'guard.json') (Join-Path $Root 'guard.json')

$ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$arg = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + (Join-Path $Root 'guard.ps1') + '"'
$action = New-ScheduledTaskAction -Execute $ps -Argument $arg

$login = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew

# remove old task if present
try { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue } catch {}

$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $login `
    -Settings $settings -Principal $principal -Description 'Kill runaway processes before Windows freezes' | Out-Null

Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 4

$t = Get-ScheduledTask -TaskName $TaskName
$i = Get-ScheduledTaskInfo -TaskName $TaskName
$p = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" |
     Where-Object { $_.CommandLine -like '*PCCrashGuard*guard.ps1*' }
$log = Join-Path $Root 'guard.log'

"INSTALL-OK task=$($t.TaskName) state=$($t.State) lastResult=$($i.LastTaskResult)"
if ($p) { "guard-pid=$($p.ProcessId -join ',')" } else { "guard-pid=NONE (task may still be starting)" }
if (Test-Path $log) { '--- log tail ---'; Get-Content $log -Tail 5 } else { 'log not created yet' }
