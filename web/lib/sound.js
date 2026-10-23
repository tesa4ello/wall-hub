// Звуковые уведомления панели. Звуки синтезируются Web Audio, без файлов.
// Настройки (громкость, тихие часы, выбор звука) берутся из config.ui.
// Модули получают play() в ctx.sound виджета (см. widgets.js).

const NOTES = {
  // [частота, начало (с), длительность (с), форма волны, громкость]
  chime: [[659.25, 0, 0.9, "sine", 1], [880, 0.16, 1.2, "sine", 0.9]],
  bell: [[1046.5, 0, 1.8, "triangle", 1], [2093, 0, 0.9, "sine", 0.25], [1568, 0.02, 1.2, "sine", 0.2]],
  soft: [[523.25, 0, 0.6, "sine", 0.7], [659.25, 0.14, 0.6, "sine", 0.7], [783.99, 0.28, 1, "sine", 0.7]],
  beep: [[880, 0, 0.14, "square", 0.35], [880, 0.22, 0.14, "square", 0.35]],
};

export const SOUNDS = Object.keys(NOTES);

let ctx = null;
let settings = {};

export function configure(ui) {
  settings = ui || {};
}

const minutes = (hm) => {
  const [h, m] = String(hm || "0:0").split(":").map(Number);
  return h * 60 + m;
};

// Тихие часы: окно может переходить через полночь (22:00–08:00).
export function isQuiet(now = new Date()) {
  if (!settings.sound_quiet) return false;
  const from = minutes(settings.sound_quiet_from);
  const to = minutes(settings.sound_quiet_to);
  const cur = now.getHours() * 60 + now.getMinutes();
  if (from === to) return false;
  return from < to ? cur >= from && cur < to : cur >= from || cur < to;
}

// Звук источника уведомлений ("system" или id модуля): свой из
// ui.sound_sources, иначе общий. "off" — источник молчит.
export function soundFor(source) {
  return (source && settings.sound_sources?.[source]) || settings.sound_name;
}

// Играет звук name (по умолчанию звук источника source или общий).
// force — игнорировать выключатель, «Не беспокоить» и тихие часы (проверка из
// админки, будильник). volume — своя громкость 0..100 (у будильника).
export function play(name, { force = false, source = null, volume: own = null } = {}) {
  name ||= soundFor(source);
  if (name === "off" && !force) return false;
  if (!force && (!settings.sound_enabled || settings.dnd || isQuiet())) return false;
  const volume = (own ?? settings.sound_volume ?? 60) / 100;
  if (volume <= 0) return false;
  const notes = NOTES[name] || NOTES[settings.sound_name] || NOTES.chime;
  try {
    ctx ??= new AudioContext();
    if (ctx.state === "suspended") ctx.resume();
    const t0 = ctx.currentTime + 0.05;
    const master = ctx.createGain();
    // Громкость на слух примерно линейная.
    master.gain.value = volume * volume * 0.6;
    master.connect(ctx.destination);
    for (const [freq, start, dur, type, level] of notes) {
      const osc = ctx.createOscillator();
      const env = ctx.createGain();
      osc.type = type;
      osc.frequency.value = freq;
      env.gain.setValueAtTime(0.0001, t0 + start);
      env.gain.exponentialRampToValueAtTime(level, t0 + start + 0.01);
      env.gain.exponentialRampToValueAtTime(0.0001, t0 + start + dur);
      osc.connect(env).connect(master);
      osc.start(t0 + start);
      osc.stop(t0 + start + dur + 0.05);
    }
    return true;
  } catch (e) {
    console.error("sound failed", e);
    return false;
  }
}

// Голосовые подсказки: фразу синтезирует сервер (/api/voice/say, голос и
// скорость из настроек), а играем здесь, чтобы работали громкость, тихие
// часы и «Не беспокоить». Фразы идут по очереди, лишние отбрасываются.
const LEVELS = ["info", "ok", "warn", "crit"];
const MAX_QUEUE = 3;
let speech = Promise.resolve();
let queued = 0;

// Зачитывать ли уведомление такого уровня от такого источника.
export function wantsVoice(level, source) {
  if (!settings.voice_enabled || !settings.sound_enabled || settings.dnd || isQuiet()) return false;
  if (source && settings.sound_sources?.[source] === "off") return false;
  const min = settings.voice_level === "crit" ? 3 : settings.voice_level === "warn" ? 2 : 0;
  return Math.max(0, LEVELS.indexOf(level)) >= min;
}

// delay (мс) — пауза перед фразой, чтобы не наложиться на звук уведомления.
export function speak(text, { delay = 0 } = {}) {
  const volume = (settings.sound_volume ?? 60) / 100;
  if (!text || volume <= 0 || queued >= MAX_QUEUE) return false;
  queued += 1;
  speech = speech
    .then(() => say(text, volume, delay))
    .catch((e) => console.error("speech failed", e))
    .finally(() => { queued -= 1; });
  return true;
}

async function say(text, volume, delay) {
  const response = await fetch("/api/voice/say", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }),
  });
  if (!response.ok) throw new Error(`voice ${response.status}`);
  const data = await response.arrayBuffer();
  ctx ??= new AudioContext();
  if (ctx.state === "suspended") await ctx.resume();
  const buffer = await ctx.decodeAudioData(data);
  if (delay) await new Promise((r) => setTimeout(r, delay));
  const src = ctx.createBufferSource();
  const gain = ctx.createGain();
  gain.gain.value = Math.min(1, volume * volume * 1.5);
  src.buffer = buffer;
  src.connect(gain).connect(ctx.destination);
  await new Promise((resolve) => {
    src.onended = resolve;
    src.start();
  });
}
