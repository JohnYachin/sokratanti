# ============================================================
# crypto-notify.ps1 — Часовые уведомления Windows с сигналами
# ============================================================

$env:PATH += ";C:\Program Files\Git\bin"

function Get-CryptoSignals {
    $coins = @(
        'bitcoin','ethereum','binancecoin','solana','ripple',
        'dogecoin','cardano','avalanche-2','chainlink','polkadot',
        'near','litecoin','uniswap','pepe','sui',
        'aptos','hedera-hashgraph','internet-computer','filecoin','arbitrum'
    )
    $ids = $coins -join ','
    $url = "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&ids=$ids&order=market_cap_desc&per_page=30&page=1&price_change_percentage=7d"
    try {
        $data = Invoke-RestMethod -Uri $url -TimeoutSec 15
        return $data
    } catch {
        Write-Host "Ошибка API: $_" -ForegroundColor Red
        return $null
    }
}

function Get-Signal($coin) {
    $price  = $coin.current_price
    $high   = if ($coin.high_24h) { $coin.high_24h } else { $price * 1.05 }
    $low    = if ($coin.low_24h)  { $coin.low_24h  } else { $price * 0.95 }
    $ch24   = $coin.price_change_percentage_24h
    $ch7d   = $coin.price_change_percentage_7d_in_currency
    $range  = if ($high -ne $low) { $high - $low } else { $price * 0.1 }
    $rsi    = [Math]::Max(0, [Math]::Min(100, (($price - $low) / $range) * 100))
    $vol    = $coin.total_volume
    $mcap   = if ($coin.market_cap -gt 0) { $coin.market_cap } else { 1 }
    $volR   = $vol / $mcap
    $score  = 0

    if ($ch24 -gt 6)  { $score += 3 }
    elseif ($ch24 -gt 2) { $score += 2 }
    elseif ($ch24 -gt 0) { $score += 1 }
    elseif ($ch24 -lt -6){ $score -= 3 }
    elseif ($ch24 -lt -2){ $score -= 2 }
    elseif ($ch24 -lt 0) { $score -= 1 }

    if ($ch7d -gt 12)  { $score += 2 }
    elseif ($ch7d -gt 4)  { $score += 1 }
    elseif ($ch7d -lt -12){ $score -= 2 }
    elseif ($ch7d -lt -4) { $score -= 1 }

    if ($rsi -lt 25)  { $score += 3 }
    elseif ($rsi -lt 40) { $score += 1 }
    elseif ($rsi -gt 75) { $score -= 3 }
    elseif ($rsi -gt 60) { $score -= 1 }

    if ($volR -gt 0.12) {
        if ($ch24 -gt 0) { $score += 2 } else { $score -= 1 }
    }

    $atr = $range * 0.5
    if ($score -ge 2) {
        $signal = "LONG"
        $entry  = $price
        $sl     = $low  * 0.985
        $tp1    = $price + $atr * 1.5
        $tp2    = $high * 1.03
    } elseif ($score -le -2) {
        $signal = "SHORT"
        $entry  = $price
        $sl     = $high * 1.015
        $tp1    = $price - $atr * 1.5
        $tp2    = $low  * 0.97
    } else {
        $signal = "NEUTRAL"
        $entry  = $price
        $sl     = $low  * 0.97
        $tp1    = $high * 1.02
        $tp2    = $null
    }

    return @{
        signal  = $signal
        score   = $score
        entry   = $entry
        tp1     = $tp1
        tp2     = $tp2
        sl      = $sl
        rsi     = $rsi
        ch24    = $ch24
    }
}

function Format-Price($n) {
    if ($n -ge 10000) { return "`$$([Math]::Round($n, 0).ToString('N0'))" }
    if ($n -ge 1)     { return "`$$([Math]::Round($n, 2))" }
    if ($n -ge 0.001) { return "`$$([Math]::Round($n, 4))" }
    return "`$$([Math]::Round($n, 6))"
}

function Send-WinNotification($title, $body) {
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType = WindowsRuntime] | Out-Null

    $template = @"
<toast>
  <visual>
    <binding template="ToastGeneric">
      <text>$title</text>
      <text>$body</text>
    </binding>
  </visual>
</toast>
"@
    try {
        $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
        $xml.LoadXml($template)
        $toast = New-Object Windows.UI.Notifications.ToastNotification $xml
        $notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Crypto Analyzer")
        $notifier.Show($toast)
    } catch {
        # Fallback: balloon tip
        Add-Type -AssemblyName System.Windows.Forms
        $icon = New-Object System.Windows.Forms.NotifyIcon
        $icon.Icon = [System.Drawing.SystemIcons]::Information
        $icon.Visible = $true
        $icon.ShowBalloonTip(8000, $title, $body, [System.Windows.Forms.ToolTipIcon]::Info)
        Start-Sleep -Seconds 2
        $icon.Dispose()
    }
}

function Run-HourlyAlert {
    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Запрашиваю данные..." -ForegroundColor Cyan

    $coins = Get-CryptoSignals
    if (-not $coins) { Write-Host "Нет данных" -ForegroundColor Yellow; return }

    $signals = @()
    foreach ($c in $coins) {
        $a = Get-Signal $c
        $signals += [PSCustomObject]@{
            Name    = $c.name
            Symbol  = $c.symbol.ToUpper()
            Signal  = $a.signal
            Score   = $a.score
            Entry   = $a.entry
            TP1     = $a.tp1
            TP2     = $a.tp2
            SL      = $a.sl
            RSI     = $a.rsi
            Ch24    = $a.ch24
        }
    }

    $longs  = $signals | Where-Object { $_.Signal -eq 'LONG'  } | Sort-Object Score -Descending | Select-Object -First 5
    $shorts = $signals | Where-Object { $_.Signal -eq 'SHORT' } | Sort-Object Score             | Select-Object -First 5

    Write-Host ""
    Write-Host "═══════════════════════════════════════" -ForegroundColor DarkGray
    Write-Host "  CRYPTO SIGNALS — $(Get-Date -Format 'HH:mm dd.MM.yyyy')" -ForegroundColor White
    Write-Host "═══════════════════════════════════════" -ForegroundColor DarkGray
    Write-Host ""

    Write-Host "📈 ТОП LONG:" -ForegroundColor Green
    foreach ($s in $longs) {
        Write-Host "  $($s.Symbol) | Вход: $(Format-Price $s.Entry) | TP1: $(Format-Price $s.TP1) | SL: $(Format-Price $s.SL) | RSI: $([Math]::Round($s.RSI,1)) | Score: +$($s.Score)" -ForegroundColor Green
    }

    Write-Host ""
    Write-Host "📉 ТОП SHORT:" -ForegroundColor Red
    foreach ($s in $shorts) {
        Write-Host "  $($s.Symbol) | Вход: $(Format-Price $s.Entry) | TP1: $(Format-Price $s.TP1) | SL: $(Format-Price $s.SL) | RSI: $([Math]::Round($s.RSI,1)) | Score: $($s.Score)" -ForegroundColor Red
    }
    Write-Host ""

    # Windows notification
    $longTxt  = ($longs  | ForEach-Object { "📈 $($_.Symbol): Вход $(Format-Price $_.Entry) → TP $(Format-Price $_.TP1)" }) -join "`n"
    $shortTxt = ($shorts | ForEach-Object { "📉 $($_.Symbol): Вход $(Format-Price $_.Entry) → TP $(Format-Price $_.TP1)" }) -join "`n"
    $body = "$longTxt`n$shortTxt"

    $totalLong  = ($signals | Where-Object { $_.Signal -eq 'LONG'  }).Count
    $totalShort = ($signals | Where-Object { $_.Signal -eq 'SHORT' }).Count
    Send-WinNotification "🚀 Крипто-сигналы ($totalLong LONG / $totalShort SHORT)" $body

    # Push stats to log
    $logLine = "$(Get-Date -Format 'yyyy-MM-dd HH:mm') | LONG:$totalLong SHORT:$totalShort | " +
               (($longs | Select-Object -First 3 | ForEach-Object { "$($_.Symbol)+$($_.Score)" }) -join ", ")
    Add-Content -Path "$PSScriptRoot\signals.log" -Value $logLine
    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Уведомление отправлено. Лог: signals.log" -ForegroundColor Cyan
}

# ── MAIN LOOP ──
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  CRYPTO NOTIFIER — запущен" -ForegroundColor Cyan
Write-Host "  Уведомления каждый час + при запуске" -ForegroundColor Cyan
Write-Host "  Ctrl+C для остановки" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan

# First run immediately
Run-HourlyAlert

# Then every hour
while ($true) {
    $now = Get-Date
    $nextHour = $now.AddHours(1).Date.AddHours($now.AddHours(1).Hour)
    $waitMs = ($nextHour - $now).TotalMilliseconds
    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Следующий сигнал в $($nextHour.ToString('HH:mm'))..." -ForegroundColor DarkGray
    Start-Sleep -Milliseconds $waitMs
    Run-HourlyAlert
}
