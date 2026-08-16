@echo off
setlocal
cd /d "%~dp0"
echo Edge TTS HTTP Server starting...
echo Keep this window open while the service is running.
echo Swagger: http://127.0.0.1:5050/docs
echo.
"%~dp0edge-tts-server.exe"
echo.
echo Service stopped. Press any key to close this window.
pause >nul
