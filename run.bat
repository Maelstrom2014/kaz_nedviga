@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Installing dependencies...
pip install -r requirements.txt -q
echo.
echo Starting application...
python app.py
pause
