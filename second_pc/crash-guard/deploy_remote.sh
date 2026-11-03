#!/usr/bin/env bash
# Deploy PCCrashGuard to the second PC over the RelayDesk/flix shell channel.
# Payload = gzip + base64, one command per file (PowerShell 5.1 friendly).
set -uo pipefail

SRC='F:\projects\mic-desktop\second_pc\crash-guard'
DST='C:\Users\Administrator\PCCrashGuard-src'
FLIX='/f/projects/flix-cli/dist/RelayDesk.exe'
PEER='d8c6383e'
DEPLOY_LOG='/f/projects/mic-desktop/second_pc/crash-guard/deploy.log'

log() { echo "$(date '+%F %T') $*" | tee -a "$DEPLOY_LOG"; }
run() { ( cd /f/projects/flix-cli/dist && timeout 90 "$FLIX" shell --timeout 60 "$PEER" "$1" 2>&1 ) | tail -5; }

log "== deploy start =="
run "powershell -NoProfile -Command \"New-Item -ItemType Directory -Force -Path '$DST' | Out-Null; 'dir-ok'\""

for f in guard.ps1 guard.json install_guard.ps1 diagnose.ps1; do
  B64=$(python -I - "$SRC\\$f" <<'PY'
import base64,gzip,sys
p=sys.argv[1]
data=open(p,'rb').read()
print(base64.b64encode(gzip.compress(data)).decode())
PY
)
  remote="$DST\\$f"
  cmd="powershell -NoProfile -Command \"\$b='$B64'; \$ms=New-Object IO.MemoryStream(,[Convert]::FromBase64String(\$b)); \$gz=New-Object IO.Compression.GzipStream(\$ms,[IO.Compression.CompressionMode]::Decompress); \$o=New-Object IO.MemoryStream; \$gz.CopyTo(\$o); \$gz.Dispose(); [IO.File]::WriteAllBytes('$remote',\$o.ToArray()); (Get-Item '$remote').Length\""
  out=$(run "$cmd")
  log "push $f ($(wc -c < "$SRC\\$f")B) -> $out"
done

log "== install =="
install_out=$(run "powershell -NoProfile -ExecutionPolicy Bypass -File '$DST\\install_guard.ps1'")
echo "$install_out" | tee -a "$DEPLOY_LOG"

log "== verify =="
run "powershell -NoProfile -Command \"(Get-ScheduledTask PCCrashGuard).State; (Get-CimInstance Win32_Process -Filter \\\"Name='powershell.exe'\\\" | Where-Object {\$_.CommandLine -like '*PCCrashGuard*'} | Select-Object -First 1).ProcessId\""
run "powershell -NoProfile -Command \"Get-Content \"\$env:LOCALAPPDATA\PCCrashGuard\guard.log\" -Tail 6\""
log "== deploy end =="
