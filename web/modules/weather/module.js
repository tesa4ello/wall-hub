// Модуль «Погода»: набор виджетов в духе iOS и Android.
//
// Данные идут с бэкенда (/api/m/weather/forecast, см. wallhub/weather.py),
// который кэширует Open-Meteo. Виджеты одного места делят один запрос:
// store() держит по записи на место и раздаёт прогноз подписчикам.
// Вёрстка подстраивается под размер плитки (ResizeObserver), а раз в минуту
// виджет перерисовывается, чтобы «сейчас» и ближайшие часы сдвигались.

import { t, tn, locale } from "/lib/i18n.js";
import { glyph, weatherIcon } from "./icons.js";

const REFRESH_MS = 10 * 60 * 1000;
const RETRY_MS = 60 * 1000;
const TICK_MS = 60 * 1000;
// Осадки за 15 минут, начиная с которых интервал считается «мокрым», мм.
const WET_QUARTER = 0.05;
const WET_HOUR = 0.1;

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const nowSec = () => Date.now() / 1000;

// --- данные ---------------------------------------------------------------

const stores = new Map();

function locationQuery(loc) {
  return loc && loc.mode === "custom" ? `?lat=${loc.lat}&lon=${loc.lon}` : "";
}

function store(api, loc) {
  const query = locationQuery(loc);
  let s = stores.get(query);
  if (!s) {
    s = { query, data: null, error: null, subs: new Set(), timer: null, loading: false };
    s.load = async () => {
      clearTimeout(s.timer);
      s.loading = true;
      try {
        s.data = await api.get("/m/weather/forecast" + query);
        s.error = null;
        s.timer = setTimeout(s.load, REFRESH_MS);
      } catch (e) {
        s.error = e.message;
        s.timer = setTimeout(s.load, RETRY_MS);
      }
      s.loading = false;
      s.subs.forEach((fn) => fn(s));
    };
    stores.set(query, s);
  }
  return s;
}

function subscribe(api, loc, fn) {
  const s = store(api, loc);
  s.subs.add(fn);
  if (s.data || s.error) fn(s);
  if (!s.data && !s.loading) s.load();
  return () => {
    s.subs.delete(fn);
    if (!s.subs.size) {
      clearTimeout(s.timer);
      stores.delete(s.query);
    }
  };
}

// --- единицы и тексты ------------------------------------------------------

function temp(c, o) {
  if (c == null) return "–";
  const v = o.temp_unit === "f" ? c * 1.8 + 32 : c;
  return `${Math.round(v)}°`;
}

function wind(ms, o) {
  if (ms == null) return "–";
  if (o.wind_unit === "kmh") return t("{v} км/ч", { v: Math.round(ms * 3.6) });
  if (o.wind_unit === "mph") return t("{v} миль/ч", { v: Math.round(ms * 2.237) });
  return t("{v} м/с", { v: Math.round(ms) });
}

function pressure(hpa, o) {
  if (hpa == null) return "–";
  if (o.pressure_unit === "hpa") return t("{v} гПа", { v: Math.round(hpa) });
  return t("{v} мм рт. ст.", { v: Math.round(hpa * 0.750062) });
}

const CONDITIONS = {
  0: "Ясно", 1: "Преимущественно ясно", 2: "Переменная облачность", 3: "Пасмурно",
  45: "Туман", 48: "Туман с изморозью",
  51: "Слабая морось", 53: "Морось", 55: "Сильная морось", 56: "Ледяная морось", 57: "Ледяная морось",
  61: "Небольшой дождь", 63: "Дождь", 65: "Сильный дождь", 66: "Ледяной дождь", 67: "Ледяной дождь",
  71: "Небольшой снег", 73: "Снег", 75: "Сильный снег", 77: "Снежная крупа",
  80: "Небольшой ливень", 81: "Ливень", 82: "Сильный ливень", 85: "Небольшой снегопад", 86: "Снегопад",
  95: "Гроза", 96: "Гроза с градом", 99: "Гроза с градом",
};
const condition = (code) => t(CONDITIONS[code] || "Нет данных");

const WIND_DIRS = ["С", "СВ", "В", "ЮВ", "Ю", "ЮЗ", "З", "СЗ"];
const windDir = (deg) => (deg == null ? "" : t(WIND_DIRS[Math.round(deg / 45) % 8]));

function uvLevel(uv) {
  if (uv == null) return "";
  if (uv < 3) return t("низкий");
  if (uv < 6) return t("умеренный");
  if (uv < 8) return t("высокий");
  if (uv < 11) return t("очень высокий");
  return t("экстремальный");
}

// Время и дни — в часовом поясе места, а не панели.
function formatters(tz) {
  const opt = (o) => {
    try {
      return new Intl.DateTimeFormat(locale(), { ...o, timeZone: tz });
    } catch {
      return new Intl.DateTimeFormat(locale(), o);
    }
  };
  const hour = opt({ hour: "numeric" });
  const time = opt({ hour: "2-digit", minute: "2-digit" });
  const weekday = opt({ weekday: "short" });
  const day = opt({ year: "numeric", month: "2-digit", day: "2-digit" });
  const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
  return {
    hour: (sec) => hour.format(sec * 1000),
    time: (sec) => time.format(sec * 1000),
    weekday: (sec) => cap(weekday.format(sec * 1000).replace(".", "")),
    sameDay: (a, b) => day.format(a * 1000) === day.format(b * 1000),
  };
}

// Цвет температуры для полос диапазона (как в iOS: от синего к красному).
const TEMP_STOPS = [
  [-25, [124, 92, 255]], [-10, [64, 124, 255]], [0, [56, 182, 255]], [8, [72, 210, 160]],
  [16, [166, 220, 70]], [22, [255, 206, 58]], [28, [255, 150, 46]], [35, [255, 76, 52]],
];
function tempColor(c) {
  if (c <= TEMP_STOPS[0][0]) return `rgb(${TEMP_STOPS[0][1]})`;
  for (let i = 1; i < TEMP_STOPS.length; i++) {
    const [t1, c1] = TEMP_STOPS[i];
    if (c <= t1) {
      const [t0, c0] = TEMP_STOPS[i - 1];
      const k = (c - t0) / (t1 - t0);
      return `rgb(${c0.map((v, j) => Math.round(v + (c1[j] - v) * k)).join(",")})`;
    }
  }
  return `rgb(${TEMP_STOPS[TEMP_STOPS.length - 1][1]})`;
}

// --- выборки из прогноза -----------------------------------------------------

// Часы, начиная с текущего (данные могут быть получены минут 10 назад).
const upcomingHours = (data) => data.hourly.filter((h) => h.time + 3600 > nowSec());
const upcomingQuarters = (data) => (data.minutely || []).filter((q) => q.time > nowSec());
const today = (data) => data.daily.find((d) => d.time + 86400 > nowSec()) || data.daily[0];

function placeName(data, o) {
  const loc = o.location || {};
  return loc.mode === "custom" ? loc.name : data.name;
}

// Сводка по осадкам на ближайшие 2 часа: {text, wet}.
function nowcast(data) {
  const quarters = upcomingQuarters(data).slice(0, 8);
  const now = nowSec();
  const snowy = quarters.some((q) => q.snow > 0) || [71, 73, 75, 77, 85, 86].includes(data.current.code);
  if (quarters.length < 2) return hourlyCast(data);
  const wet = quarters.map((q) => q.precip >= WET_QUARTER);
  const minutesTo = (q) => Math.max(5, Math.round((q.time - 900 - now) / 300) * 5);
  if (wet[0]) {
    const dry = wet.indexOf(false);
    if (dry < 0) return { wet: true, text: snowy ? t("Снег в ближайшие 2 часа") : t("Дождь в ближайшие 2 часа") };
    const n = minutesTo(quarters[dry]);
    return {
      wet: true,
      text: snowy
        ? tn(n, "Снег закончится через {n} минуту|Снег закончится через {n} минуты|Снег закончится через {n} минут")
        : tn(n, "Дождь закончится через {n} минуту|Дождь закончится через {n} минуты|Дождь закончится через {n} минут"),
    };
  }
  const first = wet.indexOf(true);
  if (first < 0) return { wet: false, text: t("Без осадков ближайшие 2 часа") };
  const n = minutesTo(quarters[first]);
  return {
    wet: false,
    text: snowy
      ? tn(n, "Снег начнётся через {n} минуту|Снег начнётся через {n} минуты|Снег начнётся через {n} минут")
      : tn(n, "Дождь начнётся через {n} минуту|Дождь начнётся через {n} минуты|Дождь начнётся через {n} минут"),
  };
}

function hourlyCast(data) {
  const hours = upcomingHours(data).slice(0, 12);
  const wet = hours.map((h) => h.precip >= WET_HOUR);
  const fmt = formatters(data.place.timezone);
  if (wet[0]) {
    const dry = wet.indexOf(false);
    return { wet: true, text: dry < 0 ? t("Осадки в ближайшие 12 часов") : t("Осадки до {time}", { time: fmt.time(hours[dry].time - 3600) }) };
  }
  const first = wet.indexOf(true);
  if (first < 0) return { wet: false, text: t("Без осадков ближайшие 12 часов") };
  // Осадки часа копятся за предыдущий час: метка 15:00 — это дождь с 14 до 15.
  const start = hours[first].time - 3600;
  if (start <= nowSec()) return { wet: false, text: t("Осадки в течение часа") };
  return { wet: false, text: t("Осадки около {time}", { time: fmt.time(start) }) };
}

// --- общие куски вёрстки -----------------------------------------------------------

function header(data, o, { icon = true } = {}) {
  const name = placeName(data, o);
  const d = today(data);
  return `
    <div class="wx-head">
      <div class="wx-head-main">
        ${o.show_name !== false && name ? `<div class="wx-place">${esc(name)}${(o.location || {}).mode !== "custom" ? glyph("location", "wx-glyph wx-place-pin") : ""}</div>` : ""}
        <div class="wx-temp">${temp(data.current.temp, o)}</div>
      </div>
      <div class="wx-head-side">
        ${icon ? weatherIcon(data.current.code, data.current.day, "wx-icon wx-head-icon") : ""}
        <div class="wx-cond">${esc(condition(data.current.code))}</div>
        ${d ? `<div class="wx-hl">${t("Макс. {max}, мин. {min}", { max: temp(d.tmax, o), min: temp(d.tmin, o) })}</div>` : ""}
      </div>
    </div>`;
}

function hourCells(data, o, count) {
  const fmt = formatters(data.place.timezone);
  return upcomingHours(data).slice(0, count).map((h, i) => `
    <div class="wx-hour">
      <div class="wx-hour-time">${i === 0 ? t("Сейчас") : esc(fmt.hour(h.time))}</div>
      ${weatherIcon(h.code, h.day, "wx-icon wx-hour-icon")}
      <div class="wx-pop">${h.pop >= 20 ? `${h.pop}%` : ""}</div>
      <div class="wx-hour-temp">${temp(i === 0 ? data.current.temp : h.temp, o)}</div>
    </div>`).join("");
}

// Почасовой график: подписи и точки над кривой температуры.
function hourChart(data, o, count) {
  const hours = upcomingHours(data).slice(0, count);
  if (!hours.length) return "";
  const fmt = formatters(data.place.timezone);
  const temps = hours.map((h, i) => (i === 0 ? data.current.temp ?? h.temp : h.temp));
  const min = Math.min(...temps);
  const max = Math.max(...temps);
  const y = (v) => (max === min ? 50 : 82 - ((v - min) / (max - min)) * 64);
  const x = (i) => ((i + 0.5) / hours.length) * 100;
  const pts = temps.map((v, i) => [x(i), y(v)]);
  // Сглаженная кривая (Catmull-Rom -> Безье).
  let d = `M${pts[0][0]} ${pts[0][1]}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[Math.max(0, i - 1)];
    const [p1, p2] = [pts[i], pts[i + 1]];
    const p3 = pts[Math.min(pts.length - 1, i + 2)];
    const c1 = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6];
    const c2 = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6];
    d += ` C${c1.map((v) => v.toFixed(2)).join(" ")} ${c2.map((v) => v.toFixed(2)).join(" ")} ${p2[0].toFixed(2)} ${p2[1].toFixed(2)}`;
  }
  const grad = `wx-g${Math.random().toString(36).slice(2, 8)}`;
  const stops = pts.map(([px], i) => `<stop offset="${px}%" stop-color="${tempColor(temps[i])}"/>`).join("");
  return `
    <div class="wx-chart" style="--n:${hours.length}">
      <div class="wx-chart-row wx-chart-times">${hours.map((h, i) => `<span>${i === 0 ? t("Сейчас") : esc(fmt.hour(h.time))}</span>`).join("")}</div>
      <div class="wx-chart-row wx-chart-icons">${hours.map((h) => `<span>${weatherIcon(h.code, h.day, "wx-icon wx-hour-icon")}<em class="wx-pop">${h.pop >= 20 ? `${h.pop}%` : ""}</em></span>`).join("")}</div>
      <div class="wx-chart-plot">
        <svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
          <defs><linearGradient id="${grad}" x1="0" x2="100" gradientUnits="userSpaceOnUse">${stops}</linearGradient></defs>
          <path d="${d}" fill="none" stroke="url(#${grad})" stroke-width="3" stroke-linecap="round" vector-effect="non-scaling-stroke"/>
        </svg>
        ${pts.map(([px, py], i) => `
          <i class="wx-dot" style="left:${px}%;top:${py}%"></i>
          <b class="wx-chart-temp" style="left:${px}%;top:${py}%">${temp(temps[i], o)}</b>`).join("")}
      </div>
    </div>`;
}

function dayRows(data, o, count) {
  const fmt = formatters(data.place.timezone);
  const days = data.daily.filter((d) => d.time + 86400 > nowSec()).slice(0, count);
  if (!days.length) return "";
  const lo = Math.min(...days.map((d) => d.tmin));
  const hi = Math.max(...days.map((d) => d.tmax));
  const span = Math.max(1, hi - lo);
  const pos = (v) => ((v - lo) / span) * 100;
  const cur = data.current.temp;
  return days.map((d, i) => {
    const left = pos(d.tmin);
    const right = pos(d.tmax);
    const now = i === 0 && cur != null ? `<b class="wx-bar-now" style="left:${Math.max(0, Math.min(100, pos(cur)))}%"></b>` : "";
    return `
      <div class="wx-day">
        <span class="wx-day-name">${i === 0 ? t("Сегодня") : esc(fmt.weekday(d.time + 43200))}</span>
        <span class="wx-day-icon">${weatherIcon(d.code, true, "wx-icon")}<em class="wx-pop">${d.pop >= 30 ? `${d.pop}%` : ""}</em></span>
        <span class="wx-day-min">${temp(d.tmin, o)}</span>
        <span class="wx-bar"><i style="left:${left}%;right:${100 - right}%;background:linear-gradient(90deg, ${tempColor(d.tmin)}, ${tempColor(d.tmax)})"></i>${now}</span>
        <span class="wx-day-max">${temp(d.tmax, o)}</span>
      </div>`;
  }).join("");
}

// Столбики осадков: по 15 минут на 2 часа или по часам на 12 часов.
function precipBars(data, o, range) {
  const fmt = formatters(data.place.timezone);
  let items;
  if (range === "12h" || upcomingQuarters(data).length < 4) {
    items = upcomingHours(data).slice(0, 12).map((h, i) => ({
      rate: h.precip, label: i === 0 ? t("Сейчас") : fmt.hour(h.time), pop: h.pop, every: 3,
    }));
  } else {
    items = upcomingQuarters(data).slice(0, 8).map((q, i) => ({
      rate: q.precip * 4, label: i === 0 ? t("Сейчас") : fmt.time(q.time - 900), snow: q.snow > 0, every: 2,
    }));
  }
  // Шкала как у iOS: слабые, умеренные, сильные осадки (мм/ч).
  const h = (rate) => (rate < 0.05 ? 0 : Math.min(100, 8 + Math.sqrt(rate / 8) * 92));
  return `
    <div class="wx-bars" style="--n:${items.length}">
      <div class="wx-bars-grid"><i></i><i></i><i></i></div>
      ${items.map((it) => `<span class="wx-bar-col${it.snow ? " snow" : ""}"><i style="height:${h(it.rate)}%"></i></span>`).join("")}
    </div>
    <div class="wx-bars-labels" style="--n:${items.length}">
      ${items.map((it, i) => `<span>${i % it.every === 0 ? esc(it.label) : ""}</span>`).join("")}
    </div>`;
}

function detailTiles(data, o, limit) {
  const c = data.current;
  const d = today(data) || {};
  const fmt = formatters(data.place.timezone);
  const tiles = [
    ["feels", t("Ощущается"), temp(c.feels, o)],
    ["wind", t("Ветер"), wind(c.wind, o), c.gusts != null ? `${windDir(c.wind_dir)} · ${t("порывы {v}", { v: wind(c.gusts, o) })}` : windDir(c.wind_dir)],
    ["humidity", t("Влажность"), c.humidity != null ? `${c.humidity}%` : "–"],
    ["pressure", t("Давление"), pressure(c.pressure, o)],
    ["uv", t("УФ-индекс"), c.uv != null ? String(Math.round(c.uv)) : "–", uvLevel(c.uv)],
    ["precip", t("Осадки сегодня"), d.precip != null ? t("{v} мм", { v: d.precip }) : "–", d.pop != null ? t("вероятность {v}%", { v: d.pop }) : ""],
    ["sunrise", t("Восход"), d.sunrise ? fmt.time(d.sunrise) : "–", d.sunset ? t("закат {time}", { time: fmt.time(d.sunset) }) : ""],
    ["clouds", t("Облачность"), c.clouds != null ? `${c.clouds}%` : "–"],
  ];
  return tiles.slice(0, limit).map(([g, title, value, sub]) => `
    <div class="wx-tile">
      <div class="wx-tile-title">${glyph(g)}${esc(title)}</div>
      <div class="wx-tile-value">${esc(value).replace(/^(-?[\d.,]+°?)\s(.+)$/, "$1<small>$2</small>")}</div>
      ${sub ? `<div class="wx-tile-sub">${esc(sub)}</div>` : ""}
    </div>`).join("");
}

function staleNote(data) {
  if (!data.stale) return "";
  const fmt = formatters(data.place.timezone);
  return `<div class="wx-stale">${t("Обновлено в {time}", { time: fmt.time(data.updated) })}</div>`;
}

// --- виджеты -------------------------------------------------------------------

const VIEWS = {
  // Маленький: место, градусы, иконка, состояние.
  now(data, o, { w, h }) {
    const d = today(data);
    const tiny = w < 140 || h < 120;
    if (tiny) {
      return `<div class="wx-now tiny">${weatherIcon(data.current.code, data.current.day, "wx-icon wx-now-icon")}<div class="wx-temp">${temp(data.current.temp, o)}</div></div>`;
    }
    const name = placeName(data, o);
    return `
      <div class="wx-now">
        ${o.show_name !== false && name ? `<div class="wx-place">${esc(name)}${(o.location || {}).mode !== "custom" ? glyph("location", "wx-glyph wx-place-pin") : ""}</div>` : ""}
        <div class="wx-temp">${temp(data.current.temp, o)}</div>
        <div class="wx-now-foot">
          ${weatherIcon(data.current.code, data.current.day, "wx-icon wx-now-icon")}
          <div class="wx-cond">${esc(condition(data.current.code))}</div>
          ${d ? `<div class="wx-hl">${t("Макс. {max}, мин. {min}", { max: temp(d.tmax, o), min: temp(d.tmin, o) })}</div>` : ""}
        </div>
        ${staleNote(data)}
      </div>`;
  },

  // Средний: сводка сверху, лента часов снизу.
  now_hourly(data, o, { w }) {
    return `
      <div class="wx-col">
        ${header(data, o)}
        <div class="wx-hours" style="--n:${Math.max(4, Math.min(8, Math.floor(w / 62)))}">${hourCells(data, o, Math.max(4, Math.min(8, Math.floor(w / 62))))}</div>
      </div>`;
  },

  hourly(data, o, { w, h }) {
    const count = Math.max(4, Math.min(Number(o.hours) || 12, Math.floor(w / 44)));
    return `
      <div class="wx-col">
        ${h >= 170 && o.show_name !== false ? `<div class="wx-title">${t("Почасовой прогноз")}${placeName(data, o) ? ` · ${esc(placeName(data, o))}` : ""}</div>` : ""}
        ${h >= 150 ? hourChart(data, o, count) : `<div class="wx-hours" style="--n:${count}">${hourCells(data, o, count)}</div>`}
      </div>`;
  },

  daily(data, o, { h }) {
    const head = h >= 220 && o.show_name !== false;
    const rows = Math.max(2, Math.min(Number(o.days) || 7, Math.floor((h - (head ? 40 : 12)) / 36)));
    return `
      <div class="wx-col">
        ${head ? `<div class="wx-title">${tn(rows, "Прогноз на {n} день|Прогноз на {n} дня|Прогноз на {n} дней")}${placeName(data, o) ? ` · ${esc(placeName(data, o))}` : ""}</div>` : ""}
        <div class="wx-days">${dayRows(data, o, rows)}</div>
      </div>`;
  },

  precip(data, o, { h }) {
    const cast = o.range === "12h" ? hourlyCast(data) : nowcast(data);
    return `
      <div class="wx-col wx-precip">
        <div class="wx-precip-head">${glyph("precip")}<span>${esc(cast.text)}</span></div>
        ${h >= 110 ? precipBars(data, o, o.range) : ""}
      </div>`;
  },

  details(data, o, { w, h }) {
    const cols = Math.max(2, Math.min(4, Math.floor(w / 130)));
    const rows = Math.max(1, Math.floor(h / 78));
    return `<div class="wx-tiles" style="--cols:${cols}">${detailTiles(data, o, cols * rows)}</div>`;
  },

  // Большой: сводка, часы и дни.
  large(data, o, { w, h }) {
    const count = Math.max(5, Math.min(10, Math.floor(w / 62)));
    const rows = Math.max(2, Math.min(10, Math.floor((h - 250) / 36)));
    return `
      <div class="wx-col">
        ${header(data, o)}
        <div class="wx-precip-line">${esc(nowcast(data).text)}</div>
        <div class="wx-hours" style="--n:${count}">${hourCells(data, o, count)}</div>
        <div class="wx-days">${dayRows(data, o, rows)}</div>
      </div>`;
  },
};

function mountWeather(view) {
  return (el, { options, api }) => {
    const o = options || {};
    el.innerHTML = `<div class="wx wx-${view.replace("_", "-")}"></div>`;
    const root = el.firstElementChild;
    let state = null;
    let size = { w: 0, h: 0 };
    let frame = 0;

    const draw = () => {
      frame = 0;
      if (!state || !size.w) return;
      if (state.data) {
        try {
          root.innerHTML = VIEWS[view](state.data, o, size);
        } catch (e) {
          console.error("weather render failed", view, e);
          root.innerHTML = `<div class="wx-empty">${t("Нет данных о погоде")}</div>`;
        }
      } else {
        root.innerHTML = `<div class="wx-empty">${t("Нет данных о погоде")}<small>${esc(state.error || "")}</small></div>`;
      }
    };
    const schedule = () => {
      if (!frame) frame = requestAnimationFrame(draw);
    };

    const observer = new ResizeObserver(([entry]) => {
      const box = entry.contentRect;
      const next = { w: Math.round(box.width), h: Math.round(box.height) };
      if (next.w !== size.w || next.h !== size.h) {
        size = next;
        schedule();
      }
    });
    observer.observe(el);
    root.innerHTML = `<div class="wx-empty">${t("Загружаю погоду…")}</div>`;
    const unsubscribe = subscribe(api, o.location, (s) => {
      state = s;
      schedule();
    });
    const tick = setInterval(schedule, TICK_MS);

    return {
      destroy() {
        unsubscribe();
        observer.disconnect();
        clearInterval(tick);
        if (frame) cancelAnimationFrame(frame);
      },
    };
  };
}

export const widgets = Object.fromEntries(Object.keys(VIEWS).map((v) => [v, mountWeather(v)]));
