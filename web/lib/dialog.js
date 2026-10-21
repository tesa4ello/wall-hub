// Простые модальные окна и всплывающие уведомления.

// choose({title, text, options: [{label, value, kind}]}) -> Promise<value|null>
export function choose({ title, text = "", options }) {
  return new Promise((resolve) => {
    const root = document.createElement("div");
    root.className = "dlg-backdrop";
    root.innerHTML = `
      <div class="dlg" role="dialog">
        <div class="dlg-title"></div>
        <div class="dlg-text"></div>
        <div class="dlg-actions"></div>
      </div>`;
    root.querySelector(".dlg-title").textContent = title;
    root.querySelector(".dlg-text").textContent = text;
    const actions = root.querySelector(".dlg-actions");
    options.forEach((opt, i) => {
      const b = document.createElement("button");
      b.className = "btn" + (opt.kind ? ` btn-${opt.kind}` : "");
      b.textContent = opt.label;
      b.dataset.i = i;
      actions.appendChild(b);
    });
    root.addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (!b && e.target !== root) return;
      root.remove();
      resolve(b ? options[b.dataset.i].value : null);
    });
    document.body.appendChild(root);
  });
}

export async function confirmDialog({ title, text = "", ok = "OK", cancel = "Отмена", danger = false }) {
  const result = await choose({
    title,
    text,
    options: [
      { label: cancel, value: false },
      { label: ok, value: true, kind: danger ? "danger" : "primary" },
    ],
  });
  return result === true;
}

let toastTimer;

export function toast(message, kind = "info") {
  let el = document.querySelector(".toast");
  if (!el) {
    el = document.createElement("div");
    el.className = "toast";
    document.body.appendChild(el);
  }
  el.textContent = message;
  el.dataset.kind = kind;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 3500);
}
