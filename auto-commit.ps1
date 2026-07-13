# ============================================================
# auto-commit.ps1 — Автоматический коммит и пуш на GitHub
# Запусти этот скрипт один раз, и все изменения в папке
# будут автоматически отправляться на GitHub.
# ============================================================

$env:PATH += ";C:\Program Files\Git\bin"
$ProjectPath = $PSScriptRoot
$CheckIntervalSeconds = 10   # Проверяем изменения каждые 10 секунд

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Sokratanti — Авто-коммит запущен" -ForegroundColor Cyan
Write-Host "  Папка: $ProjectPath" -ForegroundColor Cyan
Write-Host "  Нажми Ctrl+C чтобы остановить" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan

Set-Location $ProjectPath

function Commit-And-Push {
    $status = git status --porcelain
    if ($status) {
        $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
        $changedFiles = ($status -replace '^\s*\S+\s+', '' | Select-Object -First 5) -join ", "
        $message = "auto: $timestamp | $changedFiles"

        git add --all
        git commit -m $message
        git push origin main 2>$null
        if ($LASTEXITCODE -ne 0) {
            git push origin master 2>$null
        }

        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] ✅ Закоммичено: $message" -ForegroundColor Green
    }
}

Write-Host ""
Write-Host "Слежу за изменениями..." -ForegroundColor Yellow

while ($true) {
    try {
        Commit-And-Push
    } catch {
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] ⚠️  Ошибка: $_" -ForegroundColor Red
    }
    Start-Sleep -Seconds $CheckIntervalSeconds
}
