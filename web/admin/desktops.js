// Редактор рабочих столов в админке.
//
// Превью — настоящий экран панели 1024×600 в масштабе: виджеты монтируются
// тем же кодом, что и на панели (/lib/widgets.js). Поверх лежит слой
// с рамками: их можно таскать по сетке и тянуть за угол. Любое изменение
// сразу сохраняется и само появляется на панели.

import { t } from "/lib/i18n.js";
import { icon } from "/lib/icons.js";
import { themeFor } from "/lib/theme.js";
import { destroyAll, mountWidget, placeWidget, setupGrid } from "/lib/widgets.js";

const SCREEN = { w: 1024, h: 600 };
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const newId = (prefix) => prefix + Math.random().toString(36).slice(2, 10);
const clone = (v) => JSON.parse(JSON.stringify(v));
// Без своего имени стол называется «Стол N» на языке панели.
const deskName = (d, i) => d.name || t("Стол {n}", { n: i + 1 });

export class DesktopEditor {
  constructor(host, { config, catalog, save, toast, confirm }) {
    this.host = host;
    this.layout = config.layout;
    this.ui = config.ui;
    this.desktops = clone(config.desktops);
    this.homeAfterMin = config.layout.home_after_min;
    this.catalog = catalog.widgets;
    this.providers = catalog.providers;
    this.saveFn = save;
    this.toast = toast;
    this.confirm = confirm;
    this.index = 0;
    this.selected = null;
    this.instances = [];
    this.drag = null;

    this.onResize = () => this.fit();
    this.onMove = (e) => this.pointerMove(e);
    this.onUp = (e) => this.pointerUp(e);
    window.addEventListener("resize", this.onResize);
    window.addEventListener("pointermove", this.onMove);
    window.addEventListener("pointerup", this.onUp);
    host.addEventListener("click", (e) => this.click(e));
    host.addEventListener("change", (e) => this.change(e));
    host.addEventListener("pointerdown", (e) => this.pointerDown(e));
    this.render();
  }

  destroy() {
    destroyAll(this.instances);
    window.removeEventListener("resize", this.onResize);
    window.removeEventListener("pointermove", this.onMove);
    window.removeEventListener("pointerup", this.onUp);
  }

  get desk() {
    return this.desktops[this.index];
  }

  widget(id) {
    return this.desk.widgets.find((w) => w.id === id);
  }

  limits(meta) {
    const size = meta.size || {};
    const [minW, minH] = size.min || [1, 1];
    const [maxW, maxH] = size.max || [this.layout.cols, this.layout.rows];
    return { minW, minH, maxW: Math.min(maxW, this.layout.cols), maxH: Math.min(maxH, this.layout.rows) };
  }

  // Свободно ли место для прямоугольника (кроме виджета skipId).
  fits(rect, skipId) {
    const { cols, rows } = this.layout;
    if (rect.x < 0 || rect.y < 0 || rect.x + rect.w > cols || rect.y + rect.h > rows) return false;
    return this.desk.widgets.every((w) => w.id === skipId
      || rect.x >= w.x + w.w || w.x >= rect.x + rect.w || rect.y >= w.y + w.h || w.y >= rect.y + rect.h);
  }

  // --- сохранение -------------------------------------------------------------

  async save() {
    try {
      this.desktops = await this.saveFn(clone(this.desktops), this.homeAfterMin);
      this.index = Math.min(this.index, this.desktops.length - 1);
    } catch {
      // Ошибку показал вызывающий код; возвращаем то, что реально сохранено.
    }
    this.render();
  }

  // --- отрисовка --------------------------------------------------------------

  render() {
    destroyAll(this.instances);
    this.instances = [];
    const tabs = this.desktops.map((d, i) => `
      <button class="desk-tab ${i === this.index ? "active" : ""}" data-desk="${i}">${esc(deskName(d, i))}</button>`).join("");
    this.host.innerHTML = `
      <div class="desk-tabs">${tabs}
        <button class="desk-tab add" data-desk-add ${this.desktops.length >= 10 ? "disabled" : ""}>${icon("plus")} ${t("Стол")}</button>
      </div>
      <div class="editor">
        <aside class="palette">${this.renderPalette()}</aside>
        <div class="canvas-wrap"><div class="canvas" data-theme="${themeFor(this.ui, new Date())}">
          <div class="desk preview"></div>
          <div class="desk overlay"></div>
        </div></div>
        <aside class="props">${this.selected && this.widget(this.selected) ? this.renderWidgetProps() : this.renderDeskProps()}</aside>
      </div>`;
    this.canvas = this.host.querySelector(".canvas");
    this.overlay = this.host.querySelector(".overlay");
    const preview = this.host.querySelector(".preview");
    setupGrid(preview, this.layout);
    setupGrid(this.overlay, this.layout);
    for (const w of this.desk.widgets) {
      const cell = document.createElement("section");
      placeWidget(cell, w);
      preview.appendChild(cell);
      mountWidget(cell, w, this.catalog).then((inst) => this.instances.push(inst));
      const box = document.createElement("div");
      box.className = "box" + (w.id === this.selected ? " selected" : "");
      box.dataset.box = w.id;
      placeWidget(box, w);
      box.innerHTML = `<span class="box-label">${esc(t((this.catalog[w.type] || {}).title || w.type))}</span><span class="box-resize" data-resize="${w.id}"></span>`;
      this.overlay.appendChild(box);
    }
    if (!this.desk.widgets.length) {
      this.overlay.insertAdjacentHTML("beforeend", `<div class="canvas-empty">${t("Пустой стол: добавьте виджет из списка слева")}</div>`);
    }
    this.fit();
  }

  // Холст всегда 1024×600, в админке он просто масштабируется под ширину.
  fit() {
    const wrap = this.host.querySelector(".canvas-wrap");
    if (!wrap) return;
    const scale = Math.min(1, wrap.clientWidth / SCREEN.w);
    this.scale = scale;
    this.canvas.style.transform = `scale(${scale})`;
    wrap.style.height = `${SCREEN.h * scale}px`;
  }

  renderPalette() {
    const groups = {};
    for (const meta of Object.values(this.catalog)) {
      (groups[meta.module_title] ||= []).push(meta);
    }
    return Object.entries(groups).map(([title, items]) => `
      <div class="palette-group">${esc(t(title))}</div>
      ${items.map((m) => `
        <button class="palette-item" data-add="${esc(m.id)}" title="${esc(t(m.description || ""))}">
          ${icon(m.icon || "grid")}<span>${esc(t(m.title))}</span>${icon("plus", "icon tiny")}
        </button>`).join("")}`).join("");
  }

  renderDeskProps() {
    const last = this.desktops.length === 1;
    return `
      <div class="props-title">${t("Стол")}</div>
      <label class="prop"><span>${t("Название")}</span><input data-desk-name value="${esc(this.desk.name)}" placeholder="${esc(deskName({}, this.index))}" maxlength="40"></label>
      <div class="prop-row">
        <button class="btn btn-small" data-desk-move="-1" ${this.index === 0 ? "disabled" : ""}>← ${t("Левее")}</button>
        <button class="btn btn-small" data-desk-move="1" ${this.index === this.desktops.length - 1 ? "disabled" : ""}>${t("Правее")} →</button>
      </div>
      <button class="btn btn-small btn-danger" data-desk-delete ${last ? "disabled" : ""}>${icon("trash")} ${t("Удалить стол")}</button>
      <div class="props-title">${t("Все столы")}</div>
      <label class="prop"><span>${t("Возвращаться на первый стол через, мин")}<small>${t("0 — не возвращаться")}</small></span>
        <input type="number" min="0" max="240" data-home value="${this.homeAfterMin}">
      </label>
      <p class="muted small">${t("Нажмите на виджет на холсте, чтобы изменить его настройки. Перетаскивайте виджеты мышью, размер меняется за правый нижний угол.")}</p>`;
  }

  renderWidgetProps() {
    const w = this.widget(this.selected);
    const meta = this.catalog[w.type] || { title: w.type, options: [] };
    const lim = this.limits(meta);
    const fields = (meta.options || []).map((o) => this.renderOption(o, w.options[o.key])).join("");
    return `
      <div class="props-title">${esc(t(meta.title))}<button class="btn btn-small" data-deselect>${t("Готово")}</button></div>
      <p class="muted small">${esc(t(meta.description || ""))}</p>
      <div class="prop-row">
        <label class="prop"><span>${t("Ширина")}</span><input type="number" min="${lim.minW}" max="${lim.maxW}" data-size="w" value="${w.w}"></label>
        <label class="prop"><span>${t("Высота")}</span><input type="number" min="${lim.minH}" max="${lim.maxH}" data-size="h" value="${w.h}"></label>
      </div>
      <label class="check prop-check"><input type="checkbox" data-card ${w.card ?? meta.card ? "checked" : ""}> ${t("Плитка под виджетом")}</label>
      ${fields}
      <button class="btn btn-small btn-danger" data-remove>${icon("trash")} ${t("Убрать со стола")}</button>`;
  }

  renderOption(o, value) {
    const key = esc(o.key);
    if (o.type === "bool") {
      return `<label class="check prop-check"><input type="checkbox" data-opt="${key}" ${value ? "checked" : ""}> ${esc(t(o.title))}</label>`;
    }
    if (o.type === "select") {
      return `<label class="prop"><span>${esc(t(o.title))}</span><select data-opt="${key}">
        ${o.choices.map(([v, l]) => `<option value="${esc(v)}" ${v === value ? "selected" : ""}>${esc(t(l))}</option>`).join("")}
      </select></label>`;
    }
    if (o.type === "number") {
      return `<label class="prop"><span>${esc(t(o.title))}</span><input type="number" data-opt="${key}" data-num
        ${o.min != null ? `min="${o.min}"` : ""} ${o.max != null ? `max="${o.max}"` : ""} value="${esc(value ?? "")}"></label>`;
    }
    if (o.type === "text") {
      return `<label class="prop"><span>${esc(t(o.title))}</span><input data-opt="${key}" value="${esc(value ?? "")}"></label>`;
    }
    if (o.type === "providers") {
      // Выбранные пункты в своём порядке, затем остальные.
      const chosen = (value || []).filter((id) => this.providers.some((p) => p.id === id));
      const order = [...chosen, ...this.providers.map((p) => p.id).filter((id) => !chosen.includes(id))];
      const title = (id) => t((this.providers.find((p) => p.id === id) || {}).title || id);
      return `<div class="prop"><span>${esc(t(o.title))}</span><div class="prov-list">
        ${order.map((id, i) => `
          <div class="prov">
            <label class="check"><input type="checkbox" data-prov="${key}" data-id="${esc(id)}" ${chosen.includes(id) ? "checked" : ""}><span>${esc(title(id))}</span></label>
            <span class="order">
              <button class="btn btn-small" data-prov-move="${key}" data-id="${esc(id)}" data-dir="-1" ${i === 0 || !chosen.includes(id) ? "disabled" : ""}>↑</button>
              <button class="btn btn-small" data-prov-move="${key}" data-id="${esc(id)}" data-dir="1" ${i >= chosen.length - 1 || !chosen.includes(id) ? "disabled" : ""}>↓</button>
            </span>
          </div>`).join("")}
      </div></div>`;
    }
    return "";
  }

  // --- действия ---------------------------------------------------------------

  async click(e) {
    const b = e.target.closest("button");
    if (!b || b.disabled) return;
    const d = b.dataset;
    if (d.desk !== undefined) {
      this.index = Number(d.desk);
      this.selected = null;
      this.render();
    } else if ("deskAdd" in d) {
      this.desktops.push({ id: newId("d"), name: "", widgets: [] });
      this.index = this.desktops.length - 1;
      this.selected = null;
      await this.save();
    } else if (d.deskMove) {
      const to = this.index + Number(d.deskMove);
      const [desk] = this.desktops.splice(this.index, 1);
      this.desktops.splice(to, 0, desk);
      this.index = to;
      await this.save();
    } else if ("deskDelete" in d) {
      if (await this.confirm({ title: t("Удалить «{name}»?", { name: deskName(this.desk, this.index) }), text: t("Виджеты с этого стола пропадут."), ok: t("Удалить"), danger: true })) {
        this.desktops.splice(this.index, 1);
        this.index = Math.max(0, this.index - 1);
        await this.save();
      }
    } else if (d.add) {
      this.addWidget(d.add);
    } else if ("deselect" in d) {
      this.selected = null;
      this.render();
    } else if ("remove" in d) {
      this.desk.widgets = this.desk.widgets.filter((w) => w.id !== this.selected);
      this.selected = null;
      await this.save();
    } else if (d.provMove) {
      const w = this.widget(this.selected);
      const list = [...(w.options[d.provMove] || [])];
      const i = list.indexOf(d.id);
      const j = i + Number(d.dir);
      if (i < 0 || j < 0 || j >= list.length) return;
      [list[i], list[j]] = [list[j], list[i]];
      w.options = { ...w.options, [d.provMove]: list };
      await this.save();
    }
  }

  async change(e) {
    const el = e.target;
    const w = this.selected && this.widget(this.selected);
    if ("deskName" in el.dataset) {
      this.desk.name = el.value.trim();
      await this.save();
    } else if ("home" in el.dataset) {
      const v = parseInt(el.value, 10);
      if (Number.isNaN(v)) return;
      this.homeAfterMin = Math.max(0, Math.min(240, v));
      await this.save();
    } else if (w && el.dataset.size) {
      const rect = { ...w, [el.dataset.size]: parseInt(el.value, 10) || 1 };
      const lim = this.limits(this.catalog[w.type] || {});
      rect.w = Math.max(lim.minW, Math.min(lim.maxW, rect.w));
      rect.h = Math.max(lim.minH, Math.min(lim.maxH, rect.h));
      if (!this.fits(rect, w.id)) {
        this.toast(t("Не хватает места: сдвиньте соседние виджеты"), "error");
        return this.render();
      }
      Object.assign(w, { w: rect.w, h: rect.h });
      await this.save();
    } else if (w && "card" in el.dataset) {
      const meta = this.catalog[w.type] || {};
      if (el.checked === Boolean(meta.card)) delete w.card;
      else w.card = el.checked;
      await this.save();
    } else if (w && el.dataset.opt) {
      let value = el.type === "checkbox" ? el.checked : el.value;
      if ("num" in el.dataset) value = parseFloat(value);
      w.options = { ...w.options, [el.dataset.opt]: value };
      await this.save();
    } else if (w && el.dataset.prov) {
      const key = el.dataset.prov;
      const list = (w.options[key] || []).filter((id) => id !== el.dataset.id);
      if (el.checked) list.push(el.dataset.id);
      w.options = { ...w.options, [key]: list };
      await this.save();
    }
  }

  // Новый виджет встаёт в первое свободное место: сначала нужного размера,
  // при нехватке — минимального.
  async addWidget(type) {
    const meta = this.catalog[type];
    const lim = this.limits(meta);
    const [dw, dh] = (meta.size && meta.size.default) || [lim.minW, lim.minH];
    const sizes = [[Math.min(dw, lim.maxW), Math.min(dh, lim.maxH)], [lim.minW, lim.minH]];
    for (const [w, h] of sizes) {
      for (let y = 0; y + h <= this.layout.rows; y++) {
        for (let x = 0; x + w <= this.layout.cols; x++) {
          if (this.fits({ x, y, w, h })) {
            const options = Object.fromEntries((meta.options || []).map((o) => [o.key, clone(o.default ?? null)]));
            const widget = { id: newId("w"), type, x, y, w, h, options };
            this.desk.widgets.push(widget);
            this.selected = widget.id;
            return this.save();
          }
        }
      }
    }
    this.toast(t("На столе нет места для этого виджета"), "error");
  }

  // --- перетаскивание и изменение размера ---------------------------------------

  cellAt(e) {
    const r = this.overlay.getBoundingClientRect();
    const { cols, rows } = this.layout;
    return {
      x: Math.floor(((e.clientX - r.left) / r.width) * cols),
      y: Math.floor(((e.clientY - r.top) / r.height) * rows),
    };
  }

  pointerDown(e) {
    const box = e.target.closest(".box");
    if (!box || e.button !== 0) return;
    e.preventDefault();
    const w = this.widget(box.dataset.box);
    const cell = this.cellAt(e);
    const resize = Boolean(e.target.closest(".box-resize"));
    this.drag = { id: w.id, box, resize, start: cell, orig: { ...w }, rect: { ...w }, moved: false };
    if (this.selected !== w.id) {
      this.selected = w.id;
      this.host.querySelectorAll(".box").forEach((b) => b.classList.toggle("selected", b === box));
      this.host.querySelector(".props").innerHTML = this.renderWidgetProps();
    }
  }

  pointerMove(e) {
    const drag = this.drag;
    if (!drag) return;
    const cell = this.cellAt(e);
    const dx = cell.x - drag.start.x;
    const dy = cell.y - drag.start.y;
    const { cols, rows } = this.layout;
    const rect = { ...drag.orig };
    if (drag.resize) {
      const lim = this.limits(this.catalog[rect.type] || {});
      rect.w = Math.max(lim.minW, Math.min(lim.maxW, cols - rect.x, drag.orig.w + dx));
      rect.h = Math.max(lim.minH, Math.min(lim.maxH, rows - rect.y, drag.orig.h + dy));
    } else {
      rect.x = Math.max(0, Math.min(cols - rect.w, drag.orig.x + dx));
      rect.y = Math.max(0, Math.min(rows - rect.h, drag.orig.y + dy));
    }
    if (rect.x !== drag.rect.x || rect.y !== drag.rect.y || rect.w !== drag.rect.w || rect.h !== drag.rect.h) {
      drag.rect = rect;
      drag.moved = true;
      placeWidget(drag.box, rect);
      drag.box.classList.toggle("bad", !this.fits(rect, rect.id));
    }
  }

  async pointerUp() {
    const drag = this.drag;
    if (!drag) return;
    this.drag = null;
    if (!drag.moved) return;
    if (!this.fits(drag.rect, drag.id)) {
      // Место занято: виджет возвращается назад.
      placeWidget(drag.box, drag.orig);
      drag.box.classList.remove("bad");
      return;
    }
    Object.assign(this.widget(drag.id), { x: drag.rect.x, y: drag.rect.y, w: drag.rect.w, h: drag.rect.h });
    await this.save();
  }
}
