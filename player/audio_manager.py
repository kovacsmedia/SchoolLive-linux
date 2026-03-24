# player/audio_manager.py

import os
import threading
import tempfile
import urllib.request
from pathlib import Path
from typing  import Optional
from config  import API_BASE, get_data_dir

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

# ── Bell cache ────────────────────────────────────────────────────────────────

def _cache_path(sound_file: str) -> Path:
    return _cache_dir / sound_file

def prefetch_bell(sound_file: str) -> None:
    def _fetch():
        dest = _cache_path(sound_file)
        if dest.exists():
            return
        try:
            url = f"{API_BASE}/audio/bells/{sound_file}"
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
            if not dest.exists():
                try:
                    url = f"{API_BASE}/audio/bells/{sound_file}"
                    urllib.request.urlretrieve(url, dest)
                except Exception as e:
                    print(f"[Audio] Bell letöltés sikertelen: {sound_file}: {e}")
                    if on_done:
                        on_done()
                    return
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
            if ".mp3" in url:
                suffix = ".mp3"
            elif ".wav" in url:
                suffix = ".wav"
            elif ".ogg" in url:
                suffix = ".ogg"
            else:
                suffix = ".mp3"

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