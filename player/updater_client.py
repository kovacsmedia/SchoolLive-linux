# player/updater_client.py
#
# GitHub Releases alapú auto-update.
# Naponta egyszer ellenőrzi, hogy van-e újabb verzió.
# Ha igen, letölti a háttérben, majd elindítja a SchoolLiveUpdater.exe-t és kilép.

import os
import sys
import json
import time
import shutil
import hashlib
import threading
import tempfile
import platform
import subprocess
import urllib.request
from pathlib import Path
from typing  import Optional, Callable

from config import APP_VERSION, get_data_dir

# ── GitHub konfig ─────────────────────────────────────────────────────────────
GITHUB_OWNER   = "schoollive-hu"
GITHUB_REPO    = "SchoolLive-windows"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"

# Az asset neve a release-ben (platform szerint)
ASSET_NAME_WIN   = "SchoolLivePlayer.exe"
ASSET_NAME_LINUX = "schoollive-player"

CHECK_INTERVAL_S = 24 * 60 * 60   # 24 óra
LAST_CHECK_FILE  = get_data_dir() / "last_update_check.txt"

def _asset_name() -> str:
    return ASSET_NAME_WIN if platform.system() == "Windows" else ASSET_NAME_LINUX

def _current_exe() -> Optional[Path]:
    """A futó .exe elérési útja (csak PyInstaller bundle esetén van értelme)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable)
    return None

def _updater_exe() -> Optional[Path]:
    """SchoolLiveUpdater.exe ugyanabban a mappában mint a főalkalmazás."""
    exe = _current_exe()
    if not exe:
        return None
    name = "SchoolLiveUpdater.exe" if platform.system() == "Windows" else "schoollive-updater"
    candidate = exe.parent / name
    return candidate if candidate.exists() else None

# ── Verzió összehasonlítás ────────────────────────────────────────────────────
def _parse_version(v: str) -> tuple:
    """'v1.2.3' → (1, 2, 3)"""
    v = v.lstrip("v").strip()
    try:
        return tuple(int(x) for x in v.split("."))
    except Exception:
        return (0,)

def _is_newer(remote: str, local: str) -> bool:
    return _parse_version(remote) > _parse_version(local)

# ── GitHub Releases API ───────────────────────────────────────────────────────
def fetch_latest_release() -> Optional[dict]:
    """
    Visszatér: {
        "tag_name": "v1.2.0",
        "download_url": "https://...",
        "size": 12345678,
        "sha256": "abc123..."   # ha van asset mellé töltve .sha256 fájl
    }
    """
    try:
        req = urllib.request.Request(
            GITHUB_API_URL,
            headers={"User-Agent": f"SchoolLivePlayer/{APP_VERSION}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())

        tag  = data.get("tag_name", "")
        assets = data.get("assets", [])

        # Fő asset
        asset = next((a for a in assets if a["name"] == _asset_name()), None)
        if not asset:
            return None

        # Opcionális .sha256 ellenőrzőfájl
        sha_asset = next(
            (a for a in assets if a["name"] == f"{_asset_name()}.sha256"), None
        )
        sha256 = None
        if sha_asset:
            try:
                with urllib.request.urlopen(sha_asset["browser_download_url"],
                                            timeout=5) as r:
                    sha256 = r.read().decode().split()[0].strip()
            except Exception:
                pass

        return {
            "tag_name":     tag,
            "download_url": asset["browser_download_url"],
            "size":         asset["size"],
            "sha256":       sha256,
        }
    except Exception as e:
        print(f"[Updater] GitHub API hiba: {e}")
        return None

def _verify_sha256(path: Path, expected: str) -> bool:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest().lower() == expected.lower()

# ── Letöltés ──────────────────────────────────────────────────────────────────
def download_update(url: str, size: int,
                    on_progress: Optional[Callable[[int], None]] = None) -> Optional[Path]:
    """
    Letölti az új exe-t a temp könyvtárba.
    on_progress: 0-100 százalék callback.
    Visszatér: a letöltött fájl útvonala, vagy None hiba esetén.
    """
    tmp_dir  = Path(tempfile.gettempdir()) / "schoollive_update"
    tmp_dir.mkdir(exist_ok=True)
    dest     = tmp_dir / _asset_name()

    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": f"SchoolLivePlayer/{APP_VERSION}"}
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            downloaded = 0
            with open(dest, "wb") as f:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if on_progress and size > 0:
                        on_progress(min(100, int(downloaded * 100 / size)))
        return dest
    except Exception as e:
        print(f"[Updater] Letöltés hiba: {e}")
        if dest.exists():
            dest.unlink()
        return None

# ── Update alkalmazása ────────────────────────────────────────────────────────
def apply_update(new_exe: Path) -> bool:
    """
    Elindítja a SchoolLiveUpdater.exe-t, majd kilép a főalkalmazásból.
    Az updater felülírja az exe-t és újraindítja.
    Visszatér False ha az updater nem található.
    """
    updater = _updater_exe()
    old_exe = _current_exe()

    if not updater or not old_exe:
        print("[Updater] Updater exe nem található – manuális csere szükséges")
        return False

    args = [
        str(updater),
        "--pid",     str(os.getpid()),
        "--old",     str(old_exe),
        "--new",     str(new_exe),
        "--restart",
    ]

    kwargs = {}
    if platform.system() == "Windows":
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP |
            subprocess.DETACHED_PROCESS
        )

    subprocess.Popen(args, **kwargs)
    return True

# ── Napi ellenőrzés ───────────────────────────────────────────────────────────
def _should_check() -> bool:
    if not LAST_CHECK_FILE.exists():
        return True
    try:
        last = float(LAST_CHECK_FILE.read_text().strip())
        return (time.time() - last) >= CHECK_INTERVAL_S
    except Exception:
        return True

def _mark_checked() -> None:
    LAST_CHECK_FILE.write_text(str(time.time()))

class AutoUpdater:
    """
    Háttérszálban fut, naponta ellenőriz,
    és meghívja a callback-eket ha frissítés érhető el.
    """

    def __init__(self,
                 on_update_available: Optional[Callable[[str], None]] = None,
                 on_downloading:      Optional[Callable[[int], None]] = None,
                 on_ready_to_install: Optional[Callable[[], None]]    = None,
                 on_error:            Optional[Callable[[str], None]] = None):
        self._on_update_available = on_update_available
        self._on_downloading      = on_downloading
        self._on_ready_to_install = on_ready_to_install
        self._on_error            = on_error
        self._new_exe:  Optional[Path] = None
        self._new_tag:  str            = ""
        self._checking  = False

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True).start()

    def install_now(self) -> None:
        """Felhasználó megerősítése után hívandó."""
        if self._new_exe and self._new_exe.exists():
            if not apply_update(self._new_exe):
                if self._on_error:
                    self._on_error("Az updater nem indítható el.")
            else:
                # apply_update után az app kilép
                sys.exit(0)

    def check_now(self) -> None:
        threading.Thread(target=self._check, daemon=True).start()

    def _loop(self) -> None:
        # Első ellenőrzés indulás után 30 másodperccel
        time.sleep(30)
        while True:
            if _should_check():
                self._check()
            time.sleep(CHECK_INTERVAL_S)

    def _check(self) -> None:
        if self._checking:
            return
        self._checking = True
        try:
            print(f"[Updater] Verzió ellenőrzés (aktuális: {APP_VERSION})")
            release = fetch_latest_release()
            if not release:
                return

            _mark_checked()
            tag = release["tag_name"]
            print(f"[Updater] Legújabb: {tag}")

            if not _is_newer(tag, APP_VERSION):
                print("[Updater] Naprakész.")
                return

            # Értesítés az UI-nak
            if self._on_update_available:
                self._on_update_available(tag)

            # Letöltés háttérben
            self._new_tag = tag
            self._new_exe = download_update(
                release["download_url"],
                release["size"],
                on_progress=self._on_downloading,
            )

            if not self._new_exe:
                if self._on_error:
                    self._on_error("Letöltés sikertelen.")
                return

            # SHA256 ellenőrzés ha van
            if release.get("sha256"):
                if not _verify_sha256(self._new_exe, release["sha256"]):
                    if self._on_error:
                        self._on_error("Letöltési hiba: ellenőrző összeg nem egyezik.")
                    self._new_exe.unlink()
                    self._new_exe = None
                    return

            print(f"[Updater] ✅ Letöltve: {self._new_exe}")
            if self._on_ready_to_install:
                self._on_ready_to_install()

        except Exception as e:
            print(f"[Updater] Hiba: {e}")
            if self._on_error:
                self._on_error(str(e))
        finally:
            self._checking = False
