// Модуль «Часы».
//
// Контракт модуля: module.js экспортирует widgets — словарь
// {id виджета: mount}, где mount(el, {options, api}) -> {destroy()}.
// Описание виджетов (размеры, настройки) — в module.json.
//
// Циферблаты (options.face): digital — классические цифры, bold — крупные
// жирные, stacked — часы над минутами, cards — цифры на плитках, analog —
// стрелки с цифрами, minimal — стрелки и риски без цифр. Все обновляются
// раз в секунду (или раз в минуту без секунд) без анимаций.

import { locale } from "/lib/i18n.js";

const pad = (n) => String(n).padStart(2, "0");
const FACES = ["digital", "bold", "stacked", "cards", "analog", "minimal"];

function hours(now, h12) {
  const h = now.getHours();
  return h12 ? String(h % 12 || 12) : pad(h);
}

function digitalFace(root, face) {
  root.innerHTML = face === "stacked"
    ? `<div class="clock-stack"><span class="clock-h"></span><span class="clock-m"></span></div><div class="clock-s"></div>`
    : face === "cards"
      ? `<div class="clock-cards"><span class="clock-card clock-h"></span><span class="clock-card clock-m"></span><span class="clock-card clock-s"></span></div>`
      : `<div class="clock-time"><span class="clock-h"></span><span class="clock-colon">:</span><span class="clock-m"></span><span class="clock-s"></span></div>`;
  const h = root.querySelector(".clock-h");
  const m = root.querySelector(".clock-m");
  const s = root.querySelector(".clock-s");
  return (now, { seconds, h12 }) => {
    h.textContent = hours(now, h12);
    m.textContent = pad(now.getMinutes());
    s.hidden = !seconds;
    s.textContent = pad(now.getSeconds());
  };
}

function analogFace(root, face) {
  const marks = [];
  for (let i = 0; i < 60; i++) {
    const major = i % 5 === 0;
    if (face === "minimal" && !major) continue;
    const len = major ? 7 : 3;
    marks.push(`<line class="${major ? "clock-mark major" : "clock-mark"}" x1="50" y1="${4}" x2="50" y2="${4 + len}" transform="rotate(${i * 6} 50 50)"/>`);
  }
  const numbers = face === "analog"
    ? Array.from({ length: 12 }, (_, i) => {
      const n = i + 1;
      const a = (n * 30 * Math.PI) / 180;
      return `<text class="clock-num" x="${50 + 33 * Math.sin(a)}" y="${50 - 33 * Math.cos(a) + 3.6}">${n}</text>`;
    }).join("")
    : "";
  root.innerHTML = `
    <svg class="clock-dial" viewBox="0 0 100 100" aria-hidden="true">
      <circle class="clock-rim" cx="50" cy="50" r="48"/>
      ${marks.join("")}${numbers}
      <line class="clock-hand-h" x1="50" y1="54" x2="50" y2="27"/>
      <line class="clock-hand-m" x1="50" y1="56" x2="50" y2="14"/>
      <line class="clock-hand-s" x1="50" y1="60" x2="50" y2="10"/>
      <circle class="clock-pin" cx="50" cy="50" r="2.2"/>
    </svg>`;
  const hh = root.querySelector(".clock-hand-h");
  const mm = root.querySelector(".clock-hand-m");
  const ss = root.querySelector(".clock-hand-s");
  return (now, { seconds }) => {
    const s = now.getSeconds();
    const m = now.getMinutes() + s / 60;
    const h = (now.getHours() % 12) + m / 60;
    hh.setAttribute("transform", `rotate(${h * 30} 50 50)`);
    mm.setAttribute("transform", `rotate(${m * 6} 50 50)`);
    ss.style.display = seconds ? "" : "none";
    ss.setAttribute("transform", `rotate(${s * 6} 50 50)`);
  };
}

function mountClock(el, { options }) {
  const face = FACES.includes(options.face) ? options.face : "digital";
  const opts = { seconds: options.seconds !== false, h12: options.h12 === true };
  const showDate = options.date !== false;
  const analog = face === "analog" || face === "minimal";

  el.innerHTML = `
    <div class="clock clock-${face} ${showDate ? "" : "no-date"}">
      <div class="clock-face"></div>
      <div class="clock-date" ${showDate ? "" : "hidden"}></div>
    </div>`;
  const date = el.querySelector(".clock-date");
  const draw = (analog ? analogFace : digitalFace)(el.querySelector(".clock-face"), face);

  const weekday = new Intl.DateTimeFormat(locale(), { weekday: "long" });
  const dayMonth = new Intl.DateTimeFormat(locale(), { day: "numeric", month: "long" });
  let timer;

  function tick() {
    const now = new Date();
    draw(now, opts);
    if (showDate) date.textContent = `${weekday.format(now)}, ${dayMonth.format(now)}`;
    // Следующий тик ровно на границе секунды (или минуты без секунд),
    // чтобы цифры не «плыли».
    const wait = opts.seconds ? 1000 - now.getMilliseconds() : 60000 - now.getSeconds() * 1000 - now.getMilliseconds();
    timer = setTimeout(tick, wait + 5);
  }
  tick();

  return {
    destroy() {
      clearTimeout(timer);
    },
  };
}

export const widgets = { clock: mountClock };
