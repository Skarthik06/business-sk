# -----------------------------------------------------------------------------
# pull_db_backups.ps1 - keep a copy of the server's nightly database backups on THIS laptop.
#
# The server (scripts/db_backup.sh, 03:00 IST) writes ~/backups/<date>/{sk_studio,affiliate_rag_bot,
# scraper_api}.dump. This copies every day that isn't here yet into
#   %USERPROFILE%\Documents\BusinessSK-DB-backups\<date>\
# and keeps the last 30 days. Read-only on the server. Runs from Windows Task Scheduler
# ("BusinessSK DB backup": daily + at log-on, catches up if the laptop was off):
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\pull_db_backups.ps1 -Install
# Restore a file (needs PostgreSQL's pg_restore):  pg_restore -d <database> --clean --if-exists <file>.dump
# -----------------------------------------------------------------------------
param([switch]$Install)

$ErrorActionPreference = "Stop"
$Server = "opc@140.238.247.18"
$Key    = Join-Path $env:USERPROFILE ".ssh\oracle_business_sk"
$Dest   = Join-Path $env:USERPROFILE "Documents\BusinessSK-DB-backups"
$Keep   = 30
$Log    = Join-Path $Dest "pull.log"

if ($Install) {
    $script = $MyInvocation.MyCommand.Path
    $action = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`""
    $triggers = @((New-ScheduledTaskTrigger -Daily -At "10:00"), (New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME))
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -RunOnlyIfNetworkAvailable
    Register-ScheduledTask -TaskName "BusinessSK DB backup" -Action $action -Trigger $triggers -Settings $settings `
        -Description "Copies the Business-SK server's nightly database backups to Documents\BusinessSK-DB-backups" -Force | Out-Null
    Write-Output "Installed scheduled task 'BusinessSK DB backup' (daily 10:00 + at log-on)."
    exit 0
}

New-Item -ItemType Directory -Force -Path $Dest | Out-Null
function Log($m) { $line = "$(Get-Date -Format s)  $m"; Add-Content -Path $Log -Value $line -Encoding UTF8; Write-Output $line }

$ssh = "ssh.exe"; $scp = "scp.exe"
$opts = @("-i", $Key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", "-o", "StrictHostKeyChecking=accept-new")
$days = & $ssh @opts $Server "ls -1 ~/backups 2>/dev/null | grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'"
if ($LASTEXITCODE -ne 0) { Log "server not reachable - will try again next run"; exit 1 }

$new = 0
foreach ($d in $days) {
    $local = Join-Path $Dest $d
    if (Test-Path (Join-Path $local "sk_studio.dump")) { continue }
    New-Item -ItemType Directory -Force -Path $local | Out-Null
    & $scp @opts "${Server}:~/backups/$d/*.dump" "$local"
    if ($LASTEXITCODE -ne 0) { Log "copy of $d failed"; Remove-Item -Recurse -Force $local; continue }
    $size = "{0:N0} KB" -f ((Get-ChildItem $local -Filter *.dump | Measure-Object Length -Sum).Sum / 1KB)
    Log "copied $d ($size)"
    $new++
}
# keep the last $Keep days
Get-ChildItem $Dest -Directory | Where-Object { $_.Name -match '^\d{4}-\d{2}-\d{2}$' } |
    Sort-Object Name -Descending | Select-Object -Skip $Keep | ForEach-Object { Remove-Item -Recurse -Force $_.FullName; Log "removed old $($_.Name)" }
if ($new -eq 0) { Log "up to date" }
