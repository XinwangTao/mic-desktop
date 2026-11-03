# One-time installer for the MicDesktop deploy watcher on the SECOND PC.
# No admin required: copies the watcher to %LOCALAPPDATA%, adds a Startup
# shortcut (per-user), starts it hidden, and posts a report back to the hub.
# Pure ASCII on purpose (PowerShell 5.1 ANSI parsing of BOM-less .ps1).
param(
    [string]$ReportTo = '4c4ca286-cd87-4439-b67a-f0d1f3664371',
    [string]$Api = 'http://127.0.0.1:18998'
)
$ErrorActionPreference = 'Continue'
$notes = New-Object System.Collections.ArrayList
function Note($s) { [void]$notes.Add($s); Write-Host $s }
function Send($text) {
    try {
        $j = @{ to = $ReportTo; text = $text } | ConvertTo-Json -Compress
        $b = [System.Text.Encoding]::UTF8.GetBytes($j)
        Invoke-RestMethod -Uri ($Api + '/api/send-text') -Method Post -Body $b `
            -ContentType 'application/json; charset=utf-8' -TimeoutSec 10 | Out-Null
        Note 'REPORT-SENT'
    } catch { Note ('REPORT-FAIL: ' + $_.Exception.Message) }
}

$dir = Join-Path $env:LOCALAPPDATA 'MicDesktopWatcher'
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$dst = Join-Path $dir 'watcher.ps1'

# locate the shipped watcher.ps1 in the download folders
$src = $null
foreach ($r in @((Join-Path $env:USERPROFILE 'Downloads'), (Join-Path $env:USERPROFILE 'Desktop'))) {
    if (-not (Test-Path $r)) { continue }
    $hit = Get-ChildItem -Path $r -Filter 'watcher.ps1' -Recurse -Depth 2 -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($hit) { $src = $hit; break }
}
if (-not $src) { Note 'FAIL: watcher.ps1 not found in Downloads/Desktop'; Send ('MIC-WATCHER-INSTALL-FAIL ' + 'no-source'); return }
Copy-Item -LiteralPath $src.FullName -Destination $dst -Force
Unblock-File -LiteralPath $dst -ErrorAction SilentlyContinue
Note ('INSTALLED: ' + $dst)

# stop any previous instance
Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*MicDesktopWatcher*watcher.ps1*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 1

# per-user Startup entry (no admin needed)
$startup = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup'
New-Item -ItemType Directory -Force -Path $startup | Out-Null
$cmd = Join-Path $startup 'MicDesktopWatcher.cmd'
$line = '@echo off' + "`r`n" + 'start "" /min powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $dst + '"' + "`r`n"
Set-Content -Path $cmd -Value $line -Encoding ASCII
Note ('STARTUP: ' + $cmd)

Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile','-WindowStyle','Hidden','-ExecutionPolicy','Bypass','-File', $dst) -WindowStyle Hidden
Start-Sleep -Seconds 4
$running = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*MicDesktopWatcher*watcher.ps1*' }
if ($running) { Note ('WATCHER-RUNNING pid=' + (($running | ForEach-Object { $_.ProcessId }) -join ',')) }
else { Note 'WATCHER-NOT-RUNNING' }

$body = 'MIC-WATCHER-INSTALL ' + (($notes | ForEach-Object { $_.Trim() }) -join ' ;; ')
Write-Host ''
Write-Host '=== REPORT ==='
Write-Host $body
Send $body
Read-Host 'Press Enter to close'
