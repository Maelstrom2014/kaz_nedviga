@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Установка зависимостей...
pip install -r requirements.txt -q
echo.
echo Запуск приложения...
python app.py
pause
