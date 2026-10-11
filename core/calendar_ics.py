"""Календар через приватне iCal-посилання (Google Calendar, Outlook, Apple) — без входу в акаунт.
Google: Налаштування календаря → «Секретна адреса у форматі iCal»."""
import datetime as dt
import re
import time

import requests

_cache = {}  # url -> (час, байти)


def _fetch(url):
    url = url.strip().replace("webcal://", "https://")
    hit = _cache.get(url)
    if hit and time.time() - hit[0] < 300:
        return hit[1]
    data = requests.get(url, timeout=15).content
    _cache[url] = (time.time(), data)
    return data


def _local(v):
    """date/datetime з календаря → локальний naive datetime."""
    if isinstance(v, dt.datetime):
        if v.tzinfo:
            v = v.astimezone().replace(tzinfo=None)
        return v, False
    return dt.datetime.combine(v, dt.time()), True


def events(urls: str, days: int = 1, start: dt.date = None):
    """Події з усіх календарів на `days` днів, починаючи зі `start` (типово — сьогодні)."""
    import icalendar
    import recurring_ical_events

    start = start or dt.date.today()
    t0 = dt.datetime.combine(start, dt.time())
    t1 = t0 + dt.timedelta(days=max(1, days))
    out = []
    for url in [u for u in re.split(r"[\s,;]+", urls or "") if u.startswith(("http", "webcal"))]:
        cal = icalendar.Calendar.from_ical(_fetch(url))
        for ev in recurring_ical_events.of(cal).between(t0, t1):
            s, all_day = _local(ev.get("DTSTART").dt)
            e = ev.get("DTEND")
            e = _local(e.dt)[0] if e else s
            out.append({
                "title": str(ev.get("SUMMARY", "Без назви")),
                "start": s.isoformat(timespec="minutes"), "end": e.isoformat(timespec="minutes"),
                "all_day": all_day, "location": str(ev.get("LOCATION", "") or ""),
            })
    out.sort(key=lambda x: (x["start"][:10], not x["all_day"], x["start"]))
    return out
