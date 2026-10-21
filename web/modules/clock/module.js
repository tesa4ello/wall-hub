// Модуль «Часы». Контракт модуля: mount(el, {options, api}) -> {destroy()}.

const pad = (n) => String(n).padStart(2, "0");

export function mount(el, { options }) {
  const showSeconds = options.seconds !== false;
  const showDate = options.date !== false;

  el.innerHTML = `
    <div class="clock">
      <div class="clock-time"><span class="clock-hm"></span><span class="clock-s"></span></div>
      <div class="clock-date"></div>
    </div>`;
  const hm = el.querySelector(".clock-hm");
  const s = el.querySelector(".clock-s");
  const date = el.querySelector(".clock-date");
  s.hidden = !showSeconds;
  date.hidden = !showDate;

  const weekday = new Intl.DateTimeFormat("ru-RU", { weekday: "long" });
  const dayMonth = new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "long" });
  let timer;

  function tick() {
    const now = new Date();
    hm.textContent = `${pad(now.getHours())}:${pad(now.getMinutes())}`;
    s.textContent = pad(now.getSeconds());
    if (showDate) date.textContent = `${weekday.format(now)}, ${dayMonth.format(now)}`;
    // Следующий тик ровно на границе секунды, чтобы цифры не «плыли».
    timer = setTimeout(tick, 1000 - now.getMilliseconds() + 5);
  }
  tick();

  return {
    destroy() {
      clearTimeout(timer);
    },
  };
}
