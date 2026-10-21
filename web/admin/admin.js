// Сетевая админка: всё, что есть на экране, плюс модули, статусная строка,
// часовой пояс и пароль.

import { api } from "/lib/api.js";
import { icon, signalBars } from "/lib/icons.js";
import { confirmDialog, toast } from "/lib/dialog.js";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const TABS = [
  { id: "display", title: "Экран", icon: "display" },
  { id: "modules", title: "Модули", icon: "gear" },
  { id: "wifi", title: "Wi‑Fi", icon: "wifi" },
  { id: "updates", title: "Обновления", icon: "refresh" },
  { id: "system", title: "Система", icon: "info" },
];

const state = { tab: "display", config: null, catalog: null, wifi: null, time: null, info: null, update: null };

// Админка следует теме компьютера, на котором её открыли.
const media = matchMedia("(prefers-color-scheme: light)");
const syncTheme = () => (document.documentElement.dataset.theme = media.matches ? "light" : "dark");
media.addEventListener("change", syncTheme);
syncTheme();

// --- вход ------------------------------------------------------------------

async function boot() {
  const session = await api.get("/session");
  if (!session.authorized) {
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
    await api.post("/login", { password: $("login-password").value });
    $("login-password").value = "";
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

$("tabs").innerHTML = TABS.map((t) => `<button class="tab" data-tab="${t.id}">${icon(t.icon)}<span>${t.title}</span></button>`).join("");
$("tabs").addEventListener("click", (e) => {
  const b = e.target.closest(".tab");
  if (b) show(b.dataset.tab);
});

async function reload() {
  const [config, catalog] = await guarded(() => Promise.all([api.get("/config"), api.get("/modules")]));
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
  render();
}

function render() {
  const views = { display: renderDisplay, modules: renderModules, wifi: renderWifi, updates: renderUpdates, system: renderSystem };
  $("content").innerHTML = views[state.tab]();
}

async function saveUi(patch) {
  await guarded(() => api.post("/settings", { ui: patch }));
  state.config = await api.get("/config");
  render();
}

// --- экран ----------------------------------------------------------------------

function renderDisplay() {
  const ui = state.config.ui;
  const opts = (values, current) => values.map(([v, l]) => `<option value="${v}" ${v === current ? "selected" : ""}>${l}</option>`).join("");
  const sched = ui.theme_mode === "sun" ? "disabled" : "";
  return `
    <h1>Экран</h1>
    <section class="panel">
      <label class="field"><span>Выбор темы</span>
        <select data-ui="theme_mode">${opts([["manual", "Вручную"], ["schedule", "По времени"], ["sun", "По солнцу"]], ui.theme_mode)}</select>
      </label>
      <label class="field"><span>Тема</span>
        <select data-ui="theme" ${ui.theme_mode === "manual" ? "" : "disabled"}>${opts([["light", "Светлая"], ["dark", "Тёмная"]], ui.theme)}</select>
      </label>
      <label class="field"><span>День с<small>В режиме «по времени» и для ночного приглушения</small></span>
        <input type="time" data-ui="day_start" value="${ui.day_start}" ${sched}>
      </label>
      <label class="field"><span>Ночь с</span><input type="time" data-ui="night_start" value="${ui.night_start}" ${sched}></label>
      <label class="field"><span>Широта<small>Для режима «по солнцу»</small></span>
        <input type="number" step="0.01" min="-90" max="90" data-ui="latitude" data-float value="${ui.latitude}">
      </label>
      <label class="field"><span>Долгота</span>
        <input type="number" step="0.01" min="-180" max="180" data-ui="longitude" data-float value="${ui.longitude}">
      </label>
    </section>
    <section class="panel">
      <label class="field"><span>Ночное приглушение, %<small>0 — не приглушать</small></span>
        <input type="number" min="0" max="90" step="5" data-ui="night_dim" data-int value="${ui.night_dim}">
      </label>
      <label class="field"><span>Затемнять через, мин<small>0 — не затемнять</small></span>
        <input type="number" min="0" max="240" data-ui="dim_after_min" data-int value="${ui.dim_after_min}">
      </label>
      <label class="field"><span>Сила затемнения, %</span>
        <input type="number" min="0" max="95" step="5" data-ui="dim_level" data-int value="${ui.dim_level}">
      </label>
      <label class="field"><span>Гасить экран через, мин<small>0 — не гасить</small></span>
        <input type="number" min="0" max="240" data-ui="blank_after_min" data-int value="${ui.blank_after_min}">
      </label>
    </section>`;
}

// --- модули и статусная строка ------------------------------------------------------

function renderModules() {
  const catalog = Object.fromEntries(state.catalog.modules.map((m) => [m.id, m]));
  const configured = state.config.modules.filter((m) => catalog[m.id]);
  // Модули из каталога, которых ещё нет в настройках, показываем выключенными.
  for (const m of state.catalog.modules) {
    if (!configured.some((c) => c.id === m.id)) configured.push({ id: m.id, enabled: false, options: {} });
  }
  const modules = configured.map((m, i) => {
    const meta = catalog[m.id];
    const options = (meta.options || []).map((o) => {
      const value = m.options[o.key] ?? o.default;
      return `<label class="check"><input type="checkbox" data-mod-opt="${i}" data-key="${esc(o.key)}" ${value ? "checked" : ""}> ${esc(o.title)}</label>`;
    }).join("");
    return `
      <div class="item">
        <div class="item-main">
          <button class="switch" data-mod-toggle="${i}" aria-checked="${m.enabled}"></button>
          <div><div class="item-title">${esc(meta.title)}</div><div class="muted small">${esc(meta.description || "")}</div></div>
          <div class="order">
            <button class="btn btn-small" data-mod-move="${i}" data-dir="-1" ${i === 0 ? "disabled" : ""}>↑</button>
            <button class="btn btn-small" data-mod-move="${i}" data-dir="1" ${i === configured.length - 1 ? "disabled" : ""}>↓</button>
          </div>
        </div>
        ${options ? `<div class="item-options">${options}</div>` : ""}
      </div>`;
  }).join("");
  state.draftModules = configured;

  const active = state.config.statusbar;
  const providers = [...active.map((id) => state.catalog.providers.find((p) => p.id === id)).filter(Boolean),
    ...state.catalog.providers.filter((p) => !active.includes(p.id))];
  state.draftStatus = providers.map((p) => ({ id: p.id, on: active.includes(p.id) }));
  const status = providers.map((p, i) => `
    <div class="item"><div class="item-main">
      <button class="switch" data-st-toggle="${i}" aria-checked="${active.includes(p.id)}"></button>
      <div class="item-title">${esc(p.title)}</div>
      <div class="order">
        <button class="btn btn-small" data-st-move="${i}" data-dir="-1" ${i === 0 ? "disabled" : ""}>↑</button>
        <button class="btn btn-small" data-st-move="${i}" data-dir="1" ${i === providers.length - 1 ? "disabled" : ""}>↓</button>
      </div>
    </div></div>`).join("");

  return `
    <h1>Модули</h1>
    <p class="muted">Модули главного экрана в порядке показа.</p>
    <section class="panel list">${modules}</section>
    <h1>Статусная строка</h1>
    <section class="panel list">${status}</section>`;
}

async function saveModules(modules) {
  await guarded(() => api.post("/admin/modules", { modules }));
  await reload();
}

async function saveStatus(items) {
  await guarded(() => api.post("/admin/statusbar", { items: items.filter((i) => i.on).map((i) => i.id) }));
  await reload();
}

function move(list, i, dir) {
  const copy = [...list];
  const [item] = copy.splice(i, 1);
  copy.splice(i + dir, 0, item);
  return copy;
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
  if (!w) body = `<p class="muted">Ищу сети…</p>`;
  else if (w.error) body = `<p class="error">${esc(w.error)}</p>`;
  else if (!w.available) body = `<p class="muted">Wi‑Fi адаптер не найден</p>`;
  else {
    body = `
      <section class="panel">
        <label class="field"><span>Wi‑Fi${w.connection ? `<small>Подключено к ${esc(w.connection)}</small>` : ""}</span>
          <button class="switch" data-wifi-radio aria-checked="${w.radio}"></button>
        </label>
      </section>
      <section class="panel list">
        ${w.networks.map((n, i) => `
          <div class="item"><div class="item-main">
            ${signalBars(n.signal)}
            <div class="item-title grow">${esc(n.ssid)} ${n.secure ? icon("lock", "icon tiny") : ""}
              <div class="muted small">${n.active ? "Подключено" : n.saved ? "Сохранена" : esc(n.security || "Открытая")}</div>
            </div>
            ${n.active ? `<button class="btn btn-small" data-wifi-disconnect>Отключиться</button>` : ""}
            ${!n.active ? `<button class="btn btn-small btn-primary" data-wifi-connect="${i}">Подключиться</button>` : ""}
            ${n.saved ? `<button class="btn btn-small" data-wifi-forget="${i}">Забыть</button>` : ""}
          </div></div>`).join("") || `<p class="muted pad">Сети не найдены</p>`}
      </section>
      <form class="panel inline-form" id="hidden-form">
        <input name="ssid" placeholder="Скрытая сеть: имя" required>
        <input name="password" type="password" placeholder="Пароль">
        <button class="btn" type="submit">Подключиться</button>
      </form>`;
  }
  return `<h1>Wi‑Fi <button class="btn btn-small" data-wifi-rescan>${icon("refresh")} Обновить</button></h1>${body}`;
}

async function wifiConnect(body) {
  toast(`Подключаюсь к ${body.ssid}…`);
  await guarded(() => api.post("/wifi/connect", body));
  toast(`Подключено к ${body.ssid}`);
  await loadWifi(false);
}

// --- система -----------------------------------------------------------------------

async function loadSystem() {
  [state.time, state.info] = await guarded(() => Promise.all([api.get("/time"), api.get("/info")]));
  if (state.tab === "system") render();
}

function renderSystem() {
  const t = state.time;
  const info = state.info || {};
  return `
    <h1>Система</h1>
    <section class="panel">
      <div class="field"><span>Устройство</span><b>${esc(info.hostname || "—")}</b></div>
      <div class="field"><span>Версия</span><b>${esc(info.version || "—")}</b></div>
      <label class="field"><span>Часовой пояс</span>
        ${t ? `<select id="tz">${t.timezones.map((z) => `<option ${z === t.timezone ? "selected" : ""}>${esc(z)}</option>`).join("")}</select>` : "…"}
      </label>
    </section>
    <h1>Пароль админки</h1>
    <form class="panel inline-form" id="password-form">
      <input name="current" type="password" placeholder="Текущий пароль" autocomplete="current-password" required>
      <input name="new" type="password" placeholder="Новый пароль (от 6 символов)" autocomplete="new-password" minlength="6" required>
      <button class="btn btn-primary" type="submit">Сменить</button>
    </form>
    <h1>Питание</h1>
    <section class="panel row-buttons">
      <button class="btn" data-power="reboot">${icon("reboot")} Перезагрузить</button>
      <button class="btn btn-danger" data-power="poweroff">${icon("power")} Выключить</button>
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

const shortDate = (s) => (s ? new Date(s).toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" }) : "—");
const buildText = (b) => (b && b.commit ? `${b.ref || ""} ${b.commit.slice(0, 7)} · ${shortDate(b.date)}` : "—");

function renderUpdates() {
  const u = state.update;
  if (!u) return `<h1>Обновления</h1><div class="empty">Загрузка…</div>`;
  const st = u.status || {};
  const busy = ["checking", "installing"].includes(st.state);
  const latest = st.latest;
  const level = busy ? "busy" : st.result === "failed" ? "error" : st.available ? "new" : "";
  return `
    <h1>Обновления</h1>
    <section class="panel">
      <div class="field"><span>Установлена</span><b class="mono">${esc(buildText(u.build))}</b></div>
      <div class="field"><span>Последняя в канале${latest && latest.title ? `<small>${esc(latest.title)}</small>` : ""}</span>
        <b class="mono">${esc(buildText(latest))}</b></div>
      <div class="field"><span>Проверено</span><b>${esc(shortDate(st.checked_at))}</b></div>
      <div class="update-state ${level}">${esc(st.message || "")}</div>
      <div class="row-buttons">
        <button class="btn" data-update-run="check" ${busy ? "disabled" : ""}>${icon("refresh")} Проверить</button>
        <button class="btn btn-primary" data-update-run="install" ${busy ? "disabled" : ""}>Обновить сейчас</button>
        <button class="btn" data-update-run="rollback" ${busy ? "disabled" : ""}>Вернуть прошлую версию</button>
      </div>
    </section>
    <section class="panel">
      <label class="field"><span>Канал<small>main — каждый коммит, релизы — только теги v*</small></span>
        <select id="update-channel">
          <option value="main" ${u.settings.channel === "main" ? "selected" : ""}>Ветка ${esc(u.settings.branch)}</option>
          <option value="releases" ${u.settings.channel === "releases" ? "selected" : ""}>Релизы</option>
        </select>
      </label>
      <label class="field"><span>Ставить автоматически<small>Проверка каждую ночь около 4:00</small></span>
        <input type="checkbox" id="update-auto" ${u.settings.auto ? "checked" : ""}>
      </label>
    </section>
    <h1>Доступ к GitHub</h1>
    <form class="panel inline-form" id="token-form">
      <input name="token" type="password" autocomplete="off"
        placeholder="${u.token_set ? "Токен сохранён · вставьте новый, чтобы заменить" : "Токен для приватного репозитория"}">
      <button class="btn btn-primary" type="submit">Сохранить</button>
      ${u.token_set ? `<button class="btn" type="button" data-token-clear>Удалить</button>` : ""}
    </form>`;
}

async function saveUpdateSettings(patch) {
  await guarded(() => api.post("/update/settings", patch));
  await loadUpdate();
}

// --- события ------------------------------------------------------------------------

$("content").addEventListener("change", async (e) => {
  const el = e.target;
  if (el.dataset.ui) {
    let value = el.value;
    if ("int" in el.dataset) value = parseInt(el.value, 10);
    if ("float" in el.dataset) value = parseFloat(el.value);
    if (Number.isNaN(value)) return;
    await saveUi({ [el.dataset.ui]: value }).catch(() => render());
  } else if (el.dataset.modOpt !== undefined) {
    const m = state.draftModules[Number(el.dataset.modOpt)];
    m.options = { ...m.options, [el.dataset.key]: el.checked };
    await saveModules(state.draftModules);
  } else if (el.id === "update-channel") {
    await saveUpdateSettings({ channel: el.value }).catch(() => {});
  } else if (el.id === "update-auto") {
    await saveUpdateSettings({ auto: el.checked }).catch(() => {});
  } else if (el.id === "tz") {
    await guarded(() => api.post("/admin/timezone", { timezone: el.value }));
    toast(`Часовой пояс: ${el.value}`);
  }
});

$("content").addEventListener("submit", async (e) => {
  e.preventDefault();
  const data = Object.fromEntries(new FormData(e.target));
  if (e.target.id === "hidden-form") {
    await wifiConnect({ ssid: data.ssid, password: data.password, hidden: true }).catch(() => {});
  } else if (e.target.id === "token-form") {
    if (!data.token.trim()) return;
    await guarded(() => api.post("/update/token", { token: data.token.trim() })).then(() => {
      e.target.reset();
      toast("Токен сохранён");
      return loadUpdate();
    }).catch(() => {});
  } else if (e.target.id === "password-form") {
    await guarded(() => api.post("/admin/password", data)).then(() => {
      e.target.reset();
      toast("Пароль изменён");
    }).catch(() => {});
  }
});

$("content").addEventListener("click", async (e) => {
  const b = e.target.closest("button");
  if (!b || b.disabled || b.getAttribute("type") === "submit") return;
  const d = b.dataset;
  try {
    if (d.modToggle !== undefined) {
      const m = state.draftModules[Number(d.modToggle)];
      m.enabled = !m.enabled;
      await saveModules(state.draftModules);
    } else if (d.modMove !== undefined) {
      await saveModules(move(state.draftModules, Number(d.modMove), Number(d.dir)));
    } else if (d.stToggle !== undefined) {
      const item = state.draftStatus[Number(d.stToggle)];
      item.on = !item.on;
      await saveStatus(state.draftStatus);
    } else if (d.stMove !== undefined) {
      await saveStatus(move(state.draftStatus, Number(d.stMove), Number(d.dir)));
    } else if ("wifiRescan" in d) {
      await loadWifi(true);
    } else if ("wifiRadio" in d) {
      await guarded(() => api.post("/wifi/radio", { enabled: !state.wifi.radio }));
      await loadWifi(false);
    } else if ("wifiDisconnect" in d) {
      await guarded(() => api.post("/wifi/disconnect"));
      await loadWifi(false);
    } else if (d.wifiForget !== undefined) {
      const net = state.wifi.networks[Number(d.wifiForget)];
      if (await confirmDialog({ title: `Забыть сеть ${net.ssid}?`, ok: "Забыть", danger: true })) {
        await guarded(() => api.post("/wifi/forget", { ssid: net.ssid }));
        await loadWifi(false);
      }
    } else if (d.wifiConnect !== undefined) {
      const net = state.wifi.networks[Number(d.wifiConnect)];
      let password = "";
      if (net.secure && !net.saved) {
        password = prompt(`Пароль для ${net.ssid}`);
        if (password === null) return;
      }
      await wifiConnect({ ssid: net.ssid, password });
    } else if (d.updateRun) {
      const titles = { install: "Обновить панель сейчас?", rollback: "Вернуть прошлую версию?" };
      if (titles[d.updateRun] && !(await confirmDialog({ title: titles[d.updateRun], ok: "Да" }))) return;
      await guarded(() => api.post("/update/run", { mode: d.updateRun }));
      state.update.status = { ...state.update.status, state: d.updateRun === "check" ? "checking" : "installing", message: "Запускаю…" };
      render();
      setTimeout(loadUpdate, 1000);
    } else if ("tokenClear" in d) {
      if (await confirmDialog({ title: "Удалить токен GitHub?", ok: "Удалить", danger: true })) {
        await guarded(() => api.post("/update/token", { token: "" }));
        await loadUpdate();
      }
    } else if (d.power) {
      const reboot = d.power === "reboot";
      if (await confirmDialog({ title: reboot ? "Перезагрузить панель?" : "Выключить панель?", ok: reboot ? "Перезагрузить" : "Выключить", danger: !reboot })) {
        await guarded(() => api.post("/power", { action: d.power }));
        toast(reboot ? "Панель перезагружается" : "Панель выключается");
      }
    }
  } catch {
    // Ошибку уже показал guarded().
  }
});

boot();
