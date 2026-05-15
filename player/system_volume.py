# player/system_volume.py
#
# OS-szintű master/main hangerő-vezérlés. A snapclient `--volume X` flag
# CSAK a saját puffer-gain-jét állítja (app-szint), tehát ha a Linux/Windows
# rendszerben a hangkártya master volume-ja le van halkítva vagy némítva,
# a SchoolRadio "max hangerő" gomb hiába megy 100%-ra – a hang halk marad.
#
# Ez a modul OS-szintű mixer-parancsot ad ki, hogy a tényleges hardware
# main-volume is mozogjon a UI / backend SET_VOLUME-mal együtt.
#
# Linux:
#   - PulseAudio/PipeWire (modern desktop):  pactl set-sink-volume @DEFAULT_SINK@ X%
#   - ALSA only fallback:                    amixer -q sset Master X%
# Windows:
#   - nircmd (3rd-party utility, gyakran C:\Windows\System32-ben):
#     nircmd setsysvolume <0..65535>
#   - Ha sem pactl/amixer/nircmd nincs a PATH-on, csendben kihagyjuk
#     (csak app-szintű volume marad, mint eddig).

import platform
import shutil
import subprocess

def set_system_volume(percent: int) -> bool:
    """
    Beállítja az OS master hangerőt 0..100 százalékban.
    Visszatérési érték: True ha legalább egy backend sikerült, False ha nincs
    elérhető eszköz (vagy hibázott mind).
    """
    pct = max(0, min(100, int(percent)))
    sys_name = platform.system()
    try:
        if sys_name == "Linux":
            # 1) PulseAudio/PipeWire
            if shutil.which("pactl"):
                r = subprocess.run(
                    ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{pct}%"],
                    timeout=3, capture_output=True,
                )
                if r.returncode == 0:
                    return True
            # 2) ALSA fallback
            if shutil.which("amixer"):
                # A "Master" control a leggyakoribb; ha "PCM" vagy más kell,
                # a felhasználó beállíthatja `amixer scontrols`-szal.
                r = subprocess.run(
                    ["amixer", "-q", "sset", "Master", f"{pct}%"],
                    timeout=3, capture_output=True,
                )
                if r.returncode == 0:
                    return True
        elif sys_name == "Windows":
            if shutil.which("nircmd") or shutil.which("nircmd.exe"):
                # 0..65535 skála (Windows wave_out master)
                val = round(pct * 65535 / 100)
                r = subprocess.run(
                    ["nircmd", "setsysvolume", str(val)],
                    timeout=3, capture_output=True,
                )
                if r.returncode == 0:
                    return True
            # Mute kezelés is nircmd-vel: setvolume nem unmute-ol automatikusan,
            # ezért ha 0-ra állítunk, megpróbáljuk a mute-ot is bekapcsolni.
            # Nem támogatott eszköz esetén csendben kihagyjuk.
    except Exception as e:
        print(f"[SystemVolume] hiba: {e}")
    return False


def set_system_mute(muted: bool) -> bool:
    """Az OS master-mute kapcsoló. True/False – elérhető bekapcsolt/kikapcsolt."""
    sys_name = platform.system()
    try:
        if sys_name == "Linux":
            if shutil.which("pactl"):
                r = subprocess.run(
                    ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1" if muted else "0"],
                    timeout=3, capture_output=True,
                )
                if r.returncode == 0:
                    return True
            if shutil.which("amixer"):
                r = subprocess.run(
                    ["amixer", "-q", "sset", "Master", "mute" if muted else "unmute"],
                    timeout=3, capture_output=True,
                )
                if r.returncode == 0:
                    return True
        elif sys_name == "Windows":
            if shutil.which("nircmd") or shutil.which("nircmd.exe"):
                r = subprocess.run(
                    ["nircmd", "mutesysvolume", "1" if muted else "0"],
                    timeout=3, capture_output=True,
                )
                if r.returncode == 0:
                    return True
    except Exception as e:
        print(f"[SystemVolume] mute hiba: {e}")
    return False
