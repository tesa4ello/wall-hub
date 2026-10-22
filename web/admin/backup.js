// Вкладка «Резервная копия»: скачать настройки файлом, восстановить из
// файла или из автокопии (см. wallhub/backup.py).

import { api } from "/lib/api.js";
import { backupSummary, uploadBackup } from "/admin/transfer.js";
import { locale, t } from "/lib/i18n.js";
import { icon } from "/lib/icons.js";

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

const PARTS = {
  settings: ["Настройки и столы", "Тема, экран, виджеты, будильники, напоминания, обновления"],
  backgrounds: ["Фоны", "Загруженные картинки"],
  modules: ["Модули из магазина", "Установленные модули и их версии"],
  secrets: ["Пароли и ключи", "Пароль админки, ключи погоды, токены GitHub, данные модулей (например, SSH-ключи)"],
};
const REASONS = { daily: "Ежедневная", restore: "Перед восстановлением", manual: "Вручную" };

function size(bytes) {
  const mb = bytes / (1024 * 1024);
  return mb >= 1 ? t("{n} МБ", { n: mb.toFixed(1) }) : t("{n} КБ", { n: Math.max(1, Math.round(bytes / 1024)) });
}

function date(iso) {
  return iso ? new Intl.DateTimeFormat(locale(), { dateStyle: "medium", timeStyle: "short" }).format(new Date(iso)) : "";
}

export class BackupPanel {
  constructor(ctx) {
    // ctx: {guarded, toast, confirm, render}
    this.ctx = ctx;
    this.data = null;
    this.parts = null;
    // Что восстанавливаем: {source: "upload" | имя автокопии, info, parts}.
    this.pending = null;
    this.busy = false;
    // Форма «С другой панели»: render() её пересоздаёт, введённое храним тут.
    this.pullForm = { address: "", password: "" };
  }

  async load() {
    this.data = await this.ctx.guarded(() => api.get("/admin/backup"));
    this.parts ??= new Set(this.data.export_default);
    this.ctx.render();
  }

  exportUrl() {
    return `/api/admin/backup/export?parts=${[...this.parts].join(",")}`;
  }

  render() {
    if (!this.data) return `<h1>${t("Резервная копия")}</h1><section class="panel"><p class="muted pad">…</p></section>`;
    const check = (part, checked, attr) => {
      const [title, sub] = PARTS[part];
      return `<label class="field"><span>${t(title)}<small>${t(sub)}</small></span>
        <input type="checkbox" ${attr}="${part}" ${checked ? "checked" : ""}></label>`;
    };
    const p = this.pending;
    const snaps = this.data.snapshots;
    return `
      <h1>${t("Резервная копия")}</h1>
      <section class="panel">
        ${this.data.parts.map((part) => check(part, this.parts.has(part), "data-export")).join("")}
        <div class="row-buttons">
          <a class="btn btn-primary ${this.parts.size ? "" : "disabled"}" ${this.parts.size ? `href="${this.exportUrl()}" download` : ""}>${icon("down")} ${t("Скачать копию")}</a>
        </div>
      </section>
      <p class="muted small panel-inset">${this.parts.has("secrets") ? t("В файле будут пароль и ключи: храните его в надёжном месте.") : t("Сети Wi‑Fi и их пароли в копию не входят.")}</p>

      <h1>${t("Восстановить")}</h1>
      <section class="panel">
        ${p ? `
          <div class="pad backup-info">
            <b>${esc(p.source === "upload" ? (p.pulled ? t("Копия с другой панели") : t("Файл копии")) : t("Автокопия"))}</b>
            ${backupSummary(p.info).map((line) => `<div class="muted small">${esc(line)}</div>`).join("")}
          </div>
          ${p.info.parts.map((part) => check(part, p.parts.has(part), "data-restore-part")).join("")}
          <div class="row-buttons">
            <button class="btn" data-backup-cancel>${t("Отмена")}</button>
            <button class="btn btn-danger" data-backup-restore ${p.parts.size && !this.busy ? "" : "disabled"}>${this.busy ? t("Восстанавливаю…") : t("Восстановить")}</button>
          </div>` : `
          <div class="field"><span>${t("Файл копии")}<small>${t("ZIP-файл, скачанный с этой или другой панели")}</small></span>
            <label class="btn btn-small ${this.busy ? "disabled" : ""}">${icon("plus")}<span>${this.busy ? t("Загружаю…") : t("Выбрать файл…")}</span>
              <input type="file" accept=".zip,application/zip" data-backup-file hidden ${this.busy ? "disabled" : ""}>
            </label>
          </div>
          <form class="field" id="backup-pull"><span>${t("С другой панели по сети")}<small>${t("Её адрес и пароль админки, нужна версия 1.1.0 или новее")}</small></span>
            <div class="field-controls">
              <input name="address" value="${esc(this.pullForm.address)}" placeholder="192.168.1.20" autocomplete="off" required ${this.busy ? "disabled" : ""}>
              <input type="password" name="password" value="${esc(this.pullForm.password)}" placeholder="${t("Пароль")}" autocomplete="off" required ${this.busy ? "disabled" : ""}>
              <button class="btn btn-small" type="submit" ${this.busy ? "disabled" : ""}>${t("Забрать")}</button>
            </div>
          </form>`}
      </section>
      <p class="muted small panel-inset">${t("Перед восстановлением панель сама сохранит текущие настройки в автокопию. Восстанавливайте только свои копии: в них есть код модулей.")}</p>

      <h1>${t("Автокопии")}</h1>
      <section class="panel list">
        ${snaps.map((s) => `
          <div class="item"><div class="item-main">
            <div class="item-title grow">${esc(date(s.created))}<div class="muted small">${esc([t(REASONS[s.reason] || s.reason || ""), s.version, size(s.size)].filter(Boolean).join(" · "))}</div></div>
            <a class="btn btn-small" href="/api/admin/backup/snapshot?name=${encodeURIComponent(s.name)}" download title="${t("Скачать")}">${icon("down")}</a>
            <button class="btn btn-small" data-backup-snapshot="${esc(s.name)}">${t("Восстановить…")}</button>
          </div></div>`).join("") || `<p class="muted pad">${t("Автокопий пока нет")}</p>`}
        <div class="row-buttons"><button class="btn" data-backup-create>${t("Сделать копию сейчас")}</button></div>
      </section>
      <p class="muted small panel-inset">${t("Панель хранит {n} последних автокопий: раз в сутки и перед восстановлением. Фоны в них не входят.", { n: 5 })}</p>`;
  }

  preview(source, info) {
    this.pending = { source, info, parts: new Set(info.parts) };
    this.ctx.render();
  }

  // Загрузка файла или копия с другой панели: потом тот же предпросмотр.
  async fetchUpload(fn, pulled = false) {
    this.busy = true;
    this.ctx.render();
    try {
      const info = await this.ctx.guarded(fn);
      this.busy = false;
      this.preview("upload", info);
      this.pending.pulled = pulled;
      this.ctx.render();
    } catch {
      this.busy = false;
      this.ctx.render();
    }
  }

  async onSubmit(e, data) {
    if (e.target.id !== "backup-pull") return false;
    this.pullForm = data;
    await this.fetchUpload(() => api.post("/admin/backup/pull", data), true);
    return true;
  }

  async onChange(e) {
    const el = e.target;
    if ("backupFile" in el.dataset) {
      const file = el.files[0];
      if (file) await this.fetchUpload(() => uploadBackup("/admin/backup/upload", file));
      return true;
    }
    const set = el.dataset.export ? this.parts : el.dataset.restorePart ? this.pending?.parts : null;
    if (!set) return false;
    const part = el.dataset.export || el.dataset.restorePart;
    if (el.checked) set.add(part);
    else set.delete(part);
    this.ctx.render();
    return true;
  }

  async onClick(e) {
    const b = e.target.closest("button");
    if (!b) return false;
    const d = b.dataset;
    if ("backupCancel" in d) {
      this.pending = null;
      this.ctx.render();
    } else if (d.backupSnapshot) {
      const info = await this.ctx.guarded(() => api.post("/admin/backup/inspect", { source: d.backupSnapshot }));
      this.preview(d.backupSnapshot, info);
      document.querySelector("[data-backup-restore]")?.scrollIntoView({ block: "center" });
    } else if ("backupCreate" in d) {
      this.data = await this.ctx.guarded(() => api.post("/admin/backup/create"));
      this.ctx.toast(t("Копия сохранена"));
      this.ctx.render();
    } else if ("backupRestore" in d) {
      const p = this.pending;
      const ok = await this.ctx.confirm({
        title: t("Восстановить настройки из копии?"),
        text: t("Выбранное на панели заменится данными из копии. Текущие настройки сохранятся в автокопию."),
        ok: t("Восстановить"), danger: true,
      });
      if (!ok) return true;
      this.busy = true;
      this.ctx.render();
      try {
        await this.ctx.guarded(() => api.post("/admin/backup/restore", { source: p.source, parts: [...p.parts] }));
        this.ctx.toast(t("Настройки восстановлены"));
        // Сменились настройки, а может, и язык с паролем: начинаем заново.
        setTimeout(() => location.reload(), 800);
      } catch {
        this.busy = false;
        this.ctx.render();
      }
    } else return false;
    return true;
  }
}
