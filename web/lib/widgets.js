// Отрисовка столов и виджетов. Общая для экрана панели и превью в админке,
// поэтому в редакторе виджеты выглядят ровно так же, как на панели.

import { api } from "/lib/api.js";
import { addTranslations, t } from "/lib/i18n.js";

const modules = new Map();

// Звук для виджетов: ctx.sound.play(name?). На панели его подключает app.js,
// в превью админки он молчит. play(name, {source}) — source: id модуля, чтобы
// без имени звучал звук, выбранный для этого модуля.
let sound = { play: () => false };
export function setSound(player) {
  sound = player;
}

// Уведомление от виджета: notify({title, text, level, key, action, sound}).
// Текст модуль переводит сам через t(). На панели — всплывашка и шторка,
// в превью админки ничего не делает.
let notifyEnabled = false;
export function enableNotify() {
  notifyEnabled = true;
}

function notifier(module) {
  return (note) => {
    if (!notifyEnabled) return Promise.resolve(null);
    return api.post("/notifications", { ...note, module });
  };
}

function ensureStyle(href) {
  if (document.querySelector(`link[href="${href}"]`)) return;
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = href;
  document.head.appendChild(link);
}

function loadModule(name) {
  if (!modules.has(name)) {
    ensureStyle(`/modules/${name}/module.css`);
    modules.set(name, import(`/modules/${name}/module.js`));
  }
  return modules.get(name);
}

// Сетка стола: cols × rows одинаковых клеток.
export function setupGrid(el, layout) {
  el.style.gridTemplateColumns = `repeat(${layout.cols}, minmax(0, 1fr))`;
  el.style.gridTemplateRows = `repeat(${layout.rows}, minmax(0, 1fr))`;
}

export function placeWidget(el, w) {
  el.style.gridColumn = `${w.x + 1} / span ${w.w}`;
  el.style.gridRow = `${w.y + 1} / span ${w.h}`;
}

// Монтирует один виджет в el. Возвращает объект с destroy().
export async function mountWidget(el, w, catalog) {
  const meta = catalog[w.type];
  const [module, id] = w.type.split("/");
  el.classList.add("widget", `widget-${module}-${id}`);
  // Подложку можно включить или убрать у конкретного виджета (w.card).
  el.classList.toggle("widget-card", Boolean(w.card ?? (meta && meta.card)));
  try {
    if (!meta) throw new Error("нет в каталоге");
    const mod = await loadModule(module);
    const mount = mod.widgets && mod.widgets[id];
    if (!mount) throw new Error("модуль не экспортирует виджет");
    const ctx = {
      options: w.options || {},
      api,
      sound: { play: (name) => sound.play(name, { source: module }) },
      notify: notifier(module),
    };
    return mount(el, ctx) || {};
  } catch (e) {
    console.error("widget failed", w.type, e);
    el.innerHTML = `<div class="widget-error">${t("Виджет «{title}» не загрузился", { title: meta ? t(meta.title) : w.type })}</div>`;
    return {};
  }
}

// Рисует стол целиком. Возвращает список экземпляров для destroyAll().
export async function renderDesktop(el, desktop, layout, catalog) {
  setupGrid(el, layout);
  el.innerHTML = "";
  const instances = [];
  for (const w of desktop.widgets) {
    const cell = document.createElement("section");
    placeWidget(cell, w);
    el.appendChild(cell);
    instances.push(await mountWidget(cell, w, catalog));
  }
  return instances;
}

export function destroyAll(instances) {
  for (const inst of instances) {
    try {
      inst.destroy?.();
    } catch (e) {
      console.error(e);
    }
  }
}

export async function loadCatalog() {
  const { widgets, providers, i18n, modules } = await api.get("/catalog");
  // Переводы модулей (названия виджетов, настроек и тексты внутри виджетов).
  addTranslations(i18n);
  return { widgets: Object.fromEntries(widgets.map((w) => [w.id, w])), providers, modules };
}
