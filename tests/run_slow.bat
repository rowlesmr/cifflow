@echo off
cd /d "%~dp0.."
.venv\Scripts\pytest -m slow --tb=short -q %*
pause