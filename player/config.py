# schoollive_player/config.py

import os
import json
import platform
from pathlib import Path

APP_NAME    = "SchoolLive Player"
APP_VERSION = "1.5.5"

# Multi-node cluster: API_BASE/WS_URL korábban egyszeri, import-kori
# konstansok voltak – minden `from config import API_BASE` hívó a BETÖLTÉSKORI
# értéket kötötte be, egy későbbi módosítás nem érvényesült náluk. Mostantól
# get_api_base()/get_ws_url() FÜGGVÉNYEK, amiket minden hívónak (pl.
# sync_client.py _connect_loop()) újra kell hívnia – a `while` cikluson belüli
# hívóknál ez már eleve így működik, tehát egy set_api_base() a KÖVETKEZŐ
# iterációban magától érvényesül.
_ENV_DEFAULT_API_BASE = os.environ.get("SL_API_BASE", "https://api.schoollive.hu")
_api_base_override: str | None = None
_override_loaded = False

def get_api_base() -> str:
    global _api_base_override, _override_loaded
    if not _override_loaded:
        # Lazy betöltés – load_settings() ebben a fájlban lentebb van
        # definiálva, de Python ezt csak HÍVÁSKOR oldja fel, nem
        # importáláskor, tehát ez a sorrend biztonságos.
        try:
            saved = load_settings().get("apiBaseOverride")
            if saved:
                _api_base_override = saved
        except Exception:
            pass
        _override_loaded = True
    return _api_base_override or _ENV_DEFAULT_API_BASE

def set_api_base(url: str) -> None:
    """Multi-node cluster: node-váltáskor hívva (ld. sync_client.py 4009 ág).
    Perzisztált is, hogy egy újraindítás után is a legutóbb ismert jó node-ot
    próbálja először."""
    global _api_base_override, _override_loaded
    _api_base_override = url
    _override_loaded = True
    try:
        s = load_settings()
        s["apiBaseOverride"] = url
        save_settings(s)
    except Exception:
        pass

def get_ws_url() -> str:
    base = get_api_base()
    return base.replace("https://", "wss://").replace("http://", "ws://") + "/sync"

# Snapclient bináris keresési útvonalak platformonként
SNAPCLIENT_CANDIDATES_WIN = [
    r"C:\Program Files\Snapcast\snapclient.exe",
    r"C:\Program Files (x86)\Snapcast\snapclient.exe",
    str(Path.home() / "AppData" / "Local" / "Snapcast" / "snapclient.exe"),
    "snapclient.exe",   # PATH-ban van
]
SNAPCLIENT_CANDIDATES_LINUX = [
    "/usr/bin/snapclient",
    "/usr/local/bin/snapclient",
    "/run/host/usr/bin/snapclient",   # Flatpak sandbox
    str(Path.home() / ".local" / "bin" / "snapclient"),
    "snapclient",
]

def get_snapclient_bin() -> str | None:
    import shutil
    candidates = (
        SNAPCLIENT_CANDIDATES_WIN
        if platform.system() == "Windows"
        else SNAPCLIENT_CANDIDATES_LINUX
    )
    for c in candidates:
        # Abszolút útvonal: közvetlen fájl ellenőrzés
        if Path(c).is_absolute():
            if Path(c).is_file():
                return c
        else:
            # Relatív név: PATH-ban keresés
            found = shutil.which(c)
            if found:
                return found
    return None

# Adat könyvtár (token, client_id, hangok cache)
def get_data_dir() -> Path:
    if platform.system() == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path.home() / ".local" / "share"
    d = base / "SchoolLivePlayer"
    d.mkdir(parents=True, exist_ok=True)
    return d

# Beállítások betöltése / mentése
def load_settings() -> dict:
    p = get_data_dir() / "settings.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}

def save_settings(settings: dict) -> None:
    p = get_data_dir() / "settings.json"
    p.write_text(json.dumps(settings, indent=2), encoding="utf-8")
