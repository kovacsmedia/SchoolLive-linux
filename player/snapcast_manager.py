# schoollive_player/snapcast_manager.py
# Változások:
#   • port paraméter: start(port) / set_port() – tenant-specifikus snapserver port (pl. 1801)
#   • mute(bool) – volume=0 mute, visszaállítás eredeti hangerőre
#   • Az alapértelmezett 1704 helyett a tenant portot kell átadni!

import subprocess
import threading
import time
import platform
from typing import Optional, Callable
from config import API_BASE, get_snapclient_bin


def _get_snapserver_host() -> str:
    """
    Snapserver host meghatározása.
    Ha a gép és a szerver ugyanazon a LAN-on van, a belső IP-t használjuk
    (NAT hairpinning elkerülése – sok router nem forwardolja vissza a saját
    külső IP-re érkező kérést a belső hálózatról).
    SNAP_SERVER_HOST env változóval felülírható (pl. "192.168.1.232").
    """
    import os
    override = os.environ.get("SNAP_SERVER_HOST")
    if override:
        return override
    host = API_BASE.replace("https://", "").replace("http://", "").split("/")[0]
    return host


class SnapcastManager:
    """
    Kezeli a snapclient subprocess-t.
    Automatikusan újraindítja ha elhal, és figyeli a kapcsolat állapotát.

    Használat:
        snap = SnapcastManager(...)
        snap.set_port(1801)   # tenant-specifikus port (fetchSnapPort-ból)
        snap.start()
    """

    def __init__(self,
                 on_connected:    Optional[Callable]           = None,
                 on_disconnected: Optional[Callable]           = None,
                 on_error:        Optional[Callable[[str], None]] = None):
        self._bin             = get_snapclient_bin()
        self._proc:  Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._running         = False
        self._connected       = False
        self._volume          = 100
        self._muted           = False        # mute override
        self._volume_before_mute = 100
        self._server_host     = _get_snapserver_host()
        self._port            = 1704         # default, set_port()-tal felülírandó!
        self._on_connected    = on_connected
        self._on_disconnected = on_disconnected
        self._on_error        = on_error
        self._restart_delay   = 3
        self._lock            = threading.Lock()

    @property
    def available(self) -> bool:
        return self._bin is not None

    @property
    def connected(self) -> bool:
        return self._connected

    def set_port(self, port: int) -> None:
        """Tenant-specifikus snapserver port beállítása (pl. 1801)."""
        self._port = port
        print(f"[Snapcast] snapserver port: {self._port}")

    def start(self) -> None:
        if not self.available:
            if self._on_error:
                self._on_error(
                    "snapclient nem található. Töltsd le: "
                    "https://github.com/badaix/snapcast/releases"
                )
            return
        with self._lock:
            if self._running:
                return
            self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._running = False
        self._kill_proc()

    def restart(self) -> None:
        """Leállítja majd újraindítja (pl. unmute után)."""
        self.stop()
        time.sleep(0.5)
        with self._lock:
            self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def set_volume(self, volume: int) -> None:
        """0-100. Ha mute van, az eredeti hangerőt tárolja."""
        volume = max(0, min(100, volume))
        if self._muted:
            # Mute alatt csak a tárolt értéket frissítjük
            self._volume_before_mute = volume
        else:
            self._volume = volume
        print(f"[Snapcast] set_volume: {volume} (muted={self._muted})")
        if not self._muted and self._proc and self._proc.poll() is None:
            self._kill_proc()   # restart alkalmazza az új hangerőt

    def mute(self, muted: bool) -> None:
        """
        Eszközönkénti célzáshoz: ha a saját eszköz nincs a targetDeviceIds-ben
        → mute(True), STOP_PLAYBACK után → mute(False) + restart()
        """
        if self._muted == muted:
            return
        self._muted = muted
        print(f"[Snapcast] mute={'ON' if muted else 'OFF'}")
        if muted:
            self._volume_before_mute = self._volume
            self._volume = 0
        else:
            self._volume = self._volume_before_mute
        # Process újraindítása az új volume-mal
        if self._proc and self._proc.poll() is None:
            self._kill_proc()
        # Ha running, a _run_loop automatikusan újraindítja

    def _run_loop(self) -> None:
        while True:
            with self._lock:
                if not self._running:
                    break
            self._start_proc()
            if self._proc:
                self._proc.wait()
            self._connected = False
            if self._on_disconnected:
                self._on_disconnected()
            with self._lock:
                if not self._running:
                    break
            time.sleep(self._restart_delay)

    def _start_proc(self) -> None:
        import shutil
        use_flatpak_spawn = (
            self._bin.startswith("/run/host/")
            and shutil.which("flatpak-spawn") is not None
        )

        common_args = [
            "--host",      self._server_host,
            "--port",      str(self._port),      # ← tenant-specifikus port!
            "--logfilter", "error",
            "--volume",    str(self._volume),
        ]

        if use_flatpak_spawn:
            host_bin = self._bin.replace("/run/host", "")
            args = ["flatpak-spawn", "--host", host_bin] + common_args
        else:
            args = [self._bin] + common_args

        print(f"[Snapcast] snapclient indítás: host={self._server_host} "
              f"port={self._port} volume={self._volume}")

        kwargs = {}
        if platform.system() == "Windows":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        try:
            self._proc = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                **kwargs,
            )
            self._connected = True
            if self._on_connected:
                self._on_connected()

            for line in self._proc.stdout:
                decoded = line.decode(errors="replace").strip()
                if decoded:
                    print(f"[snapclient] {decoded}")
                    if "disconnected" in decoded.lower() or "error" in decoded.lower():
                        self._connected = False
                        if self._on_disconnected:
                            self._on_disconnected()

        except Exception as e:
            self._connected = False
            if self._on_error:
                self._on_error(f"snapclient hiba: {e}")

    def _kill_proc(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=3)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
        self._proc      = None
        self._connected = False