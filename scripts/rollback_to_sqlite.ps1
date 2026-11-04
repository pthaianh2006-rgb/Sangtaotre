$ErrorActionPreference = "Stop"
$marker = "D:\bme\.kneerom_use_sqlite"
$taskName = "KneeROM Public"

Set-Content -LiteralPath $marker -Value "SQLite rollback requested" -Encoding ascii
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($null -ne $task) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    Start-ScheduledTask -TaskName $taskName
}
Write-Output "SQLite rollback marker enabled. KneeROM will use the preserved database.db."
