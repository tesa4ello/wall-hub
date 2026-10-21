// Блок «Фон и плитки» в разделе «Экран»: загрузка картинок, выбор фона для
// светлой и тёмной темы, затемнение фона и вид стекла плиток.

import { ApiError, api } from "/lib/api.js";
import { t } from "/lib/i18n.js";
import { icon } from "/lib/icons.js";

// Экран панели, пока /api/info не сказал точнее. Картинка обрезается под
// экран, размытая копия шириной 512: после размытия детали не нужны,
// а распаковывать меньше.
const SCREEN = { w: 1024, h: 600 };
const BLUR = { w: 512, radius: 10 };

function screenOf(info) {
  const d = info && info.display;
  return d && d.width > 0 && d.height > 0 ? { w: d.width, h: d.height } : SCREEN;
}

// Названия переводятся при отрисовке.
const TILES = [
  ["baked", "Стекло (экономно)"],
  ["blur", "Стекло"],
  ["frosted", "Матовые"],
  ["solid", "Сплошные"],
];

const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

// Рисует img в canvas w×h с обрезкой «cover». grow — запас за краями, чтобы
// размытие не тянуло внутрь прозрачные края.
function drawCover(ctx, img, w, h, grow = 0) {
  const scale = Math.max((w + 2 * grow) / img.width, (h + 2 * grow) / img.height);
  const dw = img.width * scale;
  const dh = img.height * scale;
  ctx.drawImage(img, (w - dw) / 2, (h - dh) / 2, dw, dh);
}

function canvas(w, h) {
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  return c;
}

// Размытая копия. ctx.filter есть не во всех браузерах (Safari на телефоне),
// там размываем уменьшением в 16 раз и плавным растяжением обратно.
function blurred(img, screen) {
  const w = BLUR.w;
  const h = Math.round((screen.h * w) / screen.w);
  const out = canvas(w, h);
  const ctx = out.getContext("2d");
  if ("filter" in ctx) {
    ctx.filter = `blur(${BLUR.radius}px) saturate(1.4)`;
    if (ctx.filter !== "none") {
      drawCover(ctx, img, w, h, BLUR.radius * 3);
      return out;
    }
  }
  const small = canvas(Math.round(w / 16), Math.round(h / 16));
  const sctx = small.getContext("2d");
  sctx.imageSmoothingQuality = "high";
  drawCover(sctx, img, small.width, small.height);
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(small, 0, 0, w, h);
  return out;
}

// Файл пользователя → {image, blur} в виде data:-URL JPEG под экран screen.
export async function prepareImage(file, screen = SCREEN) {
  let img;
  try {
    img = await createImageBitmap(file);
  } catch {
    throw new Error(t("Не получилось открыть картинку"));
  }
  const full = canvas(screen.w, screen.h);
  const ctx = full.getContext("2d");
  ctx.imageSmoothingQuality = "high";
  drawCover(ctx, img, screen.w, screen.h);
  return {
    image: full.toDataURL("image/jpeg", 0.88),
    blur: blurred(img, screen).toDataURL("image/jpeg", 0.8),
  };
}

function gallery(name, items, current, extra) {
  const thumb = (id, inner, label) => `
    <button type="button" class="bg-thumb ${id === current ? "selected" : ""}" data-bg-pick="${name}" data-bg-id="${esc(id)}" title="${esc(label)}">${inner}</button>`;
  return `<div class="bg-gallery">
    ${extra.map(([id, label]) => thumb(id, `<span>${esc(t(label))}</span>`, t(label))).join("")}
    ${items.map((i) => `<div class="bg-item">
      ${thumb(i.id, `<img src="${esc(i.url)}" alt="" loading="lazy">`, t("Выбрать"))}
      ${name === "background" && !i.builtin ? `<button type="button" class="bg-delete" data-bg-delete="${esc(i.id)}" title="${esc(t("Удалить"))}">${icon("trash")}</button>` : ""}
    </div>`).join("")}
  </div>`;
}

export class BackgroundPanel {
  // opts: saveUi(patch), guarded(fn), toast(text), confirm({title, ok, danger}),
  // render() — перерисовать раздел.
  constructor(opts) {
    this.opts = opts;
    this.items = null;
    this.busy = false;
    this.screen = SCREEN;
  }

  async load() {
    const [{ items }, info] = await Promise.all([
      this.opts.guarded(() => api.get("/backgrounds")),
      api.get("/info").catch(() => null),
    ]);
    this.items = items;
    this.screen = screenOf(info);
  }

  render(ui) {
    if (!this.items) return `<section class="panel"><div class="pad muted">${t("Загружаю фоны…")}</div></section>`;
    const split = ui.background_dark !== "same";
    const hasBg = Boolean(ui.background) || (split && Boolean(ui.background_dark));
    const opts = TILES.map(([v, l]) => `<option value="${v}" ${v === ui.tiles ? "selected" : ""}>${esc(t(l))}</option>`).join("");
    return `
    <h1>${t("Фон и плитки")}</h1>
    <section class="panel">
      <div class="field bg-field">
        <span>${split ? t("Фон светлой темы") : t("Фон")}<small>${t("Картинка обрезается под экран {w}×{h}", this.screen)}</small></span>
        <label class="btn btn-small ${this.busy ? "disabled" : ""}">${icon("plus")}<span>${this.busy ? t("Загружаю…") : t("Загрузить")}</span>
          <input type="file" accept="image/*" data-bg-upload hidden ${this.busy ? "disabled" : ""}>
        </label>
      </div>
      ${gallery("background", this.items, ui.background, [["", "Без фона"]])}
      <label class="field"><span>${t("Отдельный фон для тёмной темы")}</span>
        <input type="checkbox" data-bg-split ${split ? "checked" : ""}>
      </label>
      ${split ? `<div class="field"><span>${t("Фон тёмной темы")}</span></div>${gallery("background_dark", this.items, ui.background_dark, [["", "Без фона"]])}` : ""}
      <label class="field"><span>${t("Затемнение фона, %")}<small>${t("Чтобы текст читался на яркой картинке")}</small></span>
        <input type="number" min="0" max="80" step="5" data-ui="bg_dim" data-int value="${ui.bg_dim}" ${hasBg ? "" : "disabled"}>
      </label>
      <label class="field"><span>${t("Прозрачность плиток, %")}<small>${t("Для стекла и матовых плиток. 0 — почти сплошные")}</small></span>
        <input type="number" min="0" max="90" step="5" data-ui="tile_transparency" data-int value="${ui.tile_transparency}" ${hasBg && ui.tiles !== "solid" ? "" : "disabled"}>
      </label>
      <label class="field"><span>${t("Плитки")}<small>${t("«Экономно» выглядит как стекло, но не нагружает панель. Без фона плитки сплошные")}</small></span>
        <select data-ui="tiles" ${hasBg ? "" : "disabled"}>${opts}</select>
      </label>
    </section>`;
  }

  // Обработчики событий раздела; возвращают true, если событие было их.
  async onClick(e) {
    const pick = e.target.closest("[data-bg-pick]");
    if (pick) {
      await this.opts.saveUi({ [pick.dataset.bgPick]: pick.dataset.bgId });
      return true;
    }
    const del = e.target.closest("[data-bg-delete]");
    if (del) {
      if (!(await this.opts.confirm({ title: t("Удалить этот фон?"), ok: t("Удалить"), danger: true }))) return true;
      const res = await this.opts.guarded(() => api.post("/admin/background/delete", { id: del.dataset.bgDelete }));
      this.items = res.items;
      await this.opts.saveUi({});
      return true;
    }
    return false;
  }

  async onChange(e, ui) {
    const el = e.target;
    if ("bgSplit" in el.dataset) {
      await this.opts.saveUi({ background_dark: el.checked ? ui.background : "same" });
      return true;
    }
    if ("bgUpload" in el.dataset) {
      const file = el.files[0];
      if (!file) return true;
      this.busy = true;
      this.opts.render();
      try {
        const body = await prepareImage(file, this.screen);
        const res = await this.opts.guarded(() => api.post("/admin/background", body));
        this.items = res.items;
        // Только что загруженный фон сразу ставим на экран.
        await this.opts.saveUi({ background: res.id });
      } catch (err) {
        if (!(err instanceof ApiError)) this.opts.toast(err.message, "error");
      } finally {
        this.busy = false;
        this.opts.render();
      }
      return true;
    }
    return false;
  }
}
