// Экранные настройки киоска: экран, Wi‑Fi, сеть, система.

import { api } from "/lib/api.js";
import { icon, signalBars } from "/lib/icons.js";
import { choose, confirmDialog, toast } from "/lib/dialog.js";
import { openKeyboard } from "/lib/keyboard.js";
import { formatTime, sunTimes } from "/lib/theme.js";

// «0.1.1 · a1b2c3d»: номер версии и коммит сборки (его пишет установщик).
const versionText = (info) => [info.version, info.build && info.build.commit && info.build.commit.slice(0, 7)].filter(Boolean).join(" · ") || "—";
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const SECTIONS = [
  { id: "display", title: "Экран", icon: "display" },
  { id: "wifi", title: "Wi‑Fi", icon: "wifi" },
  { id: "network", title: "Сеть", icon: "net" },
  { id: "system", title: "Система", icon: "info" },
];

const DIM_AFTER = [0, 1, 5, 15, 30];
const DIM_LEVEL = [50, 70, 85, 95];
const NIGHT_DIM = [0, 20, 40, 60];
const BLANK_AFTER = [0, 10, 30, 60];

const SPEED_PROVIDERS = [
  ["ookla", "Speedtest"],
  ["yandex", "Яндекс Интернетометр"],
];
const IFACE_TYPES = { wifi: "Wi‑Fi", ethernet: "Ethernet" };

const UPDATE_CHANNELS = [
  ["stable", "Стабильная"],
  ["dev", "Разработка"],
];
const UPDATE_SCHEDULES = [
  ["off", "Не проверять"],
  ["hourly", "Каждый час"],
  ["6h", "Каждые 6 ч"],
  ["daily", "Раз в день"],
];

function ago(seconds) {
  const min = Math.round((Date.now() / 1000 - seconds) / 60);
  if (min < 1) return "только что";
  if (min < 60) return `${min} мин назад`;
  const d = new Date(seconds * 1000);
  return d.toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

const mbps = (v) => (v == null ? "—" : v >= 100 ? Math.round(v) : v.toFixed(1));

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
    this.net = null;
    this.external = null;
    this.externalBusy = false;
    this.speed = null;
    this.speedTimer = null;
    this.update = null;
    this.updateTimer = null;
    // Режим первого запуска: экран выбора Wi‑Fi с кнопкой «Пропустить».
    this.setup = false;

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
    clearTimeout(this.speedTimer);
    clearTimeout(this.updateTimer);
    // Закрытие окна первого запуска равносильно «Пропустить».
    if (this.setup) this.finishSetup();
  }

  openSetup() {
    this.setup = true;
    this.section = "wifi";
    this.open();
  }

  async finishSetup() {
    this.setup = false;
    try {
      await api.post("/setup/wifi-done");
    } catch {
      // Не критично: окно просто покажется снова при следующем запуске.
    }
    if (!this.root.hidden && this.section === "wifi") this.render();
  }

  onConfig(config) {
    this.config = config;
    if (!this.root.hidden && !["wifi", "network"].includes(this.section)) this.render();
  }

  show(section) {
    this.section = section;
    clearInterval(this.wifiTimer);
    clearTimeout(this.speedTimer);
    clearTimeout(this.updateTimer);
    this.root.querySelectorAll(".nav-item").forEach((b) =>
      b.classList.toggle("active", b.dataset.section === section),
    );
    if (section === "wifi") {
      this.wifi = null;
      this.loadWifi(true);
      this.wifiTimer = setInterval(() => this.loadWifi(false), 10000);
    }
    if (section === "network") this.loadNetwork();
    if (section === "system") this.loadInfo();
    this.render();
  }

  render() {
    const body = {
      display: () => this.renderDisplay(),
      wifi: () => this.renderWifi(),
      network: () => this.renderNetwork(),
      system: () => this.renderSystem(),
    };
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
    const themeChoice = [
      [{ theme_mode: "manual", theme: "light" }, icon("sun") + "Светлая", ui.theme_mode === "manual" && ui.theme === "light"],
      [{ theme_mode: "manual", theme: "dark" }, icon("moon") + "Тёмная", ui.theme_mode === "manual" && ui.theme === "dark"],
      [{ theme_mode: "schedule" }, "По времени", ui.theme_mode === "schedule"],
      [{ theme_mode: "sun" }, "По солнцу", ui.theme_mode === "sun"],
    ];
    // Границы ночи нужны и для темы, и для ночного приглушения.
    const showSchedule = ui.theme_mode === "schedule" || (ui.theme_mode === "manual" && ui.night_dim > 0);
    let sunRow = "";
    if (ui.theme_mode === "sun") {
      const sun = sunTimes(new Date(), ui.latitude, ui.longitude);
      const text = sun.polar
        ? (sun.polar === "day" ? "Полярный день" : "Полярная ночь")
        : `Восход ${formatTime(sun.sunrise)} · закат ${formatTime(sun.sunset)}`;
      sunRow = `
        <div class="row">
          <div class="row-label">Сегодня<div class="row-hint">Координаты ${ui.latitude.toFixed(2)}, ${ui.longitude.toFixed(2)} задаются в админке</div></div>
          <div class="row-value">${text}</div>
        </div>`;
    }
    return `
      <h2>Экран</h2>
      <div class="card">
        <div class="row">
          <div class="row-label">Тема</div>
          <div class="segmented">${themeChoice
            .map(([patch, label, on]) => `<button data-patch='${JSON.stringify(patch)}' aria-pressed="${on}">${label}</button>`)
            .join("")}</div>
        </div>
        ${sunRow}
        ${showSchedule ? `
          <div class="row"><div class="row-label">День с</div>${timeStepper("day_start")}</div>
          <div class="row"><div class="row-label">Ночь с</div>${timeStepper("night_start")}</div>` : ""}
      </div>
      <div class="card">
        <div class="row">
          <div class="row-label">Приглушать ночью<div class="row-hint">${ui.theme_mode === "sun" ? "От заката до восхода" : "По времени дня и ночи"}</div></div>
          ${seg("night_dim", NIGHT_DIM.map((l) => [l, l ? `${l}%` : "Нет"]))}
        </div>
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
        <div class="row">
          <div class="row-label">Гасить экран<div class="row-hint">Выключать подсветку при долгом простое</div></div>
          ${seg("blank_after_min", BLANK_AFTER.map((m) => [m, m ? `${m} мин` : "Нет"]))}
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
    const setup = this.setup ? `
      <div class="setup">
        <div>
          <div class="setup-title">Добро пожаловать!</div>
          <div class="setup-text">Выберите сеть Wi‑Fi, к которой подключить панель. Это можно сделать и позже в настройках.</div>
        </div>
        <button class="btn" data-setup-skip>Пропустить</button>
      </div>` : "";
    return `
      ${setup}
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
      if (ok) this.connected(net.ssid);
      return;
    }
    let password = "";
    if (net.secure) {
      password = await openKeyboard({ title: `Пароль для ${net.ssid}`, password: true, minLength: 8 });
      if (password === null) return;
    }
    const ok = await this.wifiAction(`Подключаюсь к ${net.ssid}…`, () => api.post("/wifi/connect", { ssid: net.ssid, password }));
    if (ok) this.connected(net.ssid);
  }

  connected(ssid) {
    toast(`Подключено к ${ssid}`);
    // Бэкенд сам отмечает первый запуск пройденным; окно настроек закрываем.
    if (this.setup) {
      this.setup = false;
      this.close();
    }
  }

  async onHiddenNetwork() {
    const ssid = await openKeyboard({ title: "Имя сети", minLength: 1 });
    if (!ssid) return;
    const password = await openKeyboard({ title: `Пароль для ${ssid} (пусто — без пароля)`, password: true });
    if (password === null) return;
    const ok = await this.wifiAction(`Подключаюсь к ${ssid}…`, () => api.post("/wifi/connect", { ssid, password, hidden: true }));
    if (ok) this.connected(ssid);
  }

  // --- Сеть --------------------------------------------------------------------

  async loadNetwork() {
    const [net, speed] = await Promise.allSettled([api.get("/network"), api.get("/speedtest")]);
    this.net = net.value || null;
    this.speed = speed.value || null;
    if (this.section === "network" && !this.root.hidden) this.render();
    if (this.speed && this.speed.running) this.pollSpeed();
    if (!this.external) await this.loadExternal(false);
  }

  async loadExternal(refresh) {
    this.externalBusy = true;
    if (this.section === "network") this.render();
    try {
      this.external = await api.get(`/network/external${refresh ? "?refresh=1" : ""}`);
    } catch (e) {
      toast(e.message, "error");
    }
    this.externalBusy = false;
    if (this.section === "network" && !this.root.hidden) this.render();
  }

  pollSpeed() {
    clearTimeout(this.speedTimer);
    this.speedTimer = setTimeout(async () => {
      try {
        this.speed = await api.get("/speedtest");
      } catch {
        // Попробуем ещё раз на следующем шаге.
      }
      if (this.section !== "network" || this.root.hidden) return;
      this.render();
      if (!this.speed || this.speed.running) this.pollSpeed();
    }, 1000);
  }

  async runSpeedtest(provider) {
    this.speed = await api.post("/speedtest", { provider });
    this.render();
    this.pollSpeed();
  }

  renderNetwork() {
    const net = this.net;
    const ifaces = net
      ? net.interfaces.map((i) => {
          const kind = IFACE_TYPES[i.type] || i.name;
          const label = i.connection ? `${kind} · ${esc(i.connection)}` : kind;
          const value = i.addresses.length ? i.addresses.map(esc).join("<br>") : "не подключено";
          return `<div class="row"><div class="row-label">${label}<div class="row-hint">${esc(i.name)}</div></div>
            <div class="row-value ${i.addresses.length ? "mono" : ""}">${value}</div></div>`;
        }).join("")
      : `<div class="row"><div class="row-label">Загрузка…</div></div>`;
    const extra = net ? `
        <div class="row"><div class="row-label">Шлюз</div><div class="row-value mono">${esc(net.gateway || "—")}</div></div>
        <div class="row"><div class="row-label">DNS</div><div class="row-value mono">${net.dns.length ? net.dns.map(esc).join("<br>") : "—"}</div></div>` : "";

    return `
      <h2>Сеть</h2>
      <div class="card">${ifaces}${extra}</div>
      <div class="list-head"><span>Внешний адрес</span>
        <div class="list-actions">
          <button class="btn btn-small" data-external-refresh ${this.externalBusy ? "disabled" : ""}>${icon("refresh")}Обновить</button>
        </div>
      </div>
      ${this.renderExternal()}
      <div class="list-head"><span>Скорость интернета</span></div>
      ${this.renderSpeed()}`;
  }

  renderExternal() {
    const ext = this.external;
    if (!ext) return `<div class="empty">${this.externalBusy ? "Спрашиваю сервисы…" : "Нет данных"}</div>`;
    const failed = ext.failed.length ? `<div class="row-hint">Не ответили: ${ext.failed.map(esc).join(", ")}</div>` : "";
    if (!ext.addresses.length) {
      return `<div class="card"><div class="row"><div class="row-label">Ни один сервис не ответил${failed}</div></div></div>`;
    }
    if (ext.addresses.length === 1) {
      const a = ext.addresses[0];
      return `<div class="card"><div class="row">
        <div class="row-label">Одинаковый у всех сервисов<div class="row-hint">${a.services.map(esc).join(", ")}</div>${failed}</div>
        <div class="row-value mono big">${esc(a.ip)}</div>
      </div></div>`;
    }
    // Адреса разошлись: показываем, какой сервис что увидел.
    return `<div class="card">
      <div class="row"><div class="row-label ext-warn">Сервисы видят разные адреса${failed}</div></div>
      ${ext.addresses.map((a) => `<div class="row">
        <div class="row-label">${a.services.map(esc).join(", ")}</div>
        <div class="row-value mono big">${esc(a.ip)}</div>
      </div>`).join("")}
    </div>`;
  }

  renderSpeed() {
    const sp = this.speed || { running: null, results: {} };
    return `<div class="card">${SPEED_PROVIDERS.map(([id, title]) => {
      const r = sp.results[id];
      const running = sp.running === id;
      let hint = "Ещё не запускался";
      let value = "";
      if (running) hint = `<span class="speed-phase">${esc(sp.phase || "Идёт тест…")}</span>`;
      else if (r && r.error) hint = `<span class="speed-error">${esc(r.error)}</span> · ${ago(r.at)}`;
      else if (r) {
        hint = [r.server && esc(r.server), ago(r.at)].filter(Boolean).join(" · ");
        value = `<div class="speed">
          <span>${icon("down")}<b>${mbps(r.download)}</b></span>
          <span>${icon("up")}<b>${mbps(r.upload)}</b></span>
          <small>Мбит/с${r.ping != null ? ` · ${Math.round(r.ping)} мс` : ""}</small>
        </div>`;
      }
      return `<div class="row">
        <div class="row-label">${title}<div class="row-hint">${hint}</div></div>
        <div class="speed-cell">${value}
          <button class="btn btn-small" data-speedtest="${id}" ${sp.running ? "disabled" : ""}>${running ? "Идёт…" : "Запустить"}</button>
        </div>
      </div>`;
    }).join("")}</div>`;
  }

  // --- Система ----------------------------------------------------------------

  // Значок новой версии на пункте «Система» (на шестерёнке его ставит app.js).
  setUpdateBadge(on) {
    this.root.querySelector('.nav-item[data-section="system"]').classList.toggle("badge", Boolean(on));
  }

  async loadInfo() {
    const [info, net] = await Promise.allSettled([api.get("/info"), api.get("/network")]);
    this.info = info.value || null;
    this.net = net.value || this.net;
    await this.loadUpdate();
    if (this.section === "system" && !this.root.hidden) this.render();
  }

  // --- обновления (часть раздела «Система») ------------------------------------

  async loadUpdate() {
    clearTimeout(this.updateTimer);
    try {
      this.update = await api.get("/update");
    } catch {
      // Во время установки сервис перезапускается: просто ждём.
    }
    const st = this.update && this.update.status;
    const busy = st && ["checking", "installing"].includes(st.state);
    if (this.section !== "system" || this.root.hidden) return;
    this.render();
    if (busy) this.updateTimer = setTimeout(() => this.loadUpdate(), 2000);
  }

  async saveUpdate(patch) {
    await api.post("/update/settings", patch);
    await this.loadUpdate();
  }

  async runUpdate(mode) {
    if (mode === "install") {
      const ok = await confirmDialog({
        title: "Обновить панель?",
        text: "Экран перезагрузится сам, когда новая версия запустится.",
        ok: "Обновить",
      });
      if (!ok) return;
    }
    await api.post("/update/run", { mode });
    this.update.status = { ...this.update.status, state: mode === "check" ? "checking" : "installing", message: "Запускаю…" };
    this.render();
    this.updateTimer = setTimeout(() => this.loadUpdate(), 1000);
  }

  renderUpdate() {
    const u = this.update;
    if (!u) return "";
    const st = u.status || {};
    const set = u.settings;
    const busy = ["checking", "installing"].includes(st.state);
    const latest = st.latest || {};
    const seg = (key, items) =>
      `<div class="segmented">${items
        .map(([value, label]) => `<button data-upd-set="${key}" data-value='${JSON.stringify(value)}' aria-pressed="${set[key] === value}">${label}</button>`)
        .join("")}</div>`;
    const newVersion = [latest.version, latest.commit && latest.commit.slice(0, 7)].filter(Boolean).join(" · ");
    const banner = st.state === "installing"
      ? `<div class="update-banner">${icon("refresh")}<div><div class="update-banner-title">Устанавливаю обновление…</div>
          <div class="update-banner-text">Экран перезагрузится сам, когда новая версия запустится</div></div></div>`
      : u.available
        ? `<div class="update-banner">${icon("refresh")}<div>
            <div class="update-banner-title">Доступна новая версия ${esc(newVersion)}</div>
            <div class="update-banner-text">${esc(latest.title || "")}</div></div>
            <button class="btn" data-update-run="install">Обновить</button></div>`
        : "";
    const checked = st.checked_at ? ` · проверено ${ago(Date.parse(st.checked_at) / 1000)}` : "";
    const failed = st.result === "failed" && !busy;
    return `
      ${banner}
      <div class="card">
        <div class="row">
          <div class="row-label">Версия
            <div class="row-hint ${failed ? "speed-error" : ""}">${esc(st.message || "Ещё не проверялось")}${failed ? "" : checked}</div>
          </div>
          <div class="speed-cell">
            <div class="row-value mono">${esc(versionText(this.info || {}))}</div>
            <button class="btn btn-small" data-update-run="check" ${busy ? "disabled" : ""}>${icon("refresh")}${st.state === "checking" ? "Проверяю…" : "Проверить"}</button>
          </div>
        </div>
        <div class="row">
          <div class="row-label">Канал<div class="row-hint">Разработка — свежие, но менее проверенные версии</div></div>
          ${seg("channel", UPDATE_CHANNELS)}
        </div>
        <div class="row">
          <div class="row-label">Проверять обновления</div>
          ${seg("schedule", UPDATE_SCHEDULES)}
        </div>
        ${set.schedule === "daily" ? `
        <div class="row">
          <div class="row-label">Время проверки</div>
          <div class="stepper">
            <button class="btn" data-upd-time="-30">−</button>
            <span class="stepper-value">${esc(set.time)}</span>
            <button class="btn" data-upd-time="30">+</button>
          </div>
        </div>` : ""}
        <div class="row ${set.schedule === "off" ? "row-disabled" : ""}">
          <div class="row-label">Ставить автоматически<div class="row-hint">Иначе только показывать, что вышла новая версия</div></div>
          <button class="switch" data-upd-auto aria-checked="${set.auto}"></button>
        </div>
      </div>`;
  }

  renderSystem() {
    const info = this.info || {};
    const admin = this.net && this.net.admin;
    const initial = this.config.admin.initial_password;
    return `
      <h2>Система</h2>
      ${this.renderUpdate()}
      <div class="card">
        <div class="row"><div class="row-label">Имя устройства</div><div class="row-value">${esc(info.hostname || "—")}</div></div>
        ${this.update ? "" : `<div class="row"><div class="row-label">Версия</div><div class="row-value">${esc(versionText(info))}</div></div>`}
      </div>
      <div class="card">
        <div class="row">
          <div class="row-label">${icon("admin")} Админка
            <div class="row-hint">${admin
              ? "Наведите камеру телефона, подключённого к той же сети"
              : "Появится после подключения к сети"}</div>
          </div>
          ${admin && admin.qr ? `<div class="qr">${admin.qr}</div>` : ""}
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
      if (b.dataset.patch) {
        await this.save(JSON.parse(b.dataset.patch));
      } else if (b.dataset.set) {
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
      } else if ("setupSkip" in b.dataset) {
        this.close();
      } else if ("wifiHidden" in b.dataset) {
        await this.onHiddenNetwork();
      } else if ("wifiRadio" in b.dataset) {
        const enable = !this.wifi.radio;
        await this.wifiAction(enable ? "Включаю Wi‑Fi…" : "Выключаю Wi‑Fi…", () => api.post("/wifi/radio", { enabled: enable }));
      } else if (b.dataset.updSet) {
        await this.saveUpdate({ [b.dataset.updSet]: JSON.parse(b.dataset.value) });
      } else if (b.dataset.updTime) {
        await this.saveUpdate({ time: shiftTime(this.update.settings.time, Number(b.dataset.updTime)) });
      } else if ("updAuto" in b.dataset) {
        await this.saveUpdate({ auto: !this.update.settings.auto });
      } else if (b.dataset.updateRun) {
        await this.runUpdate(b.dataset.updateRun);
      } else if ("externalRefresh" in b.dataset) {
        await this.loadExternal(true);
      } else if (b.dataset.speedtest) {
        await this.runSpeedtest(b.dataset.speedtest);
      } else if (b.dataset.power) {
        await this.onPower(b.dataset.power);
      }
    } catch (err) {
      toast(err.message, "error");
    }
  }
}
