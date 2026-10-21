// Экранные настройки киоска: экран, Wi‑Fi, сеть, система.

import { api } from "/lib/api.js";
import { languages, locale, t, tn } from "/lib/i18n.js";
import { icon, signalBars } from "/lib/icons.js";
import { choose, confirmDialog, confirmWifiOff, toast } from "/lib/dialog.js";
import { openKeyboard } from "/lib/keyboard.js";
import { formatTime, sunTimes } from "/lib/theme.js";

// Подписи в константах ниже — исходные (русские) строки, t() переводит их
// при отрисовке.

// «0.1.1 · a1b2c3d»: номер версии и коммит сборки (его пишет установщик).
// Пользователю показываем только номер версии.
const versionText = (info) => info.version || "—";
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
const NET_STATES = {
  connected: "Подключено",
  disconnected: "Не подключено",
  unavailable: "Кабель не подключён",
  unmanaged: "Управляет система",
  connecting: "Подключаюсь…",
};
const NET_JOBS = {
  applying: "Применяю и проверяю связь…",
  ok: "Настройки применены",
  rolled_back: "Связь не появилась, вернул прежние настройки",
  failed: "Не удалось применить",
};
const NET_MIGRATE = {
  running: "Переношу настройки Ethernet…",
  done: "Ethernet теперь настраивается из панели",
  rolled_back: "Связь через Ethernet не поднялась, вернул прежние настройки",
  failed: "Не удалось передать Ethernet панели",
};

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
  if (min < 1) return t("только что");
  if (min < 60) return t("{n} мин назад", { n: min });
  const d = new Date(seconds * 1000);
  return d.toLocaleString(locale(), { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
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
    this.netDevices = null;
    this.netDraft = {}; // несохранённые правки IPv4 по интерфейсу
    this.netTimer = null;
    this.wifiScanning = false;
    this.net = null;
    this.external = null;
    this.externalBusy = false;
    this.speed = null;
    this.speedTimer = null;
    this.update = null;
    this.updateTimer = null;
    // Режим первого запуска: экран выбора Wi‑Fi с кнопкой «Пропустить».
    this.setup = false;

    this.renderFrame();
  }

  // Каркас окна. Вызывается снова после загрузки языка (app.js).
  renderFrame() {
    const root = this.root;
    root.innerHTML = `
      <div class="settings-nav">
        <div class="settings-heading">${t("Настройки")}</div>
        ${SECTIONS.map((s) => `<button class="nav-item" data-section="${s.id}">${icon(s.icon)}<span>${t(s.title)}</span></button>`).join("")}
      </div>
      <div class="settings-body">
        <button class="settings-close" aria-label="${t("Закрыть")}">${icon("close")}</button>
        <div class="settings-content"></div>
      </div>`;
    this.content = root.querySelector(".settings-content");
    this.content.addEventListener("click", (e) => this.onClick(e));
    root.querySelector(".settings-close").addEventListener("click", () => this.close());
    root.querySelectorAll(".nav-item").forEach((b) =>
      b.addEventListener("click", () => this.show(b.dataset.section)),
    );
  }

  open(section) {
    this.root.hidden = false;
    this.show(section || this.section);
  }

  close() {
    this.root.hidden = true;
    clearInterval(this.wifiTimer);
    clearTimeout(this.speedTimer);
    clearTimeout(this.updateTimer);
    clearTimeout(this.netTimer);
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
    clearTimeout(this.netTimer);
    this.root.querySelectorAll(".nav-item").forEach((b) =>
      b.classList.toggle("active", b.dataset.section === section),
    );
    if (section === "wifi") {
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
      [{ theme_mode: "manual", theme: "light" }, icon("sun") + t("Светлая"), ui.theme_mode === "manual" && ui.theme === "light"],
      [{ theme_mode: "manual", theme: "dark" }, icon("moon") + t("Тёмная"), ui.theme_mode === "manual" && ui.theme === "dark"],
      [{ theme_mode: "schedule" }, t("По времени"), ui.theme_mode === "schedule"],
      [{ theme_mode: "sun" }, t("По солнцу"), ui.theme_mode === "sun"],
    ];
    // Неактуальные пункты не показываем. Границы дня и ночи нужны теме «по
    // времени», а при ручной теме — только для ночного приглушения.
    const scheduleRows = `
          <div class="row"><div class="row-label">${t("День с")}</div>${timeStepper("day_start")}</div>
          <div class="row"><div class="row-label">${t("Ночь с")}</div>${timeStepper("night_start")}</div>`;
    const nightDimHint = ui.theme_mode === "sun" ? t("От заката до восхода") : ui.theme_mode === "schedule" ? t("По времени дня и ночи") : "";
    let sunRow = "";
    if (ui.theme_mode === "sun") {
      const sun = sunTimes(new Date(), ui.latitude, ui.longitude);
      const text = sun.polar
        ? t(sun.polar === "day" ? "Полярный день" : "Полярная ночь")
        : t("Восход {sunrise} · закат {sunset}", { sunrise: formatTime(sun.sunrise), sunset: formatTime(sun.sunset) });
      sunRow = `
        <div class="row">
          <div class="row-label">${t("Сегодня")}<div class="row-hint">${t("Координаты {lat}, {lon} задаются в админке", { lat: ui.latitude.toFixed(2), lon: ui.longitude.toFixed(2) })}</div></div>
          <div class="row-value">${text}</div>
        </div>`;
    }
    return `
      <h2>${t("Экран")}</h2>
      ${languages().length > 1 ? `
      <div class="card">
        <div class="row">
          <div class="row-label">${t("Язык")}</div>
          ${seg("language", languages().map((l) => [l.code, l.name]))}
        </div>
      </div>` : ""}
      <div class="card">
        <div class="row">
          <div class="row-label">${t("Тема")}</div>
          <div class="segmented">${themeChoice
            .map(([patch, label, on]) => `<button data-patch='${JSON.stringify(patch)}' aria-pressed="${on}">${label}</button>`)
            .join("")}</div>
        </div>
        ${sunRow}
        ${ui.theme_mode === "schedule" ? scheduleRows : ""}
      </div>
      <div class="card">
        <div class="row">
          <div class="row-label">${t("Приглушать ночью")}${nightDimHint ? `<div class="row-hint">${nightDimHint}</div>` : ""}</div>
          ${seg("night_dim", NIGHT_DIM.map((l) => [l, l ? `${l}%` : t("Нет")]))}
        </div>
        ${ui.theme_mode === "manual" && ui.night_dim > 0 ? scheduleRows : ""}
      </div>
      <div class="card">
        <div class="row">
          <div class="row-label">${t("Затемнять при простое")}</div>
          ${seg("dim_after_min", DIM_AFTER.map((m) => [m, m ? t("{n} мин", { n: m }) : t("Нет")]))}
        </div>
        ${ui.dim_after_min ? `
        <div class="row">
          <div class="row-label">${t("Сила затемнения")}</div>
          ${seg("dim_level", DIM_LEVEL.map((l) => [l, `${l}%`]))}
        </div>` : ""}
        <div class="row">
          <div class="row-label">${t("Гасить экран")}<div class="row-hint">${t("Выключать подсветку при долгом простое")}</div></div>
          ${seg("blank_after_min", BLANK_AFTER.map((m) => [m, m ? t("{n} мин", { n: m }) : t("Нет")]))}
        </div>
      </div>`;
  }

  // --- Wi‑Fi ------------------------------------------------------------------

  async loadWifi(rescan) {
    // Пока идёт поиск, показываем прошлое состояние: иначе переключатель
    // Wi‑Fi на эти секунды выглядел бы выключенным.
    if (rescan) {
      this.wifiScanning = true;
      if (this.section === "wifi" && !this.root.hidden && !this.wifiBusy) this.render();
    }
    try {
      this.wifi = await api.get("/wifi" + (rescan ? "?rescan=1" : ""));
    } catch (e) {
      this.wifi = { error: e.message };
    }
    if (rescan) this.wifiScanning = false;
    if (this.section === "wifi" && !this.root.hidden && !this.wifiBusy) this.render();
  }

  renderWifi() {
    const w = this.wifi;
    let list;
    if (!w || (this.wifiScanning && !(w.networks || []).length)) list = `<div class="empty">${t("Ищу сети…")}</div>`;
    else if (w.error) list = `<div class="empty">${esc(w.error)}</div>`;
    else if (!w.available) list = `<div class="empty">${t("Wi‑Fi адаптер не найден")}</div>`;
    else if (!w.radio) list = `<div class="empty">${t("Wi‑Fi выключен")}</div>`;
    else if (!w.networks.length) list = `<div class="empty">${t("Сети не найдены")}</div>`;
    else {
      list = `<div class="card list">${w.networks
        .map((n, i) => `
          <button class="net ${n.active ? "net-active" : ""}" data-net="${i}">
            ${signalBars(n.signal)}
            <span class="net-name">${esc(n.ssid)}</span>
            <span class="net-meta">${n.active ? t("Подключено") : n.saved ? t("Сохранена") : ""}</span>
            ${n.secure ? icon("lock", "icon net-lock") : ""}
          </button>`)
        .join("")}</div>`;
    }
    const busy = this.wifiBusy ? `<div class="busy">${esc(this.wifiBusy)}</div>` : "";
    const setup = this.setup ? `
      <div class="setup">
        <div>
          <div class="setup-title">${t("Добро пожаловать!")}</div>
          <div class="setup-text">${t("Выберите сеть Wi‑Fi, к которой подключить панель. Это можно сделать и позже в настройках.")}</div>
        </div>
        <div class="segmented">${languages().map((l) => `<button data-set="language" data-value='${JSON.stringify(l.code)}' aria-pressed="${this.config.ui.language === l.code}">${esc(l.name)}</button>`).join("")}</div>
        <button class="btn" data-setup-skip>${t("Пропустить")}</button>
      </div>` : "";
    return `
      ${setup}
      <h2>Wi‑Fi</h2>
      <div class="card">
        <div class="row">
          <div class="row-label">Wi‑Fi${w && w.connection ? `<div class="row-hint">${t("Подключено к {ssid}", { ssid: esc(w.connection) })}</div>` : ""}</div>
          ${w && !w.error
            ? `<button class="switch" data-wifi-radio aria-checked="${!!w.radio}" ${w.available ? "" : "disabled"}></button>`
            : `<span class="row-value">…</span>`}
        </div>
      </div>
      ${busy}
      <div class="list-head">
        <span>${t("Доступные сети")}${this.wifiScanning && w && (w.networks || []).length ? ` · ${t("ищу…")}` : ""}</span>
        <span class="list-actions">
          <button class="btn btn-small" data-wifi-hidden>${t("Скрытая сеть")}</button>
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
        text: t("Сеть подключена"),
        options: [
          { label: t("Отмена"), value: null },
          { label: t("Забыть"), value: "forget", kind: "danger" },
          { label: t("Отключиться"), value: "disconnect", kind: "primary" },
        ],
      });
      if (action === "disconnect") await this.wifiAction(t("Отключаюсь…"), () => api.post("/wifi/disconnect"));
      if (action === "forget") await this.wifiAction(t("Удаляю сеть…"), () => api.post("/wifi/forget", { ssid: net.ssid }));
      return;
    }
    if (net.saved) {
      const action = await choose({
        title: net.ssid,
        text: t("Сеть сохранена"),
        options: [
          { label: t("Отмена"), value: null },
          { label: t("Забыть"), value: "forget", kind: "danger" },
          { label: t("Подключиться"), value: "connect", kind: "primary" },
        ],
      });
      if (action === "forget") await this.wifiAction(t("Удаляю сеть…"), () => api.post("/wifi/forget", { ssid: net.ssid }));
      if (action !== "connect") return;
      const ok = await this.wifiAction(t("Подключаюсь к {ssid}…", { ssid: net.ssid }), () => api.post("/wifi/connect", { ssid: net.ssid }));
      if (ok) this.connected(net.ssid);
      return;
    }
    let password = "";
    if (net.secure) {
      password = await openKeyboard({ title: t("Пароль для {ssid}", { ssid: net.ssid }), password: true, minLength: 8 });
      if (password === null) return;
    }
    const ok = await this.wifiAction(t("Подключаюсь к {ssid}…", { ssid: net.ssid }), () => api.post("/wifi/connect", { ssid: net.ssid, password }));
    if (ok) this.connected(net.ssid);
  }

  connected(ssid) {
    toast(t("Подключено к {ssid}", { ssid }));
    // Бэкенд сам отмечает первый запуск пройденным; окно настроек закрываем.
    if (this.setup) {
      this.setup = false;
      this.close();
    }
  }

  async onHiddenNetwork() {
    const ssid = await openKeyboard({ title: t("Имя сети"), minLength: 1 });
    if (!ssid) return;
    const password = await openKeyboard({ title: t("Пароль для {ssid} (пусто — без пароля)", { ssid }), password: true });
    if (password === null) return;
    const ok = await this.wifiAction(t("Подключаюсь к {ssid}…", { ssid }), () => api.post("/wifi/connect", { ssid, password, hidden: true }));
    if (ok) this.connected(ssid);
  }

  // --- Сеть --------------------------------------------------------------------

  async loadNetwork() {
    const [net, devices, speed] = await Promise.allSettled([api.get("/network"), api.get("/network/devices"), api.get("/speedtest")]);
    this.net = net.value || null;
    this.netDevices = devices.value || null;
    this.speed = speed.value || null;
    if (this.section === "network" && !this.root.hidden) this.render();
    this.pollDevices();
    if (this.speed && this.speed.running) this.pollSpeed();
    if (!this.external) await this.loadExternal(false);
  }

  // Пока настройки применяются, раз в 2 с обновляем статус интерфейсов.
  pollDevices() {
    clearTimeout(this.netTimer);
    const d = this.netDevices;
    const busy = d && (d.devices.some((x) => x.job?.state === "applying") || d.migrate?.state === "running");
    if (!busy) return;
    this.netTimer = setTimeout(async () => {
      if (this.section !== "network" || this.root.hidden) return;
      try {
        this.netDevices = await api.get("/network/devices");
        this.net = await api.get("/network");
      } catch {
        // Связь с ядром могла моргнуть при смене адреса: попробуем ещё.
      }
      if (this.section !== "network" || this.root.hidden) return;
      this.render();
      this.pollDevices();
    }, 2000);
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
    return `
      <h2>${t("Сеть")}</h2>
      ${this.netDevices ? this.renderDevices() : this.renderInterfaces()}
      <div class="list-head"><span>${t("Внешний адрес")}</span>
        <div class="list-actions">
          <button class="btn btn-small" data-external-refresh ${this.externalBusy ? "disabled" : ""}>${icon("refresh")}${t("Перепроверить")}</button>
        </div>
      </div>
      ${this.renderExternal()}
      <div class="list-head"><span>${t("Скорость интернета")}</span></div>
      ${this.renderSpeed()}`;
  }

  // Только просмотр: если NetworkManager недоступен и настраивать нечего.
  renderInterfaces() {
    const net = this.net;
    if (!net) return `<div class="empty">${t("Загрузка…")}</div>`;
    const ifaces = net.interfaces.map((i) => {
      const kind = IFACE_TYPES[i.type] || i.name;
      const label = i.connection ? `${kind} · ${esc(i.connection)}` : kind;
      const value = i.addresses.length ? i.addresses.map(esc).join("<br>") : t("не подключено");
      return `<div class="row"><div class="row-label">${label}<div class="row-hint">${esc(i.name)}</div></div>
        <div class="row-value ${i.addresses.length ? "mono" : ""}">${value}</div></div>`;
    }).join("");
    return `<div class="card">${ifaces}
      <div class="row"><div class="row-label">${t("Шлюз")}</div><div class="row-value mono">${esc(net.gateway || "—")}</div></div>
      <div class="row"><div class="row-label">DNS</div><div class="row-value mono">${net.dns.length ? net.dns.map(esc).join("<br>") : "—"}</div></div>
    </div>`;
  }

  // Ethernet и Wi‑Fi: DHCP или адрес вручную, перенос Ethernet под панель.
  renderDevices() {
    const data = this.netDevices;
    const order = { ethernet: 0, wifi: 1 };
    const list = data.devices.filter((d) => d.type in order).sort((a, b) => order[a.type] - order[b.type]);
    if (!list.length) return `<div class="empty">${t("Сетевые адаптеры не найдены")}</div>`;
    const m = data.migrate;
    // «Готово» видно и так — Ethernet в списке как управляемый.
    const migrate = m && NET_MIGRATE[m.state] && m.state !== "done"
      ? `<div class="busy ${m.state === "failed" || m.state === "rolled_back" ? "net-error" : ""}">${esc(t(NET_MIGRATE[m.state]))}${m.state === "failed" && m.message ? `: ${esc(t(m.message, m.message_args))}` : ""}</div>` : "";
    return migrate + list.map((d) => this.renderDevice(d, m)).join("");
  }

  renderDevice(d, migrate) {
    const cur = d.current || {};
    const kind = IFACE_TYPES[d.type];
    const title = d.type === "wifi" && d.connection ? `${kind} · ${esc(d.connection)}` : kind;
    const hint = [d.device, t(NET_STATES[d.state] || d.state), cur.gateway && t("шлюз {ip}", { ip: cur.gateway }), cur.dns?.length && `DNS ${cur.dns.join(", ")}`]
      .filter(Boolean).map(esc).join(" · ");
    const head = `<div class="row"><div class="row-label">${title}<div class="row-hint">${hint}</div></div>
      <div class="row-value ${cur.addresses?.length ? "mono" : ""}">${cur.addresses?.length ? cur.addresses.map(esc).join("<br>") : "—"}</div></div>`;
    if (!d.managed) {
      return `<div class="card">${head}
        <div class="row"><div class="row-label">${t("Сейчас интерфейсом управляет система (netplan)")}<div class="row-hint">${t("Панель перенесёт настройки к себе и проверит связь. Если связь пропадёт, всё вернётся как было.")}</div></div>
          <button class="btn btn-small btn-primary" data-net-manage="${esc(d.device)}" ${migrate?.state === "running" ? "disabled" : ""}>${t("Управлять из панели")}</button></div>
      </div>`;
    }
    // Настройки IP у Wi‑Fi живут в профиле сети: без подключения менять нечего.
    if (d.type === "wifi" && !d.connection) {
      return `<div class="card">${head}<div class="row"><div class="row-label"><div class="row-hint">${t("Подключитесь к сети Wi‑Fi, чтобы настроить её адрес")}</div></div></div></div>`;
    }
    const saved = this.savedIpv4(d);
    const draft = this.netDraft[d.device];
    const v = draft || saved;
    const manual = v.method === "manual";
    const applying = d.job?.state === "applying";
    const dev = esc(d.device);
    const seg = `<div class="segmented">${[["auto", t("DHCP")], ["manual", t("Вручную")]].map(([value, label]) =>
      `<button data-net-method="${value}" data-dev="${dev}" aria-pressed="${v.method === value}">${label}</button>`).join("")}</div>`;
    const field = (key, label, hint) => `<div class="row"><div class="row-label">${label}${hint ? `<div class="row-hint">${hint}</div>` : ""}</div>
      <div class="speed-cell"><span class="row-value mono">${esc(v[key] || "—")}</span>
        <button class="btn btn-small" data-net-edit="${key}" data-dev="${dev}">${t("Изменить")}</button></div></div>`;
    const rows = [
      `<div class="row"><div class="row-label">${t("Настройка IP")}</div>${seg}</div>`,
      manual ? field("address", t("Адрес и маска"), t("Например, 192.168.1.50/24")) : "",
      manual ? field("gateway", t("Шлюз")) : "",
      field("dns", "DNS", manual ? t("Через пробел") : t("Пусто — от DHCP")),
    ];
    const job = d.job && NET_JOBS[d.job.state]
      ? `<div class="row-hint ${d.job.state === "ok" ? "" : d.job.state === "applying" ? "speed-phase" : "speed-error"}">${esc(t(NET_JOBS[d.job.state]))}${d.job.message ? `: ${esc(d.job.message)}` : ""}</div>` : "";
    let buttons = "";
    if (draft) {
      buttons = `<button class="btn btn-small" data-net-cancel="${dev}">${t("Отменить")}</button>
        <button class="btn btn-small btn-primary" data-net-apply="${dev}" ${applying ? "disabled" : ""}>${t("Применить")}</button>`;
    } else if (saved.method === "manual") {
      buttons = `<button class="btn btn-small" data-net-dhcp="${dev}" ${applying ? "disabled" : ""}>${t("Вернуть DHCP")}</button>`;
    }
    const actions = job || buttons
      ? `<div class="row"><div class="row-label">${job}</div><div class="list-actions">${buttons}</div></div>` : "";
    return `<div class="card">${head}${rows.join("")}${actions}</div>`;
  }

  // Сохранённые настройки IPv4 в виде черновика: dns — строкой через пробел.
  savedIpv4(d) {
    const s = d.ipv4 || { method: "auto" };
    return { method: s.method === "manual" ? "manual" : "auto", address: s.address || "", gateway: s.gateway || "", dns: (s.dns || []).join(" ") };
  }

  netDevice(name) {
    return this.netDevices.devices.find((d) => d.device === name);
  }

  editDraft(name, patch) {
    const d = this.netDevice(name);
    const draft = { ...(this.netDraft[d.device] || this.savedIpv4(d)), ...patch };
    // Переход на «вручную»: подставляем текущие адрес, шлюз и DNS.
    if (patch.method === "manual") {
      const cur = d.current || {};
      draft.address ||= (cur.addresses || [])[0] || "";
      draft.gateway ||= cur.gateway || "";
      draft.dns ||= (cur.dns || []).join(" ");
    }
    const saved = this.savedIpv4(d);
    const same = Object.keys(saved).every((k) => saved[k] === draft[k]);
    if (same) delete this.netDraft[name];
    else this.netDraft[name] = draft;
    this.render();
  }

  async onNetEdit(name, key) {
    const v = this.netDraft[name] || this.savedIpv4(this.netDevice(name));
    const titles = { address: t("Адрес и маска, например 192.168.1.50/24"), gateway: t("Шлюз"), dns: t("DNS через пробел") };
    const value = await openKeyboard({ title: titles[key], value: v[key] || "", layout: "sym" });
    if (value === null) return;
    this.editDraft(name, { [key]: value.trim().replace(/\s+/g, " ") });
  }

  async applyIpv4(name, v) {
    await api.post("/network/ipv4", { device: name, method: v.method, address: v.address, gateway: v.gateway, dns: v.dns });
    delete this.netDraft[name];
    toast(t("Применяю и проверяю связь…"));
    await this.loadNetwork();
  }

  async onNetDhcp(name) {
    const ok = await confirmDialog({
      title: t("Вернуть DHCP для {name}?", { name }),
      text: t("Адрес, шлюз и DNS снова будет выдавать роутер. Адрес панели может смениться."),
      ok: t("Вернуть DHCP"),
    });
    if (ok) await this.applyIpv4(name, { method: "auto", address: "", gateway: "", dns: "" });
  }

  async onNetManage(name) {
    const ok = await confirmDialog({
      title: t("Передать {name} панели?", { name }),
      text: t("Связь через кабель может прерваться на полминуты. Если она не восстановится, настройки вернутся сами."),
      ok: t("Передать"),
    });
    if (!ok) return;
    await api.post("/network/manage", { device: name });
    this.netDevices.migrate = { state: "running" };
    this.render();
    this.pollDevices();
  }

  renderExternal() {
    const ext = this.external;
    if (!ext) return `<div class="empty">${t(this.externalBusy ? "Спрашиваю сервисы…" : "Нет данных")}</div>`;
    const failed = ext.failed.length ? `<div class="row-hint">${t("Не ответили: {services}", { services: ext.failed.map((s) => esc(t(s))).join(", ") })}</div>` : "";
    if (!ext.addresses.length) {
      return `<div class="card"><div class="row"><div class="row-label">${t("Ни один сервис не ответил")}${failed}</div></div></div>`;
    }
    if (ext.addresses.length === 1) {
      const a = ext.addresses[0];
      return `<div class="card"><div class="row">
        <div class="row-label">${t("Одинаковый у всех сервисов")}<div class="row-hint">${a.services.map((s) => esc(t(s))).join(", ")}</div>${failed}</div>
        <div class="row-value mono big">${esc(a.ip)}</div>
      </div></div>`;
    }
    // Адреса разошлись: показываем, какой сервис что увидел.
    return `<div class="card">
      <div class="row"><div class="row-label ext-warn">${t("Сервисы видят разные адреса")}${failed}</div></div>
      ${ext.addresses.map((a) => `<div class="row">
        <div class="row-label">${a.services.map((s) => esc(t(s))).join(", ")}</div>
        <div class="row-value mono big">${esc(a.ip)}</div>
      </div>`).join("")}
    </div>`;
  }

  renderSpeed() {
    const sp = this.speed || { running: null, results: {} };
    return `<div class="card">${SPEED_PROVIDERS.map(([id, title]) => {
      const r = sp.results[id];
      const running = sp.running === id;
      let hint = t("Ещё не запускался");
      let value = "";
      if (running) hint = `<span class="speed-phase">${esc(t(sp.phase || "Идёт тест…"))}</span>`;
      else if (r && r.error) hint = `<span class="speed-error">${esc(t(r.error, r.error_args))}</span> · ${ago(r.at)}`;
      else if (r) {
        hint = [r.server && esc(r.server), ago(r.at)].filter(Boolean).join(" · ");
        value = `<div class="speed">
          <span>${icon("down")}<b>${mbps(r.download)}</b></span>
          <span>${icon("up")}<b>${mbps(r.upload)}</b></span>
          <small>${t("Мбит/с")}${r.ping != null ? ` · ${t("{n} мс", { n: Math.round(r.ping) })}` : ""}</small>
        </div>`;
      }
      return `<div class="row">
        <div class="row-label">${t(title)}<div class="row-hint">${hint}</div></div>
        <div class="speed-cell">${value}
          <button class="btn btn-small" data-speedtest="${id}" ${sp.running ? "disabled" : ""}>${t(running ? "Идёт…" : "Запустить")}</button>
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
    const confirms = {
      install: { title: t("Обновить панель?"), text: t("Проверю новую версию и, если она есть, поставлю. Экран перезагрузится сам."), ok: t("Обновить") },
      rollback: { title: t("Вернуть прошлую версию?"), text: t("Экран перезагрузится сам."), ok: t("Вернуть") },
    };
    if (confirms[mode] && !(await confirmDialog(confirms[mode]))) return;
    await api.post("/update/run", { mode });
    this.update.status = { ...this.update.status, state: mode === "check" ? "checking" : "installing", message: "Запускаю…", message_args: {} };
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
        .map(([value, label]) => `<button data-upd-set="${key}" data-value='${JSON.stringify(value)}' aria-pressed="${set[key] === value}">${t(label)}</button>`)
        .join("")}</div>`;
    const newVersion = latest.version || (latest.commit || "").slice(0, 7);
    const banner = st.state === "installing"
      ? `<div class="update-banner">${icon("refresh")}<div><div class="update-banner-title">${t("Устанавливаю обновление…")}</div>
          <div class="update-banner-text">${t("Экран перезагрузится сам, когда новая версия запустится")}</div></div></div>`
      : u.available
        ? `<div class="update-banner">${icon("refresh")}<div>
            <div class="update-banner-title">${t("Доступна новая версия {version}", { version: esc(newVersion) })}</div>
</div>
            <button class="btn" data-update-run="install">${t("Обновить")}</button></div>`
        : "";
    const checked = st.checked_at ? ` · ${t("проверено {ago}", { ago: ago(Date.parse(st.checked_at) / 1000) })}` : "";
    const failed = st.result === "failed" && !busy;
    return `
      ${banner}
      <div class="card">
        <div class="row">
          <div class="row-label">${t("Версия")}
            <div class="row-hint ${failed ? "speed-error" : ""}">${esc(t(st.message || "Ещё не проверялось", st.message_args))}${failed ? "" : checked}</div>
          </div>
          <div class="speed-cell">
            <div class="row-value mono">${esc(versionText(this.info || {}))}</div>
            <button class="btn btn-small" data-update-run="check" ${busy ? "disabled" : ""}>${icon("refresh")}${t(st.state === "checking" ? "Проверяю…" : "Проверить")}</button>
          </div>
        </div>
        <div class="row">
          <div class="row-label">${t("Обновление вручную")}<div class="row-hint">${t("Скачать и поставить последнюю версию канала прямо сейчас")}</div></div>
          <div class="list-actions">
            <button class="btn btn-small" data-update-run="rollback" ${busy ? "disabled" : ""}>${t("Вернуть прошлую")}</button>
            <button class="btn btn-small btn-primary" data-update-run="install" ${busy ? "disabled" : ""}>${t(st.state === "installing" ? "Обновляю…" : "Обновить")}</button>
          </div>
        </div>
        <div class="row">
          <div class="row-label">${t("Канал")}<div class="row-hint">${t("Разработка — свежие, но менее проверенные версии")}</div></div>
          ${seg("channel", UPDATE_CHANNELS)}
        </div>
        <div class="row">
          <div class="row-label">${t("Проверять обновления")}</div>
          ${seg("schedule", UPDATE_SCHEDULES)}
        </div>
        ${set.schedule === "daily" ? `
        <div class="row">
          <div class="row-label">${t("Время проверки")}</div>
          <div class="stepper">
            <button class="btn" data-upd-time="-30">−</button>
            <span class="stepper-value">${esc(set.time)}</span>
            <button class="btn" data-upd-time="30">+</button>
          </div>
        </div>` : ""}
        <div class="row ${set.schedule === "off" ? "row-disabled" : ""}">
          <div class="row-label">${t("Ставить автоматически")}<div class="row-hint">${t("Иначе только показывать, что вышла новая версия")}</div></div>
          <button class="switch" data-upd-auto aria-checked="${set.auto}"></button>
        </div>
      </div>`;
  }

  renderSystem() {
    const info = this.info || {};
    const admin = this.net && this.net.admin;
    const passwordSet = this.config.admin.password_set;
    return `
      <h2>${t("Система")}</h2>
      ${this.renderUpdate()}
      <div class="card">
        <div class="row"><div class="row-label">${t("Имя устройства")}</div><div class="row-value">${esc(info.hostname || "—")}</div></div>
        ${this.update ? "" : `<div class="row"><div class="row-label">${t("Версия")}</div><div class="row-value">${esc(versionText(info))}</div></div>`}
      </div>
      <div class="card">
        <div class="row">
          <div class="row-label">${icon("admin")} ${t("Админка")}
            ${admin ? `<div class="admin-url mono">${esc(admin.url)}</div>` : ""}
            <div class="row-hint">${admin
              ? t("Откройте адрес или наведите камеру телефона, подключённого к той же сети")
              : t("Появится после подключения к сети")}</div>
            ${admin && !passwordSet ? `<div class="row-hint">${t("Пароль ещё не задан: его попросят придумать при первом входе")}</div>` : ""}
          </div>
          ${admin && admin.qr ? `<div class="qr">${admin.qr}</div>` : ""}
        </div>
      </div>
      <div class="power">
        <button class="btn power-btn" data-power="reboot">${icon("reboot")}${t("Перезагрузить")}</button>
        <button class="btn btn-danger power-btn" data-power="poweroff">${icon("power")}${t("Выключить")}</button>
      </div>`;
  }

  async onPower(action) {
    const reboot = action === "reboot";
    const ok = await confirmDialog({
      title: t(reboot ? "Перезагрузить панель?" : "Выключить панель?"),
      text: t(reboot ? "Панель будет недоступна около минуты." : "Включить её снова можно будет только кнопкой или по питанию."),
      ok: t(reboot ? "Перезагрузить" : "Выключить"),
      danger: !reboot,
    });
    if (!ok) return;
    try {
      await api.post("/power", { action });
      toast(t(reboot ? "Перезагружаюсь…" : "Выключаюсь…"));
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
        await this.loadWifi(true);
      } else if ("setupSkip" in b.dataset) {
        this.close();
      } else if ("wifiHidden" in b.dataset) {
        await this.onHiddenNetwork();
      } else if ("wifiRadio" in b.dataset) {
        const enable = !this.wifi.radio;
        if (!enable && !(await confirmWifiOff(this.wifi))) return;
        await this.wifiAction(t(enable ? "Включаю Wi‑Fi…" : "Выключаю Wi‑Fi…"), () => api.post("/wifi/radio", { enabled: enable }));
      } else if (b.dataset.updSet) {
        await this.saveUpdate({ [b.dataset.updSet]: JSON.parse(b.dataset.value) });
      } else if (b.dataset.updTime) {
        await this.saveUpdate({ time: shiftTime(this.update.settings.time, Number(b.dataset.updTime)) });
      } else if ("updAuto" in b.dataset) {
        await this.saveUpdate({ auto: !this.update.settings.auto });
      } else if (b.dataset.updateRun) {
        await this.runUpdate(b.dataset.updateRun);
      } else if (b.dataset.netMethod) {
        this.editDraft(b.dataset.dev, { method: b.dataset.netMethod });
      } else if (b.dataset.netEdit) {
        await this.onNetEdit(b.dataset.dev, b.dataset.netEdit);
      } else if (b.dataset.netCancel) {
        delete this.netDraft[b.dataset.netCancel];
        this.render();
      } else if (b.dataset.netApply) {
        await this.applyIpv4(b.dataset.netApply, this.netDraft[b.dataset.netApply]);
      } else if (b.dataset.netDhcp) {
        await this.onNetDhcp(b.dataset.netDhcp);
      } else if (b.dataset.netManage) {
        await this.onNetManage(b.dataset.netManage);
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
