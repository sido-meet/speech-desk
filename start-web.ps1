param(
    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$App = Join-Path $Root "web_app.py"
$Stdout = Join-Path $Root "work\web.stdout.log"
$Stderr = Join-Path $Root "work\web.stderr.log"
$Url = "http://127.0.0.1:$Port"
$QwenPython = Join-Path $Root ".venv-qwen\Scripts\python.exe"
$QwenApp = Join-Path $Root "qwen_worker.py"
$QwenModel = Join-Path $Root "models-qwen\Qwen3-ASR-0.6B\model.safetensors"
$QwenPort = 8766

if (-not (Test-Path -LiteralPath $Python)) {
    Write-Host "Python virtual environment not found: $Python" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

$Listening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if (-not $Listening) {
    Start-Process `
        -FilePath $Python `
        -ArgumentList @($App, "--no-browser", "--host", "127.0.0.1", "--port", $Port) `
        -WorkingDirectory $Root `
        -WindowStyle Hidden `
        -RedirectStandardOutput $Stdout `
        -RedirectStandardError $Stderr

    $Ready = $false
    for ($Attempt = 0; $Attempt -lt 30; $Attempt++) {
        Start-Sleep -Milliseconds 200
        try {
            $Health = Invoke-RestMethod -Uri "$Url/api/health" -TimeoutSec 1
            if ($Health.ok) {
                $Ready = $true
                break
            }
        } catch {
            # The server is still starting.
        }
    }
    if (-not $Ready) {
        Write-Host "Vosk web service failed to start. See: $Stderr" -ForegroundColor Red
        Read-Host "Press Enter to exit"
        exit 1
    }
}

$QwenListening = Get-NetTCPConnection -LocalPort $QwenPort -State Listen -ErrorAction SilentlyContinue
if ((Test-Path -LiteralPath $QwenPython) -and (Test-Path -LiteralPath $QwenModel) -and -not $QwenListening) {
    Start-Process `
        -FilePath $QwenPython `
        -ArgumentList @($QwenApp, "--host", "127.0.0.1", "--port", $QwenPort) `
        -WorkingDirectory $Root `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $Root "work\qwen.stdout.log") `
        -RedirectStandardError (Join-Path $Root "work\qwen.stderr.log")
}

Start-Process $Url
