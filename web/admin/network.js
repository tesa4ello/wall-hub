// Раздел «Сеть»: проводные интерфейсы и настройка IPv4 (DHCP или вручную)
// для Ethernet и текущей сети Wi‑Fi. Список сетей Wi‑Fi рисует admin.js.

import { api } from "/lib/api.js";
import { t } from "/lib/i18n.js";

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const STATES = {
  connected: "Подключено",
  disconnected: "Не подключено",
  unavailable: "Кабель не подключён",
  unmanaged: "Управляет система",
  connecting: "Подключаюсь…",
};
const JOBS = {
  applying: "Применяю и проверяю связь…",
  ok: "Настройки применены",
  rolled_back: "Связь не появилась, вернул прежние настройки",
  failed: "Не удалось применить",
};
const MIGRATE = {
  running: "Переношу настройки Ethernet…",
  done: "Ethernet теперь настраивается из панели",
  rolled_back: "Связь через Ethernet не поднялась, вернул прежние настройки",
  failed: "Не удалось передать Ethernet панели",
};

export class NetworkPanel {
  // ctx: guarded, toast, confirm, render (перерисовать вкладку), active() — открыта ли вкладка.
  constructor(ctx) {
    this.ctx = ctx;
    this.data = null;
    this.method = {}; // выбранный в форме способ по интерфейсу
    this.timer = null;
  }

  async load() {
    try {
      this.data = await api.get("/network/devices");
    } catch (err) {
      this.data = { error: err.message, devices: [] };
    }
    this.ctx.render();
    // Пока что-то применяется, обновляем статус.
    clearTimeout(this.timer);
    const busy = this.data.devices.some((d) => d.job?.state === "applying") || this.data.migrate?.state === "running";
    if (busy && this.ctx.active()) this.timer = setTimeout(() => this.load(), 2000);
  }

  device(type) {
    return (this.data?.devices || []).filter((d) => d.type === type);
  }

  // Текущие адреса одной строкой.
  addressLine(d) {
    const cur = d.current || {};
    const parts = [t(STATES[d.state] || d.state)];
    if (cur.addresses?.length) parts.push(cur.addresses.join(", "));
    if (cur.gateway) parts.push(t("шлюз {ip}", { ip: cur.gateway }));
    return parts.map(esc).join(" · ");
  }

  renderEthernet() {
    if (!this.data) return `<h1>Ethernet</h1><p class="muted">${t("Загрузка…")}</p>`;
    if (this.data.error) return `<h1>Ethernet</h1><p class="error">${esc(this.data.error)}</p>`;
    const list = this.device("ethernet");
    if (!list.length) return `<h1>Ethernet</h1><section class="panel"><div class="field"><span>${t("Проводной адаптер не найден")}</span></div></section>`;
    const m = this.data.migrate;
    const migrate = m && MIGRATE[m.state] ? `<p class="small ${m.state === "done" ? "muted" : m.state === "running" ? "accent" : "error"} panel-inset">${esc(t(MIGRATE[m.state]))}${m.state === "failed" && m.message ? `: ${esc(t(m.message, m.message_args))}` : ""}</p>` : "";
    return `<h1>Ethernet</h1>${list.map((d) => `
      <section class="panel">
        <div class="field"><span>${esc(d.device)}<small>${this.addressLine(d)}</small></span></div>
        ${d.managed ? this.renderIpv4(d) : `
        <div class="field"><span>${t("Сейчас интерфейсом управляет система (netplan)")}<small>${t("Панель перенесёт его настройки к себе и проверит связь. Если связь пропадёт, всё вернётся как было — это безопасно и при работе через кабель.")}</small></span>
          <button class="btn btn-small btn-primary" data-net-manage="${esc(d.device)}" ${m?.state === "running" ? "disabled" : ""}>${t("Управлять из панели")}</button>
        </div>`}
      </section>`).join("")}${migrate}`;
  }

  // Форма IPv4 для интерфейса под NetworkManager.
  renderIpv4(d) {
    const saved = d.ipv4 || { method: "auto", address: "", gateway: "", dns: [] };
    const method = this.method[d.device] || saved.method;
    const manual = method === "manual";
    const cur = d.current || {};
    const job = d.job && JOBS[d.job.state]
      ? `<div class="field"><span class="small ${d.job.state === "ok" ? "muted" : d.job.state === "applying" ? "accent" : "error"}">${esc(t(JOBS[d.job.state]))}${d.job.message ? `: ${esc(d.job.message)}` : ""}</span></div>` : "";
    // Пустые поля при переходе на «вручную» заполняем текущими адресами.
    const val = (key, fallback) => esc(saved.method === "manual" ? (Array.isArray(saved[key]) ? saved[key].join(" ") : saved[key]) : fallback);
    return `
      <form class="ipv4-form" data-net-device="${esc(d.device)}">
        <label class="field"><span>${t("Настройка IP")}</span>
          <select name="method" data-net-method="${esc(d.device)}">
            <option value="auto" ${manual ? "" : "selected"}>${t("Автоматически (DHCP)")}</option>
            <option value="manual" ${manual ? "selected" : ""}>${t("Вручную")}</option>
          </select>
        </label>
        ${manual ? `
        <label class="field"><span>${t("Адрес и маска")}<small>${t("Например, 192.168.1.50/24")}</small></span>
          <input name="address" class="mono" required value="${val("address", (cur.addresses || [])[0] || "")}" placeholder="192.168.1.50/24"></label>
        <label class="field"><span>${t("Шлюз")}</span>
          <input name="gateway" class="mono" value="${val("gateway", cur.gateway || "")}" placeholder="192.168.1.1"></label>` : ""}
        <label class="field"><span>DNS<small>${manual ? t("Через пробел") : t("Пусто — от DHCP")}</small></span>
          <input name="dns" class="mono" value="${esc((saved.dns || []).join(" ") || (manual ? (cur.dns || []).join(" ") : ""))}" placeholder="1.1.1.1 8.8.8.8"></label>
        ${job}
        <div class="row-buttons"><button class="btn btn-primary" type="submit" ${d.job?.state === "applying" ? "disabled" : ""}>${t("Применить")}</button></div>
      </form>`;
  }

  // Блок IP для подключённой сети Wi‑Fi (под списком сетей).
  renderWifiIpv4() {
    const d = this.device("wifi")[0];
    if (!d || !d.managed || !d.connection) return "";
    return `
      <h1>${t("IP-адрес в сети {ssid}", { ssid: esc(d.connection) })}</h1>
      <section class="panel">
        <div class="field"><span>${esc(d.device)}<small>${this.addressLine(d)}</small></span></div>
        ${this.renderIpv4(d)}
      </section>`;
  }

  async onChange(e) {
    const dev = e.target.dataset.netMethod;
    if (!dev) return false;
    this.method[dev] = e.target.value;
    this.ctx.render();
    return true;
  }

  async onClick(e) {
    const b = e.target.closest("[data-net-manage]");
    if (!b) return false;
    const ok = await this.ctx.confirm({
      title: t("Передать {name} панели?", { name: b.dataset.netManage }),
      text: t("Связь через кабель может прерваться на полминуты. Если она не восстановится, настройки вернутся сами."),
      ok: t("Передать"),
    });
    if (!ok) return true;
    await this.ctx.guarded(() => api.post("/network/manage", { device: b.dataset.netManage }));
    this.data.migrate = { state: "running" };
    this.ctx.render();
    setTimeout(() => this.load(), 1500);
    return true;
  }

  async onSubmit(e, data) {
    const device = e.target.dataset.netDevice;
    if (!device) return false;
    const body = { device, method: data.method, address: data.address || "", gateway: data.gateway || "", dns: data.dns || "" };
    const d = this.data.devices.find((x) => x.device === device);
    const cur = (d.current?.addresses || [])[0] || "";
    // Адрес меняется: админка, открытая по старому адресу, потеряет связь.
    if (body.method === "manual" && cur && cur.split("/")[0] !== body.address.split("/")[0] && location.hostname === cur.split("/")[0]) {
      const ok = await this.ctx.confirm({
        title: t("Сменить адрес панели?"),
        text: t("Эта страница открыта по старому адресу. После смены откройте админку по новому: http://{ip}/", { ip: body.address.split("/")[0] }),
        ok: t("Сменить"),
      });
      if (!ok) return true;
    }
    await this.ctx.guarded(() => api.post("/network/ipv4", body));
    delete this.method[device];
    await this.load();
    return true;
  }

  stop() {
    clearTimeout(this.timer);
  }
}
