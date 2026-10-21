// Цветные иконки погоды (inline SVG 64×64) в духе iOS. Цвета облаков,
// капель и снежинок задаются переменными --wx-* в module.css, чтобы
// иконки читались и на светлой, и на тёмной плитке.

// Код погоды WMO -> вид иконки.
export function kindOf(code) {
  if (code == null) return "unknown";
  if (code === 0) return "clear";
  if (code === 1) return "mostly-clear";
  if (code === 2) return "partly";
  if (code === 3) return "overcast";
  if (code === 45 || code === 48) return "fog";
  if ([51, 53, 55].includes(code)) return "drizzle";
  if ([56, 57, 66, 67].includes(code)) return "sleet";
  if ([61, 63].includes(code)) return "rain";
  if (code === 65) return "heavy-rain";
  if ([80, 81, 82].includes(code)) return "showers";
  if ([71, 73, 75, 77, 85, 86].includes(code)) return "snow";
  if ([95, 96, 99].includes(code)) return "thunder";
  return "overcast";
}

const SUN_RAYS = (cx, cy, r1, r2) =>
  Array.from({ length: 8 }, (_, i) => {
    const a = (i * Math.PI) / 4;
    const p = (r) => `${(cx + Math.cos(a) * r).toFixed(1)} ${(cy + Math.sin(a) * r).toFixed(1)}`;
    return `M${p(r1)}L${p(r2)}`;
  }).join("");

const sun = (cx, cy, r) => `
  <path class="wx-ray" d="${SUN_RAYS(cx, cy, r + 4.5, r + 10)}"/>
  <circle class="wx-sun" cx="${cx}" cy="${cy}" r="${r}"/>`;

const moon = (cx, cy, r) =>
  `<path class="wx-moon" d="M${cx + r * 0.35} ${cy - r}A${r} ${r} 0 1 0 ${cx + r} ${cy + r * 0.45}A${r * 0.8} ${r * 0.8} 0 0 1 ${cx + r * 0.35} ${cy - r}Z"/>`;

// Облако с нижней кромкой на y = base, шириной ~44 при scale 1.
const cloud = (x, base, scale = 1, cls = "wx-cloud") =>
  `<path class="${cls}" transform="translate(${x} ${base}) scale(${scale})" d="M-13 0h27a9 9 0 0 0 1.2-17.9A13 13 0 0 0-9.6-21.2 10.6 10.6 0 0 0-13 0Z"/>`;

const drops = (xs, y, len = 7) =>
  xs.map((x) => `<path class="wx-drop" d="M${x} ${y}l-2.6 ${len}"/>`).join("");

const flakes = (xs, y) =>
  xs.map((x, i) => {
    const cy = y + (i % 2) * 5;
    return `<path class="wx-flake" d="M${x - 3.5} ${cy}h7M${x - 1.8} ${cy - 3}l3.6 6M${x + 1.8} ${cy - 3}l-3.6 6"/>`;
  }).join("");

const bolt = `<path class="wx-bolt" d="M33 38l-7 12h6l-3 10 10-14h-6l4-8Z"/>`;

function body(kind, day) {
  const sky = (cx, cy, r) => (day ? sun(cx, cy, r) : moon(cx, cy, r));
  switch (kind) {
    case "clear":
      return day ? sun(32, 32, 12) : moon(32, 32, 15);
    case "mostly-clear":
      return sky(25, 24, 9.5) + cloud(39, 50, 0.75);
    case "partly":
      return sky(23, 22, 9) + cloud(35, 52, 1);
    case "overcast":
      return cloud(40, 40, 0.7, "wx-cloud wx-cloud-back") + cloud(30, 50, 1);
    case "fog":
      return cloud(32, 38, 0.95) + `<path class="wx-fog" d="M14 46h36M18 53h28"/>`;
    case "drizzle":
      return cloud(32, 40, 1) + drops([24, 32, 40], 45, 4);
    case "rain":
      return cloud(32, 40, 1) + drops([24, 32, 40], 45);
    case "heavy-rain":
      return cloud(32, 38, 1) + drops([20, 27, 34, 41, 48], 43, 10);
    case "showers":
      return sky(21, 20, 7.5) + cloud(35, 42, 0.95) + drops([28, 36, 44], 47);
    case "sleet":
      return cloud(32, 40, 1) + drops([24, 40], 45) + flakes([32], 49);
    case "snow":
      return cloud(32, 40, 1) + flakes([23, 32, 41], 48);
    case "thunder":
      return cloud(32, 38, 1) + drops([22, 44], 43) + bolt;
    default:
      return cloud(32, 46, 1);
  }
}

export function weatherIcon(code, day = true, cls = "wx-icon") {
  const kind = kindOf(code);
  return `<svg class="${cls} wx-${kind}" viewBox="0 0 64 64" aria-hidden="true">${body(kind, day)}</svg>`;
}

// Маленькие контурные значки для «Подробностей».
const glyphs = {
  feels: '<path d="M14 14.8V4.5a2 2 0 0 0-4 0v10.3a4 4 0 1 0 4 0Z"/><path d="M12 9v7"/>',
  humidity: '<path d="M12 3s6 6.4 6 11a6 6 0 0 1-12 0c0-4.6 6-11 6-11Z"/>',
  wind: '<path d="M3 8h11a3 3 0 1 0-3-3"/><path d="M3 12h16a3 3 0 1 1-3 3"/><path d="M3 16h8"/>',
  pressure: '<circle cx="12" cy="13" r="8"/><path d="M12 13l4-4M12 5v2M5 13h2M17 13h2"/>',
  uv: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  sunrise: '<path d="M4 18h16M7 14a5 5 0 0 1 10 0"/><path d="M12 3v5M9.5 5.5 12 3l2.5 2.5"/><path d="M3.5 11.5l1.4 1M20.5 11.5l-1.4 1"/>',
  sunset: '<path d="M4 18h16M7 14a5 5 0 0 1 10 0"/><path d="M12 8V3M9.5 5.5 12 8l2.5-2.5"/><path d="M3.5 11.5l1.4 1M20.5 11.5l-1.4 1"/>',
  precip: '<path d="M7 15a4 4 0 0 1 .5-8 5.5 5.5 0 0 1 10.5 1.5A3.3 3.3 0 0 1 17.5 15"/><path d="M9 18l-1 3M13 18l-1 3M17 18l-1 3"/>',
  clouds: '<path d="M7 18a4.5 4.5 0 0 1 .5-9 6 6 0 0 1 11.4 2A3.5 3.5 0 0 1 18 18Z"/>',
  location: '<path d="M20 4 3 11l7 2.5L12.5 21Z"/>',
};

export function glyph(name, cls = "wx-glyph") {
  return `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${glyphs[name] || ""}</svg>`;
}
