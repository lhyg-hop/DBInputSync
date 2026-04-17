@echo off
setlocal

set "ROOT=%~dp0"
set "PYTHON=%ROOT%\.venv\Scripts\python.exe"
set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"

if not exist "%PYTHON%" (
    echo [DBInputSync] Missing virtual environment: "%PYTHON%"
    echo Please reinstall dependencies first.
    pause
    exit /b 1
)

echo [DBInputSync] Stopping existing DBInputSync Python processes...
"%PS%" -NoProfile -ExecutionPolicy Bypass -Command ^
  "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*DBInputSync*main.py*' } | ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop; Write-Host ('Stopped python PID=' + $_.ProcessId) } catch {} }"

echo [DBInputSync] Stopping existing cloudflared processes...
"%PS%" -NoProfile -ExecutionPolicy Bypass -Command ^
  "Get-Process cloudflared -ErrorAction SilentlyContinue | ForEach-Object { try { Stop-Process -Id $_.Id -Force -ErrorAction Stop; Write-Host ('Stopped cloudflared PID=' + $_.Id) } catch {} }"

echo [DBInputSync] Starting fresh instance...
cd /d "%ROOT%"
"%PYTHON%" main.py

endlocal
