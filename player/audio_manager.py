# player/audio_manager.py

import os
import shutil
import sys
import threading
import tempfile
import urllib.request
from pathlib import Path
from typing  import Optional
from config  import get_api_base, get_data_dir

try:
    import pygame
    pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)
    PYGAME_AVAILABLE = True
    print("[Audio] pygame inicializálva")
except Exception as e:
    PYGAME_AVAILABLE = False
    print(f"[Audio] pygame nem elérhető: {e}")

_cache_dir = get_data_dir() / "bells_cache"
_cache_dir.mkdir(exist_ok=True)

_lock = threading.Lock()

# UI hangerő (0-10) → pygame hangerő (0.0-0.5)
# Max 0.5 hogy ne torzítson – a rendszer hangerő adja a többit
def _safe_vol(volume: float) -> float:
    return max(0.0, min(0.5, volume * 0.5))

# ── Csengetőhang-URL nyilvántartás ───────────────────────────────────────────
#
# A backend a `/bells/sync` válaszban `sounds: [{filename, url, sizeBytes}]`
# alakban megadja, HOL van az adott hangfájl. Erre azért van szükség, mert a
# hangok mostantól tenant-szeparáltan tárolódnak
# (`/audio/bells/<tenantId>/<fájlnév>`), és a régi, kliens-oldalon
# összerakott `/audio/bells/<fájlnév>` út csak a migráció előtti fájlokra jó.
# (A backend feloldója visszaesik a régi helyre, ezért mindkettő működik –
# de a HELYES URL-t mindig a szerver tudja.)
#
# Az ESP32 és az Android eleve ezt a mezőt használja; a Python kliensek eddig
# maguk fűzték össze az utat.
_sound_urls: dict = {}


def register_sound_urls(sounds) -> None:
    """A /bells/sync `sounds` tömbjének feldolgozása (filename → url)."""
    for s in (sounds or []):
        try:
            fn = s.get("filename")
            u  = s.get("url")
            if fn and u:
                _sound_urls[fn] = u
        except Exception:
            continue


def _sound_url(sound_file: str) -> str:
    """A hangfájl teljes letöltési URL-je. Ha a szerver nem adott meg URL-t
    (régi backend, vagy még nem futott le a /bells/sync), visszaesünk a régi,
    lapos útvonalra – az a migráció előtti fájlokra továbbra is működik."""
    u = _sound_urls.get(sound_file)
    if not u:
        return f"{get_api_base()}/audio/bells/{sound_file}"
    if u.startswith("http://") or u.startswith("https://"):
        return u
    return f"{get_api_base()}{u}"


# ── Bell cache ────────────────────────────────────────────────────────────────

def _cache_path(sound_file: str) -> Path:
    return _cache_dir / sound_file

def prefetch_bell(sound_file: str) -> None:
    def _fetch():
        dest = _cache_path(sound_file)
        if dest.exists():
            return
        try:
            url = _sound_url(sound_file)
            urllib.request.urlretrieve(url, dest)
            print(f"[Audio] Cached: {sound_file}")
        except Exception as e:
            print(f"[Audio] Fetch failed: {sound_file}: {e}")
    threading.Thread(target=_fetch, daemon=True).start()

def prefetch_bells(bells: list) -> None:
    seen = set()
    for b in bells:
        sf = b.get("soundFile", "")
        if sf and sf not in seen:
            seen.add(sf)
            prefetch_bell(sf)

# ── Gyári default csengetőhangok ─────────────────────────────────────────────
#
# "A CSENGETÉS SOSEM MARADHAT EL": ha a beállított hangfájl nincs meg a helyi
# cache-ben ÉS nem tölthető le (offline a backend), a lejátszás eddig csendben
# elmaradt. Az alkalmazás mellé csomagolt default hangok az utolsó védvonal –
# ugyanazok a fájlok, mint az ESP32 firmware LittleFS képében és a szerver
# `assets/bells/` könyvtárában.
DEFAULT_SIGNAL_SOUND = "assembly-signal-bell.opus"
DEFAULT_MAIN_SOUND   = "lesson-signal-bell.opus"


def _name_variants(name: str) -> tuple:
    """A kért név, majd az azonos alapnevű társa a másik formátumban.

    Az Opus-ra állás során a szerver már .opus-t küld, a cache-ben viszont
    még a régi .mp3 lehet (vagy fordítva, ha egy régi kliens listája nem
    frissült). A kettő szétcsúszása néma csengetés lenne, ezért mindig
    megnézzük a párját is. A migráció után ártalmatlan: az első találat nyer.
    """
    if not name:
        return ()
    base, dot, ext = name.rpartition(".")
    if not dot:
        return (name,)
    other = ".mp3" if ext.lower() == "opus" else ".opus"
    return (name, base + other)
def _bundled_dir() -> Path:
    """A csomagolt hangok könyvtára. PyInstaller `--onefile` alatt a
    tartalom a `sys._MEIPASS` temp könyvtárba csomagolódik ki (a
    workflow `--add-data "player/assets:assets"` sorával), fejlesztői
    futtatáskor viszont a forrás melletti `assets/`-ben van."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        p = Path(base) / "assets"
        if p.exists():
            return p
    return Path(__file__).resolve().parent / "assets"


_BUNDLED_DIR = _bundled_dir()


def _bundled_sound(sound_file: str) -> Optional[Path]:
    """A csomagolt default hang útvonala, vagy None."""
    for base in (sound_file, DEFAULT_MAIN_SOUND, DEFAULT_SIGNAL_SOUND):
        for name in _name_variants(base):
            p = _BUNDLED_DIR / name
            if p.exists():
                return p
    return None


def ensure_default_sounds_cached() -> None:
    """A csomagolt default hangokat bemásolja a cache-be, ha nincsenek ott.
    Így egy sosem-online eszköz is tud csengetni."""
    for name in (DEFAULT_SIGNAL_SOUND, DEFAULT_MAIN_SOUND):
        dest = _cache_path(name)
        if dest.exists():
            continue
        src = _bundled_sound(name)
        if src is None:
            continue
        try:
            shutil.copyfile(src, dest)
            print(f"[Audio] Default hang telepítve a cache-be: {name}")
        except Exception as e:
            print(f"[Audio] Default hang másolás hiba ({name}): {e}")

# ── Bell lejátszás ────────────────────────────────────────────────────────────

def play_bell(sound_file: str, volume: float = 0.7,
              on_done: Optional[callable] = None) -> None:
    if not PYGAME_AVAILABLE:
        print(f"[Audio] pygame nem elérhető, bell kihagyva: {sound_file}")
        if on_done:
            on_done()
        return

    def _play():
        with _lock:
            dest = _cache_path(sound_file)
            # Az azonos alapnevű társ is jó, ha a kért alak nincs meg.
            if not dest.exists():
                for alt in _name_variants(sound_file)[1:]:
                    alt_path = _cache_path(alt)
                    if alt_path.exists():
                        dest = alt_path
                        break
            if not dest.exists():
                try:
                    url = _sound_url(sound_file)
                    urllib.request.urlretrieve(url, dest)
                except Exception as e:
                    # NEM adjuk fel: a csengetés nem maradhat el. A csomagolt
                    # default hangra esünk vissza (ugyanaz, mint az ESP32
                    # firmware-ében), és azzal szólalunk meg.
                    print(f"[Audio] Bell letöltés sikertelen: {sound_file}: {e} → default hang")
                    fallback = _bundled_sound(sound_file)
                    if fallback is None:
                        print("[Audio] ⛔ Nincs csomagolt default hang sem!")
                        if on_done:
                            on_done()
                        return
                    dest = fallback
            try:
                pygame.mixer.music.load(str(dest))
                pygame.mixer.music.set_volume(_safe_vol(volume))
                pygame.mixer.music.play()
                while pygame.mixer.music.get_busy():
                    pygame.time.wait(100)
            except Exception as e:
                print(f"[Audio] Bell lejátszás hiba: {sound_file}: {e}")
            finally:
                if on_done:
                    on_done()

    threading.Thread(target=_play, daemon=True).start()

# ── URL lejátszás (TTS / rádió fallback) ──────────────────────────────────────

def play_url(url: str, volume: float = 0.7,
             on_done: Optional[callable] = None) -> None:
    if not PYGAME_AVAILABLE:
        print(f"[Audio] pygame nem elérhető, URL kihagyva: {url[:60]}")
        if on_done:
            on_done()
        return

    print(f"[Audio] play_url: {url[:80]}")

    def _play():
        tmp_path = None
        try:
            # A rendszer egységes formátuma Opus, ezért az az alapértelmezés.
            # A többi csak a régi, még át nem állt tartalmak miatt marad itt.
            if ".opus" in url:
                suffix = ".opus"
            elif ".mp3" in url:
                suffix = ".mp3"
            elif ".wav" in url:
                suffix = ".wav"
            elif ".ogg" in url:
                suffix = ".ogg"
            else:
                suffix = ".opus"

            fd, tmp_path = tempfile.mkstemp(suffix=suffix)
            os.close(fd)
            print(f"[Audio] Letöltés: {url[:60]} → {tmp_path}")
            urllib.request.urlretrieve(url, tmp_path)
            print(f"[Audio] Letöltve, lejátszás indul")

            with _lock:
                pygame.mixer.music.load(tmp_path)
                pygame.mixer.music.set_volume(_safe_vol(volume))
                pygame.mixer.music.play()
                while pygame.mixer.music.get_busy():
                    pygame.time.wait(100)
            print(f"[Audio] Lejátszás kész")

        except Exception as e:
            print(f"[Audio] play_url hiba: {e}")
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
            if on_done:
                on_done()

    threading.Thread(target=_play, daemon=True).start()

# ── Stop ──────────────────────────────────────────────────────────────────────

def stop() -> None:
    if PYGAME_AVAILABLE:
        try:
            pygame.mixer.music.stop()
        except Exception:
            pass