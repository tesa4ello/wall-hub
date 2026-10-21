// Вид шторки: разметка и жесты внутри неё (ползунки, смахивание уведомлений).
// Данные и действия держит объект shade в app.js: сюда приходит готовое
// состояние, а наружу уходят вызовы handlers.
//
// Состояние (GET /api/shade):
//   status: {name, ip, ssid, signal, version}
//   toggles: {wifi: {available, on, ssid}, dnd, theme}
//   brightness, volume — 0–100 или null, если панель не умеет;
//   notifications: [{id, icon, level, title, text, time, action: {label, kind}}]
//   module_tiles: [{module, id, icon, title, text, level}]

import { locale, t } from "/lib/i18n.js";
import { icon, signalBars } from "/lib/icons.js";

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const LEVELS = ["info", "ok", "warn", "crit"];
const level = (l) => (LEVELS.includes(l) ? l : "info");

// Сколько уведомлений помещается в колонку; остальные — строкой «ещё N».
const MAX_NOTES = 4;
// Смахивание: сдвиг, после которого уведомление уходит.
const SWIPE_OUT = 96;

// «5 мин», «3 ч», «2 д» от времени ISO.
export function ago(iso, now = Date.now()) {
  const s = Math.max(0, (now - Date.parse(iso)) / 1000);
  if (!Number.isFinite(s)) return "";
  if (s < 60) return t("сейчас");
  if (s < 3600) return t("{n} мин", { n: Math.floor(s / 60) });
  if (s < 86400) return t("{n} ч", { n: Math.floor(s / 3600) });
  return t("{n} д", { n: Math.floor(s / 86400) });
}

function tile(action, ic, title, sub, { on = false, disabled = false } = {}) {
  return `<button class="shade-tile ${on ? "on" : ""}" data-shade="${action}" ${disabled ? "disabled" : ""}>
    ${icon(ic)}<b>${esc(title)}</b><span>${esc(sub)}</span>
  </button>`;
}

function slider(name, ic, title, value) {
  if (value == null) return "";
  return `<div class="shade-slider" data-slider="${name}" style="--v:${value / 100}">
    <div class="slider-fill"></div>${icon(ic)}<b>${esc(title)}</b><span class="slider-value">${Math.round(value)}%</span>
  </div>`;
}

// Миниатюра стола: размытая копия фона и прямоугольники виджетов по сетке.
function deskThumb(desk, i, current, layout) {
  const boxes = (desk.widgets || []).map((w) => `<i style="left:${(w.x / layout.cols) * 100}%;top:${(w.y / layout.rows) * 100}%;width:${(w.w / layout.cols) * 100}%;height:${(w.h / layout.rows) * 100}%"></i>`).join("");
  const name = desk.name || t("Стол {n}", { n: i + 1 });
  return `<button class="shade-desk ${i === current ? "current" : ""}" data-shade="desk" data-index="${i}">
    <span class="desk-mini">${boxes}</span><span class="desk-name">${esc(name)}</span>
  </button>`;
}

function note(n, now) {
  const action = n.action ? `<button class="note-act" data-shade="action" data-id="${esc(n.id)}">${esc(n.action.label)}</button>` : "";
  return `<div class="shade-note level-${level(n.level)}" data-note="${esc(n.id)}">
    <div class="note-del">${icon("trash")}</div>
    <div class="note-card">
      <span class="shade-badge">${icon(n.icon || "info")}</span>
      <div class="note-body"><b>${esc(n.title)}</b>${n.text ? `<p>${esc(n.text)}</p>` : ""}${action}</div>
      <time>${n.time ? esc(ago(n.time, now)) : ""}</time>
    </div>
  </div>`;
}

// view: {data, theme: {icon, label}, desks, layout, current}
export function render(el, view) {
  const { data } = view;
  const s = data.status || {};
  const tg = data.toggles || {};
  const wifi = tg.wifi || {};
  const now = Date.now();
  const date = new Date(now);
  const time = date.toLocaleTimeString(locale(), { hour: "2-digit", minute: "2-digit" });
  const day = date.toLocaleDateString(locale(), { weekday: "long" });
  const dm = date.toLocaleDateString(locale(), { day: "numeric", month: "long" });
  const notes = data.notifications || [];
  const tiles = data.module_tiles || [];
  const wifiSub = !wifi.available ? t("Нет адаптера") : !wifi.on ? t("Выключен") : wifi.ssid || t("Не подключён");

  el.innerHTML = `
    <div class="shade-status">
      <div class="shade-clock"><b>${esc(time)}</b><span>${esc(day)}<br>${esc(dm)}</span></div>
      <div class="pills">
        ${s.name ? `<span class="pill">${icon("display")}<b class="pill-name"></b></span>` : ""}
        ${s.ip || s.ssid ? `<span class="pill">${s.signal != null ? signalBars(s.signal) : icon("net")}<span class="pill-ssid"></span><span class="muted">${esc(s.ip || "")}</span></span>` : ""}
        ${s.version ? `<span class="pill"><span class="muted">${t("версия")}</span><span>${esc(s.version)}</span></span>` : ""}
      </div>
    </div>
    <div class="shade-main">
      <div class="shade-col">
        <div class="shade-tiles">
          ${tile("wifi", "wifi", "Wi‑Fi", "", { on: wifi.on, disabled: !wifi.available })}
          ${tile("dnd", tg.dnd ? "belloff" : "bell", t("Не беспокоить"), tg.dnd ? t("Включено") : t("Выключено"), { on: tg.dnd, disabled: tg.dnd == null })}
          ${tile("theme", view.theme.icon, t("Тема"), view.theme.label, { on: true })}
          ${tile("blank", "screenoff", t("Погасить"), t("Экран до касания"))}
        </div>
        ${data.brightness != null || data.volume != null ? `<div class="shade-sliders">
          ${slider("brightness", "sun", t("Яркость"), data.brightness)}
          ${slider("volume", "volume", t("Громкость"), data.volume)}
        </div>` : ""}
        ${tiles.length ? `<div class="shade-modules">${tiles.slice(0, 4).map((m) => `
          <div class="shade-module level-${level(m.level)}"><span class="shade-badge">${icon(m.icon || "grid")}</span>
            <div><b>${esc(m.title)}</b><small>${esc(m.text)}</small></div></div>`).join("")}</div>` : ""}
        ${view.desks.length > 1 ? `<div class="shade-desks">${view.desks.map((d, i) => deskThumb(d, i, view.current, view.layout)).join("")}</div>` : ""}
      </div>
      <div class="shade-notes">
        <div class="notes-head"><b>${notes.length ? t("Уведомления · {n}", { n: notes.length }) : t("Уведомления")}</b>
          ${notes.length > 1 ? `<button data-shade="clear">${t("Очистить")}</button>` : ""}</div>
        ${notes.length ? notes.slice(0, MAX_NOTES).map((n) => note(n, now)).join("") : `<div class="notes-empty">${icon("check")}<span>${t("Новых уведомлений нет")}</span></div>`}
        ${notes.length > MAX_NOTES ? `<div class="notes-more">${t("И ещё {n}", { n: notes.length - MAX_NOTES })}</div>` : ""}
      </div>
    </div>
    <div class="shade-bottom">
      <button class="shade-btn" data-shade="settings">${icon("gear")}<span>${t("Настройки")}</span></button>
      <span class="shade-handle"></span>
      <button class="shade-btn" data-shade="reboot">${icon("reboot")}<span>${t("Перезагрузка")}</span></button>
      <button class="shade-btn danger" data-shade="poweroff">${icon("power")}<span>${t("Выключение")}</span></button>
    </div>`;
  // Имена сети и панели приходят извне: вставляем текстом.
  const put = (sel, text) => { const n = el.querySelector(sel); if (n) n.textContent = text; };
  put('[data-shade="wifi"] span', wifiSub);
  put(".pill-name", s.name || "");
  put(".pill-ssid", s.ssid || "");
}

// Жесты внутри шторки. handlers: slide(name, value), dismiss(id). Кнопки
// (data-shade) обрабатывает app.js. Пока палец на ползунке или уведомлении, шторку не перерисовываем
// (busy) и не тянем: события не доходят до её обработчика.
export function bindGestures(el, handlers) {
  const g = { busy: false };
  let drag = null;

  el.addEventListener("pointerdown", (e) => {
    const sl = e.target.closest(".shade-slider");
    const nt = !e.target.closest("button") && e.target.closest(".shade-note");
    if (!sl && !nt) return;
    e.stopPropagation();
    g.busy = true;
    el.setPointerCapture(e.pointerId);
    drag = sl ? { kind: "slider", el: sl, rect: sl.getBoundingClientRect(), sent: 0 } : { kind: "note", el: nt, x: e.clientX, y: e.clientY, dx: 0 };
    if (sl) move(e);
  });

  const move = (e) => {
    if (!drag) return;
    if (drag.kind === "slider") {
      const v = Math.round(Math.max(0, Math.min(1, (e.clientX - drag.rect.left) / drag.rect.width)) * 100);
      drag.value = v;
      drag.el.style.setProperty("--v", v / 100);
      drag.el.querySelector(".slider-value").textContent = `${v}%`;
      // Яркость меняется прямо под пальцем, но не чаще 5 раз в секунду.
      if (Date.now() - drag.sent > 200) {
        drag.sent = Date.now();
        handlers.slide(drag.el.dataset.slider, v);
      }
    } else {
      drag.dx = e.clientX - drag.x;
      drag.el.querySelector(".note-card").style.transform = `translateX(${drag.dx}px)`;
      drag.el.classList.toggle("swiping", Math.abs(drag.dx) > 8);
    }
  };
  el.addEventListener("pointermove", move);

  const end = (cancel) => {
    if (!drag) return;
    const d = drag;
    drag = null;
    g.busy = false;
    if (d.kind === "slider") {
      if (!cancel && d.value != null) handlers.slide(d.el.dataset.slider, d.value);
      return;
    }
    const card = d.el.querySelector(".note-card");
    if (!cancel && Math.abs(d.dx) > SWIPE_OUT) {
      card.style.transform = `translateX(${Math.sign(d.dx) * 120}%)`;
      d.el.classList.add("gone");
      setTimeout(() => handlers.dismiss(d.el.dataset.note), 220);
    } else {
      card.style.transform = "";
      d.el.classList.remove("swiping");
    }
  };
  el.addEventListener("pointerup", () => end(false));
  el.addEventListener("pointercancel", () => end(true));

  return g;
}
