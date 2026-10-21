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

const $ = (id) => document.getElementById(id);
// «0.1.1 · a1b2c3d»: номер версии и коммит сборки (его пишет установщик).
const versionText = (info) => [info.version, info.build && info.build.commit && info.build.commit.slice(0, 7)].filter(Boolean).join(" · ") || "—";
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const TABS = [
  { id: "display", title: "Экран", icon: "display" },
  { id: "desktops", title: "Столы", icon: "grid" },
  { id: "modules", title: "Модули", icon: "plus" },
  { id: "screen", title: "Трансляция", icon: "eye" },
  { id: "wifi", title: "Wi‑Fi", icon: "wifi" },
  { id: "updates", title: "Обновления", icon: "refresh" },
  { id: "system", title: "Система", icon: "info" },
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
  $("tabs").innerHTML = TABS.map((tab) => `<button class="tab" data-tab="${tab.id}">${icon(tab.icon)}<span>${t(tab.title)}</span></button>`).join("");
}
$("tabs").addEventListener("click", (e) => {
  const b = e.target.closest(".tab");
  if (b) show(b.dataset.tab);
});

async function reload() {
  const [config, catalog] = await guarded(() => Promise.all([api.get("/config"), loadCatalog()]));
  state.config = config;
  state.catalog = catalog;
  show(state.tab);
}

function show(tab) {
  state.tab = tab;
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  if (tab === "wifi") loadWifi(false);
  if (tab === "system") loadSystem();
  if (tab === "updates") loadUpdate();
  if (tab === "modules") store.load().catch(() => {});
  if (tab === "display" && !backgrounds.items) backgrounds.load().then(() => state.tab === "display" && render()).catch(() => {});
  if (tab === "display") loadLocation();
  render();
}

function render() {
  const views = { display: renderDisplay, desktops: renderDesktops, modules: () => store.render(), screen: () => screenView.render(), wifi: renderWifi, updates: renderUpdates, system: renderSystem };
  editor?.destroy();
  editor = null;
  screenView.destroy();
  $("content").classList.toggle("wide", state.tab === "desktops");
  $("content").innerHTML = views[state.tab]();
  if (state.tab === "desktops") mountEditor();
  if (state.tab === "screen") screenView.mount();
}

async function saveUi(patch) {
  await guarded(() => api.post("/settings", { ui: patch }));
  state.config = await api.get("/config");
  if (state.config.ui.language !== language()) return location.reload();
  render();
}

// --- экран ----------------------------------------------------------------------

function renderDisplay() {
  const ui = state.config.ui;
  const opts = (values, current) => values.map(([v, l]) => `<option value="${v}" ${v === current ? "selected" : ""}>${esc(l)}</option>`).join("");
  const sched = ui.theme_mode === "sun" ? "disabled" : "";
  return `
    <h1>${t("Экран")}</h1>
    <section class="panel">
      <label class="field"><span>${t("Язык")}<small>${t("Общий для панели и админки")}</small></span>
        <select data-ui="language">${opts(languages().map((l) => [l.code, l.name]), ui.language)}</select>
      </label>
    </section>
    <section class="panel">
      <label class="field"><span>${t("Выбор темы")}</span>
        <select data-ui="theme_mode">${opts([["manual", t("Вручную")], ["schedule", t("По времени")], ["sun", t("По солнцу")]], ui.theme_mode)}</select>
      </label>
      <label class="field"><span>${t("Тема")}</span>
        <select data-ui="theme" ${ui.theme_mode === "manual" ? "" : "disabled"}>${opts([["light", t("Светлая")], ["dark", t("Тёмная")]], ui.theme)}</select>
      </label>
      <label class="field"><span>${t("День с")}<small>${t("В режиме «по времени» и для ночного приглушения")}</small></span>
        <input type="time" data-ui="day_start" value="${ui.day_start}" ${sched}>
      </label>
      <label class="field"><span>${t("Ночь с")}</span><input type="time" data-ui="night_start" value="${ui.night_start}" ${sched}></label>
    </section>
    ${renderLocation()}
    <section class="panel">
      <label class="field"><span>${t("Ночное приглушение, %")}<small>${t("0 — не приглушать")}</small></span>
        <input type="number" min="0" max="90" step="5" data-ui="night_dim" data-int value="${ui.night_dim}">
      </label>
      <label class="field"><span>${t("Затемнять через, мин")}<small>${t("0 — не затемнять")}</small></span>
        <input type="number" min="0" max="240" data-ui="dim_after_min" data-int value="${ui.dim_after_min}">
      </label>
      <label class="field"><span>${t("Сила затемнения, %")}</span>
        <input type="number" min="0" max="95" step="5" data-ui="dim_level" data-int value="${ui.dim_level}">
      </label>
      <label class="field"><span>${t("Гасить экран через, мин")}<small>${t("0 — не гасить")}</small></span>
        <input type="number" min="0" max="240" data-ui="blank_after_min" data-int value="${ui.blank_after_min}">
      </label>
    </section>
    ${backgrounds.render(ui)}`;
}

const screenView = new ScreenView();

const store = new StorePanel({
  guarded,
  toast,
  confirm: confirmDialog,
  render: () => state.tab === "modules" && render(),
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
    catalog: state.catalog,
    save: async (desktops, homeAfterMin) => {
      const res = await guarded(() => api.post("/admin/desktops", { desktops, home_after_min: homeAfterMin }));
      state.config.desktops = res.desktops;
      state.config.layout.home_after_min = homeAfterMin;
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
      </form>`;
  }
  return `<h1>Wi‑Fi <button class="btn btn-small" data-wifi-rescan>${icon("refresh")} ${t("Искать сети")}</button></h1>${body}`;
}

async function wifiConnect(body) {
  toast(t("Подключаюсь к {ssid}…", { ssid: body.ssid }));
  await guarded(() => api.post("/wifi/connect", body));
  toast(t("Подключено к {ssid}", { ssid: body.ssid }));
  await loadWifi(false);
}

// --- система -----------------------------------------------------------------------

async function loadSystem() {
  [state.time, state.info] = await guarded(() => Promise.all([api.get("/time"), api.get("/info")]));
  if (state.tab === "system") render();
}

function renderSystem() {
  const time = state.time;
  const info = state.info || {};
  return `
    <h1>${t("Система")}</h1>
    <section class="panel">
      <div class="field"><span>${t("Устройство")}</span><b>${esc(info.hostname || "—")}</b></div>
      <div class="field"><span>${t("Версия")}</span><b>${esc(versionText(info))}</b></div>
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
const buildText = (b) => (b && b.commit ? `${b.ref || ""} ${b.commit.slice(0, 7)} · ${shortDate(b.date)}` : "—");

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
      <div class="field"><span>${t("Установлена")}</span><b class="mono">${esc(buildText(u.build))}</b></div>
      <div class="field"><span>${t("Последняя в канале")}${latest && latest.title ? `<small>${esc(latest.title)}</small>` : ""}</span>
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
  if (e.target.id === "screen-control") return screenView.setControl(e.target.checked);
  if (el.dataset.ui) {
    let value = el.value;
    if ("int" in el.dataset) value = parseInt(el.value, 10);
    if ("float" in el.dataset) value = parseFloat(el.value);
    if (Number.isNaN(value)) return;
    await saveUi({ [el.dataset.ui]: value }).catch(() => render());
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
