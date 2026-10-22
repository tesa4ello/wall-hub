// Будильники и таймеры на стороне браузера: общее состояние для виджетов
// часов, звонка на экране и админки. Источник правды — сервер (/api/alarms,
// см. wallhub/alarms.py); киоск зовёт setRev() со счётчиком из /api/revision.

import { api } from "/lib/api.js";

let state = null;
let rev = null;
let loading = null;
const subs = new Set();

function publish(next) {
  // Разница часов браузера и сервера: оставшееся время таймеров считаем по серверу.
  state = { ...next, offset: next.now * 1000 - Date.now() };
  for (const fn of subs) {
    try {
      fn(state);
    } catch (e) {
      console.error(e);
    }
  }
  return state;
}

export function refresh() {
  loading ??= api.get("/alarms").then(publish).finally(() => { loading = null; });
  return loading;
}

// Подписка: fn(state) сразу (если состояние есть) и при каждом изменении.
export function onAlarms(fn) {
  subs.add(fn);
  if (state) fn(state);
  else refresh().catch(() => {});
  return () => subs.delete(fn);
}

export function setRev(next) {
  if (next === rev) return;
  rev = next;
  refresh().catch(() => {});
}

// Серверное «сейчас» в миллисекундах.
export function now() {
  return Date.now() + (state?.offset || 0);
}

const post = (path, body) => api.post(path, body).then(publish);

export const alarms = {
  save: (alarm) => post("/alarms", { action: "save", alarm }),
  remove: (id) => post("/alarms", { action: "delete", id }),
  toggle: (id, on) => post("/alarms", { action: on ? "enable" : "disable", id }),
  settings: (settings) => post("/alarms", { action: "settings", settings }),
  timer: (action, { id, seconds, label } = {}) => post("/timers", { action, id, seconds, label }),
  ring: (action, key) => post("/alarms/ring", { action, key }),
};

// Остаток таймера в секундах (по серверному времени).
export function remaining(timer) {
  if (!timer.running) return Math.max(0, Math.round(timer.remaining || 0));
  return Math.max(0, Math.ceil(timer.ends_at - now() / 1000));
}

export function formatDuration(seconds) {
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const pad = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(s % 60)}` : `${m}:${pad(s % 60)}`;
}
