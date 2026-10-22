"""Будильники и таймеры.

Будильники хранятся в настройках панели (config["alarms"]), таймеры — в
state/timers.json, чтобы пережить перезапуск сервиса. Срабатывание считает
сервер (фоновый поток раз в секунду), а звонит экран: в /api/revision есть
счётчик alarms, по нему киоск перечитывает /api/alarms и, пока список
ringing не пуст, показывает звонок поверх всего со звуком.

Звонок длится не дольше ring_minutes, потом снимается сам и оставляет в
шторке уведомление «Пропущен». «Отложить» переносит звонок на snooze минут.
"""

import datetime
import itertools
import json
import logging
import os
import re
import threading
import time

log = logging.getLogger(__name__)

TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
MAX_ALARMS = 20
MAX_TIMERS = 10
MAX_TIMER = 24 * 3600
DEFAULT_SETTINGS = {"snooze": 10, "ring_minutes": 10, "volume": 80, "sound": "bell"}


class AlarmError(ValueError):
    """Текст — исходная русская строка, переводит сервер (tr(e.text, **e.params))."""

    def __init__(self, text, **params):
        super().__init__(text)
        self.text = text
        self.params = params


def clean_alarm(alarm, existing_ids=()):
    """Проверяет будильник из запроса. days — дни недели 0 (пн) … 6 (вс),
    пусто — один раз (после звонка выключается)."""
    if not isinstance(alarm, dict):
        raise AlarmError("Некорректный будильник")
    t = str(alarm.get("time", ""))
    if not TIME_RE.match(t):
        raise AlarmError("Время будильника — ЧЧ:ММ")
    days = alarm.get("days") or []
    if not isinstance(days, list) or not all(isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 6 for d in days):
        raise AlarmError("Некорректные дни недели")
    alarm_id = str(alarm.get("id") or "")
    if not re.fullmatch(r"a\d{1,6}", alarm_id):
        alarm_id = "a" + str(max([int(i[1:]) for i in existing_ids if re.fullmatch(r"a\d+", i)] + [0]) + 1)
    return {
        "id": alarm_id,
        "time": t,
        "days": sorted(set(days)),
        "label": str(alarm.get("label") or "").strip()[:40],
        "enabled": alarm.get("enabled", True) is not False,
    }


class Alarms:
    def __init__(self, config, state_dir, notifications=None, tiles=None, clock=time.time, local=None):
        self.config = config
        self.path = os.path.join(state_dir, "timers.json")
        self.notifications = notifications
        self.tiles = tiles
        self.clock = clock
        # Местное время панели (подменяется в тестах).
        self.local = local or (lambda ts: datetime.datetime.fromtimestamp(ts))
        self.lock = threading.Lock()
        self.timers = self._load()
        self.ids = itertools.count(max([int(t["id"][1:]) for t in self.timers] + [0]) + 1)
        # Звонки: {"key", "kind": alarm|timer, "id", "label", "time", "since"}.
        self.ringing = []
        # Отложенные будильники: {"id", "label", "time", "at"}.
        self.snoozed = []
        # Растёт при любом изменении: киоск перечитывает /api/alarms.
        self.rev = 0
        self._last_minute = self._minute(self.clock())
        self._stop = threading.Event()
        self._thread = None

    # --- хранение --------------------------------------------------------------

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                items = json.load(f)
            return [t for t in items if isinstance(t, dict) and re.fullmatch(r"t\d+", str(t.get("id", "")))][:MAX_TIMERS]
        except (OSError, ValueError):
            return []

    def _save_timers(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.timers, f, ensure_ascii=False)
        os.replace(tmp, self.path)

    def settings(self):
        return dict(DEFAULT_SETTINGS, **(self.config.get("alarm") or {}))

    def save_settings(self, patch, sounds):
        if not isinstance(patch, dict):
            raise AlarmError("Ожидается объект")
        rules = {"snooze": (1, 60), "ring_minutes": (1, 60), "volume": (10, 100)}
        clean = {}
        for key, (lo, hi) in rules.items():
            if key in patch:
                v = patch[key]
                if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
                    raise AlarmError("Некорректная настройка звонка")
                clean[key] = v
        if "sound" in patch:
            if patch["sound"] not in sounds:
                raise AlarmError("Некорректная настройка звонка")
            clean["sound"] = patch["sound"]
        self.config.update({"alarm": clean})
        self._changed()

    def alarms(self):
        return self.config.get("alarms")

    def _changed(self):
        self.rev += 1
        self._update_tiles()
        self.config.notify()

    # --- будильники -------------------------------------------------------------

    def save_alarm(self, alarm):
        alarms = self.alarms()
        clean = clean_alarm(alarm, [a["id"] for a in alarms])
        pos = next((i for i, a in enumerate(alarms) if a["id"] == clean["id"]), None)
        if pos is None:
            if len(alarms) >= MAX_ALARMS:
                raise AlarmError("Не больше {n} будильников", n=MAX_ALARMS)
            alarms.append(clean)
        else:
            alarms[pos] = clean
        alarms.sort(key=lambda a: a["time"])
        self.config.update({"alarms": alarms})
        self._changed()
        return clean

    def delete_alarm(self, alarm_id):
        alarms = [a for a in self.alarms() if a["id"] != alarm_id]
        self.config.update({"alarms": alarms})
        with self.lock:
            self.snoozed = [s for s in self.snoozed if s["id"] != alarm_id]
        self._changed()

    def set_enabled(self, alarm_id, enabled):
        alarms = self.alarms()
        for a in alarms:
            if a["id"] == alarm_id:
                a["enabled"] = bool(enabled)
        self.config.update({"alarms": alarms})
        if not enabled:
            with self.lock:
                self.snoozed = [s for s in self.snoozed if s["id"] != alarm_id]
        self._changed()

    def next_alarm(self, now=None):
        """Ближайший включённый будильник: (будильник, datetime) или None."""
        now = self.local(self.clock() if now is None else now)
        best = None
        for a in self.alarms():
            if not a["enabled"]:
                continue
            h, m = map(int, a["time"].split(":"))
            for add in range(8):
                day = (now + datetime.timedelta(days=add)).replace(hour=h, minute=m, second=0, microsecond=0)
                if day <= now or (a["days"] and day.weekday() not in a["days"]):
                    continue
                if best is None or day < best[1]:
                    best = (a, day)
                break
        return best

    # --- таймеры ----------------------------------------------------------------

    def timer_action(self, action, timer_id=None, seconds=None, label=""):
        now = self.clock()
        with self.lock:
            timer = next((t for t in self.timers if t["id"] == timer_id), None)
            if action == "start":
                try:
                    seconds = int(seconds)
                except (TypeError, ValueError):
                    raise AlarmError("Некорректная длительность")
                if not 1 <= seconds <= MAX_TIMER:
                    raise AlarmError("Таймер — от секунды до суток")
                if len(self.timers) >= MAX_TIMERS:
                    raise AlarmError("Не больше {n} таймеров", n=MAX_TIMERS)
                timer = {"id": f"t{next(self.ids)}", "label": str(label or "").strip()[:40],
                         "duration": seconds, "ends_at": now + seconds, "remaining": None}
                self.timers.append(timer)
            elif timer is None:
                raise AlarmError("Нет такого таймера")
            elif action == "pause" and timer["ends_at"]:
                timer["remaining"] = max(0, timer["ends_at"] - now)
                timer["ends_at"] = None
            elif action == "resume" and timer["ends_at"] is None:
                timer["ends_at"] = now + (timer["remaining"] or 0)
                timer["remaining"] = None
            elif action == "add":
                extra = int(seconds or 60)
                if timer["ends_at"]:
                    timer["ends_at"] = min(timer["ends_at"] + extra, now + MAX_TIMER)
                else:
                    timer["remaining"] = min((timer["remaining"] or 0) + extra, MAX_TIMER)
                timer["duration"] = min(timer["duration"] + extra, MAX_TIMER)
            elif action == "cancel":
                self.timers.remove(timer)
            elif action not in ("pause", "resume"):
                raise AlarmError("Неизвестное действие")
            self._save_timers()
        self._changed()
        return timer

    # --- звонки -----------------------------------------------------------------

    def _ring(self, kind, item_id, label, at_time):
        key = f"{kind}:{item_id}:{int(self.clock())}"
        self.ringing.append({"key": key, "kind": kind, "id": item_id, "label": label,
                             "time": at_time, "since": self.clock()})
        log.info("ring %s %s %s", kind, item_id, at_time)

    def ring_action(self, action, key=None):
        """snooze — отложить будильник, dismiss — выключить (key=None — все звонки)."""
        snooze = self.settings()["snooze"]
        with self.lock:
            hit = [r for r in self.ringing if key is None or r["key"] == key]
            if not hit:
                return
            self.ringing = [r for r in self.ringing if r not in hit]
            if action == "snooze":
                for r in hit:
                    if r["kind"] == "alarm":
                        self.snoozed.append({"id": r["id"], "label": r["label"], "time": r["time"],
                                             "at": self.clock() + snooze * 60})
        self._changed()

    def tick(self):
        """Раз в секунду: срабатывания будильников, таймеров и отложенных."""
        now = self.clock()
        changed = False
        settings = self.settings()
        with self.lock:
            # Будильники проверяем по минутам. Если часы прыгнули (NTP,
            # ручная установка), догоняем не больше 5 минут, без старых звонков.
            minute = self._minute(now)
            if minute != self._last_minute:
                start = self._last_minute + 1 if 0 < minute - self._last_minute <= 5 else minute
                for m in range(start, minute + 1):
                    changed |= self._check_alarms(m * 60)
                self._last_minute = minute
            for s in [s for s in self.snoozed if s["at"] <= now]:
                self.snoozed.remove(s)
                self._ring("alarm", s["id"], s["label"], s["time"])
                changed = True
            done = [t for t in self.timers if t["ends_at"] and t["ends_at"] <= now]
            for t in done:
                self.timers.remove(t)
                self._ring("timer", t["id"], t["label"], _duration(t["duration"]))
            if done:
                self._save_timers()
                changed = True
            # Звонок без ответа снимается сам и остаётся в шторке пропущенным.
            limit = settings["ring_minutes"] * 60
            missed = [r for r in self.ringing if now - r["since"] >= limit]
            for r in missed:
                self.ringing.remove(r)
                self._missed(r)
            changed |= bool(missed)
        if changed:
            self._changed()

    def _check_alarms(self, ts):
        moment = self.local(ts)
        hm = moment.strftime("%H:%M")
        fired = False
        alarms = self.alarms()
        once_done = []
        for a in alarms:
            if not a["enabled"] or a["time"] != hm or (a["days"] and moment.weekday() not in a["days"]):
                continue
            self._ring("alarm", a["id"], a["label"], a["time"])
            fired = True
            if not a["days"]:
                once_done.append(a["id"])
        if once_done:
            for a in alarms:
                if a["id"] in once_done:
                    a["enabled"] = False
            self.config.update({"alarms": alarms})
        return fired

    def _missed(self, ring):
        if self.notifications is None:
            return
        title = "Пропущен будильник {time}" if ring["kind"] == "alarm" else "Таймер {time} закончился"
        try:
            self.notifications.add("clock", title, ring["label"], level="warn", icon="clock",
                                   key=f"missed:{ring['id']}", params={"time": ring["time"]}, sound=False)
        except ValueError:
            pass

    @staticmethod
    def _minute(ts):
        return int(ts // 60)

    # --- состояние и шторка -----------------------------------------------------

    def state(self):
        now = self.clock()
        with self.lock:
            timers = [dict(t, remaining=max(0, t["ends_at"] - now) if t["ends_at"] else t["remaining"],
                           running=bool(t["ends_at"])) for t in self.timers]
            ringing = [dict(r) for r in self.ringing]
            snoozed = [dict(s) for s in self.snoozed]
        nxt = self.next_alarm(now)
        return {
            "now": now,
            "alarms": self.alarms(),
            "timers": timers,
            "ringing": ringing,
            "snoozed": snoozed,
            "next": {"id": nxt[0]["id"], "time": nxt[0]["time"], "at": nxt[1].timestamp(), "label": nxt[0]["label"]} if nxt else None,
            "settings": self.settings(),
            "rev": self.rev,
        }

    def _update_tiles(self):
        """Плитка в шторке: ближайший будильник."""
        if self.tiles is None:
            return
        nxt = self.next_alarm()
        if nxt:
            alarm, when = nxt
            today = self.local(self.clock()).date()
            # «сегодня» и «завтра» переводит шторка, дата — как есть.
            day = "сегодня" if when.date() == today else "завтра" if (when.date() - today).days == 1 else when.strftime("%d.%m")
            self.tiles.set("clock", "alarm", "Будильник {time}", day, icon="clock", params={"time": alarm["time"]})
        else:
            self.tiles.remove("clock", "alarm")

    # --- поток ------------------------------------------------------------------

    def start(self):
        self._update_tiles()
        self._thread = threading.Thread(target=self._run, name="alarms", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        last_tile = None
        while not self._stop.wait(1):
            try:
                self.tick()
                # Плитка «сегодня/завтра» меняется в полночь.
                day = self.local(self.clock()).date()
                if day != last_tile:
                    last_tile = day
                    self._update_tiles()
            except Exception:
                log.exception("alarms tick failed")


def _duration(seconds):
    seconds = int(seconds)
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
