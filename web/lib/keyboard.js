// Экранная клавиатура для сенсорного экрана без физической клавиатуры.
// openKeyboard({title, value, password, minLength}) -> Promise<string|null>

import { t } from "./i18n.js";
import { icon } from "./icons.js";

const LAYOUTS = {
  en: [
    ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"],
    ["q", "w", "e", "r", "t", "y", "u", "i", "o", "p"],
    ["a", "s", "d", "f", "g", "h", "j", "k", "l"],
    ["z", "x", "c", "v", "b", "n", "m", "-", "_"],
  ],
  ru: [
    ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"],
    ["й", "ц", "у", "к", "е", "н", "г", "ш", "щ", "з", "х"],
    ["ф", "ы", "в", "а", "п", "р", "о", "л", "д", "ж", "э"],
    ["я", "ч", "с", "м", "и", "т", "ь", "б", "ю", "ъ"],
  ],
  sym: [
    ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"],
    ["!", "@", "#", "$", "%", "^", "&", "*", "(", ")"],
    ["-", "_", "=", "+", "[", "]", "{", "}", ";", ":"],
    ["'", '"', ",", ".", "/", "?", "\\", "|", "~", "`", "<", ">"],
  ],
};

export function openKeyboard({ title = "", value = "", password = false, minLength = 0 } = {}) {
  return new Promise((resolve) => {
    let text = value;
    let layout = "en";
    let shift = false;
    let reveal = !password;

    const root = document.createElement("div");
    root.className = "kb-backdrop";
    root.innerHTML = `
      <div class="kb" role="dialog" aria-label="${title}">
        <div class="kb-head">
          <div class="kb-title"></div>
          <div class="kb-field"><span class="kb-text"></span><span class="kb-caret"></span></div>
          ${password ? `<button class="kb-icon" data-act="reveal" aria-label="${t("Показать")}">${icon("eye")}</button>` : ""}
        </div>
        <div class="kb-rows"></div>
        <div class="kb-row kb-bottom">
          <button class="kb-key kb-wide" data-act="layout"></button>
          <button class="kb-key kb-wide" data-act="sym"></button>
          <button class="kb-key kb-space" data-act="space">${t("пробел")}</button>
          <button class="kb-key kb-wide kb-muted" data-act="cancel">${t("Отмена")}</button>
          <button class="kb-key kb-wide kb-primary" data-act="done">${t("Готово")}</button>
        </div>
      </div>`;
    root.querySelector(".kb-title").textContent = title;
    const rows = root.querySelector(".kb-rows");
    const textEl = root.querySelector(".kb-text");
    const doneBtn = root.querySelector('[data-act="done"]');

    function renderText() {
      textEl.textContent = reveal ? text : "•".repeat(text.length);
      doneBtn.disabled = text.length < minLength;
    }

    function renderKeys() {
      const lines = LAYOUTS[layout];
      rows.innerHTML = "";
      lines.forEach((line, i) => {
        const row = document.createElement("div");
        row.className = "kb-row";
        if (i === lines.length - 1 && layout !== "sym") {
          row.appendChild(button(icon("shift"), "shift", shift ? "kb-wide kb-on" : "kb-wide"));
        }
        for (const ch of line) {
          const key = shift ? ch.toUpperCase() : ch;
          const b = button("", "char", "");
          b.textContent = key;
          b.dataset.char = key;
          row.appendChild(b);
        }
        if (i === lines.length - 1) row.appendChild(button(icon("backspace"), "back", "kb-wide"));
        rows.appendChild(row);
      });
      root.querySelector('[data-act="layout"]').textContent = layout === "ru" ? "EN" : "RU";
      root.querySelector('[data-act="sym"]').textContent = layout === "sym" ? "abc" : "#+=";
    }

    function button(html, act, cls) {
      const b = document.createElement("button");
      b.className = "kb-key " + cls;
      b.dataset.act = act;
      b.innerHTML = html;
      return b;
    }

    function close(result) {
      root.remove();
      resolve(result);
    }

    // pointerdown вместо click: на сенсорном экране так отзывчивее.
    root.addEventListener("pointerdown", (e) => {
      const b = e.target.closest("button");
      if (!b || b.disabled) return;
      e.preventDefault();
      switch (b.dataset.act) {
        case "char":
          text += b.dataset.char;
          if (shift) { shift = false; renderKeys(); }
          break;
        case "back": text = text.slice(0, -1); break;
        case "space": text += " "; break;
        case "shift": shift = !shift; renderKeys(); break;
        case "layout": layout = layout === "ru" ? "en" : "ru"; shift = false; renderKeys(); break;
        case "sym": layout = layout === "sym" ? "en" : "sym"; shift = false; renderKeys(); break;
        case "reveal": reveal = !reveal; b.classList.toggle("kb-on", reveal); break;
        case "cancel": close(null); return;
        case "done": close(text); return;
      }
      renderText();
    });

    // С физической клавиатурой (например, при отладке) тоже можно печатать.
    root.tabIndex = -1;
    root.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && text.length >= minLength) return close(text);
      if (e.key === "Escape") return close(null);
      if (e.key === "Backspace") text = text.slice(0, -1);
      else if (e.key.length === 1) text += e.key;
      else return;
      e.preventDefault();
      renderText();
    });

    renderKeys();
    renderText();
    document.body.appendChild(root);
    root.focus();
  });
}
