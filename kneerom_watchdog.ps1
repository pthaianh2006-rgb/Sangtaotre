$ErrorActionPreference = "SilentlyContinue"

$taskName = "KneeROM Public"
$logPath = "D:\bme\logs\watchdog.log"
$backendHealthy = $false
$tunnelHealthy = $false

try {
    $response = Invoke-WebRequest -Uri "http://127.0.0.1:5001/health" -UseBasicParsing -TimeoutSec 5
    $backendHealthy = $response.StatusCode -eq 200
} catch {
    $backendHealthy = $false
}

$tunnelHealthy = $null -ne (Get-Process -Name "cloudflared" -ErrorAction SilentlyContinue)

if (-not ($backendHealthy -and $tunnelHealthy)) {
    schtasks.exe /End /TN $taskName 2>$null | Out-Null
    Start-Sleep -Seconds 3
    schtasks.exe /Run /TN $taskName | Out-Null
    Add-Content -LiteralPath $logPath -Value "$(Get-Date -Format o) restart backend=$backendHealthy tunnel=$tunnelHealthy"
} else {
    Set-Content -LiteralPath $logPath -Value "$(Get-Date -Format o) healthy backend=true tunnel=true"
}
