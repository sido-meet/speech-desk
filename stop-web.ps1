param(
    [int]$Port = 8765
)

$Ports = @($Port, 8766)
$Connections = Get-NetTCPConnection -LocalPort $Ports -State Listen -ErrorAction SilentlyContinue
if (-not $Connections) {
    Write-Host "Vosk web service is not running."
    exit 0
}

$Connections | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object {
    Stop-Process -Id $_ -Force
}
Write-Host "Vosk web service stopped."
