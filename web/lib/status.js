// Общий опрос /api/status для всех виджетов статусной строки: один запрос
// раз в 2 секунды, сколько бы виджетов ни было на столах.

import { api } from "/lib/api.js";

const listeners = new Set();
let timer = null;
let last = null;

async function poll() {
  try {
    last = (await api.get("/status")).items;
  } catch {
    last = null;
  }
  for (const fn of listeners) fn(last);
}

export function subscribe(fn) {
  listeners.add(fn);
  if (last) fn(last);
  if (!timer) {
    poll();
    timer = setInterval(poll, 2000);
  }
  return () => {
    listeners.delete(fn);
    if (!listeners.size) {
      clearInterval(timer);
      timer = null;
    }
  };
}
