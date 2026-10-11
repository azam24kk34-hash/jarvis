"""Друкує версію Джарвіса з main.py (для збірки в GitHub Actions)."""
import pathlib
import re

src = (pathlib.Path(__file__).resolve().parent.parent / "core" / "version.py").read_text(encoding="utf-8")
print(re.search(r'VERSION = "(.+?)"', src).group(1))
