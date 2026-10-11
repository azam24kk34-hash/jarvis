"""Керування OBS Studio через вбудований WebSocket-сервер (OBS 28+).
Увімкнути в OBS: Інструменти → Налаштування WebSocket-сервера → «Увімкнути», скопіювати пароль у налаштування Джарвіса.
Це надійніше за натискання кнопок мишею: працює, навіть якщо OBS згорнутий чи на іншому моніторі."""
import difflib
import time


class OBS:
    def __init__(self, config, toolbox=None):
        self.config = config
        self.tb = toolbox
        self._client = None

    def _connect(self, start_if_needed=True):
        import obsws_python as obs
        if self._client is not None:
            try:
                self._client.get_version()
                return self._client
            except Exception:
                self._client = None
        kw = dict(host=self.config.get("obs_host") or "localhost", port=int(self.config.get("obs_port") or 4455),
                  password=self.config.get("obs_password") or "", timeout=3)
        try:
            self._client = obs.ReqClient(**kw)
            return self._client
        except Exception as e:
            err = e
        if "auth" in repr(err).lower() or "password" in repr(err).lower():
            raise PermissionError("OBS_AUTH")
        if not start_if_needed or not self.tb:
            raise ConnectionError("OBS_OFFLINE")
        # OBS не запущений — запускаємо й чекаємо, поки підніметься WebSocket
        r = self.tb.resolve_app("obs")
        if not r:
            raise ConnectionError("OBS_NOT_FOUND")
        self.tb.launch_resolved(r)
        t0 = time.time()
        while time.time() - t0 < 30:
            time.sleep(2)
            try:
                self._client = obs.ReqClient(**kw)
                return self._client
            except Exception as e2:
                if "auth" in repr(e2).lower():
                    raise PermissionError("OBS_AUTH")
        raise ConnectionError("OBS_WS_OFF")

    def run(self, action, name=None):
        c = self._connect(start_if_needed=action not in ("status", "stop_stream", "stop_record"))
        if action == "start_stream":
            if c.get_stream_status().output_active:
                return "Трансляція вже йде"
            c.start_stream()
            return "Трансляцію запущено"
        if action == "stop_stream":
            c.stop_stream()
            return "Трансляцію зупинено"
        if action == "start_record":
            if c.get_record_status().output_active:
                return "Запис уже йде"
            c.start_record()
            return "Запис почато"
        if action == "stop_record":
            r = c.stop_record()
            return f"Запис зупинено{': ' + r.output_path if getattr(r, 'output_path', None) else ''}"
        if action == "pause_record":
            c.toggle_record_pause()
            return "Пауза/продовження запису"
        if action in ("start_virtual_cam", "stop_virtual_cam"):
            getattr(c, action)()
            return "Віртуальна камера " + ("увімкнена" if action.startswith("start") else "вимкнена")
        if action == "replay":
            c.save_replay_buffer()
            return "Повтор збережено"
        if action == "scenes":
            scenes = [s["sceneName"] for s in c.get_scene_list().scenes]
            return "Сцени: " + ", ".join(reversed(scenes))
        if action == "scene":
            scenes = [s["sceneName"] for s in c.get_scene_list().scenes]
            low = {x.lower(): x for x in scenes}
            q = (name or "").lower().strip()
            hit = next((x for x in scenes if q and q in x.lower()), None) or \
                low.get((difflib.get_close_matches(q, list(low), n=1, cutoff=0.4) or [""])[0])
            if not hit:
                return f"Немає сцени «{name}». Є: {', '.join(scenes)}"
            c.set_current_program_scene(hit)
            return f"Сцена: {hit}"
        if action in ("mute", "unmute", "toggle_mute"):
            inputs = [i["inputName"] for i in c.get_input_list().inputs]
            want = (name or "mic").lower()
            hit = next((i for i in inputs if want in i.lower()), None) or \
                next((i for i in inputs if any(k in i.lower() for k in ("mic", "мікро", "aux"))), None)
            if not hit:
                return f"Немає джерела «{name}». Є: {', '.join(inputs)}"
            if action == "toggle_mute":
                c.toggle_input_mute(hit)
            else:
                c.set_input_mute(hit, action == "mute")
            return f"{hit}: {'звук вимкнено' if action == 'mute' else 'звук увімкнено' if action == 'unmute' else 'перемкнено'}"
        if action == "status":
            s, r = c.get_stream_status(), c.get_record_status()
            scene = c.get_current_program_scene().current_program_scene_name
            st = f"трансляція {'йде ' + s.output_timecode[:8] if s.output_active else 'вимкнена'}"
            rc = f"запис {'йде ' + r.output_timecode[:8] if r.output_active else 'вимкнений'}"
            return f"OBS: {st}, {rc}, сцена «{scene}»"
        return f"Невідома дія OBS: {action}"


OBS_ERRORS = {
    "OBS_AUTH": "OBS не прийняв пароль WebSocket. Скопіюйте пароль з OBS (Інструменти → Налаштування WebSocket-сервера) у налаштування Джарвіса.",
    "OBS_WS_OFF": "OBS запущений, але WebSocket-сервер вимкнений. В OBS: Інструменти → Налаштування WebSocket-сервера → «Увімкнути».",
    "OBS_NOT_FOUND": "OBS Studio не знайдено на цьому ПК.",
    "OBS_OFFLINE": "OBS зараз не запущений.",
}
