// Экранные настройки киоска: экран, Wi‑Fi, система.

import { api } from "/lib/api.js";
import { icon, signalBars } from "/lib/icons.js";
import { choose, confirmDialog, toast } from "/lib/dialog.js";
import { openKeyboard } from "/lib/keyboard.js";

const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const SECTIONS = [
  { id: "display", title: "Экран", icon: "display" },
  { id: "wifi", title: "Wi‑Fi", icon: "wifi" },
  { id: "system", title: "Система", icon: "info" },
];

const DIM_AFTER = [0, 1, 5, 15, 30];
const DIM_LEVEL = [50, 70, 85, 95];

function shiftTime(hhmm, delta) {
  const [h, m] = hhmm.split(":").map(Number);
  const t = (h * 60 + m + delta + 1440) % 1440;
  return `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(t % 60).padStart(2, "0")}`;
}

export class Settings {
  constructor(root, { save }) {
    this.root = root;
    this.save = save;
    this.section = "display";
    this.config = null;
    this.wifi = null;
    this.wifiBusy = false;
    this.wifiTimer = null;

    root.innerHTML = `
      <div class="settings-nav">
        <div class="settings-heading">Настройки</div>
        ${SECTIONS.map((s) => `<button class="nav-item" data-section="${s.id}">${icon(s.icon)}<span>${s.title}</span></button>`).join("")}
      </div>
      <div class="settings-body">
        <button class="settings-close" aria-label="Закрыть">${icon("close")}</button>
        <div class="settings-content"></div>
      </div>`;
    this.content = root.querySelector(".settings-content");
    root.querySelector(".settings-close").addEventListener("click", () => this.close());
    root.querySelectorAll(".nav-item").forEach((b) =>
      b.addEventListener("click", () => this.show(b.dataset.section)),
    );
    this.content.addEventListener("click", (e) => this.onClick(e));
  }

  open() {
    this.root.hidden = false;
    this.show(this.section);
  }

  close() {
    this.root.hidden = true;
    clearInterval(this.wifiTimer);
  }

  onConfig(config) {
    this.config = config;
    if (!this.root.hidden && this.section !== "wifi") this.render();
  }

  show(section) {
    this.section = section;
    clearInterval(this.wifiTimer);
    this.root.querySelectorAll(".nav-item").forEach((b) =>
      b.classList.toggle("active", b.dataset.section === section),
    );
    if (section === "wifi") {
      this.wifi = null;
      this.loadWifi(true);
      this.wifiTimer = setInterval(() => this.loadWifi(false), 10000);
    }
    if (section === "system") this.loadInfo();
    this.render();
  }

  render() {
    const body = { display: () => this.renderDisplay(), wifi: () => this.renderWifi(), system: () => this.renderSystem() };
    this.content.innerHTML = body[this.section]();
  }

  // --- Экран ------------------------------------------------------------------

  renderDisplay() {
    const ui = this.config.ui;
    const seg = (key, items) =>
      `<div class="segmented">${items
        .map(([value, label]) => `<button data-set="${key}" data-value='${JSON.stringify(value)}' aria-pressed="${ui[key] === value}">${label}</button>`)
        .join("")}</div>`;
    const timeStepper = (key) => `
      <div class="stepper">
        <button class="btn" data-time="${key}" data-delta="-30">−</button>
        <span class="stepper-value">${ui[key]}</span>
        <button class="btn" data-time="${key}" data-delta="30">+</button>
      </div>`;
    return `
      <h2>Экран</h2>
      <div class="card">
        <div class="row ${ui.auto_theme ? "row-disabled" : ""}">
          <div class="row-label">Тема</div>
          ${seg("theme", [["light", icon("sun") + "Светлая"], ["dark", icon("moon") + "Тёмная"]])}
        </div>
        <div class="row">
          <div class="row-label">Автопереключение темы<div class="row-hint">Днём светлая, ночью тёмная</div></div>
          <button class="switch" data-toggle="auto_theme" aria-checked="${ui.auto_theme}"></button>
        </div>
        ${ui.auto_theme ? `
          <div class="row"><div class="row-label">Светлая с</div>${timeStepper("day_start")}</div>
          <div class="row"><div class="row-label">Тёмная с</div>${timeStepper("night_start")}</div>` : ""}
      </div>
      <div class="card">
        <div class="row">
          <div class="row-label">Затемнять при простое</div>
          ${seg("dim_after_min", DIM_AFTER.map((m) => [m, m ? `${m} мин` : "Нет"]))}
        </div>
        <div class="row ${ui.dim_after_min ? "" : "row-disabled"}">
          <div class="row-label">Сила затемнения</div>
          ${seg("dim_level", DIM_LEVEL.map((l) => [l, `${l}%`]))}
        </div>
      </div>`;
  }

  // --- Wi‑Fi ------------------------------------------------------------------

  async loadWifi(rescan) {
    try {
      this.wifi = await api.get("/wifi" + (rescan ? "?rescan=1" : ""));
    } catch (e) {
      this.wifi = { error: e.message };
    }
    if (this.section === "wifi" && !this.root.hidden && !this.wifiBusy) this.render();
  }

  renderWifi() {
    const w = this.wifi;
    let list;
    if (!w) list = `<div class="empty">Ищу сети…</div>`;
    else if (w.error) list = `<div class="empty">${esc(w.error)}</div>`;
    else if (!w.available) list = `<div class="empty">Wi‑Fi адаптер не найден</div>`;
    else if (!w.radio) list = `<div class="empty">Wi‑Fi выключен</div>`;
    else if (!w.networks.length) list = `<div class="empty">Сети не найдены</div>`;
    else {
      list = `<div class="card list">${w.networks
        .map((n, i) => `
          <button class="net ${n.active ? "net-active" : ""}" data-net="${i}">
            ${signalBars(n.signal)}
            <span class="net-name">${esc(n.ssid)}</span>
            <span class="net-meta">${n.active ? "Подключено" : n.saved ? "Сохранена" : ""}</span>
            ${n.secure ? icon("lock", "icon net-lock") : ""}
          </button>`)
        .join("")}</div>`;
    }
    const busy = this.wifiBusy ? `<div class="busy">${esc(this.wifiBusy)}</div>` : "";
    return `
      <h2>Wi‑Fi</h2>
      <div class="card">
        <div class="row">
          <div class="row-label">Wi‑Fi${w && w.connection ? `<div class="row-hint">Подключено к ${esc(w.connection)}</div>` : ""}</div>
          <button class="switch" data-wifi-radio aria-checked="${!!(w && w.radio)}" ${w && w.available ? "" : "disabled"}></button>
        </div>
      </div>
      ${busy}
      <div class="list-head">
        <span>Доступные сети</span>
        <span class="list-actions">
          <button class="btn btn-small" data-wifi-hidden>Скрытая сеть</button>
          <button class="btn btn-small" data-wifi-rescan>${icon("refresh")}</button>
        </span>
      </div>
      ${list}`;
  }

  async wifiAction(label, fn) {
    this.wifiBusy = label;
    this.render();
    try {
      await fn();
      return true;
    } catch (e) {
      toast(e.message, "error");
      return false;
    } finally {
      this.wifiBusy = false;
      await this.loadWifi(false);
    }
  }

  async onNetwork(net) {
    if (net.active) {
      const action = await choose({
        title: net.ssid,
        text: "Сеть подключена",
        options: [
          { label: "Отмена", value: null },
          { label: "Забыть", value: "forget", kind: "danger" },
          { label: "Отключиться", value: "disconnect", kind: "primary" },
        ],
      });
      if (action === "disconnect") await this.wifiAction("Отключаюсь…", () => api.post("/wifi/disconnect"));
      if (action === "forget") await this.wifiAction("Удаляю сеть…", () => api.post("/wifi/forget", { ssid: net.ssid }));
      return;
    }
    if (net.saved) {
      const action = await choose({
        title: net.ssid,
        text: "Сеть сохранена",
        options: [
          { label: "Отмена", value: null },
          { label: "Забыть", value: "forget", kind: "danger" },
          { label: "Подключиться", value: "connect", kind: "primary" },
        ],
      });
      if (action === "forget") await this.wifiAction("Удаляю сеть…", () => api.post("/wifi/forget", { ssid: net.ssid }));
      if (action !== "connect") return;
      const ok = await this.wifiAction(`Подключаюсь к ${net.ssid}…`, () => api.post("/wifi/connect", { ssid: net.ssid }));
      if (ok) toast(`Подключено к ${net.ssid}`);
      return;
    }
    let password = "";
    if (net.secure) {
      password = await openKeyboard({ title: `Пароль для ${net.ssid}`, password: true, minLength: 8 });
      if (password === null) return;
    }
    const ok = await this.wifiAction(`Подключаюсь к ${net.ssid}…`, () => api.post("/wifi/connect", { ssid: net.ssid, password }));
    if (ok) toast(`Подключено к ${net.ssid}`);
  }

  async onHiddenNetwork() {
    const ssid = await openKeyboard({ title: "Имя сети", minLength: 1 });
    if (!ssid) return;
    const password = await openKeyboard({ title: `Пароль для ${ssid} (пусто — без пароля)`, password: true });
    if (password === null) return;
    const ok = await this.wifiAction(`Подключаюсь к ${ssid}…`, () => api.post("/wifi/connect", { ssid, password, hidden: true }));
    if (ok) toast(`Подключено к ${ssid}`);
  }

  // --- Система ----------------------------------------------------------------

  async loadInfo() {
    try {
      this.info = await api.get("/info");
    } catch {
      this.info = null;
    }
    if (this.section === "system" && !this.root.hidden) this.render();
  }

  renderSystem() {
    const info = this.info || {};
    const initial = this.config.admin.initial_password;
    return `
      <h2>Система</h2>
      <div class="card">
        <div class="row"><div class="row-label">Имя устройства</div><div class="row-value">${esc(info.hostname || "—")}</div></div>
        <div class="row"><div class="row-label">Версия</div><div class="row-value">${esc(info.version || "—")}</div></div>
      </div>
      <div class="card">
        <div class="row">
          <div class="row-label">${icon("admin")} Админка
            <div class="row-hint">Откройте адрес в браузере на компьютере или телефоне в той же сети</div>
          </div>
          <div class="row-value mono">${esc(info.admin_url || "—")}</div>
        </div>
        ${initial ? `
        <div class="row">
          <div class="row-label">Начальный пароль<div class="row-hint">Пропадёт отсюда после смены пароля в админке</div></div>
          <div class="row-value mono big">${esc(initial)}</div>
        </div>` : ""}
      </div>
      <div class="power">
        <button class="btn power-btn" data-power="reboot">${icon("reboot")}Перезагрузить</button>
        <button class="btn btn-danger power-btn" data-power="poweroff">${icon("power")}Выключить</button>
      </div>`;
  }

  async onPower(action) {
    const reboot = action === "reboot";
    const ok = await confirmDialog({
      title: reboot ? "Перезагрузить панель?" : "Выключить панель?",
      text: reboot ? "Панель будет недоступна около минуты." : "Включить её снова можно будет только кнопкой или по питанию.",
      ok: reboot ? "Перезагрузить" : "Выключить",
      danger: !reboot,
    });
    if (!ok) return;
    try {
      await api.post("/power", { action });
      toast(reboot ? "Перезагружаюсь…" : "Выключаюсь…");
    } catch (e) {
      toast(e.message, "error");
    }
  }

  // --- обработка нажатий --------------------------------------------------------

  async onClick(e) {
    const b = e.target.closest("button");
    if (!b || b.disabled) return;
    const ui = this.config.ui;
    try {
      if (b.dataset.set) {
        await this.save({ [b.dataset.set]: JSON.parse(b.dataset.value) });
      } else if (b.dataset.toggle) {
        await this.save({ [b.dataset.toggle]: !ui[b.dataset.toggle] });
      } else if (b.dataset.time) {
        await this.save({ [b.dataset.time]: shiftTime(ui[b.dataset.time], Number(b.dataset.delta)) });
      } else if (b.dataset.net !== undefined) {
        await this.onNetwork(this.wifi.networks[Number(b.dataset.net)]);
      } else if ("wifiRescan" in b.dataset) {
        this.wifi = null;
        this.render();
        await this.loadWifi(true);
      } else if ("wifiHidden" in b.dataset) {
        await this.onHiddenNetwork();
      } else if ("wifiRadio" in b.dataset) {
        const enable = !this.wifi.radio;
        await this.wifiAction(enable ? "Включаю Wi‑Fi…" : "Выключаю Wi‑Fi…", () => api.post("/wifi/radio", { enabled: enable }));
      } else if (b.dataset.power) {
        await this.onPower(b.dataset.power);
      }
    } catch (err) {
      toast(err.message, "error");
    }
  }
}
