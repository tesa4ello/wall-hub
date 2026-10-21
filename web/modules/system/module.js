// Модуль «Система»: виджет статусной строки (пункты — провайдеры wallhub/status).

import { icon } from "/lib/icons.js";
import { subscribe } from "/lib/status.js";

function mountStatusbar(el, { options }) {
  const order = options.items || [];
  el.innerHTML = `<div class="sb sb-${options.align || "left"}"></div>`;
  const host = el.firstElementChild;

  const unsubscribe = subscribe((items) => {
    if (!items) return;
    const byId = Object.fromEntries(items.map((it) => [it.id, it]));
    const shown = order.map((id) => byId[id]).filter(Boolean);
    host.innerHTML = shown
      .map((it) => `<div class="sb-item" data-level="${it.level || "ok"}">${icon(it.icon)}<span></span></div>`)
      .join("");
    // Текст вставляем через textContent: он приходит из системы (имена сетей и т. п.).
    host.querySelectorAll(".sb-item").forEach((node, i) => {
      node.querySelector("span").textContent = shown[i].text;
      node.title = shown[i].title || "";
    });
  });

  return { destroy: unsubscribe };
}

export const widgets = { statusbar: mountStatusbar };
