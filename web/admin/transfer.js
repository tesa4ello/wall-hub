// Перенос на новую панель: на экране первого входа в админку, пока пароль
// не задан. Копию забирают со старой панели по сети (адрес и её пароль) или
// загружают файлом, затем выбирают части и восстанавливают
// (/api/setup/transfer/*, см. wallhub/backup.py).

import { api, ApiError } from "/lib/api.js";
import { locale, t } from "/lib/i18n.js";

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

const PARTS = {
  settings: "Настройки и столы",
  backgrounds: "Фоны",
  modules: "Модули из магазина",
  secrets: "Пароли и ключи",
};

// Файл копии как есть: JSON-обёртка api.js для него не подходит.
export async function uploadBackup(path, file) {
  let response;
  try {
    response = await fetch("/api" + path, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/octet-stream" }, body: file,
    });
  } catch {
    throw new ApiError(t("Нет связи с панелью"), 0);
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(data.error || t("Ошибка {status}", { status: response.status }), response.status);
  return data;
}

export function backupSummary(info) {
  const when = info.created ? new Intl.DateTimeFormat(locale(), { dateStyle: "medium", timeStyle: "short" }).format(new Date(info.created)) : "";
  return [
    t("Панель «{panel}», {date}, версия {version}", { panel: info.panel || "—", date: when, version: info.version || "?" }),
    t("Столов: {d}, будильников: {a}, модулей: {m}", { d: info.desktops ?? 0, a: info.alarms ?? 0, m: Object.keys(info.modules || {}).length }),
  ];
}

export class Transfer {
  constructor(root, { onDone, onBack }) {
    this.root = root;
    this.onDone = onDone;
    this.onBack = onBack;
    this.info = null;
    this.parts = null;
    this.busy = "";
    this.error = "";
    // Введённое в форму: render() пересоздаёт её, а после ошибки поля нужны.
    this.form = { address: "", password: "" };
    root.addEventListener("submit", (e) => this.onSubmit(e));
    root.addEventListener("click", (e) => this.onClick(e));
    root.addEventListener("change", (e) => this.onChange(e));
  }

  show() {
    this.info = null;
    this.error = "";
    this.root.hidden = false;
    this.render();
  }

  render() {
    const busy = this.busy ? "disabled" : "";
    const error = this.error ? `<div class="error">${esc(this.error)}</div>` : "";
    if (this.info) {
      this.root.innerHTML = `
        <div class="transfer-head">${t("Перенос с другой панели")}</div>
        <div class="transfer-info">${backupSummary(this.info).map((line) => `<div>${esc(line)}</div>`).join("")}</div>
        ${this.info.parts.map((p) => `<label class="check"><input type="checkbox" data-part="${p}" ${this.parts.has(p) ? "checked" : ""}> ${t(PARTS[p])}</label>`).join("")}
        ${this.info.parts.includes("secrets") ? "" : `<p class="muted small">${t("В копии нет пароля: его попросят придумать после переноса.")}</p>`}
        <button type="button" class="btn btn-primary" data-restore ${busy || (this.parts.size ? "" : "disabled")}>${esc(this.busy || t("Перенести"))}</button>
        <button type="button" class="btn" data-back ${busy}>${t("Назад")}</button>
        ${error}`;
      return;
    }
    this.root.innerHTML = `
      <div class="transfer-head">${t("Перенос с другой панели")}</div>
      <p class="muted">${t("Новая панель заберёт настройки, столы, будильники, модули, фоны и пароль со старой. Старой панели нужна версия 1.1.0 или новее.")}</p>
      <form class="transfer-form" id="transfer-pull">
        <input name="address" value="${esc(this.form.address)}" placeholder="${t("Адрес старой панели, например 192.168.1.20")}" autocomplete="off" required ${busy}>
        <input type="password" name="password" value="${esc(this.form.password)}" placeholder="${t("Пароль админки старой панели")}" autocomplete="off" required ${busy}>
        <button class="btn btn-primary" type="submit" ${busy}>${esc(this.busy || t("Забрать настройки"))}</button>
      </form>
      <div class="transfer-or muted">${t("или из файла копии")}</div>
      <label class="btn ${this.busy ? "disabled" : ""}">${t("Выбрать файл…")}
        <input type="file" accept=".zip,application/zip" data-file hidden ${busy}>
      </label>
      <button type="button" class="btn" data-back ${busy}>${t("Назад")}</button>
      ${error}`;
  }

  async run(label, fn) {
    this.busy = label;
    this.error = "";
    this.render();
    try {
      return await fn();
    } catch (err) {
      this.error = err.message;
      return null;
    } finally {
      this.busy = "";
      this.render();
    }
  }

  preview(info) {
    if (!info) return;
    this.info = info;
    this.parts = new Set(info.parts);
    this.render();
  }

  async onSubmit(e) {
    if (e.target.id !== "transfer-pull") return;
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.target));
    this.form = data;
    this.preview(await this.run(t("Забираю…"), () => api.post("/setup/transfer/pull", data)));
  }

  async onChange(e) {
    const el = e.target;
    if ("file" in el.dataset && el.files[0]) {
      const file = el.files[0];
      this.preview(await this.run(t("Загружаю…"), () => uploadBackup("/setup/transfer/upload", file)));
    } else if (el.dataset.part) {
      if (el.checked) this.parts.add(el.dataset.part);
      else this.parts.delete(el.dataset.part);
      this.render();
    }
  }

  async onClick(e) {
    const b = e.target.closest("button");
    if (!b || b.type === "submit") return;
    if ("back" in b.dataset) {
      if (this.info) {
        this.info = null;
        this.render();
      } else {
        this.root.hidden = true;
        this.onBack();
      }
    } else if ("restore" in b.dataset) {
      const done = await this.run(t("Переношу…"), () => api.post("/setup/transfer/restore", { parts: [...this.parts] }));
      if (done) {
        this.root.hidden = true;
        this.onDone();
      }
    }
  }
}
