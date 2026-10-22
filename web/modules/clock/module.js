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

import { locale, t } from "/lib/i18n.js";
import { icon } from "/lib/icons.js";
import { openKeyboard } from "/lib/keyboard.js";
import { alarms as alarmApi, formatDuration, onAlarms, remaining } from "/lib/alarms.js";

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

// --- будильник ---------------------------------------------------------------

const esc = (v) => String(v ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

// Короткие имена дней недели: 0 — понедельник, как в сервере.
function dayNames() {
  const fmt = new Intl.DateTimeFormat(locale(), { weekday: "short" });
  // 2024-01-01 — понедельник.
  return Array.from({ length: 7 }, (_, i) => fmt.format(new Date(2024, 0, 1 + i)));
}

function daysText(days) {
  if (!days.length) return t("Один раз");
  if (days.length === 7) return t("Каждый день");
  if (days.join() === "0,1,2,3,4") return t("По будням");
  if (days.join() === "5,6") return t("По выходным");
  const names = dayNames();
  return days.map((d) => names[d]).join(", ");
}

function whenText(next) {
  if (!next) return "";
  const at = new Date(next.at * 1000);
  const today = new Date();
  const days = Math.round((new Date(at.getFullYear(), at.getMonth(), at.getDate()) - new Date(today.getFullYear(), today.getMonth(), today.getDate())) / 86400000);
  const day = days === 0 ? t("сегодня") : days === 1 ? t("завтра") : new Intl.DateTimeFormat(locale(), { weekday: "long" }).format(at);
  return next.label ? `${day} · ${next.label}` : day;
}

// Кнопка, которая при удержании повторяет действие (перебор часов и минут).
function holdRepeat(button, fn) {
  let timer = null;
  const stop = () => { clearTimeout(timer); clearInterval(timer); timer = null; };
  button.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    fn();
    timer = setTimeout(() => { timer = setInterval(fn, 110); }, 450);
  });
  for (const ev of ["pointerup", "pointerleave", "pointercancel"]) button.addEventListener(ev, stop);
}

// Редактор будильника поверх экрана. Возвращает Promise, который
// разрешается после сохранения, удаления или отмены.
function editAlarm(alarm) {
  const draft = alarm ? { ...alarm, days: [...alarm.days] } : { time: "07:00", days: [0, 1, 2, 3, 4], label: "", enabled: true };
  let [h, m] = draft.time.split(":").map(Number);
  return new Promise((resolve) => {
    const root = document.createElement("div");
    root.className = "dlg-backdrop clock-editor-backdrop";
    const names = dayNames();
    root.innerHTML = `
      <div class="dlg clock-editor" role="dialog">
        <div class="dlg-title">${alarm ? t("Будильник") : t("Новый будильник")}</div>
        <div class="clock-ed-time">
          <div class="clock-ed-col"><button class="btn" data-step="h+">${icon("up")}</button><span class="clock-ed-h"></span><button class="btn" data-step="h-">${icon("down")}</button></div>
          <span class="clock-ed-colon">:</span>
          <div class="clock-ed-col"><button class="btn" data-step="m+">${icon("up")}</button><span class="clock-ed-m"></span><button class="btn" data-step="m-">${icon("down")}</button></div>
        </div>
        <div class="clock-ed-days">${names.map((n, i) => `<button class="clock-ed-day" data-day="${i}">${esc(n)}</button>`).join("")}</div>
        <button class="btn clock-ed-label" data-label></button>
        <div class="dlg-actions">
          ${alarm ? `<button class="btn btn-danger clock-ed-delete" data-act="delete">${icon("trash")}</button>` : ""}
          <button class="btn" data-act="cancel">${t("Отмена")}</button>
          <button class="btn btn-primary" data-act="save">${t("Сохранить")}</button>
        </div>
      </div>`;
    const draw = () => {
      root.querySelector(".clock-ed-h").textContent = pad(h);
      root.querySelector(".clock-ed-m").textContent = pad(m);
      root.querySelectorAll("[data-day]").forEach((b) => b.setAttribute("aria-pressed", draft.days.includes(Number(b.dataset.day))));
      const label = root.querySelector("[data-label]");
      label.textContent = draft.label || t("Подпись (необязательно)");
      label.classList.toggle("muted", !draft.label);
    };
    const steps = { "h+": () => { h = (h + 1) % 24; }, "h-": () => { h = (h + 23) % 24; }, "m+": () => { m = (m + 1) % 60; }, "m-": () => { m = (m + 59) % 60; } };
    root.querySelectorAll("[data-step]").forEach((b) => holdRepeat(b, () => { steps[b.dataset.step](); draw(); }));
    const close = (value) => { root.remove(); resolve(value); };
    root.addEventListener("click", async (e) => {
      if (e.target === root) return close(null);
      const b = e.target.closest("button");
      if (!b || b.dataset.step) return;
      if (b.dataset.day !== undefined) {
        const d = Number(b.dataset.day);
        draft.days = draft.days.includes(d) ? draft.days.filter((x) => x !== d) : [...draft.days, d].sort();
        draw();
      } else if ("label" in b.dataset) {
        const text = await openKeyboard({ title: t("Подпись будильника"), value: draft.label, layout: locale().startsWith("ru") ? "ru" : "en" });
        if (text !== null) draft.label = text.slice(0, 40);
        draw();
      } else if (b.dataset.act === "cancel") {
        close(null);
      } else if (b.dataset.act === "delete") {
        await alarmApi.remove(alarm.id).catch(() => {});
        close("deleted");
      } else if (b.dataset.act === "save") {
        draft.time = `${pad(h)}:${pad(m)}`;
        draft.enabled = true;
        try {
          await alarmApi.save(draft);
          close("saved");
        } catch (err) {
          root.querySelector(".dlg-title").textContent = err.message;
        }
      }
    });
    draw();
    document.body.appendChild(root);
  });
}

function mountAlarm(el) {
  el.innerHTML = `
    <div class="clock-al">
      <div class="clock-al-head">${icon("alarm")}<span>${t("Будильник")}</span><button class="clock-al-add" data-add title="${t("Новый будильник")}">${icon("plus")}</button></div>
      <div class="clock-al-next"></div>
      <div class="clock-al-list"></div>
    </div>`;
  const next = el.querySelector(".clock-al-next");
  const list = el.querySelector(".clock-al-list");
  let state = null;

  function render(st) {
    state = st;
    next.innerHTML = st.next
      ? `<div class="clock-al-time">${esc(st.next.time)}</div><div class="clock-al-when">${esc(whenText(st.next))}</div>`
      : `<div class="clock-al-empty">${t("Нет включённых будильников")}</div>`;
    list.innerHTML = st.alarms.map((a) => `
      <div class="clock-al-row ${a.enabled ? "" : "off"}" data-edit="${esc(a.id)}">
        <span class="clock-al-row-time">${esc(a.time)}</span>
        <span class="clock-al-row-days">${esc(a.label ? `${a.label} · ${daysText(a.days)}` : daysText(a.days))}</span>
        <button class="switch" data-toggle="${esc(a.id)}" aria-checked="${a.enabled}"></button>
      </div>`).join("");
  }

  async function onClick(e) {
    const b = e.target.closest("[data-toggle], [data-add], [data-edit]");
    if (!b || !state) return;
    e.stopPropagation();
    if (b.dataset.toggle) {
      const a = state.alarms.find((x) => x.id === b.dataset.toggle);
      await alarmApi.toggle(a.id, !a.enabled).catch(() => {});
    } else if ("add" in b.dataset) {
      await editAlarm(null);
    } else if (b.dataset.edit) {
      await editAlarm(state.alarms.find((x) => x.id === b.dataset.edit));
    }
  }

  el.addEventListener("click", onClick);
  const off = onAlarms(render);
  // Подпись «сегодня/завтра» меняется в полночь: перерисовка раз в минуту.
  const timer = setInterval(() => state && render(state), 60000);
  return {
    destroy() {
      off();
      clearInterval(timer);
      el.removeEventListener("click", onClick);
    },
  };
}

// --- таймер -----------------------------------------------------------------

const PRESETS = [60, 180, 300, 600, 900, 1800];

// Свой таймер: минуты и секунды кнопками.
function customTimer() {
  let min = 5;
  let sec = 0;
  return new Promise((resolve) => {
    const root = document.createElement("div");
    root.className = "dlg-backdrop clock-editor-backdrop";
    root.innerHTML = `
      <div class="dlg clock-editor" role="dialog">
        <div class="dlg-title">${t("Таймер")}</div>
        <div class="clock-ed-time">
          <div class="clock-ed-col"><button class="btn" data-step="m+">${icon("up")}</button><span class="clock-ed-h"></span><small>${t("мин")}</small><button class="btn" data-step="m-">${icon("down")}</button></div>
          <span class="clock-ed-colon">:</span>
          <div class="clock-ed-col"><button class="btn" data-step="s+">${icon("up")}</button><span class="clock-ed-m"></span><small>${t("сек")}</small><button class="btn" data-step="s-">${icon("down")}</button></div>
        </div>
        <div class="dlg-actions">
          <button class="btn" data-act="cancel">${t("Отмена")}</button>
          <button class="btn btn-primary" data-act="start">${t("Запустить")}</button>
        </div>
      </div>`;
    const draw = () => {
      root.querySelector(".clock-ed-h").textContent = pad(min);
      root.querySelector(".clock-ed-m").textContent = pad(sec);
    };
    const steps = { "m+": () => { min = Math.min(min + 1, 999); }, "m-": () => { min = Math.max(min - 1, 0); }, "s+": () => { sec = (sec + 5) % 60; }, "s-": () => { sec = (sec + 55) % 60; } };
    root.querySelectorAll("[data-step]").forEach((b) => holdRepeat(b, () => { steps[b.dataset.step](); draw(); }));
    root.addEventListener("click", (e) => {
      if (e.target === root) { root.remove(); return resolve(null); }
      const b = e.target.closest("[data-act]");
      if (!b) return;
      root.remove();
      resolve(b.dataset.act === "start" && min * 60 + sec > 0 ? min * 60 + sec : null);
    });
    draw();
    document.body.appendChild(root);
  });
}

function mountTimer(el) {
  el.innerHTML = `<div class="clock-tm"></div>`;
  const root = el.querySelector(".clock-tm");
  let state = null;
  let tick = null;
  const minutes = (s) => t("{n} мин", { n: s / 60 });

  function render(st) {
    state = st;
    clearInterval(tick);
    tick = null;
    const timers = st.timers;
    if (!timers.length) {
      root.innerHTML = `
        <div class="clock-al-head">${icon("timer")}<span>${t("Таймер")}</span></div>
        <div class="clock-tm-presets">
          ${PRESETS.map((s) => `<button class="clock-tm-preset" data-start="${s}">${esc(minutes(s))}</button>`).join("")}
          <button class="clock-tm-preset" data-custom>${t("Свой…")}</button>
        </div>`;
      return;
    }
    const [first, ...rest] = timers;
    root.innerHTML = `
      <div class="clock-al-head">${icon("timer")}<span>${esc(first.label || t("Таймер"))}</span><button class="clock-al-add" data-custom title="${t("Ещё таймер")}">${icon("plus")}</button></div>
      <div class="clock-tm-left ${first.running ? "" : "paused"}" data-left="${esc(first.id)}"></div>
      <div class="clock-tm-actions">
        <button class="btn clock-tm-btn" data-act="${first.running ? "pause" : "resume"}" data-id="${esc(first.id)}">${icon(first.running ? "pause" : "play")}</button>
        <button class="btn clock-tm-btn" data-act="add" data-id="${esc(first.id)}">+1 ${t("мин")}</button>
        <button class="btn clock-tm-btn" data-act="cancel" data-id="${esc(first.id)}">${icon("close")}</button>
      </div>
      ${rest.length ? `<div class="clock-tm-rest">${rest.map((x) => `<span data-left="${esc(x.id)}" class="${x.running ? "" : "paused"}"></span>`).join("")}</div>` : ""}`;
    const draw = () => {
      for (const x of timers) {
        const node = root.querySelector(`[data-left="${x.id}"]`);
        if (node) node.textContent = formatDuration(remaining(x));
      }
    };
    draw();
    if (timers.some((x) => x.running)) tick = setInterval(draw, 1000);
  }

  async function onClick(e) {
    const b = e.target.closest("button");
    if (!b || !state) return;
    e.stopPropagation();
    try {
      if (b.dataset.start) await alarmApi.timer("start", { seconds: Number(b.dataset.start) });
      else if ("custom" in b.dataset) {
        const seconds = await customTimer();
        if (seconds) await alarmApi.timer("start", { seconds });
      } else if (b.dataset.act) await alarmApi.timer(b.dataset.act, { id: b.dataset.id, seconds: 60 });
    } catch (err) {
      console.error(err);
    }
  }

  el.addEventListener("click", onClick);
  const off = onAlarms(render);
  return {
    destroy() {
      off();
      clearInterval(tick);
      el.removeEventListener("click", onClick);
    },
  };
}

export const widgets = { clock: mountClock, alarm: mountAlarm, timer: mountTimer };
