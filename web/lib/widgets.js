// Отрисовка столов и виджетов. Общая для экрана панели и превью в админке,
// поэтому в редакторе виджеты выглядят ровно так же, как на панели.

import { api } from "/lib/api.js";

const modules = new Map();

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
  el.classList.toggle("widget-card", Boolean(meta && meta.card));
  try {
    if (!meta) throw new Error("нет в каталоге");
    const mod = await loadModule(module);
    const mount = mod.widgets && mod.widgets[id];
    if (!mount) throw new Error("модуль не экспортирует виджет");
    return mount(el, { options: w.options || {}, api }) || {};
  } catch (e) {
    console.error("widget failed", w.type, e);
    el.innerHTML = `<div class="widget-error">Виджет «${meta ? meta.title : w.type}» не загрузился</div>`;
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
  const { widgets, providers } = await api.get("/catalog");
  return { widgets: Object.fromEntries(widgets.map((w) => [w.id, w])), providers };
}
