// Общие мелочи виджетов часов и календаря.

import { locale } from "/lib/i18n.js";

export const pad = (n) => String(n).padStart(2, "0");

export const esc = (v) => String(v ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

// Короткие имена дней недели: 0 — понедельник, как в сервере.
export function dayNames() {
  const fmt = new Intl.DateTimeFormat(locale(), { weekday: "short" });
  // 2024-01-01 — понедельник.
  return Array.from({ length: 7 }, (_, i) => fmt.format(new Date(2024, 0, 1 + i)));
}

// Кнопка, которая при удержании повторяет действие (перебор часов и минут).
export function holdRepeat(button, fn) {
  let timer = null;
  const stop = () => { clearTimeout(timer); clearInterval(timer); timer = null; };
  button.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    fn();
    timer = setTimeout(() => { timer = setInterval(fn, 110); }, 450);
  });
  for (const ev of ["pointerup", "pointerleave", "pointercancel"]) button.addEventListener(ev, stop);
}
