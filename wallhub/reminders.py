"""Напоминания для календаря на экране.

Календарь открывается нажатием на часы. Напоминания хранятся в настройках
панели (config["reminders"]): дата, необязательное время и текст. У
напоминания со временем в свой час появляется уведомление в шторке со звуком.

Поле source — откуда напоминание: пока только "panel" (создано на панели).
Сюда же потом лягут события из внешних календарей (iCloud по CalDAV): они
будут приходить со своим source и только читаться, а пароль останется в
data_dir панели, не в конфиге.
"""

import datetime
import logging
import re
import threading
import time

log = logging.getLogger(__name__)

DATE_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
MAX_REMINDERS = 300
MAX_TEXT = 120


class ReminderError(ValueError):
    """Текст — исходная русская строка, переводит сервер (tr(e.text, **e.params))."""

    def __init__(self, text, **params):
        super().__init__(text)
        self.text = text
        self.params = params


def clean_reminder(item, existing_ids=()):
    if not isinstance(item, dict):
        raise ReminderError("Некорректное напоминание")
    date = str(item.get("date", ""))
    try:
        if not DATE_RE.match(date):
            raise ValueError
        datetime.date.fromisoformat(date)
    except ValueError:
        raise ReminderError("Дата напоминания — ГГГГ-ММ-ДД")
    t = str(item.get("time") or "")
    if t and not TIME_RE.match(t):
        raise ReminderError("Время напоминания — ЧЧ:ММ")
    text = " ".join(str(item.get("text") or "").split())[:MAX_TEXT]
    if not text:
        raise ReminderError("Напишите текст напоминания")
    rid = str(item.get("id") or "")
    if not re.fullmatch(r"r\d{1,6}", rid):
        rid = "r" + str(max([int(i[1:]) for i in existing_ids if re.fullmatch(r"r\d+", i)] + [0]) + 1)
    return {"id": rid, "date": date, "time": t, "text": text, "done": item.get("done") is True, "source": "panel"}


class Reminders:
    def __init__(self, config, notifications=None, clock=time.time, local=None):
        self.config = config
        self.notifications = notifications
        self.clock = clock
        self.local = local or (lambda ts: datetime.datetime.fromtimestamp(ts))
        self.lock = threading.Lock()
        # Растёт при любом изменении: киоск перечитывает /api/calendar.
        self.rev = 0
        self._last_minute = int(self.clock() // 60)
        self._stop = threading.Event()

    def items(self):
        return self.config.get("reminders") or []

    def _store(self, items):
        items.sort(key=lambda r: (r["date"], r["time"] or "99:99", r["id"]))
        self.config.update({"reminders": items})
        self.rev += 1
        self.config.notify()

    def save(self, item):
        with self.lock:
            items = self.items()
            clean = clean_reminder(item, [r["id"] for r in items])
            pos = next((i for i, r in enumerate(items) if r["id"] == clean["id"]), None)
            if pos is None:
                if len(items) >= MAX_REMINDERS:
                    raise ReminderError("Не больше {n} напоминаний", n=MAX_REMINDERS)
                items.append(clean)
            else:
                items[pos] = clean
            self._store(items)
        return clean

    def delete(self, rid):
        with self.lock:
            self._store([r for r in self.items() if r["id"] != rid])
        if self.notifications is not None:
            self.notifications.remove_key("calendar", f"reminder:{rid}")

    def set_done(self, rid, done):
        with self.lock:
            items = self.items()
            for r in items:
                if r["id"] == rid:
                    r["done"] = bool(done)
            self._store(items)
        if done and self.notifications is not None:
            self.notifications.remove_key("calendar", f"reminder:{rid}")

    def state(self):
        return {"items": self.items(), "rev": self.rev, "today": self.local(self.clock()).date().isoformat()}

    def tick(self):
        """Раз в минуту: уведомления о напоминаниях со временем. Если часы
        прыгнули, догоняем не больше 5 минут, как будильники."""
        minute = int(self.clock() // 60)
        if minute == self._last_minute:
            return
        start = self._last_minute + 1 if 0 < minute - self._last_minute <= 5 else minute
        self._last_minute = minute
        for m in range(start, minute + 1):
            moment = self.local(m * 60)
            date, hm = moment.date().isoformat(), moment.strftime("%H:%M")
            for r in self.items():
                if r["date"] == date and r["time"] == hm and not r["done"]:
                    self._notify(r)

    def _notify(self, r):
        if self.notifications is None:
            return
        log.info("reminder %s %s %s", r["id"], r["date"], r["time"])
        try:
            # Текст пользователя идёт подстановкой, чтобы скобки в нём не
            # сломали перевод и форматирование.
            self.notifications.add("calendar", "Напоминание {time}", "{text}", icon="calendar",
                                   key=f"reminder:{r['id']}", params={"time": r["time"], "text": r["text"]})
        except ValueError:
            pass

    def start(self):
        threading.Thread(target=self._run, name="reminders", daemon=True).start()

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.wait(5):
            try:
                self.tick()
            except Exception:
                log.exception("reminders tick failed")
