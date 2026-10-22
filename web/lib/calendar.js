// Напоминания календаря на стороне браузера (см. wallhub/reminders.py).
// Как и будильники: киоск зовёт setRev() со счётчиком из /api/revision,
// подписчики получают свежее состояние.

import { api } from "/lib/api.js";

let state = null;
let rev = null;
let loading = null;
const subs = new Set();

function publish(next) {
  state = next;
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
  loading ??= api.get("/calendar").then(publish).finally(() => { loading = null; });
  return loading;
}

// Подписка: fn(state) сразу (если состояние есть) и при каждом изменении.
export function onCalendar(fn) {
  subs.add(fn);
  if (state) fn(state);
  else refresh().catch(() => {});
  return () => subs.delete(fn);
}

export function setRev(next) {
  if (next === rev) return;
  rev = next;
  if (subs.size) refresh().catch(() => {});
}

const post = (body) => api.post("/calendar", body).then(publish);

export const reminders = {
  save: (item) => post({ action: "save", item }),
  remove: (id) => post({ action: "delete", id }),
  done: (id, done) => post({ action: done ? "done" : "undone", id }),
};
