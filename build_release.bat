@echo off
setlocal

set "ROOT=%~dp0"
set "PYTHON=%ROOT%\.venv\Scripts\python.exe"
set "VENDOR_DIR=%ROOT%build\vendor"
set "VENDOR_CLOUDFLARED=%VENDOR_DIR%\cloudflared.exe"
set "DIST_DIR=%ROOT%dist\DBInputSync"
set "RELEASE_DIR=%ROOT%release"
set "RELEASE_NAME=DBInputSync-Windows-Portable"
set "ARCHIVE_BASE=%RELEASE_DIR%\%RELEASE_NAME%"
set "ARCHIVE_ZIP=%ARCHIVE_BASE%.zip"

if not exist "%PYTHON%" (
    echo [build] Missing virtual environment: "%PYTHON%"
    echo [build] Please create .venv and install requirements first.
    exit /b 1
)

echo [build] Installing PyInstaller...
"%PYTHON%" -m pip install pyinstaller >nul
if errorlevel 1 exit /b 1

echo [build] Locating cloudflared...
for /f "usebackq delims=" %%I in (`powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$candidates = @(); $pathHit = (Get-Command cloudflared -ErrorAction SilentlyContinue).Source; if ($pathHit) { $candidates += $pathHit }; $wingetRoot = Join-Path $env:LOCALAPPDATA 'Microsoft\\WinGet\\Packages'; if (Test-Path $wingetRoot) { Get-ChildItem $wingetRoot -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -like 'Cloudflare.cloudflared_*' } | ForEach-Object { $candidates += (Join-Path $_.FullName 'cloudflared.exe') } }; $candidates += (Join-Path $env:ProgramFiles 'cloudflared\\cloudflared.exe'); $candidates += (Join-Path ${env:ProgramFiles(x86)} 'cloudflared\\cloudflared.exe'); $candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1"`) do set "CLOUDFLARED_PATH=%%I"

if not defined CLOUDFLARED_PATH (
    echo [build] cloudflared.exe was not found. Install it first, then rerun this script.
    exit /b 1
)

echo [build] Using cloudflared: %CLOUDFLARED_PATH%
if exist "%VENDOR_DIR%" rmdir /s /q "%VENDOR_DIR%"
mkdir "%VENDOR_DIR%"
copy /y "%CLOUDFLARED_PATH%" "%VENDOR_CLOUDFLARED%" >nul

echo [build] Cleaning previous artifacts...
if exist "%ROOT%build\pyinstaller" rmdir /s /q "%ROOT%build\pyinstaller"
if exist "%ROOT%dist" rmdir /s /q "%ROOT%dist"
if exist "%RELEASE_DIR%" rmdir /s /q "%RELEASE_DIR%"
mkdir "%RELEASE_DIR%"

echo [build] Packaging application...
"%PYTHON%" -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onedir ^
  --name DBInputSync ^
  --distpath "%ROOT%dist" ^
  --workpath "%ROOT%build\pyinstaller" ^
  --add-data "%ROOT%templates;templates" ^
  --add-data "%ROOT%hot-rule.txt;." ^
  --add-binary "%VENDOR_CLOUDFLARED%;." ^
  "%ROOT%main.py"
if errorlevel 1 exit /b 1

echo [build] Preparing portable package...
copy /y "%ROOT%release-start.bat" "%DIST_DIR%\start.bat" >nul
copy /y "%ROOT%README.md" "%DIST_DIR%\README.md" >nul
copy /y "%ROOT%LICENSE" "%DIST_DIR%\LICENSE" >nul

echo [build] Creating zip archive...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Compress-Archive -Path '%DIST_DIR%\*' -DestinationPath '%ARCHIVE_ZIP%' -Force"
if errorlevel 1 exit /b 1

echo.
echo [build] Portable folder: %DIST_DIR%
echo [build] Release archive: %ARCHIVE_ZIP%
echo [build] Users can unzip and run start.bat directly.

endlocal
