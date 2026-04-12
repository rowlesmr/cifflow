@echo off
cd /d "%~dp0.."
.venv\Scripts\pytest --tb=short -q %*
pause