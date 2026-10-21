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

// Встроенные фоны нарисованы под несколько разрешений: берём самый маленький
// вариант, который закрывает экран в физических пикселях, иначе самый большой.
export function pickSize(item, width, height) {
  const sizes = Object.entries(item.sizes || {})
    .map(([key, files]) => ({ w: Number(key.split("x")[0]), h: Number(key.split("x")[1]), files }))
    .sort((a, b) => a.w * a.h - b.w * b.h);
  if (!sizes.length) return item;
  return (sizes.find((s) => s.w >= width && s.h >= height) || sizes[sizes.length - 1]).files;
}

function screenSize() {
  const dpr = window.devicePixelRatio || 1;
  return [Math.round(screen.width * dpr), Math.round(screen.height * dpr)];
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
    root.style.setProperty("--glass-alpha", 1 - (ui.tile_transparency ?? 58) / 100);
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
      const files = pickSize(item, ...screenSize());
      try {
        await Promise.all([preload(files.url), preload(files.blur)]);
      } catch {
        return;
      }
      if (id !== this.id) return;
      back.style.backgroundImage = `url("${files.url}")`;
      document.documentElement.style.setProperty("--wallpaper-blur", `url("${files.blur}")`);
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
