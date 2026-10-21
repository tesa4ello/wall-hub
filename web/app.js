// Оболочка киоска: тема, статусная строка, модули, затемнение, настройки.

import { api } from "/lib/api.js";
import { icon } from "/lib/icons.js";
import { themeFor } from "/lib/theme.js";
import { Settings } from "/settings.js";

const state = {
  config: null,
  revision: -1,
  modulesKey: "",
  mounted: [],
};

const $ = (id) => document.getElementById(id);

// --- тема -----------------------------------------------------------------

function applyTheme() {
  if (!state.config) return;
  document.documentElement.dataset.theme = themeFor(state.config.ui, new Date());
}

// --- модули ---------------------------------------------------------------

async function mountModules() {
  const enabled = state.config.modules.filter((m) => m.enabled);
  const key = JSON.stringify(enabled);
  if (key === state.modulesKey) return;
  state.modulesKey = key;

  for (const m of state.mounted) m.instance?.destroy?.();
  state.mounted = [];
  const host = $("modules");
  host.innerHTML = "";
  host.dataset.count = enabled.length;

  for (const m of enabled) {
    const el = document.createElement("section");
    el.className = `module module-${m.id}`;
    host.appendChild(el);
    try {
      ensureStyle(`/modules/${m.id}/module.css`);
      const mod = await import(`/modules/${m.id}/module.js`);
      const instance = mod.mount(el, { options: m.options || {}, api });
      state.mounted.push({ id: m.id, instance });
    } catch (e) {
      console.error("module failed", m.id, e);
      el.innerHTML = `<div class="module-error">Модуль «${m.id}» не загрузился</div>`;
    }
  }
}

function ensureStyle(href) {
  if (document.querySelector(`link[href="${href}"]`)) return;
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = href;
  document.head.appendChild(link);
}

// --- статусная строка -----------------------------------------------------

function renderStatus(items) {
  const host = $("status-items");
  host.innerHTML = items
    .map((it) => `<div class="status-item" data-level="${it.level || "ok"}" title="">${icon(it.icon)}<span></span></div>`)
    .join("");
  // Текст вставляем через textContent: он приходит из системы (имена сетей и т. п.).
  host.querySelectorAll(".status-item").forEach((el, i) => {
    el.querySelector("span").textContent = items[i].text;
    el.title = items[i].title || "";
  });
}

async function pollStatus() {
  try {
    const data = await api.get("/status");
    renderStatus(data.items);
    $("status-offline").hidden = true;
  } catch {
    $("status-offline").hidden = false;
  }
}

// --- настройки и их изменения из админки ------------------------------------

async function loadConfig() {
  state.config = await api.get("/config");
  state.revision = state.config.revision;
  applyTheme();
  dimmer.configure(state.config.ui);
  await mountModules();
  settings.onConfig(state.config);
}

async function pollRevision() {
  try {
    const { revision } = await api.get("/revision");
    if (revision !== state.revision) await loadConfig();
  } catch {
    // Сервис перезапускается: статусная строка покажет «нет связи».
  }
}

// --- затемнение при простое -------------------------------------------------

const dimmer = {
  timer: null,
  afterMs: 0,
  level: 80,
  dimmed: false,

  configure(ui) {
    this.afterMs = ui.dim_after_min * 60 * 1000;
    this.level = ui.dim_level;
    $("dim").style.setProperty("--dim", this.level / 100);
    this.reset();
  },

  reset() {
    clearTimeout(this.timer);
    if (this.afterMs > 0) this.timer = setTimeout(() => this.dim(), this.afterMs);
  },

  dim() {
    this.dimmed = true;
    $("dim").hidden = false;
    requestAnimationFrame(() => $("dim").classList.add("on"));
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

$("open-settings").innerHTML = icon("gear");
$("open-settings").addEventListener("click", () => settings.open());

async function start() {
  try {
    await loadConfig();
  } catch {
    // Бэкенд ещё не поднялся: пробуем снова.
    $("status-offline").hidden = false;
    setTimeout(start, 2000);
    return;
  }
  pollStatus();
  setInterval(pollStatus, 2000);
  setInterval(pollRevision, 3000);
  setInterval(applyTheme, 30000);
}

start();
