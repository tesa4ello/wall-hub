"""Голосовые подсказки: панель зачитывает уведомления вслух.

Речь синтезирует сервер (RHVoice, если установлен, иначе eSpeak NG) в
WAV, а играет киоск через Web Audio, как и остальные звуки: так работают
громкость панели, тихие часы и «Не беспокоить» (см. web/lib/sound.js).
Готовые фразы кэшируются в state/voice-cache: уведомления повторяются.
"""

import hashlib
import logging
import os
import re
import shutil
import subprocess
import threading

log = logging.getLogger(__name__)

RHVOICE_DIRS = ("/usr/share/RHVoice/voices", "/usr/local/share/RHVoice/voices")
LANGS = {"Russian": "ru", "English": "en"}
ESPEAK_VOICES = {"ru": "ru", "en": "en-gb"}
MAX_TEXT = 400
CYRILLIC_RE = re.compile("[\u0400-\u04ff]")
CACHE_KEEP = 60
TIMEOUT = 20


class VoiceError(Exception):
    pass


class Voice:
    def __init__(self, cache_dir, which=shutil.which, run=subprocess.run, voices_dirs=RHVOICE_DIRS):
        self.cache_dir = cache_dir
        self.which = which
        self.run = run
        self.voices_dirs = voices_dirs
        self.lock = threading.Lock()

    # --- что установлено -------------------------------------------------------

    def rhvoice_voices(self):
        """Голоса RHVoice: [{id, title, lang, gender}], только ru и en."""
        if not self.which("RHVoice-test"):
            return []
        voices = []
        for base in self.voices_dirs:
            try:
                names = sorted(os.listdir(base))
            except OSError:
                continue
            for name in names:
                info = {}
                try:
                    with open(os.path.join(base, name, "voice.info"), encoding="utf-8") as f:
                        for line in f:
                            key, _, value = line.strip().partition("=")
                            info[key] = value
                except OSError:
                    continue
                lang = LANGS.get(info.get("language"))
                if lang and not any(v["id"] == name for v in voices):
                    voices.append({"id": name, "title": info.get("name") or name, "lang": lang,
                                   "gender": info.get("gender", "")})
        return voices

    def state(self):
        """Для админки: движок и голоса. engine None — синтезатора нет."""
        voices = self.rhvoice_voices()
        if voices:
            return {"engine": "rhvoice", "voices": voices}
        if self.which("espeak-ng"):
            return {"engine": "espeak-ng", "voices": []}
        return {"engine": None, "voices": []}

    # --- синтез ------------------------------------------------------------------

    def _pick(self, voices, lang, wanted):
        """Голос RHVoice: выбранный, если он на нужном языке, иначе первый
        голос языка (женский, если есть)."""
        same = [v for v in voices if v["lang"] == lang]
        for v in same:
            if v["id"] == wanted:
                return v["id"]
        female = [v for v in same if v["gender"] == "female"]
        return (female or same)[0]["id"] if same else None

    def _candidates(self, lang, voice):
        """Чем пробовать произнести: [(движок, голос)] по порядку. Русские
        голоса RHVoice читают и английский, eSpeak NG — запасной вариант."""
        voices = self.rhvoice_voices()
        out = []
        for want in (lang, "ru"):
            name = self._pick(voices, want, voice)
            if name and ("rhvoice", name) not in out:
                out.append(("rhvoice", name))
        if self.which("espeak-ng"):
            out.append(("espeak-ng", ESPEAK_VOICES[lang]))
        return out

    def synth(self, text, lang="ru", voice="", rate=100):
        """Путь к WAV с фразой. Повторные фразы берутся из кэша."""
        text = " ".join(str(text or "").split())[:MAX_TEXT]
        if not text:
            raise VoiceError("Нечего произносить")
        lang = "en" if str(lang).startswith("en") else "ru"
        # Кириллицу английский голос RHVoice молча пропускает (код 0, без
        # файла): русский текст читаем русским голосом при любом языке панели.
        if CYRILLIC_RE.search(text):
            lang = "ru"
        rate = max(50, min(200, int(rate or 100)))
        candidates = self._candidates(lang, voice)
        if not candidates:
            raise VoiceError("Синтезатор речи не установлен")
        with self.lock:
            for engine, name in candidates:
                path = self._make(engine, name, rate, text)
                if path:
                    return path
        raise VoiceError("Не удалось синтезировать речь")

    def _make(self, engine, name, rate, text):
        """WAV из кэша или новый; None — движок не справился."""
        key = hashlib.sha256(f"{engine}\0{name}\0{rate}\0{text}".encode()).hexdigest()[:32]
        path = os.path.join(self.cache_dir, key + ".wav")
        if os.path.isfile(path):
            os.utime(path)
            return path
        os.makedirs(self.cache_dir, mode=0o700, exist_ok=True)
        tmp = path + ".tmp"
        if engine == "rhvoice":
            # RHVoice: скорость в процентах, 100 — обычная.
            cmd = ["RHVoice-test", "-p", name, "-r", str(rate), "-o", tmp]
        else:
            # eSpeak NG: слов в минуту, обычная — 175.
            cmd = ["espeak-ng", "-v", name, "-s", str(round(175 * rate / 100)), "-w", tmp, "--stdin"]
        try:
            self.run(cmd, input=text.encode(), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                     timeout=TIMEOUT, check=True)
            if not os.path.getsize(tmp):
                raise OSError("empty output")
            os.replace(tmp, path)
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("синтез речи не удался (%s %s): %s", engine, name, e)
            try:
                os.remove(tmp)
            except OSError:
                pass
            return None
        self._prune()
        return path

    def _prune(self):
        try:
            files = [os.path.join(self.cache_dir, n) for n in os.listdir(self.cache_dir) if n.endswith(".wav")]
        except OSError:
            return
        files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        for old in files[CACHE_KEEP:]:
            try:
                os.remove(old)
            except OSError:
                pass
