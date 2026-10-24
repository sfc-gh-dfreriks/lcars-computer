"""Ears and voice for the LCARS computer, all local on the Mac.

- listen():  Whisper large-v3-turbo on Apple silicon (mlx-whisper), about 1 s per phrase.
- speak():   macOS `say` (Samantha) at a measured pace, filtered like a shipboard speaker:
             band-limited, lightly compressed, with a short metallic room echo.
- chimes:    original synthesized tones in the spirit of the TNG computer chirps (not samples
             from the show), written once to static/sounds/.

The style is inspired by the Enterprise-D computer; it is not a clone of Majel
Barrett-Roddenberry's voice, whose likeness belongs to her estate.
"""
import hashlib
import os
import re
import subprocess
import tempfile

import numpy as np
import soundfile as sf

HERE = os.path.dirname(os.path.abspath(__file__))
SOUNDS = os.path.join(HERE, "static", "sounds")
CACHE = os.path.join(HERE, ".tts-cache")
VOICE = os.environ.get("LCARS_VOICE", "Samantha")
RATE = int(os.environ.get("LCARS_RATE", "168"))          # words per minute: even, unhurried
STT_MODEL = os.environ.get("LCARS_STT", "mlx-community/whisper-large-v3-turbo")
SR = 44100

# Shipboard-speaker treatment: narrow band, gentle compression, short metallic slap echo.
SPEAKER_FX = ("highpass=f=220,lowpass=f=7200,"
              "acompressor=threshold=-20dB:ratio=3:attack=5:release=80,"
              "aecho=0.8:0.35:28|52:0.16|0.08,"
              "equalizer=f=2600:t=q:w=1.2:g=3,loudnorm=I=-17:TP=-1.5")


# ------------------------------------------------------------------ listening
def listen(wav_path: str) -> str:
    import mlx_whisper
    r = mlx_whisper.transcribe(wav_path, path_or_hf_repo=STT_MODEL, language="en",
                               initial_prompt="Computer, Snowflake, Cortex, SQL, semantic view, SAP, BDC.")
    return r["text"].strip()


def warm_up():
    """Load Whisper once at start so the first request isn't slow."""
    path = os.path.join(SOUNDS, "ack.wav")
    if os.path.exists(path):
        listen(path)


# ------------------------------------------------------------------ speaking
def for_speech(text: str) -> str:
    """Make text sound right when spoken: no markdown, paths shortened, symbols in words."""
    t = re.sub(r"`{1,3}[^`]*`{1,3}", lambda m: m.group(0).strip("`"), text)
    t = re.sub(r"[*_#>|]", " ", t)
    t = re.sub(r"https?://\S+", "a link", t)
    t = re.sub(r"(/[\w.\-]+){2,}", lambda m: m.group(0).rsplit("/", 1)[-1], t)    # paths -> file name
    t = t.replace("&", " and ").replace("%", " percent").replace("->", " to ")
    t = re.sub(r"\bSQL\b", "sequel", t)
    return re.sub(r"\s+", " ", t).strip()


def speak(text: str) -> str:
    """Render `text` in the computer voice; returns the cached WAV path."""
    text = for_speech(text)
    os.makedirs(CACHE, exist_ok=True)
    key = hashlib.sha1(f"{VOICE}|{RATE}|{SPEAKER_FX}|{text}".encode()).hexdigest()[:16]
    out = os.path.join(CACHE, f"{key}.wav")
    if not os.path.exists(out):
        with tempfile.TemporaryDirectory() as d:
            raw = os.path.join(d, "raw.aiff")
            subprocess.run(["say", "-v", VOICE, "-r", str(RATE), "-o", raw, text], check=True)
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", raw, "-af", SPEAKER_FX, "-ar", str(SR), "-ac", "1", out],
                           check=True)
    return out


# ------------------------------------------------------------------ chimes
def _tone(freq, ms, vol=0.35, attack=4, release=40):
    n = int(SR * ms / 1000)
    t = np.arange(n) / SR
    # Sine with a little 2nd and 3rd harmonic gives the glassy "computer" timbre.
    w = np.sin(2 * np.pi * freq * t) + 0.25 * np.sin(4 * np.pi * freq * t) + 0.08 * np.sin(6 * np.pi * freq * t)
    env = np.minimum(1, t / (attack / 1000)) * np.minimum(1, (t[-1] - t + 1e-9) / (release / 1000))
    return (vol * w * env / 1.33).astype(np.float32)


def _gap(ms):
    return np.zeros(int(SR * ms / 1000), dtype=np.float32)


def _seq(*parts):
    return np.concatenate(parts)


CHIMES = {
    # Request acknowledged: a quick rising two-note chirp.
    "ack": lambda: _seq(_tone(1318.5, 55), _gap(12), _tone(1975.5, 95, release=70)),
    # Working: soft three-note burble, played while the agent runs tools.
    "working": lambda: _seq(_tone(1568, 40, 0.2), _tone(1760, 40, 0.2), _tone(1568, 60, 0.2, release=50)),
    # Input required: falling then rising, "your turn".
    "question": lambda: _seq(_tone(1975.5, 70), _gap(30), _tone(1318.5, 70), _gap(30), _tone(1760, 120, release=90)),
    # Task complete: ascending triad.
    "done": lambda: _seq(_tone(1046.5, 60), _tone(1318.5, 60), _tone(1568, 140, release=110)),
    # Unable to comply: low double tone.
    "error": lambda: _seq(_tone(392, 140, 0.4), _gap(60), _tone(370, 220, 0.4, release=150)),
    # Listening: single short rising blip when the mic opens.
    "listen": lambda: _tone(1760, 70, 0.25),
}


def build_chimes():
    os.makedirs(SOUNDS, exist_ok=True)
    for name, fn in CHIMES.items():
        path = os.path.join(SOUNDS, f"{name}.wav")
        if not os.path.exists(path):
            sf.write(path, fn(), SR)
