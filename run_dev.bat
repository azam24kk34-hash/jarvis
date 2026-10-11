@echo off
chcp 65001 >nul
REM Запуск Jarvis без збірки (потрібен Python 3.10+)
python -m pip install -r requirements.txt
python main.py
