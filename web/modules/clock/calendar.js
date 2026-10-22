// Календарь поверх экрана: открывается нажатием на часы. Слева месяц,
// справа напоминания выбранного дня. Напоминания хранит сервер
// (/api/calendar, wallhub/reminders.py); у напоминания со временем в свой
// час появляется уведомление в шторке.

import { locale, t } from "/lib/i18n.js";
import { icon } from "/lib/icons.js";
import { openKeyboard } from "/lib/keyboard.js";
import { onCalendar, reminders } from "/lib/calendar.js";
import { dayNames, esc, holdRepeat, pad } from "./util.js";

const iso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const parse = (s) => {
  const [y, m, d] = s.split("-").map(Number);
  return new Date(y, m - 1, d);
};
const layout = () => (locale().startsWith("ru") ? "ru" : "en");

const IDLE_MS = 120000;
let open = null;

export function openCalendar() {
  if (open) return;
  const root = document.createElement("div");
  root.className = "dlg-backdrop cal-backdrop";
  root.innerHTML = `
    <div class="dlg cal" role="dialog">
      <div class="cal-month">
        <div class="cal-head">
          <button class="btn cal-nav" data-nav="-1" title="${t("Предыдущий месяц")}">${icon("left")}</button>
          <div class="cal-title"></div>
          <button class="btn cal-nav" data-nav="1" title="${t("Следующий месяц")}">${icon("right")}</button>
        </div>
        <div class="cal-grid"></div>
      </div>
      <div class="cal-day">
        <div class="cal-day-head">
          <div class="cal-day-title"></div>
          <button class="btn cal-close" data-close title="${t("Закрыть")}">${icon("close")}</button>
        </div>
        <div class="cal-list"></div>
        <button class="btn btn-primary cal-add" data-add>${icon("plus")} ${t("Напоминание")}</button>
      </div>
    </div>`;

  const today = () => iso(new Date());
  let selected = today();
  let month = parse(selected);
  month.setDate(1);
  let items = [];

  const monthFmt = new Intl.DateTimeFormat(locale(), { month: "long", year: "numeric" });
  const dayFmt = new Intl.DateTimeFormat(locale(), { weekday: "long", day: "numeric", month: "long" });

  function drawMonth() {
    root.querySelector(".cal-title").textContent = monthFmt.format(month);
    const byDay = new Map();
    for (const r of items) {
      const v = byDay.get(r.date) || { all: 0, open: 0 };
      v.all += 1;
      if (!r.done) v.open += 1;
      byDay.set(r.date, v);
    }
    // Неделя с понедельника, шесть строк, чтобы сетка не прыгала.
    const first = new Date(month);
    first.setDate(1 - ((month.getDay() + 6) % 7));
    const now = today();
    const cells = dayNames().map((n) => `<div class="cal-wd">${esc(n)}</div>`);
    for (let i = 0; i < 42; i++) {
      const d = new Date(first.getFullYear(), first.getMonth(), first.getDate() + i);
      const key = iso(d);
      const mark = byDay.get(key);
      const cls = ["cal-cell"];
      if (d.getMonth() !== month.getMonth()) cls.push("other");
      if (key === now) cls.push("today");
      if (key === selected) cls.push("selected");
      if (d.getDay() === 0 || d.getDay() === 6) cls.push("weekend");
      cells.push(`<button class="${cls.join(" ")}" data-date="${key}">${d.getDate()}${mark ? `<i class="cal-dot ${mark.open ? "" : "done"}"></i>` : ""}</button>`);
    }
    root.querySelector(".cal-grid").innerHTML = cells.join("");
  }

  function drawDay() {
    const title = dayFmt.format(parse(selected));
    root.querySelector(".cal-day-title").textContent = title.charAt(0).toUpperCase() + title.slice(1);
    const list = items.filter((r) => r.date === selected);
    root.querySelector(".cal-list").innerHTML = list.length
      ? list.map((r) => `
        <div class="cal-item ${r.done ? "done" : ""}" data-edit="${esc(r.id)}">
          <button class="cal-check" data-done="${esc(r.id)}" aria-pressed="${r.done}">${r.done ? icon("check") : ""}</button>
          <span class="cal-item-time">${esc(r.time)}</span>
          <span class="cal-item-text">${esc(r.text)}</span>
        </div>`).join("")
      : `<p class="cal-empty">${t("Напоминаний нет")}</p>`;
  }

  const off = onCalendar((st) => {
    items = st.items;
    drawMonth();
    drawDay();
  });

  // Панель висит на стене: забытый календарь закрывается сам через
  // 2 минуты без касаний (если поверх не открыт редактор).
  let idle = null;
  const touch = () => {
    clearTimeout(idle);
    idle = setTimeout(() => {
      if (document.querySelector(".cal-editor-backdrop, .kb-backdrop")) touch();
      else close();
    }, IDLE_MS);
  };
  document.addEventListener("pointerdown", touch, true);
  touch();

  function close() {
    off();
    clearTimeout(idle);
    document.removeEventListener("pointerdown", touch, true);
    document.removeEventListener("keydown", onKey);
    root.remove();
    open = null;
  }
  const onKey = (e) => { if (e.key === "Escape") close(); };
  document.addEventListener("keydown", onKey);

  root.addEventListener("click", async (e) => {
    if (e.target === root) return close();
    const b = e.target.closest("button, [data-edit]");
    if (!b) return;
    const d = b.dataset;
    if ("close" in d) close();
    else if (d.nav) {
      month = new Date(month.getFullYear(), month.getMonth() + Number(d.nav), 1);
      drawMonth();
    } else if (d.date) {
      selected = d.date;
      const picked = parse(selected);
      if (picked.getMonth() !== month.getMonth()) month = new Date(picked.getFullYear(), picked.getMonth(), 1);
      drawMonth();
      drawDay();
    } else if (d.done) {
      const r = items.find((x) => x.id === d.done);
      await reminders.done(r.id, !r.done).catch(() => {});
    } else if ("add" in d) {
      await editReminder({ date: selected, time: "", text: "" });
    } else if (d.edit) {
      await editReminder(items.find((x) => x.id === d.edit));
    }
  });

  drawMonth();
  drawDay();
  document.body.appendChild(root);
  open = { close };
}

// Редактор напоминания: текст с экранной клавиатуры, время по желанию.
async function editReminder(item) {
  const draft = { ...item };
  if (!draft.id) {
    const text = await openKeyboard({ title: t("Напоминание"), layout: layout() });
    if (!text || !text.trim()) return;
    draft.text = text.trim().slice(0, 120);
  }
  let [h, m] = (draft.time || "09:00").split(":").map(Number);
  let timed = Boolean(draft.time);
  return new Promise((resolve) => {
    const root = document.createElement("div");
    root.className = "dlg-backdrop cal-editor-backdrop";
    root.innerHTML = `
      <div class="dlg clock-editor" role="dialog">
        <div class="dlg-title">${item.id ? t("Напоминание") : t("Новое напоминание")}</div>
        <div class="dlg-text cal-ed-date"></div>
        <button class="btn clock-ed-label cal-ed-text" data-text></button>
        <label class="cal-ed-timed"><span>${t("Напомнить в определённое время")}</span><button class="switch" data-timed></button></label>
        <div class="clock-ed-time cal-ed-time">
          <div class="clock-ed-col"><button class="btn" data-step="h+">${icon("up")}</button><span class="clock-ed-h"></span><button class="btn" data-step="h-">${icon("down")}</button></div>
          <span class="clock-ed-colon">:</span>
          <div class="clock-ed-col"><button class="btn" data-step="m+">${icon("up")}</button><span class="clock-ed-m"></span><button class="btn" data-step="m-">${icon("down")}</button></div>
        </div>
        <div class="dlg-actions">
          ${item.id ? `<button class="btn btn-danger clock-ed-delete" data-act="delete">${icon("trash")}</button>` : ""}
          <button class="btn" data-act="cancel">${t("Отмена")}</button>
          <button class="btn btn-primary" data-act="save">${t("Сохранить")}</button>
        </div>
      </div>`;
    const dateFmt = new Intl.DateTimeFormat(locale(), { weekday: "long", day: "numeric", month: "long", year: "numeric" });
    root.querySelector(".cal-ed-date").textContent = dateFmt.format(parse(draft.date));
    const draw = () => {
      root.querySelector("[data-text]").textContent = draft.text;
      root.querySelector("[data-timed]").setAttribute("aria-checked", timed);
      root.querySelector(".cal-ed-time").hidden = !timed;
      root.querySelector(".clock-ed-h").textContent = pad(h);
      root.querySelector(".clock-ed-m").textContent = pad(m);
    };
    // Минуты шагом 5: напоминание ставят на «ровное» время.
    const steps = { "h+": () => { h = (h + 1) % 24; }, "h-": () => { h = (h + 23) % 24; }, "m+": () => { m = (m + 5 - (m % 5)) % 60; }, "m-": () => { m = (m - (m % 5 || 5) + 60) % 60; } };
    root.querySelectorAll("[data-step]").forEach((b) => holdRepeat(b, () => { steps[b.dataset.step](); draw(); }));
    const close = (value) => { root.remove(); resolve(value); };
    root.addEventListener("click", async (e) => {
      if (e.target === root) return close(null);
      const b = e.target.closest("button");
      if (!b || b.dataset.step) return;
      if ("text" in b.dataset) {
        const text = await openKeyboard({ title: t("Напоминание"), value: draft.text, layout: layout() });
        if (text && text.trim()) draft.text = text.trim().slice(0, 120);
        draw();
      } else if ("timed" in b.dataset) {
        timed = !timed;
        draw();
      } else if (b.dataset.act === "cancel") {
        close(null);
      } else if (b.dataset.act === "delete") {
        await reminders.remove(item.id).catch(() => {});
        close("deleted");
      } else if (b.dataset.act === "save") {
        draft.time = timed ? `${pad(h)}:${pad(m)}` : "";
        try {
          await reminders.save(draft);
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
