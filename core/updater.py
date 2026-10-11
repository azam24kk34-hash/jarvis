"""Оновлення Джарвіса з GitHub Releases вашого репозиторію.
Під час збірки GitHub Actions записує назву репозиторію в assets/build_info.json.
Коли ви публікуєте реліз (тег v7.0.1 тощо), Джарвіс помітить нову версію і за командою «онови себе»
завантажить Jarvis-Setup.exe і встановить його тихо."""
import json
import os
import re
import subprocess
import tempfile

import requests

from .config import resource_path


def build_info():
    try:
        with open(resource_path("assets/build_info.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _ver(s):
    return tuple(int(x) for x in re.findall(r"\d+", s or "")[:3]) or (0,)


def check(current):
    """→ (нова_версія, url_інсталятора) або (None, None). Приватні репозиторії API без токена не віддає — тоді мовчимо."""
    repo = build_info().get("repo")
    if not repo:
        return None, None
    r = requests.get(f"https://api.github.com/repos/{repo}/releases/latest", timeout=10,
                     headers={"Accept": "application/vnd.github+json"})
    if r.status_code != 200:
        return None, None
    rel = r.json()
    tag = rel.get("tag_name", "")
    if _ver(tag) <= _ver(current):
        return None, None
    url = next((a["browser_download_url"] for a in rel.get("assets", []) if a.get("name") == "Jarvis-Setup.exe"), None)
    return tag.lstrip("v"), url


def install(url, on_progress=None):
    """Завантажити інсталятор і запустити тихе встановлення (він сам закриє й перезапустить Джарвіса)."""
    path = os.path.join(tempfile.gettempdir(), "Jarvis-Setup.exe")
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        done = 0
        with open(path, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
                done += len(chunk)
                if on_progress and total:
                    on_progress(done * 100 // total)
    subprocess.Popen([path, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"],
                     creationflags=0x00000008 if os.name == "nt" else 0)
    return path
