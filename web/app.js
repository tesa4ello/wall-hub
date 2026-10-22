// Оболочка киоска: тема, рабочие столы с виджетами, шторка, затемнение,
// настройки.

import { api } from "/lib/api.js";
import { language, setLanguage, t, translateDom } from "/lib/i18n.js";
import { icon } from "/lib/icons.js";
import { confirmDialog, confirmWifiOff, toast } from "/lib/dialog.js";
import { isNight, themeFor } from "/lib/theme.js";
import { Wallpaper } from "/lib/wallpaper.js";
import * as sound from "/lib/sound.js";
import { alarms as alarmApi, onAlarms, setRev as setAlarmsRev } from "/lib/alarms.js";
import { setRev as setCalendarRev } from "/lib/calendar.js";
import { destroyAll, enableNotify, loadCatalog, renderDesktop, setSound } from "/lib/widgets.js";
import { Settings } from "/settings.js";
import * as shadeView from "/shade.js";

const state = {
  config: null,
  catalog: null,
  revision: -1,
  build: null,
  updateAvailable: null, // null — ещё не знаем (до первого ответа сервера)
  moduleUpdates: null, // сколько модулей можно обновить (ставятся из админки)
};

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

// --- тема -----------------------------------------------------------------

function applyTheme() {
  if (!state.config) return;
  const ui = state.config.ui;
  const now = new Date();
  const root = document.documentElement;
  const theme = themeFor(ui, now);
  if (root.dataset.theme !== theme) {
    // Плавная смена темы: временно включаем переходы цветов у всех элементов.
    root.classList.add("theme-anim");
    root.dataset.theme = theme;
    // Админка повторяет тему экрана: сообщаем о смене сразу, не дожидаясь опроса.
    api.get(`/revision?theme=${theme}`).catch(() => {});
    clearTimeout(applyTheme.timer);
    applyTheme.timer = setTimeout(() => root.classList.remove("theme-anim"), 1600);
  }
  wallpaper.apply(ui, theme).catch((e) => console.error("wallpaper", e));
  // Ночью экран постоянно приглушён, чтобы не светил в глаза.
  // Яркость из шторки и ночное приглушение: берём более тёмное.
  const night = Math.max(isNight(ui, now) ? ui.night_dim : 0, 100 - (ui.brightness ?? 100));
  $("night").style.opacity = night / 100;
  dimmer.setFloor(night);
}

const wallpaper = new Wallpaper($("wallpaper"), $("desktops"));

// --- рабочие столы ------------------------------------------------------------

// Все столы смонтированы сразу и лежат лентой; перелистывание — сдвиг ленты.
const desks = {
  index: 0,
  key: "",
  instances: [],
  homeTimer: null,

  async render(config, catalog) {
    const key = JSON.stringify([config.desktops, config.layout, Object.keys(catalog)]);
    if (key === this.key) return;
    this.key = key;
    destroyAll(this.instances);
    this.instances = [];
    const track = $("desk-track");
    track.innerHTML = "";
    for (const desktop of config.desktops) {
      const el = document.createElement("div");
      el.className = "desk";
      track.appendChild(el);
      this.instances.push(...(await renderDesktop(el, desktop, config.layout, catalog)));
    }
    $("dots").innerHTML = config.desktops.map(() => "<i></i>").join("");
    $("dots").hidden = config.desktops.length < 2;
    this.go(Math.min(this.index, config.desktops.length - 1), false);
    wallpaper.layout();
    this.carouselNext();
  },

  get count() {
    return state.config ? state.config.desktops.length : 1;
  },

  go(index, animate = true) {
    this.index = Math.max(0, Math.min(this.count - 1, index));
    const track = $("desk-track");
    track.style.transition = animate ? "" : "none";
    track.style.transform = `translateX(${-this.index * 100}%)`;
    $("dots").querySelectorAll("i").forEach((d, i) => d.classList.toggle("on", i === this.index));
  },

  // Через layout.home_after_min минут без касаний возвращаемся на первый стол.
  // С каруселью вместо этого через carousel.resume минут листаем дальше.
  touched() {
    clearTimeout(this.homeTimer);
    if (this.carouselOn()) {
      this.carouselNext(Math.max(state.config.layout.carousel.resume * 60, 1) * 1000);
      return;
    }
    const minutes = state.config ? state.config.layout.home_after_min : 0;
    if (minutes > 0 && this.index > 0) this.homeTimer = setTimeout(() => this.go(0), minutes * 60000);
  },

  // --- карусель: столы листаются сами по кругу ---------------------------------

  carouselTimer: null,

  // Столы, которые участвуют в карусели (по порядку).
  carouselDesks() {
    return state.config.desktops.map((d, i) => (d.carousel === false ? -1 : i)).filter((i) => i >= 0);
  },

  carouselOn() {
    const car = state.config?.layout.carousel;
    return Boolean(car?.enabled) && this.carouselDesks().length > 1;
  },

  // Сколько показывать текущий стол: свой интервал или общий.
  carouselDelay() {
    const desk = state.config.desktops[this.index];
    return ((desk && desk.carousel !== false && desk.interval) || state.config.layout.carousel.interval) * 1000;
  },

  carouselNext(delay = this.carouselDelay()) {
    clearTimeout(this.carouselTimer);
    if (!this.carouselOn()) return;
    this.carouselTimer = setTimeout(() => this.carouselStep(), delay);
  },

  carouselStep() {
    // Пока открыты настройки или шторка, стол не меняем.
    if ($("settings").hidden && !shade.open) {
      const order = this.carouselDesks();
      this.go(order.find((i) => i > this.index) ?? order[0]);
    }
    this.carouselNext();
  },
};

// Жесты на столах: свайп влево/вправо листает столы, долгое нажатие (2 с)
// открывает настройки.
const LONG_PRESS_MS = 2000;
{
  let start = null;
  let longTimer = null;
  const host = $("desktops");
  host.addEventListener("pointerdown", (e) => {
    start = { x: e.clientX, y: e.clientY, swiping: false };
    clearTimeout(longTimer);
    longTimer = setTimeout(() => {
      start = null;
      settings.open();
    }, LONG_PRESS_MS);
  });
  window.addEventListener("pointermove", (e) => {
    if (!start) return;
    const dx = e.clientX - start.x;
    const dy = e.clientY - start.y;
    // Палец на сенсоре немного «плывёт»: долгое нажатие сбивает только заметный сдвиг.
    if (Math.abs(dx) > 24 || Math.abs(dy) > 24) clearTimeout(longTimer);
    if (!start.swiping && Math.abs(dx) > 16 && Math.abs(dx) > Math.abs(dy) && desks.count > 1) start.swiping = true;
    if (start.swiping) {
      const track = $("desk-track");
      track.style.transition = "none";
      track.style.transform = `translateX(calc(${-desks.index * 100}% + ${dx}px))`;
    }
  });
  const end = (e) => {
    // pointercancel браузер шлёт и посреди долгого нажатия (жесты сенсора),
    // поэтому таймер снимаем только при настоящем отпускании.
    if (e.type === "pointerup") clearTimeout(longTimer);
    if (start && start.swiping) {
      const dx = e.clientX - start.x;
      const threshold = window.innerWidth * 0.15;
      desks.go(desks.index + (dx < -threshold ? 1 : dx > threshold ? -1 : 0));
    }
    start = null;
  };
  window.addEventListener("pointerup", end);
  window.addEventListener("pointercancel", end);
}

// --- шторка ---------------------------------------------------------------------

// Свайп вниз от верхнего края открывает шторку — пульт быстрого доступа:
// переключатели, яркость и громкость, уведомления, столы, питание. Вид
// шторки — в shade.js, здесь данные и действия.
const THEMES = [
  [{ theme_mode: "manual", theme: "dark" }, "moon", "Тёмная"],
  [{ theme_mode: "manual", theme: "light" }, "sun", "Светлая"],
  [{ theme_mode: "schedule" }, "clock", "По времени"],
  // Подписи переводятся при отрисовке.
];

const shade = {
  open: false,
  wifi: null,
  // Последний ответ GET /api/shade: статус, переключатели, уведомления, плитки модулей.
  data: null,
  // Номер последнего уведомления, о котором уже сообщили (всплывашка, звук).
  noteSeq: null,
  notesRev: null,
  // Жесты внутри шторки (shade.js): пока палец на ползунке или уведомлении,
  // не перерисовываем.
  gestures: null,

  themeIndex() {
    const ui = state.config.ui;
    if (ui.theme_mode === "manual") return ui.theme === "light" ? 1 : 0;
    return 2;
  },

  render() {
    if (!state.config || !this.data || this.gestures?.busy) return;
    const ui = state.config.ui;
    const theme = THEMES[this.themeIndex()];
    shadeView.render($("shade"), {
      data: this.data,
      theme: { icon: ui.theme_mode === "sun" ? "sun" : theme[1], label: t(ui.theme_mode === "sun" ? "По солнцу" : theme[2]) },
      desks: state.config.desktops,
      layout: state.config.layout,
      current: desks.index,
    });
  },

  // Состояние шторки держим свежим в фоне, чтобы она открывалась сразу
  // с готовыми данными (Wi‑Fi без списка сетей: он запускает сканирование).
  async refresh() {
    try {
      this.data = await api.get("/shade");
      const w = this.data.toggles.wifi;
      this.wifi = { available: w.available, radio: w.on, connection: w.ssid, other_connection: w.other_connection };
    } catch {
      // Оставляем прошлое состояние, обновим на следующем шаге.
    }
    if (this.open) this.render();
  },

  // Из долгого опроса: появились уведомления или изменился список.
  async onNotes({ seq, rev }) {
    const first = this.noteSeq === null;
    if (rev !== this.notesRev || first) {
      this.notesRev = rev;
      await this.refresh();
    }
    if (first) {
      this.noteSeq = seq;
      return;
    }
    if (seq <= this.noteSeq) return;
    const fresh = (this.data?.notifications || []).filter((n) => typeof n.id === "number" && n.id > this.noteSeq);
    this.noteSeq = seq;
    // «Не беспокоить»: копим в шторке молча.
    if (state.config?.ui.dnd) return;
    for (const n of fresh.reverse()) {
      toast(n.text ? `${n.title} · ${n.text}` : n.title, n.level === "crit" ? "error" : undefined);
      if (n.sound) sound.play(null, { source: n.source });
    }
  },

  // id приходит из разметки строкой; у уведомлений модулей он числовой.
  noteId(id) {
    return /^\d+$/.test(id) ? Number(id) : id;
  },

  async dismiss(id) {
    // Убираем сразу, не дожидаясь ядра: уведомление уже уехало пальцем.
    if (this.data) {
      this.data.notifications = id === "all" ? [] : this.data.notifications.filter((n) => String(n.id) !== String(id));
      this.render();
    }
    await api.post("/notifications/dismiss", id === "all" ? { all: true } : { id: this.noteId(id) });
    await this.refresh();
  },

  async runAction(id) {
    const res = await api.post("/notifications/action", { id: this.noteId(id) });
    if (res.kind === "update") this.hide();
    await this.refresh();
    return res;
  },

  // Яркость, громкость, «Не беспокоить»: {brightness?, volume?, dnd?}.
  async set(patch) {
    this.data = await api.post("/shade", patch);
    await loadConfig();
    if (this.open) this.render();
  },

  start() {
    this.gestures = shadeView.bindGestures($("shade"), {
      // Ползунок шлёт значения и под пальцем, и при отпускании.
      slide: (name, value) => this.set({ [name]: value }).catch(() => this.refresh()),
      dismiss: (id) => this.dismiss(id).catch(() => this.refresh()),
    });
    this.refresh();
    setInterval(() => { if (!document.hidden) this.refresh(); }, 15000);
  },

  show() {
    this.open = true;
    this.openedAt = Date.now();
    this.render();
    $("shade-backdrop").hidden = false;
    requestAnimationFrame(() => {
      $("shade").style.transform = "";
      $("shade").classList.add("open");
      $("shade-backdrop").classList.add("on");
    });
    this.refresh();
  },

  hide() {
    this.open = false;
    $("shade").style.transform = "";
    $("shade").classList.remove("open");
    $("shade-backdrop").classList.remove("on");
    setTimeout(() => { if (!this.open) $("shade-backdrop").hidden = true; }, 300);
  },

  async power(action) {
    const reboot = action === "reboot";
    const ok = await confirmDialog({
      title: t(reboot ? "Перезагрузить панель?" : "Выключить панель?"),
      text: t(reboot ? "Панель будет недоступна около минуты." : "Включить её снова можно будет только кнопкой или по питанию."),
      ok: t(reboot ? "Перезагрузить" : "Выключить"),
      danger: !reboot,
    });
    if (!ok) return;
    this.hide();
    await api.post("/power", { action });
  },

  async action(name, el) {
    if (name === "settings" || name === "update") {
      this.hide();
      settings.open(name === "update" ? "system" : undefined);
    } else if (name === "theme") {
      // Плитку переключаем сразу, не дожидаясь ответа ядра.
      const next = THEMES[(this.themeIndex() + 1) % THEMES.length][0];
      Object.assign(state.config.ui, next);
      this.render();
      await api.post("/settings", { ui: next });
      await loadConfig();
      this.render();
    } else if (name === "dismiss" || name === "action" || name === "clear") {
      if (name === "action") await this.runAction(el.dataset.id);
      else await this.dismiss(name === "clear" ? "all" : el.dataset.id);
    } else if (name === "dnd") {
      await this.set({ dnd: !state.config.ui.dnd });
    } else if (name === "blank") {
      this.hide();
      dimmer.blank();
    } else if (name === "desk") {
      this.hide();
      desks.go(Number(el.dataset.index));
    } else if (name === "reboot" || name === "poweroff") {
      await this.power(name);
    } else if (name === "wifi" && this.wifi) {
      if (this.wifi.radio && !(await confirmWifiOff(this.wifi))) return;
      const enabled = !this.wifi.radio;
      this.wifi = { ...this.wifi, radio: enabled, connection: null };
      Object.assign(this.data.toggles.wifi, { on: enabled, ssid: null });
      this.render();
      await api.post("/wifi/radio", { enabled });
      await this.refresh();
    }
  },
};

{
  // Тянем шторку пальцем от верхнего края и закрываем свайпом вверх.
  let drag = null;
  const shadeEl = $("shade");
  const begin = (e, fromOpen) => {
    drag = { y: e.clientY, fromOpen, height: shadeEl.offsetHeight || 520 };
    shadeEl.style.transition = "none";
  };
  $("edge").addEventListener("pointerdown", (e) => {
    if (!shade.open) {
      shade.render();
      begin(e, false);
    }
  });
  // Ползунки и уведомления ведут свои жесты (shade.js), шторку за них не тянем.
  shadeEl.addEventListener("pointerdown", (e) => {
    if (!e.target.closest(".shade-slider, .shade-note")) begin(e, true);
  });
  window.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const dy = e.clientY - drag.y;
    const offset = drag.fromOpen ? Math.min(0, dy) : Math.min(0, dy - drag.height);
    shadeEl.style.transform = `translateY(${offset}px)`;
  });
  window.addEventListener("pointerup", (e) => {
    if (!drag) return;
    const dy = e.clientY - drag.y;
    shadeEl.style.transition = "";
    const wasOpen = drag.fromOpen;
    drag = null;
    if (!wasOpen) {
      // Свайп вниз или просто касание верхнего края.
      if (dy > 50 || Math.abs(dy) < 8) shade.show();
      else shadeEl.style.transform = "";
    } else if (dy < -40) {
      shade.hide();
    } else {
      shadeEl.style.transform = "";
    }
  });
  // Жест прервал браузер: возвращаем шторку на место.
  window.addEventListener("pointercancel", () => {
    if (!drag) return;
    drag = null;
    shadeEl.style.transition = "";
    shadeEl.style.transform = "";
  });
  shadeEl.addEventListener("click", (e) => {
    const b = e.target.closest("[data-shade]");
    if (b && !b.disabled) shade.action(b.dataset.shade, b).catch((err) => { console.error(err); shade.refresh(); });
  });
  // Касание, открывшее шторку, тут же даёт click по подложке: его пропускаем.
  $("shade-backdrop").addEventListener("click", () => {
    if (Date.now() - shade.openedAt > 400) shade.hide();
  });
}

// --- настройки и их изменения из админки ------------------------------------

async function loadConfig() {
  const config = await api.get("/config");
  // Язык сменился в админке: проще всего перерисовать всё с нуля.
  if (state.config && config.ui.language !== language()) return location.reload();
  if (!state.catalog) {
    await setLanguage(config.ui.language);
    translateDom();
    settings.renderFrame();
    state.catalog = await loadCatalog();
  }
  state.config = config;
  state.revision = config.revision;
  applyTheme();
  dimmer.configure(config.ui);
  sound.configure(config.ui);
  await desks.render(config, state.catalog.widgets);
  settings.onConfig(config);
}

// Долгий опрос: сервер держит запрос, пока не изменятся настройки или модули
// (или 20 секунд), поэтому изменения из админки видны на экране сразу.
async function pollRevision() {
  const theme = document.documentElement.dataset.theme;
  const wait = state.modules === undefined ? "" : `&wait=${state.revision}&modules=${state.modules}`;
  try {
    const { revision, build, update, progress, version, modules, sound: soundReq, module_updates: moduleUpdates, notes, alarms: alarmsRev, calendar: calendarRev } = await api.get(`/revision?theme=${theme}${wait}`);
    $("status-offline").hidden = true;
    if (alarmsRev !== undefined) setAlarmsRev(alarmsRev);
    if (calendarRev !== undefined) setCalendarRev(calendarRev);
    if (notes) shade.onNotes(notes);
    updateBanner.show(progress, version);
    notifySounds(update, progress, soundReq, moduleUpdates);
    state.updateAvailable = Boolean(update);
    state.moduleUpdates = moduleUpdates || 0;
    $("edge-dot").hidden = !update && !moduleUpdates;
    settings.setUpdateBadge(update);
    // После обновления сервис отдаёт новую версию: перезагружаем страницу,
    // чтобы подхватить новый фронтенд.
    if (state.build === null) state.build = build;
    else if (build !== state.build) return location.reload();
    // Поставили или удалили модуль: каталог виджетов поменялся.
    if (state.modules === undefined) state.modules = modules;
    else if (modules !== state.modules) return location.reload();
    if (revision !== state.revision) await loadConfig();
    return true;
  } catch {
    // Во время установки сервис перезапускается: это не обрыв связи.
    $("status-offline").hidden = updateBanner.offline();
    return false;
  }
}

// --- звуковые уведомления -------------------------------------------------------

setSound({ play: (name, opts) => sound.play(name, opts) });
enableNotify();

let soundSeq = null;
function notifySounds(update, progress, req, moduleUpdates) {
  // Проверка звука из админки: играем, даже если звук выключен.
  if (req) {
    if (soundSeq !== null && req.seq !== soundSeq) sound.play(req.name, { force: true });
    soundSeq = req.seq;
  }
  const ui = state.config?.ui;
  if (!ui?.sound_update) return;
  // Вышла новая версия: только при смене «нет» → «есть», не при загрузке.
  if (update && state.updateAvailable === false) sound.play(null, { source: "system" });
  // Появились обновления модулей (автоустановка выключена).
  if (moduleUpdates && state.moduleUpdates === 0) sound.play(null, { source: "system" });
  // Обновление закончилось. После установки страница перезагружается,
  // поэтому сыгранное помним в localStorage, чтобы не повторять.
  if (progress && progress.state !== "installing" && progress.finished_at) {
    let played = null;
    try { played = localStorage.getItem("wallhub-update-sound"); } catch {}
    if (played !== progress.finished_at) {
      try { localStorage.setItem("wallhub-update-sound", progress.finished_at); } catch {}
      sound.play(progress.result === "failed" ? "beep" : null, { source: "system" });
    }
  }
}

async function revisionLoop() {
  for (;;) {
    // После ошибки пауза, чтобы не долбить упавший сервис.
    if (!(await pollRevision())) await new Promise((r) => setTimeout(r, 3000));
  }
}

// --- звонок будильника и таймера ---------------------------------------------------

// Пока сервер держит звонок (ringing), поверх всего экран со временем и
// кнопками «Отложить» и «Выключить», звук повторяется. Звонит и в «Не
// беспокоить», и в тихие часы: будильник поставили сами.
const ringer = {
  el: null,
  timer: null,
  ringing: [],
  settings: {},

  update(st) {
    this.ringing = st.ringing || [];
    this.settings = st.settings || {};
    if (!this.ringing.length) return this.stop();
    this.render();
    if (dimmer.dimmed) dimmer.wake();
    else dimmer.reset();
    if (!this.timer) {
      this.ring();
      this.timer = setInterval(() => this.ring(), 3000);
    }
  },

  ring() {
    sound.play(this.settings.sound || "bell", { force: true, volume: this.settings.volume ?? 80 });
  },

  render() {
    if (!this.el) {
      this.el = document.createElement("div");
      this.el.className = "ring";
      this.el.addEventListener("click", (e) => this.onClick(e));
      document.body.appendChild(this.el);
    }
    const r = this.ringing[0];
    const alarm = r.kind === "alarm";
    const more = this.ringing.length > 1 ? `<div class="ring-more">${t("И ещё {n}", { n: this.ringing.length - 1 })}</div>` : "";
    this.el.innerHTML = `
      <div class="ring-icon">${icon(alarm ? "alarm" : "timer")}</div>
      <div class="ring-time">${alarm ? esc(r.time) : t("Время вышло")}</div>
      <div class="ring-label">${esc(r.label || (alarm ? t("Будильник") : t("Таймер {time}", { time: r.time })))}</div>
      ${more}
      <div class="ring-actions">
        ${alarm ? `<button class="btn ring-btn" data-ring="snooze">${t("Отложить на {n} мин", { n: this.settings.snooze || 10 })}</button>` : ""}
        <button class="btn btn-primary ring-btn" data-ring="dismiss">${t("Выключить звонок")}</button>
      </div>`;
  },

  async onClick(e) {
    const b = e.target.closest("[data-ring]");
    if (!b) return;
    const r = this.ringing[0];
    // Звук стихает сразу, не дожидаясь ответа сервера.
    this.stopSound();
    try {
      await alarmApi.ring(b.dataset.ring, r.key);
    } catch (err) {
      toast(err.message, "error");
    }
  },

  stopSound() {
    clearInterval(this.timer);
    this.timer = null;
  },

  stop() {
    this.stopSound();
    this.el?.remove();
    this.el = null;
  },
};

onAlarms((st) => ringer.update(st));

// --- ход обновления -----------------------------------------------------------

// Плашка внизу экрана: какой этап идёт, сколько прошло, чем закончилось.
// Нажатия не перехватывает, кроме готового результата: его можно смахнуть касанием.
// Успешное обновление скрывается само через 15 секунд, ошибка остаётся.
const UPDATE_STEPS = ["download", "install", "verify", "reboot"];
const UPDATE_OK_HIDE_MS = 15000;

const updateBanner = {
  el: $("upd-progress"),
  progress: null,
  version: "",
  dismissed: null,
  timer: null,
  hideTimer: null,
  hideFor: null,

  show(progress, version) {
    this.progress = progress || null;
    this.version = version || "";
    this.render();
  },

  // Связи нет: если шла установка, значит сервис перезапускается.
  offline() {
    if (this.progress?.state !== "installing") return false;
    this.progress = { ...this.progress, restarting: true };
    this.render();
    return true;
  },

  render() {
    const p = this.progress;
    if (!p || (p.state !== "installing" && p.finished_at === this.dismissed)) {
      this.el.hidden = true;
      clearInterval(this.timer);
      this.timer = null;
      return;
    }
    const running = p.state === "installing";
    const failed = !running && p.result === "failed";
    let title;
    if (running) title = p.phase === "rollback" ? t("Откат обновления") : (p.target ? t("Обновление до {version}", { version: p.target }) : t("Обновление"));
    else title = failed ? t("Обновление не удалось") : (p.message === "Возвращена прошлая версия" ? t("Возвращена прошлая версия") : t("Обновлено до {version}", { version: this.version }));
    const message = p.restarting ? t(p.phase === "reboot" ? "Перезагружаю панель…" : "Перезапускаю сервис…") : t(p.message || "", p.message_args);
    const step = UPDATE_STEPS.indexOf(p.phase);
    const steps = running && step >= 0
      ? `<div class="ub-steps">${UPDATE_STEPS.map((_, i) => `<i class="${i < step ? "done" : i === step ? "on" : ""}"></i>`).join("")}</div>`
      : "";
    this.el.className = `upd-progress ${running ? "running" : failed ? "failed" : "ok"}`;
    this.el.innerHTML = `
      <span class="ub-icon">${icon(running ? "refresh" : failed ? "info" : "check")}</span>
      <div class="ub-body">
        <div class="ub-title"><b></b><span class="ub-time"></span></div>
        <div class="ub-text"></div>
        ${steps}
      </div>`;
    this.el.querySelector("b").textContent = title;
    this.el.querySelector(".ub-text").textContent = running || failed ? message : t("Нажмите, чтобы скрыть");
    this.el.hidden = false;
    this.tick();
    if (running && !this.timer) this.timer = setInterval(() => this.tick(), 1000);
    if (!running) { clearInterval(this.timer); this.timer = null; }
    if (!running && !failed && this.hideFor !== p.finished_at) {
      clearTimeout(this.hideTimer);
      this.hideFor = p.finished_at;
      this.hideTimer = setTimeout(() => this.dismiss(), UPDATE_OK_HIDE_MS);
    }
  },

  dismiss() {
    if (this.progress?.state === "installing") return;
    this.dismissed = this.progress?.finished_at;
    this.render();
  },

  // Сколько длится установка: «1:23».
  tick() {
    const p = this.progress;
    const el = this.el.querySelector(".ub-time");
    if (!el || p?.state !== "installing" || !p.started_at) return;
    const s = Math.max(0, Math.round((Date.now() - Date.parse(p.started_at)) / 1000));
    el.textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  },
};

updateBanner.el.addEventListener("click", () => updateBanner.dismiss());

// --- затемнение при простое -------------------------------------------------

// Два шага: через dim_after_min экран затемняется до dim_level, через
// blank_after_min становится полностью чёрным (подсветку при этом гасит
// X-сервер через DPMS, см. deploy/display-sync.py). Первое касание после
// этого только будит экран и не нажимает кнопку под затемнением.
const dimmer = {
  timers: [],
  dimMs: 0,
  blankMs: 0,
  level: 80,
  floor: 0,
  dimmed: false,

  configure(ui) {
    this.dimMs = ui.dim_after_min * 60 * 1000;
    this.blankMs = ui.blank_after_min * 60 * 1000;
    this.level = ui.dim_level;
    this.reset();
  },

  // Затемнение при простое не должно быть слабее ночного приглушения.
  setFloor(percent) {
    this.floor = percent;
  },

  reset() {
    this.timers.forEach(clearTimeout);
    this.timers = [];
    if (this.dimMs > 0) this.timers.push(setTimeout(() => this.dim(Math.max(this.level, this.floor)), this.dimMs));
    if (this.blankMs > 0) this.timers.push(setTimeout(() => this.dim(100), this.blankMs));
  },

  dim(percent) {
    this.dimmed = true;
    const el = $("dim");
    el.style.setProperty("--dim", percent / 100);
    el.hidden = false;
    requestAnimationFrame(() => el.classList.add("on"));
  },

  // «Погасить» из шторки: чёрный экран до первого касания.
  blank() {
    this.timers.forEach(clearTimeout);
    this.timers = [];
    // Касание, которым нажали кнопку, ещё не закончилось: ждём его конца.
    setTimeout(() => this.dim(100), 300);
  },

  wake() {
    this.dimmed = false;
    const el = $("dim");
    el.classList.remove("on");
    setTimeout(() => { if (!this.dimmed) el.hidden = true; }, 600);
    this.reset();
  },
};

// Любое касание сбрасывает таймер. Касание затемнённого экрана только
// будит его и не доходит до кнопок под затемнением.
window.addEventListener("pointerdown", (e) => {
  if (dimmer.dimmed) {
    e.preventDefault();
    e.stopPropagation();
    dimmer.wake();
    return;
  }
  dimmer.reset();
  desks.touched();
}, true);
window.addEventListener("keydown", () => (dimmer.dimmed ? dimmer.wake() : dimmer.reset()), true);
$("dim").addEventListener("click", (e) => e.stopPropagation());

// Киоск: никаких контекстных меню, выделения текста и жестов масштабирования.
// Без сенсора правая кнопка мыши открывает настройки, как долгое нажатие.
window.addEventListener("contextmenu", (e) => {
  e.preventDefault();
  if (mouseMode && $("settings").hidden) settings.open();
});
window.addEventListener("wheel", (e) => { if (e.ctrlKey) e.preventDefault(); }, { passive: false });

// --- экран без сенсора ------------------------------------------------------------

// Управление мышью: колесо листает столы, правая кнопка открывает настройки,
// верхний край со шторкой подсвечивается.
let mouseMode = false;
{
  let lastWheel = 0;
  $("desktops").addEventListener("wheel", (e) => {
    if (!mouseMode || e.ctrlKey) return;
    const delta = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY;
    if (Math.abs(delta) < 10 || e.timeStamp - lastWheel < 400) return;
    lastWheel = e.timeStamp;
    desks.go(desks.index + (delta > 0 ? 1 : -1));
  }, { passive: true });
}

// Киоск сообщает ядру размер экрана: по нему ядро выбирает масштаб интерфейса,
// а админка рисует холст редактора столов.
function reportDisplay() {
  api.post("/display/report", {
    screen_w: screen.width, screen_h: screen.height, dpr: devicePixelRatio,
    view_w: innerWidth, view_h: innerHeight,
  }).catch(() => {});
}

async function detectInput() {
  try {
    const info = await api.get("/display");
    mouseMode = !info.touch;
  } catch {
    mouseMode = false;
  }
  document.documentElement.classList.toggle("mouse", mouseMode);
}

// --- запуск -------------------------------------------------------------------

const settings = new Settings($("settings"), {
  save: async (patch) => {
    await api.post("/settings", { ui: patch });
    await loadConfig();
  },
});

async function start() {
  try {
    await loadConfig();
  } catch {
    // Бэкенд ещё не поднялся: пробуем снова.
    $("status-offline").hidden = false;
    setTimeout(start, 2000);
    return;
  }
  // Первый запуск: сразу предлагаем выбрать Wi‑Fi сеть.
  if (!state.config.setup.wifi_done) settings.openSetup();
  revisionLoop();
  shade.start();
  setInterval(applyTheme, 30000);
  reportDisplay();
  window.addEventListener("resize", () => {
    clearTimeout(reportDisplay.timer);
    reportDisplay.timer = setTimeout(reportDisplay, 1000);
  });
  detectInput();
}

start();
