// Вкладка «SSH-ключи»: ключи, с которыми панель сама ходит по SSH (например,
// модуль мониторинга серверов). Приватные ключи хранит только сервис,
// здесь видны и копируются лишь публичные.

import { api } from "/lib/api.js";
import { t } from "/lib/i18n.js";
import { icon } from "/lib/icons.js";

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

export class SshKeysPanel {
  // ctx: guarded, toast, confirm, render (перерисовать вкладку).
  constructor(ctx) {
    this.ctx = ctx;
    this.keys = null;
    this.importing = false;
  }

  async load() {
    const { keys } = await this.ctx.guarded(() => api.get("/ssh/keys"));
    this.keys = keys;
    this.ctx.render();
  }

  async act(body) {
    const { keys } = await this.ctx.guarded(() => api.post("/ssh/keys", body));
    this.keys = keys;
    this.ctx.render();
  }

  render() {
    if (!this.keys) return `<h1>${t("SSH-ключи")}</h1><div class="empty">${t("Загрузка…")}</div>`;
    // Название ключа по умолчанию переводим, свои названия показываем как есть.
    const title = (k) => (k.id === "panel" && k.title === "Ключ панели" ? t(k.title) : k.title);
    const rows = this.keys.map((k) => `
      <div class="item ssh-key">
        <div class="item-main">
          <div class="grow">
            <div class="item-title">${esc(title(k))} <span class="muted small mono">${esc(k.type)}</span></div>
            <textarea class="ssh-public mono" readonly rows="2">${esc(k.public_key)}</textarea>
          </div>
          <div class="order">
            <button class="btn btn-small" data-ssh-copy="${esc(k.id)}">${t("Копировать")}</button>
            <button class="btn btn-small" data-ssh-delete="${esc(k.id)}">${icon("trash")}</button>
          </div>
        </div>
      </div>`).join("");
    return `
      <h1>${t("SSH-ключи")}</h1>
      <section class="panel list">
        ${rows || `<div class="item muted">${t("Ключей пока нет")}</div>`}
      </section>
      <p class="muted small panel-inset">${t("Публичный ключ добавьте в ~/.ssh/authorized_keys на сервере. Приватные ключи хранятся только на панели и наружу не отдаются.")}</p>
      <h1>${t("Новый ключ")}</h1>
      <form class="panel inline-form" id="ssh-generate-form">
        <input name="title" placeholder="${t("Название, например «Домашние серверы»")}" maxlength="60" autocomplete="off">
        <button class="btn btn-primary" type="submit">${t("Создать")}</button>
      </form>
      ${this.importing ? `
      <form class="panel ssh-import" id="ssh-import-form">
        <label class="field"><span>${t("Название")}</span><input name="title" maxlength="60" autocomplete="off"></label>
        <label class="field ssh-private"><span>${t("Приватный ключ")}<small>${t("OpenSSH или PEM, без пароля")}</small></span>
          <textarea name="private" class="mono" rows="6" spellcheck="false" placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"></textarea>
        </label>
        <div class="row-buttons">
          <button class="btn btn-primary" type="submit">${t("Импортировать")}</button>
          <button class="btn" type="button" data-ssh-import-cancel>${t("Отмена")}</button>
        </div>
      </form>` : `
      <div class="row-buttons flush"><button class="btn" data-ssh-import>${t("Импортировать свой ключ")}</button></div>`}`;
  }

  async onClick(e) {
    const b = e.target.closest("button");
    if (!b) return false;
    const d = b.dataset;
    if (d.sshCopy) {
      const key = this.keys.find((k) => k.id === d.sshCopy);
      try {
        await navigator.clipboard.writeText(key.public_key);
        this.ctx.toast(t("Публичный ключ скопирован"));
      } catch {
        // Админка открыта по http: буфер обмена недоступен, выделяем текст.
        const area = b.closest(".ssh-key").querySelector("textarea");
        area.select();
        this.ctx.toast(t("Скопируйте выделенный ключ вручную"));
      }
    } else if (d.sshDelete) {
      const key = this.keys.find((k) => k.id === d.sshDelete);
      const ok = await this.ctx.confirm({
        title: t("Удалить ключ «{title}»?", { title: key.title }),
        text: t("Модули, которые им пользуются, перестанут подключаться к серверам."), ok: t("Удалить"), danger: true,
      });
      if (ok) await this.act({ action: "delete", id: key.id });
    } else if ("sshImport" in d) {
      this.importing = true;
      this.ctx.render();
    } else if ("sshImportCancel" in d) {
      this.importing = false;
      this.ctx.render();
    } else {
      return false;
    }
    return true;
  }

  async onSubmit(e, data) {
    if (e.target.id === "ssh-generate-form") {
      await this.act({ action: "generate", title: data.title.trim() || t("Ключ панели") });
      this.ctx.toast(t("Ключ создан"));
      return true;
    }
    if (e.target.id === "ssh-import-form") {
      await this.act({ action: "import", title: data.title.trim() || t("Импортированный ключ"), private: data.private });
      this.importing = false;
      this.ctx.render();
      this.ctx.toast(t("Ключ импортирован"));
      return true;
    }
    return false;
  }
}
