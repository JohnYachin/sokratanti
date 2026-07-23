# autocommit.ps1 — запускается Планировщиком задач Windows каждые 5 минут
$env:PATH += ";C:\Program Files\Git\bin"
$repo = "C:\Users\GPD\Projects\sokratanti"
$log  = "$repo\autocommit.log"

Set-Location $repo

$status = git status --porcelain 2>&1
if ($status) {
    $ts    = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $files = ($status | ForEach-Object { ($_ -replace '^\s*\S+\s+','').Trim() } | Select-Object -First 5) -join ", "
    git add --all 2>&1 | Out-Null
    git commit -m "auto: $ts | $files" 2>&1 | Out-Null
    $push = git push origin main 2>&1
    Add-Content $log "[$ts] PUSHED: $files"
} else {
    Add-Content $log "[$(Get-Date -Format 'HH:mm:ss')] no changes"
}
