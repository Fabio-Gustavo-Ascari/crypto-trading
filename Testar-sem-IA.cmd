@echo off
cd /d "%~dp0"
"%~dp0.venv\Scripts\python.exe" -m crypto_harness once --observe
pause
