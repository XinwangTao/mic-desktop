# MicDesktop deployment helper for the SECOND PC (no admin needed).
# Pure ASCII on purpose: Windows PowerShell 5.1 reads BOM-less .ps1 as ANSI,
# Chinese comments/strings would corrupt the parser.
#
# Usage (double-click or):
#   powershell -ExecutionPolicy Bypass -File deploy_second_pc.ps1
#   powershell -ExecutionPolicy Bypass -File deploy_second_pc.ps1 -NoLaunch
#
# What it does:
#   1. locates MicDesktop.exe that flix dropped (Downloads/Desktop/...)
#   2. verifies byte size, copies it to %USERPROFILE%\MicDesktop, unblocks it
#   3. tests TCP reachability of the STT service on the hub PC
#   4. launches the app and checks the process + window title + log
#   5. posts a report back to the hub PC through RelayDesk local API
param(
    [string]$SttHost = '192.168.237.7',
    [int]$SttPort = 8390,
    [string]$ReportTo = '4c4ca286-cd87-4439-b67a-f0d1f3664371',
    [string]$Api = 'http://127.0.0.1:18998',
    [switch]$NoLaunch
)
$ErrorActionPreference = 'Continue'
$EXPECTED = 52559183
$notes = New-Object System.Collections.ArrayList
function Note($s) { [void]$notes.Add($s); Write-Host $s }

# ---- 1. locate -------------------------------------------------------------
$roots = @(
    (Join-Path $env:USERPROFILE 'Downloads'),
    (Join-Path $env:USERPROFILE 'Desktop'),
    (Join-Path $env:USERPROFILE 'Documents')
) | Where-Object { Test-Path $_ }
$cands = @()
foreach ($r in $roots) {
    $cands += Get-ChildItem -Path $r -Filter 'MicDesktop.exe' -Recurse -Depth 3 -File -ErrorAction SilentlyContinue
}
if (-not $cands) { Note 'FAIL: MicDesktop.exe not found under Downloads/Desktop/Documents'; }
$src = $cands | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $src) {
    Note 'FAIL: cannot continue without the exe'
} else {
    Note ("FOUND: " + $src.FullName + " size=" + $src.Length)
    if ($src.Length -ne $EXPECTED) { Note ("FAIL: size mismatch, expected " + $EXPECTED + " - ask hub to resend") }

    # ---- 2. copy + unblock -------------------------------------------------
    $dest = Join-Path $env:USERPROFILE 'MicDesktop'
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    $dst = Join-Path $dest 'MicDesktop.exe'
    Copy-Item -LiteralPath $src.FullName -Destination $dst -Force
    Unblock-File -LiteralPath $dst -ErrorAction SilentlyContinue
    $got = (Get-Item -LiteralPath $dst).Length
    Note ("COPIED: " + $dst + " size=" + $got)
    if ($got -ne $EXPECTED) { Note 'FAIL: copy size mismatch' }

    # ---- 3. STT reachability ----------------------------------------------
    $tcp = $null
    try {
        $tcp = New-Object System.Net.Sockets.TcpClient
        $iar = $tcp.BeginConnect($SttHost, $SttPort, $null, $null)
        if ($iar.AsyncWaitHandle.WaitOne(3000) -and $tcp.Connected) { Note ("STT-TCP: open " + $SttHost + ":" + $SttPort) }
        else { Note ("STT-TCP: FAIL timeout " + $SttHost + ":" + $SttPort) }
    } catch { Note ("STT-TCP: FAIL " + $_.Exception.Message) }
    finally { if ($tcp) { $tcp.Close() } }

    # ---- 4. launch ---------------------------------------------------------
    if ($NoLaunch) {
        Note 'LAUNCH: skipped (-NoLaunch)'
    } else {
        $proc = Start-Process -FilePath $dst -WorkingDirectory $dest -PassThru
        Note ("LAUNCH: pid=" + $proc.Id)
        Start-Sleep -Seconds 12
        $p = Get-Process -Id $proc.Id -ErrorAction SilentlyContinue
        if ($p) {
            $title = $p.MainWindowTitle
            Note ("PROC: alive pid=" + $p.Id + " title='" + $title + "'")
        } else {
            $p2 = Get-Process -Name 'MicDesktop' -ErrorAction SilentlyContinue
            if ($p2) { Note ("PROC: respawned/other pid=" + ($p2.Id -join ',') + " title='" + ($p2 | Select-Object -First 1).MainWindowTitle + "'") }
            else { Note 'PROC: DEAD - app exited or crashed' }
        }
        $log = Join-Path $dest 'log.txt'
        if (Test-Path $log) {
            $tail = Get-Content -LiteralPath $log -Tail 8 -ErrorAction SilentlyContinue
            Note ('LOGTAIL: ' + (($tail | ForEach-Object { $_.Trim() }) -join ' | '))
        } else {
            Note 'LOGTAIL: log.txt not created'
        }
        $cfg = Join-Path $dest 'config.json'
        if (Test-Path $cfg) { Note ('CONFIG: present ' + $cfg) } else { Note 'CONFIG: absent (first-run wizard expected)' }
    }
}

# ---- 5. report back --------------------------------------------------------
$body = 'DEPLOY-B ' + (($notes | ForEach-Object { $_.Trim() }) -join ' ;; ')
Write-Host ''
Write-Host '=== REPORT ==='
Write-Host $body
try {
    $json = @{ to = $ReportTo; text = $body } | ConvertTo-Json -Compress
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
    $resp = Invoke-RestMethod -Uri ($Api + '/api/send-text') -Method Post -Body $bytes -ContentType 'application/json; charset=utf-8' -TimeoutSec 10
    Write-Host ('REPORT-SENT: ' + ($resp | ConvertTo-Json -Compress))
} catch {
    Write-Host ('REPORT-FAIL: ' + $_.Exception.Message)
    Write-Host 'Copy the REPORT line above and paste it to the hub machine.'
}
Read-Host 'Press Enter to close'
