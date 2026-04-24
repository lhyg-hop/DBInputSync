@echo off
setlocal

set "ROOT=%~dp0"
set "APP=%ROOT%DBInputSync.exe"

if not exist "%APP%" (
    echo [DBInputSync] Missing executable: "%APP%"
    pause
    exit /b 1
)

echo [DBInputSync] Stopping existing DBInputSync and cloudflared processes...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Get-CimInstance Win32_Process | Where-Object { ($_.Name -eq 'DBInputSync.exe') -or ($_.Name -eq 'python.exe' -and $_.CommandLine -like '*DBInputSync*main.py*') } | ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch {} }; Get-Process cloudflared -ErrorAction SilentlyContinue | ForEach-Object { try { Stop-Process -Id $_.Id -Force -ErrorAction Stop } catch {} }"

echo [DBInputSync] Starting portable release...
cd /d "%ROOT%"
start "" "%APP%"

timeout /t 3 >nul
start "" "http://127.0.0.1:5000/control"

endlocal
