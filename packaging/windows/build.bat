@echo off
setlocal
cd /d "%~dp0"
echo Building the portable Windows application. Internet is required on first build.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1"
set "BUILD_EXIT=%ERRORLEVEL%"
echo.
if not "%BUILD_EXIT%"=="0" echo Build failed. Please send build.log back for diagnosis.
pause
exit /b %BUILD_EXIT%
