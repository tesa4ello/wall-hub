// Вкладка «Модули»: модули из подключённых каталогов (магазина), установка,
// обновление, удаление и сами каталоги.

import { api } from "/lib/api.js";
import { language, t } from "/lib/i18n.js";
import { icon } from "/lib/icons.js";

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

export class StorePanel {
  // ctx: guarded, toast, confirm, render (перерисовать вкладку), changed (после установки или удаления).
  constructor(ctx) {
    this.ctx = ctx;
    this.data = null;
    this.busy = null; // id модуля или "refresh", пока идёт запрос
    this.settingsFor = null; // id модуля, чья страница настроек открыта
    this.page = null; // смонтированный admin.js модуля
  }

  // Загруженный на панели модуль (из /api/catalog) или undefined.
  loadedModule(id) {
    return this.ctx.modules().find((x) => x.id === id && !x.error);
  }

  // Страница настроек модуля: его admin.js, export mount(el, {api, t}).
  renderSettings() {
    const id = this.settingsFor;
    // Модуля из папки разработчика нет в листинге магазина: берём загруженный.
    const m = this.data?.modules.find((x) => x.id === id) || this.loadedModule(id);
    const sub = m?.description ? this.tr(m, m.description) : t("Настройки модуля");
    return `
      <h1>${esc(m?.title ? this.tr(m, m.title) : id)}</h1>
      <p class="page-sub">${esc(sub)}</p>
      <div class="row-buttons flush"><button class="btn btn-small" data-store-back>← ${t("Модули")}</button></div>
      <div id="module-admin"></div>`;
  }

  async mount() {
    this.destroy();
    const el = document.getElementById("module-admin");
    const id = this.settingsFor;
    if (!el || !id) return;
    try {
      const mod = await import(`/modules/${id}/admin.js`);
      if (this.settingsFor !== id || !el.isConnected) return;
      this.page = mod.mount(el, { api, t }) || null;
    } catch (e) {
      console.error("module admin failed", id, e);
      el.innerHTML = `<div class="panel"><div class="item error">${esc(t("Страница настроек модуля не загрузилась"))}</div></div>`;
    }
  }

  destroy() {
    try {
      this.page?.destroy?.();
    } catch (e) {
      console.error(e);
    }
    this.page = null;
  }

  async load(path = "/store", body) {
    const call = body ? () => api.post(path, body) : () => api.get(path);
    this.data = await this.ctx.guarded(call);
    this.ctx.render();
  }

  // Подписи из каталога переводятся его же переводами (модуль ещё не установлен).
  tr(m, text) {
    return (m.i18n?.[language()] || {})[text] || t(text);
  }

  render() {
    if (this.settingsFor) return this.renderSettings();
    const d = this.data;
    if (!d) return `<h1>${t("Модули")}</h1><div class="empty">${t("Загрузка…")}</div>`;
    const names = Object.fromEntries(d.catalogs.map((c) => [c.id, c.repo]));
    // Модули не из каталогов (папка разработчика): показываем, чтобы их можно было настроить.
    const known = new Set(d.modules.map((m) => m.id));
    const local = this.ctx.modules().filter((m) => m.source !== "builtin" && !m.error && !known.has(m.id))
      .map((m) => ({ ...m, widgets: [], local: true }));
    const rows = [...d.modules, ...local].map((m) => this.renderModule(m, names)).join("");
    return `
      <h1>${t("Модули")}</h1>
      <section class="panel list">
        ${rows || `<div class="item muted">${t("В подключённых каталогах пока нет модулей")}</div>`}
      </section>
      <div class="row-buttons flush">
        <button class="btn" data-store-refresh ${this.busy ? "disabled" : ""}>${icon("refresh")} ${t("Проверить каталоги")}</button>
      </div>
      <h1>${t("Каталоги")}</h1>
      <section class="panel list">${d.catalogs.map((c, i) => this.renderCatalog(c, i, d.catalogs.length)).join("")}</section>
      <form class="panel inline-form" id="catalog-form">
        <input name="url" placeholder="${t("Ссылка на репозиторий GitHub или owner/repo")}" autocomplete="off">
        <input name="branch" placeholder="main" class="narrow" autocomplete="off">
        <button class="btn btn-primary" type="submit">${t("Подключить")}</button>
      </form>
      <p class="muted small panel-inset">${t("Подключая каталог, вы доверяете всем его модулям: их код работает на панели.")}</p>
      <h1>${t("Доступ к GitHub")}</h1>
      <form class="panel inline-form" id="store-token-form">
        <input name="token" type="password" autocomplete="off"
          placeholder="${t(d.token_set ? "Токен сохранён · вставьте новый, чтобы заменить" : "Токен для приватного репозитория")}">
        <button class="btn btn-primary" type="submit" ${this.busy ? "disabled" : ""}>${t("Сохранить")}</button>
        ${d.token_set ? `<button class="btn" type="button" data-store-token-clear>${t("Удалить")}</button>` : ""}
      </form>
      <p class="muted small panel-inset">${t("Один токен на панель: им же скачиваются обновления. Для приватных каталогов токену нужен доступ на чтение к каждому репозиторию (у fine-grained токена — Contents: Read-only).")}</p>`;
  }

  renderModule(m, names) {
    const busy = this.busy === m.id;
    let state = "";
    let actions = "";
    if (m.local) {
      state = t("Из папки модулей");
      const loaded = this.loadedModule(m.id);
      if (loaded?.admin) actions += `<button class="btn btn-small" data-store-settings="${esc(m.id)}">${t("Настроить")}</button>`;
      if (loaded?.backend_error) state += ` · ${t("серверная часть не запустилась: {error}", { error: loaded.backend_error })}`;
    } else if (m.builtin) {
      state = t("Входит в ядро");
    } else if (!m.compatible && !m.installed) {
      state = t("Не подходит к этой версии панели");
    } else {
      if (m.installed) {
        state = m.update ? t("Установлена {old}, доступна {new}", { old: m.installed, new: m.version }) : t("Установлена {version}", { version: m.installed });
        if (m.orphan) state += ` · ${t("нет в каталогах")}`;
      }
      if (!m.installed) actions += `<button class="btn btn-primary btn-small" data-store-install="${esc(m.id)}" ${this.busy ? "disabled" : ""}>${busy ? t("Ставлю…") : t("Установить")}</button>`;
      if (m.update) actions += `<button class="btn btn-primary btn-small" data-store-install="${esc(m.id)}" ${this.busy ? "disabled" : ""}>${busy ? t("Обновляю…") : t("Обновить")}</button>`;
      const loaded = this.loadedModule(m.id);
      if (loaded?.admin) actions += `<button class="btn btn-small" data-store-settings="${esc(m.id)}">${t("Настроить")}</button>`;
      if (loaded?.backend_error) state += ` · ${t("серверная часть не запустилась: {error}", { error: loaded.backend_error })}`;
      if (m.installed) actions += `<button class="btn btn-small" data-store-remove="${esc(m.id)}" ${this.busy ? "disabled" : ""}>${t("Удалить")}</button>`;
    }
    const widgets = m.widgets.map((w) => `<span class="chip">${icon(w.icon || "grid", "icon tiny")} ${esc(this.tr(m, w.title))}</span>`).join("");
    return `
      <div class="item">
        <div class="item-main">
          <div class="grow">
            <div class="item-title">${esc(this.tr(m, m.title))} <span class="muted small mono">${esc(m.version)}</span></div>
            <div class="muted small">${esc(this.tr(m, m.description))}</div>
            ${widgets ? `<div class="chips">${widgets}</div>` : ""}
            <div class="small ${m.update ? "accent" : "muted"}">${[state, !m.builtin && names[m.catalog]].filter(Boolean).map(esc).join(" · ")}</div>
          </div>
          <div class="order">${actions}</div>
        </div>
      </div>`;
  }

  renderCatalog(c, i, count) {
    const error = c.enabled && c.error ? `<div class="small error">${esc(t(c.error, c.error_args))}</div>` : "";
    const commit = c.commit ? ` · <span class="mono">${esc(c.commit.slice(0, 7))}</span>` : "";
    return `
      <div class="item">
        <div class="item-main">
          <label class="check grow"><input type="checkbox" data-catalog-toggle="${esc(c.id)}" ${c.enabled ? "checked" : ""}>
            <span><span class="item-title">${esc(c.repo)}</span>
            <span class="muted small">${t("ветка {branch}", { branch: esc(c.branch) })}${commit}${c.main ? ` · ${t("основной")}` : ""}</span></span></label>
          ${c.main ? "" : `<div class="order">
            <button class="btn btn-small" data-catalog="up" data-id="${esc(c.id)}" ${i <= 1 ? "disabled" : ""} title="${t("Выше")}">${icon("up")}</button>
            <button class="btn btn-small" data-catalog="down" data-id="${esc(c.id)}" ${i === count - 1 ? "disabled" : ""} title="${t("Ниже")}">${icon("down")}</button>
            <button class="btn btn-small" data-catalog="remove" data-id="${esc(c.id)}" title="${t("Отключить каталог")}">${icon("trash")}</button></div>`}
        </div>
        ${error}
      </div>`;
  }

  async run(id, fn) {
    this.busy = id;
    this.ctx.render();
    try {
      await fn();
    } finally {
      this.busy = null;
      this.ctx.render();
    }
  }

  // true — событие обработано панелью.
  async onClick(e) {
    const b = e.target.closest("button");
    if (!b) return false;
    const d = b.dataset;
    if (d.storeSettings) {
      this.settingsFor = d.storeSettings;
      this.ctx.render();
    } else if ("storeBack" in d) {
      this.settingsFor = null;
      this.ctx.render();
    } else if ("storeRefresh" in d) {
      await this.run("refresh", () => this.load("/store/refresh", {}));
    } else if (d.storeInstall) {
      const m = this.data.modules.find((x) => x.id === d.storeInstall);
      await this.run(m.id, async () => {
        await this.load("/store/install", { id: m.id });
        this.ctx.toast(t(m.update ? "Модуль «{title}» обновлён" : "Модуль «{title}» установлен", { title: this.tr(m, m.title) }));
        await this.ctx.changed();
      });
    } else if (d.storeRemove) {
      const m = this.data.modules.find((x) => x.id === d.storeRemove);
      const ok = await this.ctx.confirm({
        title: t("Удалить модуль «{title}»?", { title: this.tr(m, m.title) }),
        text: t("Его виджеты будут убраны со всех столов."), ok: t("Удалить"), danger: true,
      });
      if (!ok) return true;
      await this.run(m.id, async () => {
        await this.load("/store/remove", { id: m.id });
        await this.ctx.changed();
      });
    } else if ("storeTokenClear" in d) {
      const ok = await this.ctx.confirm({ title: t("Удалить токен GitHub?"), ok: t("Удалить"), danger: true });
      if (!ok) return true;
      await this.saveToken("");
    } else if (d.catalog) {
      if (d.catalog === "remove") {
        const c = this.data.catalogs.find((x) => x.id === d.id);
        const ok = await this.ctx.confirm({
          title: t("Отключить каталог {repo}?", { repo: c.repo }),
          text: t("Установленные из него модули останутся, но обновляться не будут."), ok: t("Отключить"), danger: true,
        });
        if (!ok) return true;
      }
      await this.load("/store/catalogs", { action: d.catalog, id: d.id });
    } else {
      return false;
    }
    return true;
  }

  async onChange(e) {
    const id = e.target.dataset.catalogToggle;
    if (!id) return false;
    await this.load("/store/catalogs", { action: e.target.checked ? "enable" : "disable", id });
    return true;
  }

  // Токен общий с обновлениями. После смены сразу перечитываем каталоги.
  async saveToken(token) {
    await this.run("refresh", async () => {
      await this.ctx.guarded(() => api.post("/update/token", { token }));
      await this.load("/store/refresh", {});
    });
  }

  async onSubmit(e, data) {
    if (e.target.id === "store-token-form") {
      if (!data.token.trim()) return true;
      await this.saveToken(data.token.trim());
      e.target.reset?.();
      this.ctx.toast(t("Токен сохранён"));
      return true;
    }
    if (e.target.id !== "catalog-form") return false;
    if (!data.url.trim()) return true;
    await this.run("refresh", () => this.load("/store/catalogs", { action: "add", url: data.url.trim(), branch: data.branch.trim() || "main" }));
    this.ctx.toast(t("Каталог подключён"));
    return true;
  }
}
