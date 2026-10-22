// Сетевая админка: всё, что есть на экране, плюс модули, статусная строка,
// часовой пояс и пароль. Подписи в константах — исходные строки, t()
// переводит их при отрисовке.

import { api } from "/lib/api.js";
import { language, languages, locale, setLanguage, t, translateDom } from "/lib/i18n.js";
import { icon, signalBars } from "/lib/icons.js";
import { confirmDialog, confirmWifiOff, toast } from "/lib/dialog.js";
import { loadCatalog } from "/lib/widgets.js";
import { BackgroundPanel } from "/admin/background.js";
import { DesktopEditor } from "/admin/desktops.js";
import { StorePanel } from "/admin/store.js";
import { ScreenView } from "/admin/screen.js";
import { NetworkPanel } from "/admin/network.js";
import { WeatherPanel } from "/admin/weather.js";

const $ = (id) => document.getElementById(id);
// «0.1.1 · a1b2c3d»: номер версии и коммит сборки (его пишет установщик).
// Пользователю показываем только номер версии.
const versionText = (info) => info.version || "—";
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

// group — заголовок группы в боковом меню, sub — подзаголовок страницы.
const TABS = [
  { id: "display", title: "Экран", icon: "display", group: "Панель", sub: "Тема, затемнение, местоположение и фон" },
  { id: "desktops", title: "Столы", icon: "grid", group: "Панель", sub: "Виджеты на рабочих столах панели" },
  { id: "screen", title: "Трансляция", icon: "eye", group: "Панель", sub: "Что сейчас на экране панели" },
  { id: "modules", title: "Модули", icon: "plus", group: "Расширения", sub: "Дополнительные виджеты из каталогов" },
  { id: "weather", title: "Погода", icon: "sun", group: "Расширения", sub: "Источники прогноза и место для виджетов погоды" },
  { id: "wifi", title: "Сеть", icon: "wifi", group: "Устройство", sub: "Wi‑Fi, Ethernet и IP-адреса" },
  { id: "updates", title: "Обновления", icon: "refresh", group: "Устройство", sub: "Версия панели и автоматические обновления" },
  { id: "system", title: "Система", icon: "info", group: "Устройство", sub: "Часовой пояс, пароль и питание" },
];

const state = { tab: "display", config: null, catalog: null, wifi: null, time: null, info: null, update: null };

// Админка показывает ту же тему, что сейчас на экране панели. Пока панель
// о ней не сообщила (киоск не запущен), следуем теме компьютера.
// Язык общий с панелью: если его сменили на экране, перезагружаемся.
const media = matchMedia("(prefers-color-scheme: light)");
let panelTheme = null;
const syncTheme = () =>
  (document.documentElement.dataset.theme = panelTheme || (media.matches ? "light" : "dark"));
media.addEventListener("change", syncTheme);
syncTheme();
async function pollPanel() {
  try {
    const panel = await api.get("/theme");
    panelTheme = panel.theme;
    syncTheme();
    if (languageLoaded && panel.language !== language()) location.reload();
  } catch {}
}
pollPanel();
setInterval(pollPanel, 5000);

// --- вход ------------------------------------------------------------------

// Пока пароль не задан, форма входа превращается в форму создания пароля.
let setupMode = false;
let languageLoaded = false;

async function boot() {
  const session = await api.get("/session");
  if (!languageLoaded) {
    await setLanguage(session.language);
    languageLoaded = true;
    translateDom();
    renderTabs();
  }
  if (!session.authorized) {
    setupMode = session.setup;
    $("login-hint").textContent = setupMode
      ? t("Первый вход: придумайте пароль админки, не короче 6 символов.")
      : t("Введите пароль админки. Забыли — сбросьте его на панели: sudo ./install.sh --reset-password.");
    $("login-password").placeholder = t(setupMode ? "Новый пароль" : "Пароль");
    $("login-password").autocomplete = setupMode ? "new-password" : "current-password";
    $("login-password").minLength = setupMode ? 6 : 0;
    $("login-repeat").hidden = !setupMode;
    $("login-repeat").required = setupMode;
    $("login-submit").textContent = t(setupMode ? "Задать пароль и войти" : "Войти");
    $("login").hidden = false;
    $("app").hidden = true;
    $("login-password").focus();
    return;
  }
  $("login").hidden = true;
  $("app").hidden = false;
  await reload();
}

$("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("login-error").textContent = "";
  try {
    const password = $("login-password").value;
    if (setupMode) {
      if (password !== $("login-repeat").value) throw new Error(t("Пароли не совпадают"));
      await api.post("/setup/password", { new: password });
    } else {
      await api.post("/login", { password });
    }
    $("login-password").value = "";
    $("login-repeat").value = "";
    await boot();
  } catch (err) {
    $("login-error").textContent = err.message;
  }
});

$("logout").addEventListener("click", async () => {
  await api.post("/logout");
  boot();
});

// Любой 401 возвращает на экран входа (например, после перезапуска сервиса).
async function guarded(fn) {
  try {
    return await fn();
  } catch (err) {
    if (err.status === 401) boot();
    else toast(err.message, "error");
    throw err;
  }
}

// --- навигация -------------------------------------------------------------

function renderTabs() {
  // Разделы без группы (например, добавленные модулями) идут в конце.
  $("tabs").innerHTML = TABS.map((tab, i) => {
    const head = tab.group && tab.group !== (TABS[i - 1] || {}).group ? `<div class="tab-group">${esc(t(tab.group))}</div>` : "";
    return `${head}<button class="tab" data-tab="${tab.id}">${icon(tab.icon)}<span>${t(tab.title)}</span></button>`;
  }).join("");
  const logoutIcon = $("logout-icon");
  if (logoutIcon) logoutIcon.outerHTML = icon("back");
}
$("tabs").addEventListener("click", (e) => {
  const b = e.target.closest(".tab");
  if (b) show(b.dataset.tab);
});

async function reload() {
  const [config, catalog, info] = await guarded(() => Promise.all([api.get("/config"), loadCatalog(), api.get("/info").catch(() => ({}))]));
  state.config = config;
  state.catalog = catalog;
  setInfo(info);
  show(state.tab);
}

// Экран панели (разрешение, масштаб, сенсор) нужен редактору столов,
// трансляции и обрезке фонов: пропорции экрана — в CSS-переменной.
function setInfo(info) {
  state.info = info;
  $("brand-version").textContent = info.version || "";
  const d = info.display;
  if (d?.width && d?.height) document.documentElement.style.setProperty("--screen-ratio", `${d.width} / ${d.height}`);
}

function show(tab) {
  state.tab = tab;
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  // На телефоне вкладки листаются по горизонтали: выбранная должна быть видна.
  document.querySelector(".tab.active")?.scrollIntoView({ block: "nearest", inline: "nearest" });
  if (tab === "wifi") {
    loadWifi(false);
    network.load();
  } else network.stop();
  if (tab === "system") loadSystem();
  if (tab === "updates") loadUpdate();
  if (tab === "modules") store.load().catch(() => {});
  if (tab === "weather") weather.load().catch(() => {});
  if (tab === "display" && !backgrounds.items) backgrounds.load().then(() => state.tab === "display" && render()).catch(() => {});
  if (tab === "display") loadLocation();
  render();
}

function render() {
  const views = { display: renderDisplay, desktops: renderDesktops, modules: () => store.render(), weather: () => weather.render(), screen: () => screenView.render(), wifi: renderWifi, updates: renderUpdates, system: renderSystem };
  editor?.destroy();
  editor = null;
  screenView.destroy();
  store.destroy();
  $("content").classList.toggle("wide", state.tab === "desktops");
  $("content").innerHTML = views[state.tab]();
  // Подзаголовок страницы под первым заголовком.
  const tab = TABS.find((x) => x.id === state.tab);
  const head = $("content").firstElementChild;
  if (tab && tab.sub && head && head.tagName === "H1" && !head.nextElementSibling?.classList.contains("page-sub")) head.insertAdjacentHTML("afterend", `<p class="page-sub">${esc(t(tab.sub))}</p>`);
  if (state.tab === "desktops") mountEditor();
  if (state.tab === "screen") screenView.mount();
  if (state.tab === "modules") store.mount();
}

async function saveUi(patch) {
  await guarded(() => api.post("/settings", { ui: patch }));
  state.config = await api.get("/config");
  if (state.config.ui.language !== language()) return location.reload();
  render();
}

// --- экран ----------------------------------------------------------------------

function renderScale(ui, opts) {
  const d = state.info?.display;
  if (!d) return "";
  const values = ["0.75", "1", "1.25", "1.5", "1.75", "2", "2.5", "3"].map((v) => [v, `×${v}`]);
  return `
    <section class="panel">
      <label class="field"><span>${t("Масштаб интерфейса")}<small>${t("Экран {w}×{h}, интерфейс {vw}×{vh}. После смены экран перезапустится", { w: d.width, h: d.height, vw: d.view.width, vh: d.view.height })}</small></span>
        <select data-ui="scale">${opts([["auto", t("Авто (×{scale})", { scale: d.scale_auto })], ...values], ui.scale)}</select>
      </label>
    </section>`;
}

function renderDisplay() {
  const ui = state.config.ui;
  const opts = (values, current) => values.map(([v, l]) => `<option value="${v}" ${v === current ? "selected" : ""}>${esc(l)}</option>`).join("");
  // Неактуальные пункты скрываем, как на экране панели.
  const showSchedule = ui.theme_mode === "schedule" || (ui.theme_mode === "manual" && ui.night_dim > 0);
  return `
    <h1>${t("Экран")}</h1>
    <section class="panel">
      <label class="field"><span>${t("Имя панели")}<small>${t("Видно в шторке. Пусто — имя устройства ({host})", { host: state.info?.hostname || "—" })}</small></span>
        <input data-ui="panel_name" maxlength="40" value="${esc(ui.panel_name || "")}" placeholder="${esc(state.info?.hostname || "")}">
      </label>
    </section>
    ${languages().length > 1 ? `
    <section class="panel">
      <label class="field"><span>${t("Язык")}<small>${t("Общий для панели и админки")}</small></span>
        <select data-ui="language">${opts(languages().map((l) => [l.code, l.name]), ui.language)}</select>
      </label>
    </section>` : ""}
    ${renderScale(ui, opts)}
    <section class="panel">
      <label class="field"><span>${t("Выбор темы")}</span>
        <select data-ui="theme_mode">${opts([["manual", t("Вручную")], ["schedule", t("По времени")], ["sun", t("По солнцу")]], ui.theme_mode)}</select>
      </label>
      ${ui.theme_mode === "manual" ? `
      <label class="field"><span>${t("Тема")}</span>
        <select data-ui="theme">${opts([["light", t("Светлая")], ["dark", t("Тёмная")]], ui.theme)}</select>
      </label>` : ""}
      ${showSchedule ? `
      <label class="field"><span>${t("День с")}<small>${t(ui.theme_mode === "schedule" ? "С этого времени — светлая тема" : "Для ночного приглушения")}</small></span>
        <input type="time" data-ui="day_start" value="${ui.day_start}">
      </label>
      <label class="field"><span>${t("Ночь с")}</span><input type="time" data-ui="night_start" value="${ui.night_start}"></label>` : ""}
    </section>
    ${renderLocation()}
    <section class="panel">
      <label class="field"><span>${t("Ночное приглушение, %")}<small>${t("0 — не приглушать")}</small></span>
        <input type="number" min="0" max="90" step="5" data-ui="night_dim" data-int value="${ui.night_dim}">
      </label>
      <label class="field"><span>${t("Затемнять через, мин")}<small>${t("0 — не затемнять")}</small></span>
        <input type="number" min="0" max="240" data-ui="dim_after_min" data-int value="${ui.dim_after_min}">
      </label>
      ${ui.dim_after_min ? `
      <label class="field"><span>${t("Сила затемнения, %")}</span>
        <input type="number" min="0" max="95" step="5" data-ui="dim_level" data-int value="${ui.dim_level}">
      </label>` : ""}
      <label class="field"><span>${t("Гасить экран через, мин")}<small>${t("0 — не гасить")}</small></span>
        <input type="number" min="0" max="240" data-ui="blank_after_min" data-int value="${ui.blank_after_min}">
      </label>
    </section>
    ${renderSound(ui)}
    ${backgrounds.render(ui)}`;
}

// Звуковые уведомления: играет сама панель, здесь только настройки.
const SOUND_NAMES = [["chime", "Перезвон"], ["bell", "Колокольчик"], ["soft", "Мягкий"], ["beep", "Сигнал"]];

function renderSound(ui) {
  const off = ui.sound_enabled ? "" : "disabled";
  const quiet = ui.sound_enabled && ui.sound_quiet ? "" : "disabled";
  const names = SOUND_NAMES.map(([v, l]) => `<option value="${v}" ${v === ui.sound_name ? "selected" : ""}>${esc(t(l))}</option>`).join("");
  return `
    <h1>${t("Звук")}</h1>
    <section class="panel">
      <label class="field"><span>${t("Звуковые уведомления")}<small>${t("Панель подаёт звук, например когда вышло обновление")}</small></span>
        <input type="checkbox" data-ui="sound_enabled" ${ui.sound_enabled ? "checked" : ""}>
      </label>
      <label class="field"><span>${t("Громкость, %")}</span>
        <input type="number" min="0" max="100" step="10" data-ui="sound_volume" data-int value="${ui.sound_volume}" ${off}>
      </label>
      <div class="field"><span>${t("Звук")}<small>${t("«Проверить» играет на панели, даже когда звук выключен")}</small></span>
        <div class="field-controls">
          <select data-ui="sound_name" ${off}>${names}</select>
          <button class="btn btn-small" type="button" data-sound-test>${t("Проверить")}</button>
        </div>
      </div>
      <label class="field"><span>${t("Об обновлениях")}<small>${t("Вышла новая версия, обновление закончилось")}</small></span>
        <input type="checkbox" data-ui="sound_update" ${ui.sound_update ? "checked" : ""} ${off}>
      </label>
      ${ui.sound_enabled ? renderSourceSounds(ui) : ""}
      <label class="field"><span>${t("Тихие часы")}<small>${t("В это время панель молчит")}</small></span>
        <input type="checkbox" data-ui="sound_quiet" ${ui.sound_quiet ? "checked" : ""} ${off}>
      </label>
      <label class="field"><span>${t("Тихо с")}</span><input type="time" data-ui="sound_quiet_from" value="${ui.sound_quiet_from}" ${quiet}></label>
      <label class="field"><span>${t("Тихо до")}</span><input type="time" data-ui="sound_quiet_to" value="${ui.sound_quiet_to}" ${quiet}></label>
    </section>`;
}

// Свой звук уведомлений у системы и у каждого модуля из магазина.
function renderSourceSounds(ui) {
  const sources = [["system", t("Система")], ...(state.catalog?.modules || [])
    .filter((m) => m.source !== "builtin").map((m) => [m.id, t(m.title)])];
  const choices = [["", t("Общий звук")], ...SOUND_NAMES.map(([v, l]) => [v, t(l)]), ["off", t("Без звука")]];
  return `<div class="field"><span>${t("Звук по источнику")}<small>${t("Модулю можно дать свой звук или выключить его уведомления")}</small></span></div>
    ${sources.map(([id, title]) => {
      const cur = ui.sound_sources?.[id] || "";
      return `<div class="field field-sub"><span>${esc(title)}</span>
        <div class="field-controls">
          <select data-source-sound="${esc(id)}">${choices.map(([v, l]) => `<option value="${v}" ${v === cur ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>
          <button class="btn btn-small" type="button" data-sound-test-source="${esc(id)}" ${cur === "off" ? "disabled" : ""}>${t("Проверить")}</button>
        </div></div>`;
    }).join("")}`;
}

const screenView = new ScreenView();

const weather = new WeatherPanel({ guarded, toast, render: () => state.tab === "weather" && render() });

const network = new NetworkPanel({ guarded, toast, confirm: confirmDialog, render: () => state.tab === "wifi" && render(), active: () => state.tab === "wifi" });

const store = new StorePanel({
  guarded,
  toast,
  confirm: confirmDialog,
  render: () => state.tab === "modules" && render(),
  modules: () => state.catalog?.modules || [],
  // Набор виджетов поменялся: каталог для редактора столов перечитываем.
  changed: async () => {
    state.config = await api.get("/config");
    state.catalog = await loadCatalog();
  },
});

const backgrounds = new BackgroundPanel({
  saveUi: (patch) => saveUi(patch),
  guarded,
  toast,
  confirm: confirmDialog,
  render: () => state.tab === "display" && render(),
});

// Местоположение панели: тема «по солнцу» и виджеты с местом «как у панели».
function locationStatus(loc) {
  const last = loc && loc.last;
  if (!last || !last.at) return "";
  const time = new Date(last.at * 1000).toLocaleTimeString(locale(), { hour: "2-digit", minute: "2-digit" });
  if (!last.source) return t("Не удалось определить ({time}): {error}", { time, error: last.error });
  const text = last.source === "yandex"
    ? (last.accuracy ? t("Яндекс Локатор, {time}, точность {m} м", { time, m: Math.round(last.accuracy) }) : t("Яндекс Локатор, {time}", { time }))
    : t("По IP, {time}", { time });
  return last.error ? `${text}. ${t("Локатор не ответил, взято место по IP")}` : text;
}

function renderLocation() {
  const ui = state.config.ui;
  const loc = state.location;
  const ps = state.placeSearch;
  const opts = [["ip", t("Автоматически по IP")], ["yandex", t("Яндекс Локатор (по Wi‑Fi)")], ["manual", t("Вручную")]]
    .map(([v, l]) => `<option value="${v}" ${v === ui.location_mode ? "selected" : ""}>${esc(l)}</option>`).join("");
  const where = ui.location_name || t("Без названия");
  return `
    <section class="panel">
      <label class="field"><span>${t("Местоположение")}<small>${t("Для темы «по солнцу» и виджетов погоды с местом «как у панели»")}</small></span>
        <select data-ui="location_mode">${opts}</select>
      </label>
      <div class="field"><span>${esc(where)}<small>${esc(`${ui.latitude.toFixed(4)}, ${ui.longitude.toFixed(4)}`)}${loc && locationStatus(loc) ? ` · ${esc(locationStatus(loc))}` : ""}</small></span>
        ${ui.location_mode === "manual" ? "" : `<button class="btn" data-locate>${icon("refresh")} ${t("Определить сейчас")}</button>`}
      </div>
      ${ui.location_mode === "yandex" ? `
        <form class="inline-form panel-inset" id="locator-key-form">
          <input name="key" type="password" autocomplete="off"
            placeholder="${t(state.config.location && state.config.location.yandex_key_set ? "Ключ сохранён · вставьте новый, чтобы заменить" : "Ключ API Яндекс Локатора")}">
          <button class="btn btn-primary" type="submit">${t("Сохранить")}</button>
        </form>
        <p class="muted small panel-inset">${t("Ключ выдаётся в кабинете разработчика Яндекса (developer.tech.yandex.ru), сервис «Локатор». Без ключа или при ошибке место берётся по IP.")}</p>` : ""}
      ${ui.location_mode === "manual" ? `
        <form class="inline-form panel-inset" id="place-form">
          <input name="q" placeholder="${t("Найти город")}" value="${esc(ps ? ps.query : "")}" autocomplete="off">
          <button class="btn" type="submit">${t("Найти")}</button>
        </form>
        ${ps && ps.results ? (ps.results.length ? `<div class="place-results panel-inset">${ps.results.map((r, i) => `
          <button class="place-result" data-place-pick="${i}">${esc(r.name)}<small>${esc(r.region)}</small></button>`).join("")}</div>`
          : `<p class="muted small panel-inset">${t("Ничего не нашлось")}</p>`) : ""}
        <label class="field"><span>${t("Название")}</span>
          <input data-ui="location_name" maxlength="80" value="${esc(ui.location_name)}">
        </label>
        <label class="field"><span>${t("Широта")}</span>
          <input type="number" step="0.0001" min="-90" max="90" data-ui="latitude" data-float value="${ui.latitude}">
        </label>
        <label class="field"><span>${t("Долгота")}</span>
          <input type="number" step="0.0001" min="-180" max="180" data-ui="longitude" data-float value="${ui.longitude}">
        </label>` : ""}
    </section>`;
}

async function loadLocation() {
  try {
    state.location = await guarded(() => api.get("/location"));
  } catch {
    return;
  }
  if (state.tab === "display") render();
}

// --- рабочие столы ------------------------------------------------------------------

// Редактор живёт в /admin/desktops.js и сам управляет своим DOM: здесь только
// место под него и пересоздание при смене вкладки или настроек.
let editor = null;

function renderDesktops() {
  return `<h1>${t("Рабочие столы")}</h1><div id="desk-editor"></div>`;
}

function mountEditor() {
  editor?.destroy();
  editor = new DesktopEditor($("desk-editor"), {
    config: state.config,
    screen: state.info?.display?.view,
    catalog: state.catalog,
    save: async (desktops, { homeAfterMin, carousel }) => {
      const res = await guarded(() => api.post("/admin/desktops", { desktops, home_after_min: homeAfterMin, carousel }));
      state.config.desktops = res.desktops;
      state.config.layout.home_after_min = homeAfterMin;
      state.config.layout.carousel = res.carousel;
      return res.desktops;
    },
    toast,
    confirm: confirmDialog,
  });
}

// --- Wi‑Fi --------------------------------------------------------------------------

async function loadWifi(rescan) {
  state.wifi = null;
  render();
  try {
    state.wifi = await guarded(() => api.get("/wifi" + (rescan ? "?rescan=1" : "")));
  } catch (err) {
    state.wifi = { error: err.message };
  }
  if (state.tab === "wifi") render();
}

function renderWifi() {
  const w = state.wifi;
  let body;
  if (!w) body = `<p class="muted">${t("Ищу сети…")}</p>`;
  else if (w.error) body = `<p class="error">${esc(w.error)}</p>`;
  else if (!w.available) body = `<p class="muted">${t("Wi‑Fi адаптер не найден")}</p>`;
  else {
    body = `
      <section class="panel">
        <label class="field"><span>Wi‑Fi${w.connection ? `<small>${t("Подключено к {ssid}", { ssid: esc(w.connection) })}</small>` : ""}</span>
          <button class="switch" data-wifi-radio aria-checked="${w.radio}"></button>
        </label>
      </section>
      <section class="panel list">
        ${w.networks.map((n, i) => `
          <div class="item"><div class="item-main">
            ${signalBars(n.signal)}
            <div class="item-title grow">${esc(n.ssid)} ${n.secure ? icon("lock", "icon tiny") : ""}
              <div class="muted small">${n.active ? t("Подключено") : n.saved ? t("Сохранена") : esc(n.security || t("Открытая"))}</div>
            </div>
            ${n.active ? `<button class="btn btn-small" data-wifi-disconnect>${t("Отключиться")}</button>` : ""}
            ${!n.active ? `<button class="btn btn-small btn-primary" data-wifi-connect="${i}">${t("Подключиться")}</button>` : ""}
            ${n.saved ? `<button class="btn btn-small" data-wifi-forget="${i}">${t("Забыть")}</button>` : ""}
          </div></div>`).join("") || `<p class="muted pad">${t("Сети не найдены")}</p>`}
      </section>
      <form class="panel inline-form" id="hidden-form">
        <input name="ssid" placeholder="${t("Скрытая сеть: имя")}" required>
        <input name="password" type="password" placeholder="${t("Пароль")}">
        <button class="btn" type="submit">${t("Подключиться")}</button>
      </form>
      ${renderKnownWifi(w)}`;
  }
  return `<h1>${t("Сеть")}</h1><h1>Wi‑Fi <button class="btn btn-small" data-wifi-rescan>${icon("refresh")} ${t("Искать сети")}</button></h1>${body}${network.renderWifiIpv4()}${network.renderEthernet()}`;
}

// Известные (сохранённые) сети, в том числе те, что сейчас не видны.
function renderKnownWifi(w) {
  if (!(w.saved || []).length) return "";
  const visible = new Set(w.networks.map((n) => n.ssid));
  return `
    <h1>${t("Известные сети")}</h1>
    <section class="panel list">
      ${w.saved.map((name, i) => `
        <div class="item"><div class="item-main">
          <div class="item-title grow">${esc(name)}
            <div class="muted small">${name === w.connection ? t("Подключено") : !w.radio ? "" : visible.has(name) ? t("Рядом") : t("Не в зоне")}</div>
          </div>
          <button class="btn btn-small" data-known-forget="${i}">${t("Забыть")}</button>
        </div></div>`).join("")}
    </section>`;
}

async function wifiConnect(body) {
  toast(t("Подключаюсь к {ssid}…", { ssid: body.ssid }));
  await guarded(() => api.post("/wifi/connect", body));
  toast(t("Подключено к {ssid}", { ssid: body.ssid }));
  await loadWifi(false);
}

// --- система -----------------------------------------------------------------------

async function loadSystem() {
  let info;
  [state.time, info] = await guarded(() => Promise.all([api.get("/time"), api.get("/info")]));
  setInfo(info);
  if (state.tab === "system") render();
}

function displayNote(d) {
  const parts = [d.connector, t("масштаб ×{scale}", { scale: d.scale })];
  if (d.source === "default") parts.push(t("не удалось определить, взято по умолчанию"));
  return parts.filter(Boolean).join(" · ");
}

function renderSystem() {
  const time = state.time;
  const info = state.info || {};
  return `
    <h1>${t("Система")}</h1>
    <section class="panel">
      <div class="field"><span>${t("Устройство")}</span><b>${esc(info.hostname || "—")}</b></div>
      <div class="field"><span>${t("Версия")}</span><b>${esc(versionText(info))}</b></div>
      ${info.display ? `
      <div class="field"><span>${t("Экран")}<small>${esc(displayNote(info.display))}</small></span><b>${info.display.width}×${info.display.height}</b></div>
      <div class="field"><span>${t("Сенсорный экран")}<small>${esc(info.display.touch_devices.join(", ") || t("Управление мышью: колесо листает столы, правая кнопка открывает настройки"))}</small></span><b>${info.display.touch ? t("Есть") : t("Не найден")}</b></div>` : ""}
      <label class="field"><span>${t("Часовой пояс")}</span>
        ${time ? `<select id="tz">${time.timezones.map((z) => `<option ${z === time.timezone ? "selected" : ""}>${esc(z)}</option>`).join("")}</select>` : "…"}
      </label>
    </section>
    <h1>${t("Пароль админки")}</h1>
    <form class="panel inline-form" id="password-form">
      <input name="current" type="password" placeholder="${t("Текущий пароль")}" autocomplete="current-password" required>
      <input name="new" type="password" placeholder="${t("Новый пароль (от 6 символов)")}" autocomplete="new-password" minlength="6" required>
      <button class="btn btn-primary" type="submit">${t("Сменить")}</button>
    </form>
    <h1>${t("Питание")}</h1>
    <section class="panel row-buttons">
      <button class="btn" data-power="reboot">${icon("reboot")} ${t("Перезагрузить")}</button>
      <button class="btn btn-danger" data-power="poweroff">${icon("power")} ${t("Выключить")}</button>
    </section>`;
}

// --- обновления ------------------------------------------------------------------

// Пока обновлятор работает, статус опрашивается каждые 2 секунды.
let updatePoll = null;

async function loadUpdate() {
  clearTimeout(updatePoll);
  try {
    state.update = await api.get("/update");
  } catch (err) {
    // Во время установки сервис перезапускается, это не ошибка.
    if (err.status === 401) return boot();
    if (state.update) state.update.status.state = "installing";
  }
  if (state.tab !== "updates") return;
  const busy = state.update && ["checking", "installing"].includes(state.update.status.state);
  if (busy) updatePoll = setTimeout(loadUpdate, 2000);
  // Пока вкладка открыта, поля ввода не перерисовываются (можно вводить токен).
  if (!document.activeElement || !document.activeElement.closest("#content input")) render();
}

const shortDate = (s) => (s ? new Date(s).toLocaleString(locale(), { dateStyle: "short", timeStyle: "short" }) : "—");
// Только номер версии; без него (старые сборки) — короткий коммит.
const buildText = (b, version) => (b && b.commit ? version || b.version || b.commit.slice(0, 7) : version || "—");

function renderUpdates() {
  const u = state.update;
  if (!u) return `<h1>${t("Обновления")}</h1><div class="empty">${t("Загрузка…")}</div>`;
  const st = u.status || {};
  const busy = ["checking", "installing"].includes(st.state);
  const latest = st.latest;
  const level = busy ? "busy" : st.result === "failed" ? "error" : st.available ? "new" : "";
  return `
    <h1>${t("Обновления")}</h1>
    <section class="panel">
      <div class="field"><span>${t("Установлена")}</span><b class="mono">${esc(buildText(u.build, u.version))}</b></div>
      <div class="field"><span>${t("Последняя в канале")}</span>
        <b class="mono">${esc(buildText(latest))}</b></div>
      <div class="field"><span>${t("Проверено")}</span><b>${esc(shortDate(st.checked_at))}</b></div>
      <div class="update-state ${level}">${esc(st.message ? t(st.message, st.message_args) : "")}</div>
      <div class="row-buttons">
        <button class="btn" data-update-run="check" ${busy ? "disabled" : ""}>${icon("refresh")} ${t("Проверить")}</button>
        <button class="btn btn-primary" data-update-run="install" ${busy ? "disabled" : ""}>${t("Обновить сейчас")}</button>
        <button class="btn" data-update-run="rollback" ${busy ? "disabled" : ""}>${t("Вернуть прошлую версию")}</button>
      </div>
    </section>
    <section class="panel">
      <label class="field"><span>${t("Канал")}<small>${t("Разработка — свежие, но менее проверенные версии")}</small></span>
        <select id="update-channel">
          <option value="stable" ${u.settings.channel === "stable" ? "selected" : ""}>${t("Стабильная")} (${esc(u.settings.branches.stable)})</option>
          <option value="dev" ${u.settings.channel === "dev" ? "selected" : ""}>${t("Разработка")} (${esc(u.settings.branches.dev)})</option>
        </select>
      </label>
      <label class="field"><span>${t("Проверять обновления")}</span>
        <select id="update-schedule">
          ${[["off", "Не проверять"], ["hourly", "Каждый час"], ["6h", "Каждые 6 часов"], ["daily", "Раз в день"]]
            .map(([v, l]) => `<option value="${v}" ${u.settings.schedule === v ? "selected" : ""}>${t(l)}</option>`).join("")}
        </select>
      </label>
      <label class="field"><span>${t("Время проверки")}<small>${t("Для «раз в день»")}</small></span>
        <input type="time" id="update-time" value="${esc(u.settings.time)}" ${u.settings.schedule === "daily" ? "" : "disabled"}>
      </label>
      <label class="field"><span>${t("Ставить автоматически")}<small>${t("Иначе только показывать, что вышла новая версия")}</small></span>
        <input type="checkbox" id="update-auto" ${u.settings.auto ? "checked" : ""}>
      </label>
    </section>
    <h1>${t("Доступ к GitHub")}</h1>
    <form class="panel inline-form" id="token-form">
      <input name="token" type="password" autocomplete="off"
        placeholder="${t(u.token_set ? "Токен сохранён · вставьте новый, чтобы заменить" : "Токен для приватного репозитория")}">
      <button class="btn btn-primary" type="submit">${t("Сохранить")}</button>
      ${u.token_set ? `<button class="btn" type="button" data-token-clear>${t("Удалить")}</button>` : ""}
    </form>`;
}

async function saveUpdateSettings(patch) {
  await guarded(() => api.post("/update/settings", patch));
  await loadUpdate();
}

// --- события ------------------------------------------------------------------------

$("content").addEventListener("change", async (e) => {
  const el = e.target;
  if (state.tab === "display" && (await backgrounds.onChange(e, state.config.ui).catch(() => true))) return;
  if (state.tab === "modules" && (await store.onChange(e).catch(() => true))) return;
  if (state.tab === "weather" && (await weather.onChange(e).catch(() => true))) return;
  if (state.tab === "wifi" && (await network.onChange(e).catch(() => true))) return;
  if (e.target.id === "screen-control") return screenView.setControl(e.target.checked);
  if (el.dataset.sourceSound) {
    const sources = { ...(state.config.ui.sound_sources || {}) };
    if (el.value) sources[el.dataset.sourceSound] = el.value;
    else delete sources[el.dataset.sourceSound];
    await saveUi({ sound_sources: sources }).catch(() => render());
  } else if (el.dataset.ui) {
    let value = el.type === "checkbox" ? el.checked : el.value;
    if ("int" in el.dataset) value = parseInt(el.value, 10);
    if ("float" in el.dataset) value = parseFloat(el.value);
    if (Number.isNaN(value)) return;
    await saveUi({ [el.dataset.ui]: value }).catch(() => render());
    // Другой масштаб — другой размер холста редактора столов.
    if (el.dataset.ui === "scale") {
      setInfo(await api.get("/info"));
      render();
    }
    // Сменили способ на автоматический: сразу определяем место.
    if (el.dataset.ui === "location_mode" && value !== "manual") {
      await guarded(() => api.post("/location/detect", { mode: value })).catch(() => {});
      state.config = await api.get("/config");
      await loadLocation();
    }
  } else if (el.id === "update-channel") {
    await saveUpdateSettings({ channel: el.value }).catch(() => {});
  } else if (el.id === "update-schedule") {
    await saveUpdateSettings({ schedule: el.value }).catch(() => {});
  } else if (el.id === "update-time") {
    if (el.value) await saveUpdateSettings({ time: el.value }).catch(() => {});
  } else if (el.id === "update-auto") {
    await saveUpdateSettings({ auto: el.checked }).catch(() => {});
  } else if (el.id === "tz") {
    await guarded(() => api.post("/admin/timezone", { timezone: el.value }));
    toast(t("Часовой пояс: {tz}", { tz: el.value }));
  }
});

$("content").addEventListener("submit", async (e) => {
  e.preventDefault();
  const data = Object.fromEntries(new FormData(e.target));
  if (state.tab === "modules" && (await store.onSubmit(e, data).catch(() => true))) return;
  if (state.tab === "weather" && (await weather.onSubmit(e, data).catch(() => true))) return;
  if (state.tab === "wifi" && (await network.onSubmit(e, data).catch(() => true))) return;
  if (e.target.id === "hidden-form") {
    await wifiConnect({ ssid: data.ssid, password: data.password, hidden: true }).catch(() => {});
  } else if (e.target.id === "place-form") {
    const query = (data.q || "").trim();
    if (query.length < 2) return;
    state.placeSearch = { query, results: null };
    await guarded(() => api.get(`/m/weather/search?q=${encodeURIComponent(query)}`)).then((res) => {
      state.placeSearch.results = res.results;
      render();
    }).catch(() => {});
  } else if (e.target.id === "locator-key-form") {
    if (!data.key.trim()) return;
    await guarded(() => api.post("/location/key", { key: data.key.trim() })).then(async () => {
      toast(t("Ключ сохранён"));
      state.config = await api.get("/config");
      render();
    }).catch(() => {});
  } else if (e.target.id === "token-form") {
    if (!data.token.trim()) return;
    await guarded(() => api.post("/update/token", { token: data.token.trim() })).then(() => {
      e.target.reset();
      toast(t("Токен сохранён"));
      return loadUpdate();
    }).catch(() => {});
  } else if (e.target.id === "password-form") {
    await guarded(() => api.post("/admin/password", data)).then(() => {
      e.target.reset();
      toast(t("Пароль изменён"));
    }).catch(() => {});
  }
});

$("content").addEventListener("click", async (e) => {
  const b = e.target.closest("button");
  if (!b || b.disabled || b.getAttribute("type") === "submit") return;
  if (state.tab === "display" && (await backgrounds.onClick(e).catch(() => true))) return;
  if (state.tab === "modules" && (await store.onClick(e).catch(() => true))) return;
  if (state.tab === "weather" && (await weather.onClick(e).catch(() => true))) return;
  if (state.tab === "wifi" && (await network.onClick(e).catch(() => true))) return;
  if ("screenReconnect" in b.dataset) return screenView.mount();
  const d = b.dataset;
  try {
    if ("locate" in d) {
      b.disabled = true;
      const found = await guarded(() => api.post("/location/detect", { mode: state.config.ui.location_mode }));
      toast(found.name ? t("Местоположение: {name}", { name: found.name }) : t("Местоположение обновлено"));
      state.config = await api.get("/config");
      await loadLocation();
    } else if (d.placePick !== undefined) {
      const r = state.placeSearch.results[Number(d.placePick)];
      state.placeSearch = null;
      await saveUi({ location_name: r.name, latitude: r.lat, longitude: r.lon });
    } else if ("wifiRescan" in d) {
      await loadWifi(true);
    } else if ("wifiRadio" in d) {
      if (state.wifi.radio && !(await confirmWifiOff(state.wifi))) return;
      await guarded(() => api.post("/wifi/radio", { enabled: !state.wifi.radio }));
      await loadWifi(false);
    } else if ("wifiDisconnect" in d) {
      await guarded(() => api.post("/wifi/disconnect"));
      await loadWifi(false);
    } else if (d.knownForget !== undefined) {
      const ssid = state.wifi.saved[Number(d.knownForget)];
      if (await confirmDialog({ title: t("Забыть сеть {ssid}?", { ssid }), text: t("Панель перестанет к ней подключаться. Чтобы вернуться, понадобится пароль."), ok: t("Забыть"), danger: true })) {
        await guarded(() => api.post("/wifi/forget", { ssid }));
        await loadWifi(false);
      }
    } else if (d.wifiForget !== undefined) {
      const net = state.wifi.networks[Number(d.wifiForget)];
      if (await confirmDialog({ title: t("Забыть сеть {ssid}?", { ssid: net.ssid }), ok: t("Забыть"), danger: true })) {
        await guarded(() => api.post("/wifi/forget", { ssid: net.ssid }));
        await loadWifi(false);
      }
    } else if (d.wifiConnect !== undefined) {
      const net = state.wifi.networks[Number(d.wifiConnect)];
      let password = "";
      if (net.secure && !net.saved) {
        password = prompt(t("Пароль для {ssid}", { ssid: net.ssid }));
        if (password === null) return;
      }
      await wifiConnect({ ssid: net.ssid, password });
    } else if (d.updateRun) {
      const titles = { install: "Обновить панель сейчас?", rollback: "Вернуть прошлую версию?" };
      if (titles[d.updateRun] && !(await confirmDialog({ title: t(titles[d.updateRun]), ok: t("Да") }))) return;
      await guarded(() => api.post("/update/run", { mode: d.updateRun }));
      state.update.status = { ...state.update.status, state: d.updateRun === "check" ? "checking" : "installing", message: "Запускаю…", message_args: {} };
      render();
      setTimeout(loadUpdate, 1000);
    } else if (d.soundTestSource) {
      const name = state.config.ui.sound_sources?.[d.soundTestSource] || state.config.ui.sound_name;
      await guarded(() => api.post("/sound/test", { name })).then(() => toast(t("Звук отправлен на панель"))).catch(() => {});
    } else if ("soundTest" in d) {
      const name = document.querySelector('[data-ui="sound_name"]')?.value;
      await guarded(() => api.post("/sound/test", { name })).then(() => toast(t("Звук отправлен на панель"))).catch(() => {});
    } else if ("tokenClear" in d) {
      if (await confirmDialog({ title: t("Удалить токен GitHub?"), ok: t("Удалить"), danger: true })) {
        await guarded(() => api.post("/update/token", { token: "" }));
        await loadUpdate();
      }
    } else if (d.power) {
      const reboot = d.power === "reboot";
      if (await confirmDialog({ title: t(reboot ? "Перезагрузить панель?" : "Выключить панель?"), ok: t(reboot ? "Перезагрузить" : "Выключить"), danger: !reboot })) {
        await guarded(() => api.post("/power", { action: d.power }));
        toast(t(reboot ? "Панель перезагружается" : "Панель выключается"));
      }
    }
  } catch {
    // Ошибку уже показал guarded().
  }
});

boot();
