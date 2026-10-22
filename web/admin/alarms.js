// Вкладка «Будильники»: будильники панели, идущие таймеры и настройки звонка.
// Состояние — с сервера (/api/alarms), как у виджетов часов на экране.

import { api } from "/lib/api.js";
import { locale, t } from "/lib/i18n.js";
import { icon } from "/lib/icons.js";
import { alarms as alarmApi, formatDuration, onAlarms, remaining } from "/lib/alarms.js";

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const SOUNDS = [["bell", "Колокольчик"], ["chime", "Перезвон"], ["soft", "Мягкий"], ["beep", "Сигнал"]];

function dayNames() {
  const fmt = new Intl.DateTimeFormat(locale(), { weekday: "short" });
  return Array.from({ length: 7 }, (_, i) => fmt.format(new Date(2024, 0, 1 + i)));
}

export class AlarmsPanel {
  constructor(ctx) {
    // ctx: {guarded, toast, confirm, render}
    this.ctx = ctx;
    this.state = null;
    this.off = null;
    this.tick = null;
  }

  load() {
    this.off ??= onAlarms((st) => {
      this.state = st;
      this.ctx.render();
    });
  }

  // Каждую секунду обновляем только остаток таймеров, не всю страницу.
  mount() {
    clearInterval(this.tick);
    if (!this.state?.timers.some((x) => x.running)) return;
    this.tick = setInterval(() => {
      for (const x of this.state.timers) {
        const node = document.querySelector(`[data-timer-left="${x.id}"]`);
        if (node) node.textContent = formatDuration(remaining(x));
      }
    }, 1000);
  }

  destroy() {
    clearInterval(this.tick);
  }

  render() {
    const st = this.state;
    if (!st) return `<h1>${t("Будильники")}</h1><section class="panel"><p class="muted pad">…</p></section>`;
    const names = dayNames();
    const row = (a) => `
      <form class="item alarm-row" data-alarm="${esc(a.id)}">
        <div class="item-main">
          <input type="time" name="time" value="${esc(a.time)}" required>
          <div class="alarm-days">${names.map((n, i) => `<label class="alarm-day"><input type="checkbox" name="d${i}" ${a.days.includes(i) ? "checked" : ""}><span>${esc(n)}</span></label>`).join("")}</div>
          <input name="label" value="${esc(a.label)}" maxlength="40" placeholder="${t("Подпись")}">
          <button class="switch" type="button" data-alarm-toggle="${esc(a.id)}" aria-checked="${a.enabled}"></button>
          <button class="btn btn-small" type="button" data-alarm-delete="${esc(a.id)}" title="${t("Удалить")}">${icon("trash")}</button>
        </div>
      </form>`;
    const s = st.settings;
    return `
      <h1>${t("Будильники")}</h1>
      <section class="panel list">
        ${st.alarms.map(row).join("") || `<p class="muted pad">${t("Будильников пока нет")}</p>`}
      </section>
      <form class="panel inline-form" id="alarm-add">
        <input type="time" name="time" value="07:00" required>
        <input name="label" maxlength="40" placeholder="${t("Подпись (необязательно)")}">
        <button class="btn btn-primary" type="submit">${icon("plus")} ${t("Добавить")}</button>
      </form>
      <p class="muted small panel-inset">${t("Без отмеченных дней будильник звонит один раз и выключается. Изменения в строке сохраняются сами.")}</p>
      ${st.timers.length ? `
      <h1>${t("Таймеры")}</h1>
      <section class="panel list">
        ${st.timers.map((x) => `
          <div class="item"><div class="item-main">
            <div class="item-title grow">${esc(x.label || t("Таймер"))}<div class="muted small">${x.running ? t("Идёт") : t("На паузе")}</div></div>
            <b class="mono" data-timer-left="${esc(x.id)}">${formatDuration(remaining(x))}</b>
            <button class="btn btn-small" data-timer-cancel="${esc(x.id)}">${t("Остановить")}</button>
          </div></div>`).join("")}
      </section>` : ""}
      <h1>${t("Звонок")}</h1>
      <section class="panel">
        <label class="field"><span>${t("Звук")}</span>
          <select data-alarm-set="sound">${SOUNDS.map(([v, l]) => `<option value="${v}" ${s.sound === v ? "selected" : ""}>${t(l)}</option>`).join("")}</select>
        </label>
        <label class="field"><span>${t("Громкость звонка, %")}<small>${t("Будильник звонит и в тихие часы, и в «Не беспокоить»")}</small></span>
          <input type="number" min="10" max="100" step="5" data-alarm-set="volume" value="${s.volume}">
        </label>
        <label class="field"><span>${t("Отложить на, мин")}</span>
          <input type="number" min="1" max="60" data-alarm-set="snooze" value="${s.snooze}">
        </label>
        <label class="field"><span>${t("Звонить не дольше, мин")}<small>${t("Потом звонок снимется и останется в шторке пропущенным")}</small></span>
          <input type="number" min="1" max="60" data-alarm-set="ring_minutes" value="${s.ring_minutes}">
        </label>
        <div class="row-buttons"><button class="btn" data-alarm-test>${icon("volume")} ${t("Проверить звук")}</button></div>
      </section>`;
  }

  // Строка будильника -> объект для сервера.
  formAlarm(form) {
    const a = this.state.alarms.find((x) => x.id === form.dataset.alarm);
    const data = new FormData(form);
    return {
      ...a,
      time: data.get("time"),
      label: data.get("label") || "",
      days: [0, 1, 2, 3, 4, 5, 6].filter((i) => data.get(`d${i}`)),
    };
  }

  // true — событие обработано.
  async onChange(e) {
    const el = e.target;
    const form = el.closest("form[data-alarm]");
    if (form) {
      if (!form.reportValidity()) return true;
      await this.ctx.guarded(() => alarmApi.save(this.formAlarm(form))).catch(() => {});
      return true;
    }
    const key = el.dataset.alarmSet;
    if (key) {
      const value = key === "sound" ? el.value : Number(el.value);
      await this.ctx.guarded(() => alarmApi.settings({ [key]: value })).catch(() => {});
      return true;
    }
    return false;
  }

  async onClick(e) {
    const b = e.target.closest("button");
    if (!b) return false;
    const d = b.dataset;
    if (d.alarmToggle) {
      await this.ctx.guarded(() => alarmApi.toggle(d.alarmToggle, b.getAttribute("aria-checked") !== "true"));
    } else if (d.alarmDelete) {
      const a = this.state.alarms.find((x) => x.id === d.alarmDelete);
      if (!(await this.ctx.confirm({ title: t("Удалить будильник {time}?", { time: a.time }), ok: t("Удалить"), danger: true }))) return true;
      await this.ctx.guarded(() => alarmApi.remove(a.id));
    } else if (d.timerCancel) {
      await this.ctx.guarded(() => alarmApi.timer("cancel", { id: d.timerCancel }));
    } else if ("alarmTest" in d) {
      await this.ctx.guarded(() => api.post("/sound/test", { name: this.state.settings.sound }));
      this.ctx.toast(t("Звук сыграет на панели"));
    } else return false;
    return true;
  }

  async onSubmit(e, data) {
    if (e.target.id === "alarm-add") {
      await this.ctx.guarded(() => alarmApi.save({ time: data.time, label: data.label || "", days: [0, 1, 2, 3, 4] }));
      return true;
    }
    if (e.target.dataset.alarm) {
      await this.ctx.guarded(() => alarmApi.save(this.formAlarm(e.target)));
      return true;
    }
    return false;
  }
}
