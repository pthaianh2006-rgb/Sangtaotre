$ErrorActionPreference = "Stop"

$projectRoot = "D:\bme\aclproject2-main\aclproject2-main"
$python = "D:\bme\.venv\Scripts\python.exe"
$cloudflared = "C:\Program Files (x86)\cloudflared\cloudflared.exe"
$tunnelConfig = "C:\Users\DELL\.cloudflared\config.yml"
$secretFile = "D:\bme\.kneerom_secret"
$databaseSecretFile = "D:\bme\.kneerom_database_url"
$sqliteRollbackMarker = "D:\bme\.kneerom_use_sqlite"
$databaseProbe = "D:\bme\scripts\check_postgres.py"
$logDir = "D:\bme\logs"

$createdNew = $false
$mutex = [Threading.Mutex]::new($true, "Local\KneeROMPublic", [ref]$createdNew)
if (-not $createdNew) { exit 0 }

New-Item -ItemType Directory -Path $logDir -Force | Out-Null

function Stop-StaleKneeListener([int]$Port) {
    $listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    foreach ($listener in $listeners) {
        $processId = [int]$listener.OwningProcess
        if ($processId -gt 0 -and $processId -ne $PID) {
            Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
        }
    }
}

# Task Scheduler may stop the launcher before its child processes. Clear only
# KneeROM's dedicated web and tunnel-metrics listeners before starting again.
Stop-StaleKneeListener 5001
Stop-StaleKneeListener 20242
Start-Sleep -Milliseconds 800

if (-not (Test-Path -LiteralPath $secretFile)) {
    $bytes = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    [Convert]::ToBase64String($bytes) | Set-Content -LiteralPath $secretFile -NoNewline -Encoding ascii
}

$env:ACL_SECRET_KEY = (Get-Content -LiteralPath $secretFile -Raw).Trim()
$env:ACL_COOKIE_SECURE = "1"
$env:ACL_DEBUG = "0"
$env:ACL_ENABLE_HARDWARE = "1"
$env:ACL_CAMERA_SOURCE = "Camo"
$env:ACL_CAMERA_WIDTH = "1920"
$env:ACL_CAMERA_HEIGHT = "1080"
$env:ACL_CAMERA_FPS = "30"
$env:ACL_USE_IMU = "1"
$env:ACL_IMU_MODE = "ble"
$env:ACL_BLE_NAME = "KneeROM_7C0A7C"
$env:ACL_IMU_HOST = "192.168.4.1"
$env:ACL_IMU_TCP_PORT = "8080"
$env:ACL_DATABASE = Join-Path $projectRoot "database.db"
$env:ACL_EXPORT_DIR = Join-Path $projectRoot "exports"

if (Test-Path -LiteralPath $sqliteRollbackMarker) {
    Remove-Item Env:ACL_DATABASE_URL -ErrorAction SilentlyContinue
    Set-Content -LiteralPath (Join-Path $logDir "launcher.status.log") -Value "database=sqlite rollback=active" -Encoding ascii
} else {
    if (-not (Test-Path -LiteralPath $databaseSecretFile)) {
        throw "PostgreSQL secret is missing. Use rollback_to_sqlite.ps1 for intentional SQLite recovery."
    }
    $env:ACL_DATABASE_URL = (Get-Content -LiteralPath $databaseSecretFile -Raw).TrimStart([char]0xFEFF).Trim()
    & $python $databaseProbe
    if ($LASTEXITCODE -ne 0) { throw "PostgreSQL preflight failed; KneeROM was not started." }
    Set-Content -LiteralPath (Join-Path $logDir "launcher.status.log") -Value "database=postgresql status=ready" -Encoding ascii
}

function Start-KneeBackend {
    Start-Process -FilePath $python `
        -ArgumentList @("-m", "waitress", "--listen=127.0.0.1:5001", "--threads=8", "app:app") `
        -WorkingDirectory $projectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logDir "waitress.out.log") `
        -RedirectStandardError (Join-Path $logDir "waitress.err.log") `
        -PassThru
}

function Start-KneeTunnel {
    Start-Process -FilePath $cloudflared `
        -ArgumentList @("tunnel", "--config", $tunnelConfig, "run", "kneerom-local") `
        -WorkingDirectory "D:\bme" `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logDir "cloudflared.out.log") `
        -RedirectStandardError (Join-Path $logDir "cloudflared.err.log") `
        -PassThru
}

$backend = $null
$tunnel = $null
try {
    $backend = Start-KneeBackend
    $backendReady = $false
    for ($attempt = 1; $attempt -le 15; $attempt++) {
        if ($backend.HasExited) {
            throw "KneeROM backend exited during startup (exit code $($backend.ExitCode))."
        }
        try {
            Invoke-RestMethod -Uri "http://127.0.0.1:5001/health" -TimeoutSec 3 | Out-Null
            $backendReady = $true
            break
        } catch {
            Start-Sleep -Seconds 2
        }
    }
    if (-not $backendReady) {
        throw "KneeROM backend did not become healthy within 30 seconds."
    }
    $tunnel = Start-KneeTunnel

    while ($true) {
        Start-Sleep -Seconds 5
        if ($backend.HasExited) {
            Start-Sleep -Seconds 2
            $backend = Start-KneeBackend
        }
        if ($tunnel.HasExited) {
            Start-Sleep -Seconds 2
            $tunnel = Start-KneeTunnel
        }
    }
} finally {
    foreach ($process in @($backend, $tunnel)) {
        if ($null -ne $process -and -not $process.HasExited) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        }
    }
    if ($createdNew) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
