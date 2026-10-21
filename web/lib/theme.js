// Выбор темы с учётом автопереключения по расписанию.

function minutes(hhmm) {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + m;
}

export function themeFor(ui, now) {
  if (!ui.auto_theme) return ui.theme;
  const t = now.getHours() * 60 + now.getMinutes();
  const day = minutes(ui.day_start);
  const night = minutes(ui.night_start);
  // Расписание может переходить через полночь в любую сторону.
  const isDay = day <= night ? t >= day && t < night : t >= day || t < night;
  return isDay ? "light" : "dark";
}
