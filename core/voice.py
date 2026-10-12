"""Голос Jarvis: постійне прослуховування з wake word «Джарвіс», розпізнавання українською, озвучення через edge-tts."""
import asyncio
import difflib
import hashlib
import os
import queue
import random
import re
import threading
import time

import numpy as np

WAKE_REPLIES = ["Так, сер?", "Слухаю, сер.", "До ваших послуг.", "Для вас, сер, — завжди.", "Я тут, сер.", "Слухаю уважно."]

FOLLOW_UP_SECONDS = 20  # скільки секунд після відповіді можна говорити без «Джарвіс»
# як Google іноді «чує» ім'я — теж вважаємо зверненням
EXTRA_WAKES = ["джарвіс", "джарвис", "джарвіса", "джарвісе", "джервіс", "джерві", "жарвіс", "чарвіс", "джавіс", "джаріс",
               "гарвіс", "джарвез", "джарвес", "jarvis", "jarvys", "javis", "джарвиз", "джарвізе", "харвіс"]
SLEEP_RE = re.compile(r"\b(спи|засни|не слухай|режим сну|відпочинь|помовч|тихий режим)\b")
WAKE_RE = re.compile(r"\b(прокинься|прокидайся|слухай мене|я тут|режим розмови)\b")
AWAKE_SECONDS = 8      # скільки чекати команду після одного лише «Джарвіс»


class Voice:
    def __init__(self, config, on_command, on_state, play_audio, on_heard=None, on_stop=None, on_notice=None):
        """
        on_command(text)  — розпізнана команда для мозку
        on_state(state)   — 'idle' | 'listening' | 'awake' | 'recognizing' | 'speaking' | 'mic_error'
        play_audio(mp3_bytes) — відтворює звук (у HUD) і блокує до завершення
        """
        self.config = config
        self.on_command = on_command
        self.on_state = on_state
        self.play_audio = play_audio
        self.on_heard = on_heard or (lambda t: None)
        self.on_stop = on_stop or (lambda: None)
        self.on_notice = on_notice or (lambda t: None)
        self.on_ignored = lambda t: None       # показати в HUD, що почуто, але без «Джарвіс»
        self.sleeping = False                 # «Джарвіс, спи» — слухає лише своє ім'я
        self.music_playing = lambda: False    # під час музики завжди потрібне «Джарвіс» (щоб не реагувати на пісні)
        self.level = 0.0
        self._stop = False
        self.busy = False
        from .config import app_dir
        self._cache_dir = app_dir() / "voice_cache"
        self._cache_dir.mkdir(exist_ok=True)
        self.speaking = False
        self.muted = False
        self._speak_end = 0.0
        self._awake_until = 0.0
        self.tick = 0.0
        self._force_command = False
        self._speak_lock = threading.Lock()
        self.mic_ok = False

    # ------------------------------------------------------------ TTS
    async def _tts_async(self, text):
        import edge_tts
        com = edge_tts.Communicate(text, self.config.get("voice"), rate=self.config.get("voice_rate"),
                                   pitch=self.config.get("voice_pitch"))
        data = bytearray()
        async for chunk in com.stream():
            if chunk["type"] == "audio":
                data += chunk["data"]
        return bytes(data)

    def _cache_path(self, text):
        key = hashlib.md5(f"{self.config.get('voice')}|{self.config.get('voice_rate')}|"
                          f"{self.config.get('voice_pitch')}|{text}".encode()).hexdigest()
        return self._cache_dir / f"{key}.mp3"

    def tts(self, text, cache=False):
        """Синтез мови. cache=True — зберегти на диск (для фраз, що мають звучати навіть офлайн)."""
        p = self._cache_path(text)
        if p.exists():
            return p.read_bytes()
        audio = asyncio.run(self._tts_async(text))
        if cache and audio:
            try:
                p.write_bytes(audio)
            except Exception:
                pass
        return audio

    def prewarm(self, phrases):
        """Заздалегідь синтезувати короткі фрази, щоб Джарвіс відповідав миттєво."""
        def run():
            for t in phrases:
                try:
                    self.tts(t, cache=True)
                except Exception:
                    return
        threading.Thread(target=run, daemon=True).start()

    def stop(self):
        """Перервати мовлення (Esc, клік по реактору або Ctrl+Alt+J під час відповіді)."""
        if self.speaking:
            self._stop = True
            self.on_stop()

    def stream(self, follow_up=True, cache=False, on_sentence=None):
        """Потокове мовлення: текст подається шматками (з Claude), озвучується реченнями одразу."""
        return SpeechStream(self, follow_up=follow_up, cache=cache, on_sentence=on_sentence)

    def speak(self, text, follow_up=True, cache=False):
        if not text:
            return
        s = self.stream(follow_up=follow_up, cache=cache)
        if cache:
            s.push(text)          # коротка фраза з кешу — цілою
        else:
            s.feed(text)
        s.end()
        s.wait()

    # ------------------------------------------------------------ STT
    def push_to_talk(self):
        """Кнопка мікрофона / гаряча клавіша: наступна фраза — команда без wake word.
        Якщо Джарвіс саме говорить — перебиваємо його (як Тоні у фільмі)."""
        self._force_command = True
        self.stop()
        self.on_state("awake")

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _strip_wake(self, text):
        """Повертає (чи є wake word, решта фрази)."""
        words = re.findall(r"[\w'’-]+", text.lower())
        wakes = [w.lower() for w in self.config.get("wake_words")] + EXTRA_WAKES
        for i, w in enumerate(words):
            pair = w + (words[i + 1] if i + 1 < len(words) else "")  # «джар віс» → «джарвіс»
            for cand, used in ((w, 1), (pair, 2)):
                if cand in wakes or any(cand.startswith(k) for k in wakes if len(k) > 4) \
                        or (len(cand) >= 5 and difflib.get_close_matches(cand, wakes, n=1, cutoff=0.72)):
                    rest = " ".join(words[:i] + words[i + used:]).strip(" ,.!?")
                    return True, rest
        return False, text

    # ------------------------------------------------------------ рівень мікрофона
    def _on_audio(self, data):
        try:
            x = np.frombuffer(data, dtype=np.int16).astype(np.float32)
            rms = float(np.sqrt(np.mean(x * x))) if len(x) else 0.0
            self.level = 0.0 if self.speaking else min(1.0, (rms / 5000.0) ** 0.6)
        except Exception:
            pass

    def _load_detector(self):
        from .config import resource_path
        from .wakeword import WakeWordDetector
        return WakeWordDetector(resource_path(os.path.join("assets", "wakeword")),
                                threshold=float(self.config.get("wake_threshold") or 0.5))

    def _loop(self):
        import speech_recognition as sr
        from .wakeword import CHUNK
        r = sr.Recognizer()
        r.pause_threshold = 0.6
        r.dynamic_energy_threshold = True
        r.operation_timeout = 8  # запит до Google не може «повиснути» назавжди — інакше Джарвіс перестає чути
        try:
            mic = sr.Microphone(sample_rate=16000)
            with mic as source:
                r.adjust_for_ambient_noise(source, duration=1.0)
            # якщо під час калібрування грала музика чи був шум — поріг злітає й голос «не чути»
            r.energy_threshold = max(150, min(r.energy_threshold, 1500))
            print(f"Мікрофон відкрито, поріг гучності {r.energy_threshold:.0f}")
            if self.config.get("mic_energy_threshold"):
                r.energy_threshold = int(self.config.get("mic_energy_threshold"))
                r.dynamic_energy_threshold = False
            self.mic_ok = True
            self.on_state("listening")
        except Exception as e:
            print("Mic error:", repr(e))
            self.on_state("mic_error")
            return

        detector, detector_failed = None, False
        with mic as source:
            source.stream = _Tap(source.stream, self._on_audio)
            while True:
                self.tick = time.time()
                always = self.always_active()
                offline = self.config.get("wake_engine") == "offline" and not detector_failed and not always
                if offline and detector is None:
                    try:
                        detector = self._load_detector()
                        print("Офлайн wake word завантажено")
                    except Exception as e:
                        print("Wake word model error:", repr(e))
                        detector_failed = True
                        self.on_notice("Офлайн-розпізнавання «Hey Jarvis» недоступне, перемикаюсь на звичайний режим.")
                        continue
                if self.muted or self.speaking or (not self.config.get("wake_word_enabled") and not self._force_command):
                    source.stream.read(1024)  # читаємо, щоб у буфері не накопичувався старий звук
                    continue
                awake = self._force_command or time.time() < self._awake_until

                # ---- офлайн-очікування «Hey Jarvis»: звук не залишає комп'ютер
                if offline and not awake:
                    if self._awake_until:
                        self._awake_until = 0
                        self.on_state("listening")
                    data = source.stream.read(CHUNK)
                    score = detector.process(data)
                    if time.time() - self._speak_end < 0.8 or score < detector.threshold:
                        continue
                    detector.reset()
                    self.on_heard("Hey Jarvis")
                    self._wake_reply()
                    continue

                # ---- прослуховування фрази (Google)
                try:
                    audio = r.listen(source, timeout=2, phrase_time_limit=10)
                except sr.WaitTimeoutError:
                    if self._awake_until and time.time() > self._awake_until and not self._force_command:
                        self._awake_until = 0
                        self.on_state("listening")
                    continue
                except Exception as e:
                    print("Listen error:", repr(e))
                    time.sleep(1)
                    continue
                # відкидаємо те, що мікрофон почув під час власної мови Jarvis
                if self.speaking or time.time() - self._speak_end < 0.5 or self.muted:
                    continue
                awake = self._force_command or time.time() < self._awake_until
                if awake:
                    self.on_state("recognizing")
                try:
                    res = r.recognize_google(audio, language="uk-UA", show_all=True)
                    alts = [a.get("transcript", "") for a in (res or {}).get("alternative", [])] if isinstance(res, dict) else []
                    if not alts:
                        raise sr.UnknownValueError()
                    # «Джарвіс» шукаємо в усіх варіантах розпізнавання, а не лише в першому
                    text = next((a for a in alts if self._strip_wake(a)[0]), alts[0])
                except sr.UnknownValueError:
                    if awake:
                        self.on_state("awake" if self._force_command else "listening")
                    continue
                except Exception as e:
                    print("Recognize error:", repr(e))
                    self.on_state("listening")
                    time.sleep(1)
                    continue
                text = (text or "").strip()
                if not text:
                    continue
                has_wake, rest = self._strip_wake(text)
                low = text.lower()
                # «Джарвіс, спи» / «Джарвіс, прокинься» — режим сну без звернення до мозку
                if (has_wake or awake) and SLEEP_RE.search(low):
                    self._force_command, self._awake_until, self.sleeping = False, 0, True
                    self.on_heard(text)
                    self.speak("Добре. Скажіть «Джарвіс, прокинься», коли знадоблюсь.", follow_up=False)
                    continue
                if self.sleeping:
                    if has_wake and (WAKE_RE.search(low) or not rest):
                        self.sleeping = False
                        self.on_heard(text)
                        self.speak("Я знову з вами, сер.", follow_up=True)
                        continue
                    if not has_wake:
                        continue
                if not (has_wake or awake or self._force_command or self.always_active()):
                    print(f"Почуто без «Джарвіс»: {text[:60]}")
                    self.on_ignored(text)
                    continue
                if self._force_command or awake:
                    self._force_command = False
                    self._awake_until = 0
                    self.on_heard(text)
                    self.on_command(rest if has_wake and rest else text)
                elif has_wake:
                    self.on_heard(text)
                    if rest and len(rest) > 2:
                        self.on_command(rest)
                    else:
                        self._wake_reply()
                elif always and len(re.findall(r"\w+", text)) >= 2:
                    # режим «завжди слухаю»: будь-яка фраза — до Джарвіса (мозок сам відкине чужі розмови)
                    self.on_heard(text)
                    self.on_command(text, ambient=True)
                # інакше — звичайна розмова в кімнаті, ігноруємо

    def always_active(self):
        """Чи слухати все без «Джарвіс» просто зараз."""
        if self.config.get("listen_mode") != "always" or self.sleeping:
            return False
        try:
            return not self.music_playing()
        except Exception:
            return True

    def _wake_reply(self):
        self.on_state("awake")
        if self.config.get("speak_responses"):
            self.speak(random.choice(WAKE_REPLIES), follow_up=False, cache=True)
        self._awake_until = time.time() + AWAKE_SECONDS
        self.on_state("awake")


class _Tap:
    """Обгортка потоку мікрофона: кожен прочитаний шматок іде ще й на вимірювання рівня."""

    def __init__(self, inner, cb):
        self._inner, self._cb = inner, cb

    def read(self, size):
        data = self._inner.read(size)
        self._cb(data)
        return data

    def __getattr__(self, name):
        return getattr(self._inner, name)


class SpeechStream:
    """Текст → речення → синтез → відтворення, усе конвеєром.
    Перше речення починає звучати, щойно Claude його дописав, а наступні синтезуються паралельно."""

    BOUNDARY = re.compile(r"[.!?…]+[»\")]?(?=\s)")

    def __init__(self, voice, follow_up=True, cache=False, on_sentence=None):
        self.v = voice
        self.follow_up = follow_up
        self.cache = cache
        self.on_sentence = on_sentence or (lambda t: None)
        self.buf = ""
        self.first = True
        self.texts = queue.Queue()
        self.done = threading.Event()
        threading.Thread(target=self._run, daemon=True, name="speech").start()

    # ---- вхід
    def feed(self, delta):
        self.buf += delta
        ends = [m.end() for m in self.BOUNDARY.finditer(self.buf)]
        if not ends:
            return
        eager = self.first or self.texts.empty()   # синтезатор вільний — віддаємо одразу перше речення
        target = 20 if eager else 90
        ok = [e for e in ends if e >= target]
        if not ok:
            return
        cut = ok[0] if eager else ends[-1]
        self.push(self.buf[:cut])
        self.buf = self.buf[cut:].lstrip()

    def push(self, text):
        text = re.sub(r"[*#`_]+", "", text).strip()
        if re.fullmatch(r"[\s—–\-.]*", text):  # «—» = «це не до мене» (режим «завжди слухаю»)
            return
        if text:
            self.first = False
            try:
                self.on_sentence(text)
            except Exception:
                pass
            self.texts.put(text)

    def end(self):
        if self.buf.strip():
            self.push(self.buf)
        self.buf = ""
        self.texts.put(None)

    def wait(self, timeout=None):
        self.done.wait(timeout)

    # ---- конвеєр
    def _produce(self, audio_q):
        v = self.v
        while True:
            t = self.texts.get()
            if t is None:
                break
            if v._stop or not v.config.get("speak_responses"):
                continue
            try:
                audio_q.put(v.tts(t, cache=self.cache))
            except Exception as e:
                print("TTS error:", repr(e))
        audio_q.put(None)

    def _run(self):
        v = self.v
        audio_q = queue.Queue()
        threading.Thread(target=self._produce, args=(audio_q,), daemon=True, name="tts").start()
        started = False
        # чекаємо на перший звук БЕЗ блокування: поки Claude думає, інші фрази (нагадування, «Так, сер?») можуть звучати
        first = audio_q.get()
        lock = v._speak_lock if first is not None else threading.Lock()
        with lock:
            try:
                audio = first
                while True:
                    if audio is None:
                        break
                    if not v._stop:
                        if not started:
                            started = True
                            v.speaking = True
                        v.on_state("speaking")
                        v.play_audio(audio)
                    while True:
                        try:
                            audio = audio_q.get(timeout=0.4)
                            break
                        except queue.Empty:
                            if started and v.busy:
                                v.on_state("thinking")   # Джарвіс договорив речення, а мозок ще працює
            finally:
                if started:
                    v.speaking = False
                    v._speak_end = time.time()
                    if self.follow_up and not v._stop and v.config.get("wake_word_enabled"):
                        v._awake_until = time.time() + FOLLOW_UP_SECONDS
                    if v.busy:
                        v.on_state("thinking")
                    elif not v._force_command:
                        v.on_state("listening" if v.mic_ok else "idle")
                v._stop = False
                self.done.set()
