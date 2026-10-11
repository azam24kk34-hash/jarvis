"""Браузер Джарвіса на справжньому Microsoft Edge (через Playwright)."""
import pathlib

import pytest

from conftest import windows_only

PAGE = """<html><head><meta charset="utf-8"><title>Тестова сторінка</title></head><body>
<a href="#about">Про нас</a>
<input type="search" placeholder="Пошук" id="q">
<button aria-label="Знайти" onclick="document.getElementById('r').innerText='Результати: '+document.getElementById('q').value">🔍</button>
<main><p id="r">порожньо</p></main></body></html>"""


@pytest.fixture(scope="module")
def browser(tmp_path_factory):
    from core.browser import Browser
    from core.config import Config
    b = Browser(Config())
    yield b
    try:
        b.close()
    except Exception:
        pass


@windows_only
def test_edge_snapshot_type_click_read(browser, tmp_path):
    f = tmp_path / "page.html"
    f.write_text(PAGE, encoding="utf-8")
    print(browser.open(pathlib.Path(f).as_uri()))
    snap = browser.snapshot()
    print(snap)
    assert 'search "Пошук"' in snap and 'button "Знайти"' in snap
    browser.type("Залізна людина", target="Пошук")
    browser.click(text="Знайти")
    assert "Результати: Залізна людина" in browser.read()
    assert len(browser.screenshot()) > 1000
