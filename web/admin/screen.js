// Вкладка «Трансляция»: экран панели в реальном времени через noVNC.
// По умолчанию только просмотр; управление включается переключателем.

import { t } from "/lib/i18n.js";
import { icon } from "/lib/icons.js";

let RFB = null; // noVNC грузится, только когда вкладку открыли

export class ScreenView {
  constructor() {
    this.rfb = null;
    this.control = false;
    this.status = "";
  }

  render() {
    return `
      <h1>${t("Трансляция экрана")}</h1>
      <div class="screen-bar">
        <label class="check"><input type="checkbox" id="screen-control" ${this.control ? "checked" : ""}>
          <span>${t("Управление")}</span></label>
        <span class="muted small grow" id="screen-status"></span>
        <button class="btn btn-small" data-screen-reconnect>${icon("refresh")} ${t("Переподключить")}</button>
      </div>
      <div class="screen-view" id="screen-view"></div>
      <p class="muted small">${t("Пока вкладка открыта, экран транслируется только вам, после входа в админку. С включённым управлением клики и клавиши уходят на панель.")}</p>`;
  }

  setStatus(text) {
    this.status = text;
    const el = document.getElementById("screen-status");
    if (el) el.textContent = text;
  }

  async mount() {
    this.destroy();
    const host = document.getElementById("screen-view");
    if (!host) return;
    this.setStatus(t("Подключаюсь…"));
    try {
      RFB ??= (await import("/lib/vendor/novnc-rfb.js")).default;
    } catch {
      this.setStatus(t("Не удалось загрузить просмотрщик"));
      return;
    }
    const url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/screen/ws`;
    const rfb = new RFB(host, url);
    rfb.scaleViewport = true;
    rfb.viewOnly = !this.control;
    rfb.showDotCursor = true;
    rfb.addEventListener("connect", () => this.setStatus(t("Подключено")));
    rfb.addEventListener("disconnect", (e) => {
      if (this.rfb === rfb) this.rfb = null;
      this.setStatus(e.detail.clean ? t("Отключено") : t("Нет связи с экраном панели"));
    });
    this.rfb = rfb;
  }

  setControl(on) {
    this.control = on;
    if (this.rfb) this.rfb.viewOnly = !on;
  }

  destroy() {
    const rfb = this.rfb;
    this.rfb = null;
    try {
      rfb?.disconnect();
    } catch {}
  }
}
