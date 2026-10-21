// Фоновая картинка экрана и стекло плиток.
//
// Стекло бывает четырёх видов (ui.tiles):
//   blur   — настоящее размытие backdrop-filter;
//   baked  — «экономное»: плитка показывает свой кусок заранее размытой копии
//            фона, браузеру не нужно размывать на каждом кадре;
//   frosted — полупрозрачная плитка без размытия;
//   solid  — непрозрачная плитка, как без фона.
// Без фона плитки всегда сплошные.

import { api } from "/lib/api.js";

export const TILE_MODES = ["baked", "blur", "frosted", "solid"];

// Какой фон показывать в теме: у тёмной может быть свой или «как у светлой».
export function backgroundFor(ui, theme) {
  if (theme === "dark" && ui.background_dark !== "same") return ui.background_dark;
  return ui.background;
}

function preload(url) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(url);
    img.onerror = reject;
    img.src = url;
  });
}

export class Wallpaper {
  // host — слой под столами с двумя картинками для плавной смены.
  constructor(host, desks) {
    this.host = host;
    this.desks = desks;
    this.layers = [0, 1].map(() => {
      const el = document.createElement("div");
      el.className = "wallpaper-layer";
      host.appendChild(el);
      return el;
    });
    this.front = 0;
    this.id = null;
    this.items = new Map();
    new ResizeObserver(() => this.layout()).observe(desks);
  }

  async apply(ui, theme) {
    const root = document.documentElement;
    root.style.setProperty("--bg-dim", (ui.bg_dim || 0) / 100);
    const id = backgroundFor(ui, theme) || "";
    if (id !== this.id) {
      this.id = id;
      await this.show(id);
    }
    const on = Boolean(this.items.get(this.id));
    document.body.classList.toggle("has-wallpaper", on);
    for (const mode of TILE_MODES) document.body.classList.toggle(`tiles-${mode}`, on && ui.tiles === mode);
  }

  async show(id) {
    let item = this.items.get(id);
    if (id && !item) {
      // Список фонов перечитываем, только когда выбран незнакомый.
      const { items } = await api.get("/backgrounds");
      this.items = new Map(items.map((i) => [i.id, i]));
      item = this.items.get(id);
    }
    if (id !== this.id) return;
    const back = this.layers[1 - this.front];
    if (item) {
      try {
        await Promise.all([preload(item.url), preload(item.blur)]);
      } catch {
        return;
      }
      if (id !== this.id) return;
      back.style.backgroundImage = `url("${item.url}")`;
      document.documentElement.style.setProperty("--wallpaper-blur", `url("${item.blur}")`);
    }
    // Новая картинка проявляется поверх старой.
    back.classList.toggle("on", Boolean(item));
    this.layers[this.front].classList.remove("on");
    this.front = 1 - this.front;
  }

  // Для «экономного» стекла каждой плитке нужен сдвиг размытой копии на её
  // место на экране. Плитка считается относительно своего стола: столы
  // во весь экран, поэтому в покое это и есть координаты на экране.
  layout() {
    for (const desk of this.desks.querySelectorAll(".desk")) {
      const base = desk.getBoundingClientRect();
      for (const el of desk.querySelectorAll(".widget-card")) {
        const r = el.getBoundingClientRect();
        el.style.setProperty("--tile-x", `${base.left - r.left}px`);
        el.style.setProperty("--tile-y", `${base.top - r.top}px`);
      }
    }
  }
}
