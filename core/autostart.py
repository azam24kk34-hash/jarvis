"""Автозапуск Джарвіса разом з Windows (HKCU\\...\\Run, без прав адміністратора)."""
import os
import platform
import sys

KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
NAME = "Jarvis"


def command():
    if getattr(sys, "frozen", False):          # зібраний Jarvis.exe
        return f'"{sys.executable}" --autostart'
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    main = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "main.py"))
    return f'"{pyw if os.path.exists(pyw) else sys.executable}" "{main}" --autostart'


def set_enabled(on: bool):
    if platform.system() != "Windows":
        return False
    import winreg
    # CreateKeyEx: у «свіжому» профілі Windows розділу Run може ще не існувати
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, NAME, 0, winreg.REG_SZ, command())
        else:
            try:
                winreg.DeleteValue(k, NAME)
            except FileNotFoundError:
                pass
    return on


def is_enabled() -> bool:
    if platform.system() != "Windows":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY) as k:
            winreg.QueryValueEx(k, NAME)
            return True
    except OSError:
        return False
