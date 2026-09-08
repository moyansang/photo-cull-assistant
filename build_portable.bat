@echo off
setlocal
cd /d %~dp0
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_exe.ps1"
if errorlevel 1 goto :fail
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0make_portable_zip.ps1"
if errorlevel 1 goto :fail
echo.
echo Build finished. See dist folder.
pause
exit /b 0
:fail
echo.
echo Build failed. Please copy the error message and send it to ChatGPT.
pause
exit /b 1
