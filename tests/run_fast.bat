@echo off
cd /d "%~dp0.."
.venv\Scripts\pytest -m "not slow" --tb=short -q %*
pause