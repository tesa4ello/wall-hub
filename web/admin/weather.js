// Вкладка «Погода»: источники прогноза (порядок, включение, ключи, проверка),
// интервал обновления и единицы по умолчанию. Настройки отдельных виджетов
// (место, свои единицы) остаются в редакторе столов.

import { api } from "/lib/api.js";
import { locale, t } from "/lib/i18n.js";
import { icon } from "/lib/icons.js";

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const UNITS = {
  temp: ["Температура", [["c", "°C"], ["f", "°F"]]],
  wind: ["Ветер", [["ms", "м/с"], ["kmh", "км/ч"], ["mph", "миль/ч"]]],
  pressure: ["Давление", [["mmhg", "мм рт. ст."], ["hpa", "гПа"]]],
};

export class WeatherPanel {
  // ctx: guarded, toast, render (перерисовать вкладку).
  constructor(ctx) {
    this.ctx = ctx;
    this.data = null;
    this.testing = null; // id провайдера, который сейчас проверяется
  }

  async load() {
    this.data = await this.ctx.guarded(() => api.get("/m/weather/settings"));
    this.ctx.render();
  }

  async save(patch) {
    this.data = await this.ctx.guarded(() => api.post("/m/weather/settings", patch));
    this.ctx.render();
  }

  status(p) {
    const s = p.status;
    if (!s) return p.key && !p.key_set ? t("Нужен ключ") : "";
    const time = new Date(s.at * 1000).toLocaleTimeString(locale(), { hour: "2-digit", minute: "2-digit" });
    return s.ok ? t("Ответил в {time} за {ms} мс", { time, ms: s.ms }) : t("Ошибка в {time}: {error}", { time, error: s.error });
  }

  render() {
    const d = this.data;
    if (!d) return `<h1>${t("Погода")}</h1><div class="empty">${t("Загрузка…")}</div>`;
    const meta = Object.fromEntries(d.providers.map((p) => [p.id, p]));
    const order = d.settings.providers;
    const rows = order.map((o, i) => {
      const p = meta[o.id];
      const s = p.status;
      return `
        <div class="item">
          <div class="item-main">
            <label class="check grow"><input type="checkbox" data-weather-enable="${esc(p.id)}" ${o.enabled ? "checked" : ""}>
              <span><span class="item-title">${esc(t(p.title))}</span><br>
              <span class="muted small">${esc(t(p.note))} <a href="${esc(p.site)}" target="_blank" rel="noopener">${esc(p.site.replace(/^https?:\/\//, ""))}</a></span></span></label>
            <div class="order">
              <button class="btn btn-small" data-weather-test="${esc(p.id)}" ${this.testing || (p.key && !p.key_set) ? "disabled" : ""}>${this.testing === p.id ? t("Проверяю…") : t("Проверить")}</button>
              <button class="btn btn-small" data-weather-move="-1" data-id="${esc(p.id)}" ${i === 0 ? "disabled" : ""} title="${t("Выше")}">${icon("up")}</button>
              <button class="btn btn-small" data-weather-move="1" data-id="${esc(p.id)}" ${i === order.length - 1 ? "disabled" : ""} title="${t("Ниже")}">${icon("down")}</button>
            </div>
          </div>
          ${p.key ? `
            <form class="inline-form weather-key" data-weather-key="${esc(p.id)}">
              <input name="key" type="password" autocomplete="off"
                placeholder="${t(p.key_set ? "Ключ сохранён · вставьте новый, чтобы заменить" : "Ключ API")}">
              <button class="btn btn-small btn-primary" type="submit">${t("Сохранить")}</button>
              ${p.key_set ? `<button class="btn btn-small" type="button" data-weather-key-clear="${esc(p.id)}">${t("Удалить")}</button>` : ""}
            </form>` : ""}
          ${this.status(p) ? `<div class="small ${s && !s.ok ? "error" : "muted"}">${esc(this.status(p))}</div>` : ""}
        </div>`;
    }).join("");
    const unitFields = Object.entries(UNITS).map(([key, [title, choices]]) => `
      <label class="field"><span>${t(title)}</span>
        <select data-weather-unit="${key}">${choices.map(([v, l]) => `<option value="${v}" ${d.settings.units[key] === v ? "selected" : ""}>${esc(t(l))}</option>`).join("")}</select>
      </label>`).join("");
    return `
      <h1>${t("Погода")}</h1>
      <h1>${t("Источники")}</h1>
      <p class="muted small">${t("Прогноз берётся у первого источника по списку, который ответил. Если источник не отвечает, панель полчаса спрашивает следующие.")}</p>
      <section class="panel list">${rows}</section>
      <h1>${t("Обновление")}</h1>
      <section class="panel">
        <label class="field"><span>${t("Обновлять прогноз каждые, мин")}<small>${t("От 5 до 60. Яндекс Погода — не чаще раза в 30 минут.")}</small></span>
          <input type="number" min="5" max="60" data-weather-refresh value="${d.settings.refresh_min}">
        </label>
      </section>
      <h1>${t("Единицы по умолчанию")}</h1>
      <section class="panel">${unitFields}</section>
      <p class="muted small panel-inset">${t("Место и свои единицы для каждого виджета задаются в редакторе столов.")}</p>`;
  }

  // true — событие обработано вкладкой.
  async onChange(e) {
    const el = e.target;
    if (el.dataset.weatherEnable) {
      const providers = this.data.settings.providers.map((p) => (p.id === el.dataset.weatherEnable ? { ...p, enabled: el.checked } : p));
      await this.save({ providers });
    } else if ("weatherRefresh" in el.dataset) {
      const v = parseInt(el.value, 10);
      if (Number.isNaN(v)) return true;
      await this.save({ refresh_min: Math.max(5, Math.min(60, v)) });
    } else if (el.dataset.weatherUnit) {
      await this.save({ units: { [el.dataset.weatherUnit]: el.value } });
    } else {
      return false;
    }
    return true;
  }

  async onSubmit(e, data) {
    const id = e.target.dataset.weatherKey;
    if (!id) return false;
    if (!(data.key || "").trim()) return true;
    this.data = await this.ctx.guarded(() => api.post("/m/weather/key", { id, key: data.key.trim() }));
    this.ctx.toast(t("Ключ сохранён"));
    this.ctx.render();
    return true;
  }

  async onClick(e) {
    const b = e.target.closest("button");
    if (!b) return false;
    const d = b.dataset;
    if (d.weatherMove) {
      const list = [...this.data.settings.providers];
      const i = list.findIndex((p) => p.id === d.id);
      const j = i + Number(d.weatherMove);
      if (i < 0 || j < 0 || j >= list.length) return true;
      [list[i], list[j]] = [list[j], list[i]];
      await this.save({ providers: list });
    } else if (d.weatherKeyClear) {
      this.data = await this.ctx.guarded(() => api.post("/m/weather/key", { id: d.weatherKeyClear, key: "" }));
      this.ctx.render();
    } else if (d.weatherTest) {
      this.testing = d.weatherTest;
      this.ctx.render();
      try {
        const r = await api.post("/m/weather/test", { id: d.weatherTest });
        this.ctx.toast(t("Работает: сейчас {temp}°, ответ за {ms} мс", { temp: Math.round(r.temp), ms: r.ms }));
      } catch (err) {
        this.ctx.toast(err.message, "error");
      } finally {
        this.testing = null;
        await this.load().catch(() => {});
      }
    } else {
      return false;
    }
    return true;
  }
}
