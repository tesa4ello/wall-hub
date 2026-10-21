// Выбор темы и определение «ночи»: вручную, по расписанию или по солнцу.

const RAD = Math.PI / 180;
const DAY_MS = 86400000;
const J1970 = 2440588;
const J2000 = 2451545;
const J0 = 0.0009;
const OBLIQUITY = RAD * 23.4397;
// Высота центра солнца при восходе/закате с учётом рефракции и диска.
const SUNRISE_ALT = RAD * -0.833;

const toDays = (date) => date.valueOf() / DAY_MS - 0.5 + J1970 - J2000;
const fromJulian = (j) => new Date((j + 0.5 - J1970) * DAY_MS);

// Восход и закат на дату по координатам (упрощённый алгоритм NOAA, точность
// около минуты). Возвращает {sunrise, sunset} либо {polar: "day" | "night"}.
export function sunTimes(date, lat, lon) {
  const lw = RAD * -lon;
  const phi = RAD * lat;
  // Берём полдень местного дня, чтобы получить события именно этого дня.
  const noon = new Date(date);
  noon.setHours(12, 0, 0, 0);
  const d = toDays(noon);
  const n = Math.round(d - J0 - lw / (2 * Math.PI));
  const ds = J0 + lw / (2 * Math.PI) + n;
  const M = RAD * (357.5291 + 0.98560028 * ds);
  const C = RAD * (1.9148 * Math.sin(M) + 0.02 * Math.sin(2 * M) + 0.0003 * Math.sin(3 * M));
  const L = M + C + RAD * 102.9372 + Math.PI;
  const dec = Math.asin(Math.sin(OBLIQUITY) * Math.sin(L));
  const jNoon = J2000 + ds + 0.0053 * Math.sin(M) - 0.0069 * Math.sin(2 * L);

  const cosH = (Math.sin(SUNRISE_ALT) - Math.sin(phi) * Math.sin(dec)) / (Math.cos(phi) * Math.cos(dec));
  if (cosH < -1) return { polar: "day" };
  if (cosH > 1) return { polar: "night" };
  const w = Math.acos(cosH);
  const jSet = J2000 + J0 + (w + lw) / (2 * Math.PI) + n + 0.0053 * Math.sin(M) - 0.0069 * Math.sin(2 * L);
  const jRise = jNoon - (jSet - jNoon);
  return { sunrise: fromJulian(jRise), sunset: fromJulian(jSet) };
}

function minutes(hhmm) {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
}

export function isNight(ui, now) {
  if (ui.theme_mode === "sun") {
    const sun = sunTimes(now, ui.latitude, ui.longitude);
    if (sun.polar) return sun.polar === "night";
    return now < sun.sunrise || now >= sun.sunset;
  }
  // В ручном режиме ночь тоже считается по расписанию: это нужно для приглушения.
  const t = now.getHours() * 60 + now.getMinutes();
  const day = minutes(ui.day_start);
  const night = minutes(ui.night_start);
  // Расписание может переходить через полночь в любую сторону.
  const isDay = day <= night ? t >= day && t < night : t >= day || t < night;
  return !isDay;
}

export function themeFor(ui, now) {
  if (ui.theme_mode === "manual") return ui.theme;
  return isNight(ui, now) ? "dark" : "light";
}

export function formatTime(date) {
  return `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}
