// Оболочка киоска: тема, рабочие столы с виджетами, шторка, затемнение,
// настройки.

import { api } from "/lib/api.js";
import { language, setLanguage, t, translateDom } from "/lib/i18n.js";
import { icon } from "/lib/icons.js";
import { isNight, themeFor } from "/lib/theme.js";
import { destroyAll, loadCatalog, renderDesktop } from "/lib/widgets.js";
import { Settings } from "/settings.js";

const state = {
  config: null,
  catalog: null,
  revision: -1,
  build: null,
  updateAvailable: false,
};

const $ = (id) => document.getElementById(id);

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
    clearTimeout(applyTheme.timer);
    applyTheme.timer = setTimeout(() => root.classList.remove("theme-anim"), 1600);
  }
  // Ночью экран постоянно приглушён, чтобы не светил в глаза.
  const night = isNight(ui, now) ? ui.night_dim : 0;
  $("night").style.opacity = night / 100;
  dimmer.setFloor(night);
}

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
  touched() {
    clearTimeout(this.homeTimer);
    const minutes = state.config ? state.config.layout.home_after_min : 0;
    if (minutes > 0 && this.index > 0) this.homeTimer = setTimeout(() => this.go(0), minutes * 60000);
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
    if (Math.abs(dx) > 10 || Math.abs(dy) > 10) clearTimeout(longTimer);
    if (!start.swiping && Math.abs(dx) > 16 && Math.abs(dx) > Math.abs(dy) && desks.count > 1) start.swiping = true;
    if (start.swiping) {
      const track = $("desk-track");
      track.style.transition = "none";
      track.style.transform = `translateX(calc(${-desks.index * 100}% + ${dx}px))`;
    }
  });
  const end = (e) => {
    clearTimeout(longTimer);
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

// Свайп вниз от верхнего края открывает шторку: Wi‑Fi, тема, настройки и
// уведомление о новой версии.
const THEMES = [
  [{ theme_mode: "manual", theme: "dark" }, "moon", "Тёмная"],
  [{ theme_mode: "manual", theme: "light" }, "sun", "Светлая"],
  [{ theme_mode: "schedule" }, "clock", "По времени"],
  // Подписи переводятся при отрисовке.
];

const shade = {
  open: false,
  wifi: null,

  themeIndex() {
    const ui = state.config.ui;
    if (ui.theme_mode === "manual") return ui.theme === "light" ? 1 : 0;
    return 2;
  },

  render() {
    const ui = state.config.ui;
    const theme = THEMES[this.themeIndex()];
    const themeLabel = t(ui.theme_mode === "sun" ? "По солнцу" : theme[2]);
    const w = this.wifi;
    const wifiSub = !w ? "…" : !w.radio ? t("Выключен") : w.connection || t("Не подключён");
    $("shade").innerHTML = `
      <div class="shade-tiles">
        <button class="tile ${w && w.radio ? "on" : ""}" data-shade="wifi" ${w && w.available ? "" : "disabled"}>
          ${icon("wifi")}<span class="tile-title">Wi‑Fi</span><span class="tile-sub"></span>
        </button>
        <button class="tile" data-shade="theme">${icon(ui.theme_mode === "sun" ? "sun" : theme[1])}<span class="tile-title">${t("Тема")}</span><span class="tile-sub">${themeLabel}</span></button>
        <button class="tile" data-shade="settings">${icon("gear")}<span class="tile-title">${t("Настройки")}</span><span class="tile-sub">${t("Все параметры")}</span></button>
      </div>
      ${state.updateAvailable ? `
      <button class="shade-update" data-shade="update">${icon("refresh")}<span>${t("Доступна новая версия")}</span><b>${t("Подробнее")}</b></button>` : ""}
      <div class="shade-handle"></div>`;
    // Имя сети вставляем через textContent: оно приходит извне.
    $("shade").querySelector('[data-shade="wifi"] .tile-sub').textContent = wifiSub;
  },

  async show() {
    this.open = true;
    this.render();
    $("shade-backdrop").hidden = false;
    requestAnimationFrame(() => {
      $("shade").style.transform = "";
      $("shade").classList.add("open");
      $("shade-backdrop").classList.add("on");
    });
    try {
      this.wifi = await api.get("/wifi");
    } catch {
      this.wifi = null;
    }
    if (this.open) this.render();
  },

  hide() {
    this.open = false;
    $("shade").style.transform = "";
    $("shade").classList.remove("open");
    $("shade-backdrop").classList.remove("on");
    setTimeout(() => { if (!this.open) $("shade-backdrop").hidden = true; }, 300);
  },

  async action(name) {
    if (name === "settings" || name === "update") {
      this.hide();
      settings.open(name === "update" ? "system" : undefined);
    } else if (name === "theme") {
      const next = THEMES[(this.themeIndex() + 1) % THEMES.length][0];
      await api.post("/settings", { ui: next });
      await loadConfig();
      this.render();
    } else if (name === "wifi" && this.wifi) {
      await api.post("/wifi/radio", { enabled: !this.wifi.radio });
      this.wifi = await api.get("/wifi");
      this.render();
    }
  },
};

{
  // Тянем шторку пальцем от верхнего края и закрываем свайпом вверх.
  let drag = null;
  const shadeEl = $("shade");
  const begin = (e, fromOpen) => {
    drag = { y: e.clientY, fromOpen, height: shadeEl.offsetHeight || 220 };
    shadeEl.style.transition = "none";
  };
  $("edge").addEventListener("pointerdown", (e) => {
    if (!shade.open) {
      shade.render();
      begin(e, false);
    }
  });
  shadeEl.addEventListener("pointerdown", (e) => begin(e, true));
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
      if (dy > 50) shade.show();
      else shadeEl.style.transform = "";
    } else if (dy < -40) {
      shade.hide();
    } else {
      shadeEl.style.transform = "";
    }
  });
  shadeEl.addEventListener("click", (e) => {
    const b = e.target.closest("[data-shade]");
    if (b && !b.disabled) shade.action(b.dataset.shade).catch((err) => console.error(err));
  });
  $("shade-backdrop").addEventListener("click", () => shade.hide());
}

// --- настройки и их изменения из админки ------------------------------------

async function loadConfig() {
  const config = await api.get("/config");
  // Язык сменился в админке: проще всего перерисовать всё с нуля.
  if (state.config && config.ui.language !== language()) return location.reload();
  if (!state.catalog) {
    await setLanguage(config.ui.language);
    translateDom();
    state.catalog = await loadCatalog();
  }
  state.config = config;
  state.revision = config.revision;
  applyTheme();
  dimmer.configure(config.ui);
  await desks.render(config, state.catalog.widgets);
  settings.onConfig(config);
}

async function pollRevision() {
  try {
    const { revision, build, update } = await api.get(`/revision?theme=${document.documentElement.dataset.theme}`);
    $("status-offline").hidden = true;
    state.updateAvailable = Boolean(update);
    $("edge-dot").hidden = !update;
    settings.setUpdateBadge(update);
    // После обновления сервис отдаёт новую версию: перезагружаем страницу,
    // чтобы подхватить новый фронтенд.
    if (state.build === null) state.build = build;
    else if (build !== state.build) return location.reload();
    if (revision !== state.revision) await loadConfig();
  } catch {
    $("status-offline").hidden = false;
  }
}

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
window.addEventListener("contextmenu", (e) => e.preventDefault());
window.addEventListener("wheel", (e) => { if (e.ctrlKey) e.preventDefault(); }, { passive: false });

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
  pollRevision();
  setInterval(pollRevision, 3000);
  setInterval(applyTheme, 30000);
}

start();
