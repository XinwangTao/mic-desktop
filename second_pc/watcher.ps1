# MicDesktop deploy watcher (second PC).
# Polls RelayDesk bubbles for whitelisted [mic-*] commands from the hub PC and
# executes ONLY those fixed actions. No arbitrary command support.
# Pure ASCII: PowerShell 5.1 reads BOM-less .ps1 as ANSI.
param(
    [string]$From = '4c4ca286-cd87-4439-b67a-f0d1f3664371',
    [string]$Api = 'http://127.0.0.1:18998',
    [int]$IntervalSec = 15,
    [string]$TargetDir = "$env:USERPROFILE\MicDesktop",
    [string]$StateFile = "$env:LOCALAPPDATA\MicDesktopWatcher\state.json"
)
$ErrorActionPreference = 'Continue'
$WorkDir = Split-Path -Parent $PSCommandPath
$LogFile = Join-Path $WorkDir 'watcher.log'

function Log($m) {
    $line = "{0} {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m
    Add-Content -Path $LogFile -Value $line -Encoding UTF8 -ErrorAction SilentlyContinue
}
function Send($text) {
    try {
        $j = @{ to = $From; text = $text } | ConvertTo-Json -Compress
        $b = [System.Text.Encoding]::UTF8.GetBytes($j)
        Invoke-RestMethod -Uri ($Api + '/api/send-text') -Method Post -Body $b `
            -ContentType 'application/json; charset=utf-8' -TimeoutSec 10 | Out-Null
    } catch { Log ("send-fail " + $_.Exception.Message) }
}
function Sha256($path) {
    $o = & certutil -hashfile $path SHA256 2>$null
    foreach ($l in $o) {
        $t = ($l -replace '\s', '')
        if ($t -match '^[0-9a-fA-F]{64}$') { return $t.ToLower() }
    }
    return 'unknown'
}
function SaveState($t) {
    try { @{ last = $t } | ConvertTo-Json -Compress | Set-Content -Path $StateFile -Encoding ASCII } catch {}
}

function Find-App {
    # onefile spawns a bootloader parent with no window plus the real child;
    # reports must name the child, otherwise "title=" comes back empty.
    $all = Get-Process -Name 'MicDesktop' -ErrorAction SilentlyContinue
    if (-not $all) { return $null }
    $win = $all | Where-Object { $_.MainWindowTitle } | Select-Object -First 1
    if ($win) { return $win }
    return ($all | Select-Object -First 1)
}

function DoDeploy {
    $cand = Get-ChildItem -Path (Join-Path $env:USERPROFILE 'Downloads') -Filter 'MicDesktop.exe' `
        -Recurse -Depth 2 -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if (-not $cand) {
        $cur = Join-Path $TargetDir 'MicDesktop.exe'
        if (-not (Test-Path $cur)) { Send 'MIC-DEPLOY-FAIL: no MicDesktop.exe under Downloads and none deployed'; return }
        Get-Process -Name 'MicDesktop' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 3
        $proc = Start-Process -FilePath $cur -WorkingDirectory $TargetDir -PassThru
        Start-Sleep -Seconds 12
        $p = Find-App
        if ($p) { Send ("MIC-DEPLOY-OK mode=restart-only pid=" + $p.Id + " title=" + $p.MainWindowTitle) }
        else { Send 'MIC-DEPLOY-FAIL: restart-only, process died' }
        Log 'deploy restart-only'
        return
    }
    # wait until the transfer settles
    $prev = -1
    for ($i = 0; $i -lt 8; $i++) {
        $cur = (Get-Item -LiteralPath $cand.FullName).Length
        if ($cur -eq $prev) { break }
        $prev = $cur
        Start-Sleep -Seconds 2
    }
    $hash = Sha256 $cand.FullName
    $size = (Get-Item -LiteralPath $cand.FullName).Length
    New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null
    Get-Process -Name 'MicDesktop' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 3
    $dst = Join-Path $TargetDir 'MicDesktop.exe'
    try {
        Copy-Item -LiteralPath $cand.FullName -Destination $dst -Force -ErrorAction Stop
        Unblock-File -LiteralPath $dst -ErrorAction SilentlyContinue
    } catch {
        Send ('MIC-DEPLOY-FAIL: copy ' + $_.Exception.Message); return
    }
    $proc = Start-Process -FilePath $dst -WorkingDirectory $TargetDir -PassThru
    Start-Sleep -Seconds 12
    $p = Find-App
    if ($p) {
        Send ("MIC-DEPLOY-OK size=" + $size + " sha256=" + $hash + " pid=" + $p.Id + " title=" + $p.MainWindowTitle)
    } else {
        Send ("MIC-DEPLOY-FAIL: process died; size=" + $size + " sha256=" + $hash)
    }
    Log ("deploy done size=$size sha=$hash")
}

function DoStatus {
    $p = Get-Process -Name 'MicDesktop' -ErrorAction SilentlyContinue
    if ($p) {
        $ids = ($p | ForEach-Object { $_.Id }) -join ','
        $t = ($p | Where-Object { $_.MainWindowTitle } | Select-Object -First 1).MainWindowTitle
        Send ("MIC-STATUS running=" + $p.Count + " pid=" + $ids + " title=" + $t)
    } else {
        Send 'MIC-STATUS running=0'
    }
}

function DoLog {
    $f = Join-Path $TargetDir 'log.txt'
    if (-not (Test-Path $f)) { Send 'MIC-LOG-FAIL: no log.txt'; return }
    $tail = Get-Content -LiteralPath $f -Tail 12 -ErrorAction SilentlyContinue
    Send ('MIC-LOG ' + (($tail | ForEach-Object { $_.Trim() }) -join ' || '))
}

New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
$last = [long]0
if (Test-Path $StateFile) {
    try { $last = [long](Get-Content $StateFile -Raw | ConvertFrom-Json).last } catch { $last = 0 }
} else {
    $last = [long]([DateTimeOffset]::Now.ToUnixTimeMilliseconds())
    SaveState $last
}
Log ("watcher start last=$last api=$Api")
Send ('MIC-WATCHER-ON host=' + $env:COMPUTERNAME + ' pid=' + $PID)

while ($true) {
    try {
        $bs = Invoke-RestMethod -Uri ($Api + '/api/bubbles') -TimeoutSec 8
        foreach ($b in $bs) {
            if ([string]$b.from -ne $From) { continue }
            $bt = [long]$b.time
            if ($bt -le $last) { continue }
            $c = [string]$b.content
            if ($c -match '\[mic-deploy\]') { Log 'cmd deploy'; DoDeploy }
            elseif ($c -match '\[mic-status\]') { Log 'cmd status'; DoStatus }
            elseif ($c -match '\[mic-log\]') { Log 'cmd log'; DoLog }
            $last = $bt
            SaveState $last
        }
    } catch {
        Log ("poll-fail " + $_.Exception.Message)
    }
    Start-Sleep -Seconds $IntervalSec
}
