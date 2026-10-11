@echo off
chcp 65001 >nul
REM Локальна збірка Jarvis.exe на Windows (альтернатива GitHub Actions)
python -m pip install -r requirements.txt pyinstaller
pyinstaller --noconfirm --clean --onedir --windowed --name Jarvis --icon assets/icon.ico --add-data "ui;ui" --add-data "assets;assets" --collect-all speech_recognition --collect-all edge_tts --hidden-import pyaudio --hidden-import clr --hidden-import icalendar --hidden-import recurring_ical_events --collect-data tzdata --collect-binaries onnxruntime --collect-all playwright --collect-all uiautomation --collect-submodules comtypes --hidden-import obsws_python --hidden-import onnxruntime main.py
echo.
start /wait "" dist\Jarvis\Jarvis.exe --selftest
type "%TEMP%\jarvis_selftest.txt"
echo.
echo Готово: dist\Jarvis\Jarvis.exe  (інсталятор: встановіть Inno Setup і запустіть ISCC installer.iss)
pause
