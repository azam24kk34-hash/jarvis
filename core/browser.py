"""Браузер Джарвіса: він сам відкриває сайти, натискає кнопки, вводить текст і читає сторінки.

Працює через Playwright з окремим профілем Microsoft Edge (є в кожній Windows 10/11) або Chrome.
Профіль зберігається в %APPDATA%\\Jarvis\\browser — у ньому один раз входите в YouTube Music / Google,
і далі Джарвіс користується вашим акаунтом.

Playwright не можна викликати з різних потоків, тому всі дії виконуються в одному потоці «browser»
через чергу, а інструменти лише чекають на результат."""
import base64
import os
import queue
import re
import shutil
import subprocess
import threading
import time

from .config import app_dir

PROFILE_DIR = app_dir() / "browser"

SNAPSHOT_JS = r"""
(maxItems) => {
  const sel = 'a[href],button,input:not([type=hidden]),textarea,select,[role=button],[role=link],[role=tab],' +
              '[role=menuitem],[role=option],[role=checkbox],[role=switch],[role=searchbox],[contenteditable=true]';
  document.querySelectorAll('[data-jref]').forEach(e => e.removeAttribute('data-jref'));
  const out = [];
  const seen = new Set();
  const H = window.innerHeight, W = window.innerWidth;
  for (const el of document.querySelectorAll(sel)) {
    if (out.length >= maxItems) break;
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4 || r.bottom < 0 || r.top > H * 1.5 || r.right < 0 || r.left > W) continue;
    const st = getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none' || parseFloat(st.opacity) === 0) continue;
    let name = (el.getAttribute('aria-label') || el.innerText || el.getAttribute('placeholder') ||
                el.getAttribute('title') || el.value || (el.querySelector('img') || {}).alt || '').trim();
    name = name.replace(/\s+/g, ' ').slice(0, 80);
    const tag = el.tagName.toLowerCase();
    const role = el.getAttribute('role') || (tag === 'a' ? 'link' : tag === 'input' ? (el.type || 'input') : tag);
    if (!name && !['input', 'textarea', 'search', 'text', 'searchbox'].includes(role)) continue;
    const key = role + '|' + name + '|' + Math.round(r.top / 8);
    if (seen.has(key)) continue;
    seen.add(key);
    const id = out.length + 1;
    el.setAttribute('data-jref', String(id));
    out.push(`[${id}] ${role} "${name}"`);
  }
  return out.join('\n');
}
"""

READ_JS = r"""
() => {
  const pick = document.querySelector('main, article, [role=main]') || document.body;
  return (pick.innerText || '').replace(/\n{3,}/g, '\n\n').trim();
}
"""

# ---------------- YouTube Music (кілька варіантів селекторів: інтерфейс YT Music іноді змінюється)
YTM_NOW_JS = r"""
() => {
  const m = navigator.mediaSession && navigator.mediaSession.metadata;
  const v = document.querySelector('video');
  const bar = document.querySelector('ytmusic-player-bar');
  const txt = s => { const e = bar && bar.querySelector(s); return e ? (e.innerText || e.title || '').trim() : ''; };
  // назва: панель плеєра → mediaSession → заголовок вкладки («Пісня - YouTube Music»)
  const t = txt('.title') || (m && m.title) || document.title.replace(/\s*[-–|]\s*YouTube Music\s*$/i, '').trim();
  const a = (txt('.byline').split('•')[0] || '').trim() || (m && m.artist) || '';
  return {title: t || '', artist: (a || '').trim(), album: m ? m.album : '', playing: !!(v && !v.paused && !v.ended),
          signed_in: !document.querySelector('a[href*="accounts.google.com/ServiceLogin"], ytmusic-settings-button a[href*="signin"]')};
}
"""

YTM_PLAY_HEADER_JS = r"""
(shuffle) => {
  const want = shuffle ? /(shuffle|перемішати|перемешать|випадков)/i : /^(play|відтворити|воспроизвести|слухати|play all)/i;
  const other = shuffle ? /^(play|відтворити|воспроизвести)/i : /(shuffle|перемішати|перемешать)/i;
  const roots = document.querySelectorAll('ytmusic-responsive-header-renderer, ytmusic-detail-header-renderer, ytmusic-immersive-header-renderer, #header, ytmusic-browse-response');
  const cands = [];
  for (const root of roots.length ? roots : [document]) {
    root.querySelectorAll('button, a, ytmusic-play-button-renderer, yt-button-shape, [role=button]').forEach(b => cands.push(b));
  }
  const label = b => (b.getAttribute('aria-label') || b.innerText || b.title || '').trim();
  let btn = cands.find(b => want.test(label(b))) || cands.find(b => other.test(label(b)));
  if (!btn) btn = document.querySelector('ytmusic-responsive-header-renderer ytmusic-play-button-renderer, ytmusic-detail-header-renderer ytmusic-play-button-renderer');
  if (!btn) return false;
  btn.scrollIntoView({block: 'center'});
  btn.click();
  return label(btn) || true;
}
"""

YTM_PLAYLISTS_JS = r"""
() => {
  const out = [];
  document.querySelectorAll('ytmusic-two-row-item-renderer, ytmusic-responsive-list-item-renderer').forEach(it => {
    const a = it.querySelector('a[href*="list="]');
    const t = it.querySelector('.title, yt-formatted-string.title');
    if (a && t) out.push({title: t.innerText.trim(), href: a.href});
  });
  return out;
}
"""

YTM_FIRST_SONG_JS = r"""
() => {
  // верхній результат або перша пісня в пошуку
  const btn = document.querySelector('ytmusic-card-shelf-renderer ytmusic-play-button-renderer') ||
              document.querySelector('ytmusic-shelf-renderer ytmusic-responsive-list-item-renderer ytmusic-play-button-renderer') ||
              document.querySelector('ytmusic-play-button-renderer');
  if (!btn) return false;
  btn.scrollIntoView({block: 'center'});
  btn.click();
  return true;
}
"""

YTM_CONTROL_JS = r"""
(action) => {
  const v = document.querySelector('video');
  const bar = document.querySelector('ytmusic-player-bar') || document;
  const click = s => { const b = bar.querySelector(s); if (b) { b.click(); return true; } return false; };
  if (action === 'pause') { if (v) v.pause(); return !!v; }
  if (action === 'resume') { if (v) v.play(); return !!v; }
  if (action === 'next') return click('.next-button, tp-yt-paper-icon-button.next-button, [aria-label*="Next"], [aria-label*="Наступ"]');
  if (action === 'previous') return click('.previous-button, [aria-label*="Previous"], [aria-label*="Попере"]');
  if (action === 'like') return click('ytmusic-like-button-renderer #button-shape-like button, ytmusic-like-button-renderer .like, [aria-label="Like"], [aria-label*="Подоба"]');
  if (action === 'shuffle') return click('.shuffle, [aria-label*="Shuffle"], [aria-label*="Перемі"]');
  if (action === 'repeat') return click('.repeat, [aria-label*="Repeat"], [aria-label*="Повтор"]');
  if (action.startsWith('volume:')) { if (v) { v.volume = Math.max(0, Math.min(1, parseInt(action.slice(7)) / 100)); return true; } }
  return false;
}
"""


def _find_exe(names):
    """Шлях до Edge/Chrome для звичайного (не автоматизованого) запуску — щоб увійти в акаунт."""
    pf = [os.environ.get(k, "") for k in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA")]
    rel = {"msedge": r"Microsoft\Edge\Application\msedge.exe", "chrome": r"Google\Chrome\Application\chrome.exe"}
    for n in names:
        for base in pf:
            p = os.path.join(base, rel[n]) if base else ""
            if p and os.path.exists(p):
                return p
        w = shutil.which(n)
        if w:
            return w
    return None


class Browser:
    YTM_HOST = "music.youtube.com"

    def __init__(self, config):
        self.config = config
        self._q = queue.Queue()
        self._thread = None
        self._pw = self._ctx = self._page = None
        self.music_playing = False
        self._duck_lock = threading.Lock()
        self._duck_want = self._duck_state = False
        self._duck_pending = False

    # ------------------------------------------------------------ інфраструктура
    def call(self, fn, *args, timeout=90):
        """Виконати fn(*args) у потоці браузера й повернути результат."""
        if not self._thread or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._loop, daemon=True, name="browser")
            self._thread.start()
        box = {}
        done = threading.Event()
        self._q.put((fn, args, box, done))
        if not done.wait(timeout):
            raise TimeoutError("Браузер не відповідає")
        if "error" in box:
            raise box["error"]
        return box.get("result")

    def _loop(self):
        while True:
            fn, args, box, done = self._q.get()
            try:
                box["result"] = fn(*args)
            except Exception as e:
                box["error"] = e
                if "closed" in str(e).lower() or "Target page" in str(e):
                    self._reset()
            finally:
                done.set()

    def _track(self, info):
        self.music_playing = bool(info.get("playing"))
        return info

    def _reset(self):
        self.music_playing = False
        for obj in (self._ctx, self._pw):
            try:
                if obj is self._pw and obj:
                    obj.stop()
                elif obj:
                    obj.close()
            except Exception:
                pass
        self._pw = self._ctx = self._page = None

    def _ensure(self):
        if self._ctx:
            try:
                pages = [p for p in self._ctx.pages if not p.is_closed()]
                if pages:
                    if not self._page or self._page.is_closed():
                        self._page = pages[-1]
                    return self._page
                self._page = self._ctx.new_page()
                return self._page
            except Exception:
                self._reset()
        from playwright.sync_api import sync_playwright
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        pref = self.config.get("browser_channel") or "msedge"
        channels = [pref] + [c for c in ("msedge", "chrome", None) if c != pref]
        last = None
        for ch in channels:
            try:
                self._ctx = self._pw.chromium.launch_persistent_context(
                    str(PROFILE_DIR), channel=ch, headless=bool(os.environ.get("JARVIS_HEADLESS")), no_viewport=True,
                    ignore_default_args=["--enable-automation"],
                    args=["--autoplay-policy=no-user-gesture-required", "--disable-blink-features=AutomationControlled",
                          "--no-default-browser-check", "--start-maximized"])
                print("Browser launched:", ch or "chromium")
                break
            except Exception as e:
                last = e
                print(f"Browser channel {ch} failed:", repr(e)[:200])
        if not self._ctx:
            self._pw.stop()
            self._pw = None
            hint = (" Схоже, профіль зайнятий: закрийте вікно входу Джарвіса й повторіть."
                    if last and ("in use" in str(last) or "exitCode=21" in str(last)) else "")
            raise RuntimeError(f"Не вдалося запустити браузер (Edge/Chrome).{hint} {str(last)[:200]}")
        self._ctx.on("page", lambda p: setattr(self, "_page", p))  # нові вкладки стають поточними
        self._page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        return self._page

    def _settle(self, page, ms=600):
        try:
            page.wait_for_load_state("domcontentloaded", timeout=8000)
        except Exception:
            pass
        page.wait_for_timeout(ms)

    def _window(self, state):
        """Згорнути / показати вікно браузера (CDP)."""
        page = self._ensure()
        s = self._ctx.new_cdp_session(page)
        wid = s.send("Browser.getWindowForTarget")["windowId"]
        if state == "normal":
            s.send("Browser.setWindowBounds", {"windowId": wid, "bounds": {"windowState": "normal"}})
            page.bring_to_front()
        else:
            s.send("Browser.setWindowBounds", {"windowId": wid, "bounds": {"windowState": state}})
        s.detach()

    # ------------------------------------------------------------ загальні дії (виконуються в потоці браузера)
    def _open(self, url):
        page = self._ensure()
        if not re.match(r"^[a-z]+://", url):
            local = re.match(r"^(localhost|127\.|192\.168\.|10\.)", url)
            url = ("http://" if local else "https://") + url
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        self._settle(page, 900)
        page.bring_to_front()
        return f"Відкрито: {page.title()} ({page.url})"

    def _snapshot(self):
        page = self._ensure()
        for attempt in range(3):  # сторінка могла саме перезавантажуватись
            try:
                items = page.evaluate(SNAPSHOT_JS, 120)
                break
            except Exception:
                if attempt == 2:
                    raise
                self._settle(page, 800)
        return f"Сторінка: {page.title()}\nURL: {page.url}\nЕлементи (використовуй номер у browser_click/browser_type):\n{items}"

    def _locate(self, page, ref=None, text=None):
        if ref:
            loc = page.locator(f'[data-jref="{int(ref)}"]')
            if loc.count():
                return loc.first
        if text:
            for loc in (page.get_by_role("button", name=text), page.get_by_role("link", name=text),
                        page.get_by_label(text), page.get_by_placeholder(text), page.get_by_text(text)):
                try:
                    if loc.count():
                        return loc.first
                except Exception:
                    continue
        raise RuntimeError("Елемент не знайдено — зроби browser_snapshot ще раз")

    def _click(self, ref=None, text=None):
        page = self._ensure()
        self._locate(page, ref, text).click(timeout=8000)
        self._settle(self._page or page)
        return f"Натиснуто. Тепер: {self._page.title()} ({self._page.url})"

    def _type(self, text, ref=None, target=None, enter=False):
        page = self._ensure()
        loc = self._locate(page, ref, target) if (ref or target) else None
        if loc:
            loc.click(timeout=8000)
            try:
                loc.fill(text, timeout=5000)
            except Exception:
                page.keyboard.type(text, delay=20)
        else:
            page.keyboard.type(text, delay=20)
        if enter:
            page.keyboard.press("Enter")
            self._settle(page, 1200)
        return f"Введено «{text}»{' + Enter' if enter else ''}. Сторінка: {page.title()}"

    def _press(self, key):
        page = self._ensure()
        page.keyboard.press(key)
        self._settle(page, 400)
        return f"Натиснуто {key}"

    def _scroll(self, direction):
        page = self._ensure()
        dy = {"down": 700, "up": -700, "bottom": 100000, "top": -100000}.get(direction, 700)
        page.mouse.wheel(0, dy)
        page.wait_for_timeout(500)
        return "Прокручено"

    def _read(self):
        page = self._ensure()
        txt = page.evaluate(READ_JS)
        return f"{page.title()} ({page.url})\n\n{txt[:6000]}"

    def _back(self):
        page = self._ensure()
        page.go_back(wait_until="domcontentloaded", timeout=15000)
        self._settle(page)
        return f"Назад: {page.title()}"

    def _shot(self):
        page = self._ensure()
        data = page.screenshot(type="jpeg", quality=70)
        return base64.b64encode(data).decode()

    def _tabs(self):
        self._ensure()
        return "\n".join(f"{i + 1}. {p.title()} — {p.url}" for i, p in enumerate(self._ctx.pages))

    def _close(self):
        self._reset()
        return "Браузер закрито"

    # ------------------------------------------------------------ YouTube Music
    def _ytm_goto(self, url):
        page = self._ensure()
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        self._settle(page, 2500)
        return page

    def _ytm_now(self, wait_playing=0):
        page = self._ensure()
        info = self._track(page.evaluate(YTM_NOW_JS))
        t0 = time.time()
        while wait_playing and not (info.get("playing") and info.get("title")) and time.time() - t0 < wait_playing:
            page.wait_for_timeout(700)
            info = self._track(page.evaluate(YTM_NOW_JS))
        return info

    def _ytm_ensure_playing(self, page):
        info = self._ytm_now(wait_playing=6)
        if not info.get("playing"):
            page.evaluate(YTM_CONTROL_JS, "resume")
            info = self._ytm_now(wait_playing=4)
        return info

    def _ytm_play_video(self, video_id):
        page = self._ytm_goto(f"https://music.youtube.com/watch?v={video_id}")
        return self._ytm_ensure_playing(page)

    def _ytm_search_play(self, query):
        from urllib.parse import quote_plus
        page = self._ytm_goto(f"https://music.youtube.com/search?q={quote_plus(query)}")
        if not page.evaluate(YTM_FIRST_SONG_JS):
            raise RuntimeError("Не знайшов кнопку відтворення в результатах пошуку")
        page.wait_for_timeout(1500)
        return self._ytm_ensure_playing(page)

    def _ytm_liked(self, shuffle=True):
        page = self._ytm_goto("https://music.youtube.com/playlist?list=LM")
        if not page.evaluate(YTM_PLAY_HEADER_JS, bool(shuffle)):
            info = page.evaluate(YTM_NOW_JS)
            if not info.get("signed_in"):
                raise PermissionError("NOT_SIGNED_IN")
            raise RuntimeError("Не знайшов кнопку відтворення плейлиста")
        page.wait_for_timeout(1500)
        return self._ytm_ensure_playing(page)

    def _ytm_playlists(self):
        page = self._ytm_goto("https://music.youtube.com/library/playlists")
        items = page.evaluate(YTM_PLAYLISTS_JS)
        if not items:
            info = page.evaluate(YTM_NOW_JS)
            if not info.get("signed_in"):
                raise PermissionError("NOT_SIGNED_IN")
        return items

    def _ytm_playlist(self, name, shuffle=False):
        import difflib
        items = self._ytm_playlists()
        if not items:
            raise RuntimeError("У бібліотеці немає плейлистів")
        titles = [i["title"] for i in items]
        low = name.lower()
        hit = next((i for i in items if low in i["title"].lower()), None)
        if not hit:
            m = difflib.get_close_matches(name, titles, n=1, cutoff=0.4)
            hit = next((i for i in items if m and i["title"] == m[0]), None)
        if not hit:
            raise LookupError("Плейлисти в бібліотеці: " + ", ".join(titles[:30]))
        page = self._ytm_goto(hit["href"])
        page.evaluate(YTM_PLAY_HEADER_JS, bool(shuffle))
        page.wait_for_timeout(1500)
        info = self._ytm_ensure_playing(page)
        info["playlist"] = hit["title"]
        return info

    def _ytm_control(self, action):
        page = self._ensure()
        if self.YTM_HOST not in page.url:
            for p in self._ctx.pages:
                if self.YTM_HOST in p.url:
                    page = self._page = p
                    break
            else:
                raise RuntimeError("YouTube Music зараз не відкритий")
        ok = page.evaluate(YTM_CONTROL_JS, action)
        page.wait_for_timeout(1200)
        info = self._track(page.evaluate(YTM_NOW_JS))
        info["ok"] = bool(ok)
        return info

    def _duck(self, on):
        for p in self._ctx.pages:
            if self.YTM_HOST in p.url:
                p.evaluate("""(on) => { const v = document.querySelector('video'); if (!v) return;
                    if (on) { if (window.__jvVol === undefined) { window.__jvVol = v.volume; v.volume = v.volume * 0.25; } }
                    else if (window.__jvVol !== undefined) { v.volume = window.__jvVol; delete window.__jvVol; } }""", on)

    def duck(self, on):
        """Приглушити музику, поки Джарвіс слухає або говорить (як у фільмі). Браузер не запускає.
        Береться лише останнє бажане значення, тож швидкі зміни стану не переплутаються."""
        if self._ctx is None:
            return
        with self._duck_lock:
            self._duck_want = bool(on)
            if self._duck_pending:
                return
            self._duck_pending = True

        def job():
            with self._duck_lock:
                want = self._duck_want
                self._duck_pending = False
            if want != self._duck_state:
                self._duck_state = want
                self._duck(want)
        threading.Thread(target=lambda: self._safe(job), daemon=True).start()

    @property
    def running(self):
        return self._ctx is not None

    def ytm_is_open(self):
        if self._ctx is None:
            return False
        try:
            return bool(self.call(lambda: any(self.YTM_HOST in p.url for p in self._ctx.pages), timeout=5))
        except Exception:
            return False

    def _safe(self, fn, *a):
        try:
            self.call(fn, *a, timeout=10)
        except Exception as e:
            print("Browser duck error:", repr(e)[:150])

    # ------------------------------------------------------------ публічні методи
    def open(self, url): return self.call(self._open, url)
    def snapshot(self): return self.call(self._snapshot)
    def click(self, ref=None, text=None): return self.call(self._click, ref, text)
    def type(self, text, ref=None, target=None, enter=False): return self.call(self._type, text, ref, target, enter)
    def press(self, key): return self.call(self._press, key)
    def scroll(self, direction): return self.call(self._scroll, direction)
    def read(self): return self.call(self._read)
    def back(self): return self.call(self._back)
    def screenshot(self): return self.call(self._shot)
    def tabs(self): return self.call(self._tabs)
    def close(self): return self.call(self._close)
    def window(self, state): return self.call(self._window, state)
    def ytm_play_video(self, vid): return self.call(self._ytm_play_video, vid)
    def ytm_search_play(self, q): return self.call(self._ytm_search_play, q)
    def ytm_liked(self, shuffle=True): return self.call(self._ytm_liked, shuffle)
    def ytm_playlist(self, name, shuffle=False): return self.call(self._ytm_playlist, name, shuffle)
    def ytm_playlists(self): return self.call(self._ytm_playlists)
    def ytm_control(self, action): return self.call(self._ytm_control, action)
    def ytm_now(self): return self.call(self._ytm_now)

    def login_window(self, url="https://music.youtube.com"):
        """Відкрити той самий профіль у ЗВИЧАЙНОМУ Edge/Chrome (без автоматизації), щоб увійти в Google:
        Google не пускає вхід у браузер, яким керує програма. Після входу вікно треба закрити."""
        self.call(self._close)
        exe = _find_exe([self.config.get("browser_channel") or "msedge", "msedge", "chrome"])
        if not exe:
            raise RuntimeError("Не знайдено Microsoft Edge або Google Chrome")
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        subprocess.Popen([exe, f"--user-data-dir={PROFILE_DIR}", "--no-first-run", "--no-default-browser-check", url])
        return exe
