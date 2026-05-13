# player/device_agent.py
#
# Native player ESP-uniform DeviceAgent.
#
# Az ESP32 `DeviceAgent.cpp` viselkedéséhez illesztett polling kliens:
#   • POST /devices/poll (x-device-key header, body: {ping: timestamp})
#     – válasz: {ok, command: {id, payload} | null}
#   • POST /devices/ack  (x-device-key header, body: {commandId, ok, error?})
#
# Parancsok (DeviceAgent.cpp:152-159, looksLikeAudioCommand):
#   • SET_VOLUME (payload.volume 0..10)   → on_set_volume(vol)
#   • MUTE       (payload.mute  bool)     → on_mute(muted)
#   • REBOOT                              → on_reboot()
#   • SHOW_MESSAGE (payload.message)      → on_show_message(msg)
#   • Audio parancsok (PLAY_URL/TTS/BELL/PLAY_AUDIO/MIC_AUDIO/VOICE_MESSAGE
#     vagy bármi url/text mezővel) → NEM hajtjuk végre Python oldalon,
#     mert a hangot a snapclient bináris kapja a snapserveren keresztül.
#     Csak ACK-olunk, így a backend tudja hogy a parancsot átvettük.
#
# Az ACK fontos még a hangos parancsoknál is, mert a backend a `SENT`
# állapotból csak ACK után engedi át a következő parancsot a queue-ban
# (lásd pollCommands - in-flight detektálás).

import json
import threading
import time
import urllib.request
import urllib.error
from typing import Callable, Optional

from config import API_BASE


POLL_INTERVAL_S = 2.0
HTTP_TIMEOUT_S  = 8.0


# A hangos parancsok action-jei – ezekre csak ACK kell, a snapclient
# folyamat valós időben kapja meg a streamet a snapserverről.
_AUDIO_ACTIONS = {
    "PLAY_URL",
    "TTS",
    "BELL",
    "PLAY_AUDIO",
    "MIC_AUDIO",
    "VOICE_MESSAGE",
}


class DeviceAgent:
    """
    Háttér thread, ami 2 másodpercenként pollozza a backend-et a kiküldött
    parancsokért, és a callback-ekkel jelez az alkalmazás felé.

    Hibatűrés:
      • Hálózati hiba → csendes wait + retry a következő ciklusban.
      • Ismeretlen action → ACK ok=false, error="Unknown action: X".

    Lifecycle:
      agent = DeviceAgent(device_key=..., on_set_volume=..., ...)
      agent.start()    # háttér thread
      ...
      agent.stop()     # graceful leállás (max POLL_INTERVAL_S várakozás)
    """

    def __init__(
        self,
        device_key: str,
        on_set_volume:   Optional[Callable[[int], None]]  = None,
        on_mute:         Optional[Callable[[bool], None]] = None,
        on_reboot:       Optional[Callable[[], None]]     = None,
        on_show_message: Optional[Callable[[str], None]]  = None,
        on_audio_command: Optional[Callable[[dict], None]] = None,
    ) -> None:
        self._key             = device_key
        self._on_set_volume   = on_set_volume
        self._on_mute         = on_mute
        self._on_reboot       = on_reboot
        self._on_show_message = on_show_message
        self._on_audio_command = on_audio_command
        self._stop_flag       = False
        self._thread: Optional[threading.Thread] = None

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_flag = False
        self._thread = threading.Thread(
            target=self._loop, name="DeviceAgentPoll", daemon=True,
        )
        self._thread.start()
        print("[Agent] DeviceAgent indult (poll 2s)")

    def stop(self) -> None:
        self._stop_flag = True

    # ── Belső loop ───────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while not self._stop_flag:
            try:
                cmd = self._poll_once()
                if cmd is not None:
                    self._execute(cmd)
            except Exception as e:
                # Csendes – a backend lehet épp restart alatt, vagy nincs net
                print(f"[Agent] poll hiba: {e}")
            time.sleep(POLL_INTERVAL_S)

    # ── HTTP ──────────────────────────────────────────────────────────────────

    def _poll_once(self) -> Optional[dict]:
        body = json.dumps({"ping": int(time.time() * 1000)}).encode()
        req = urllib.request.Request(
            f"{API_BASE}/devices/poll",
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-device-key": self._key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
                data = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            # 401 → nincs még aktív device record (provisioning pending),
            #       ne logoljuk minden pollnál; 5xx esetleg backend hiba.
            if e.code != 401:
                print(f"[Agent] poll HTTP {e.code}: {e.reason}")
            return None

        if not data.get("ok"):
            return None
        return data.get("command")  # None ha nincs új parancs

    def _ack(self, command_id: str, ok: bool, error: str = "") -> None:
        if not command_id:
            return
        body: dict = {"commandId": command_id, "ok": ok}
        if not ok and error:
            body["error"] = error
        try:
            req = urllib.request.Request(
                f"{API_BASE}/devices/ack",
                data=json.dumps(body).encode(),
                headers={
                    "Content-Type": "application/json",
                    "x-device-key": self._key,
                },
                method="POST",
            )
            urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S).read()
        except Exception as e:
            print(f"[Agent] ack hiba: {e}")

    # ── Parancsfeldolgozás ───────────────────────────────────────────────────

    def _execute(self, cmd: dict) -> None:
        command_id = str(cmd.get("id") or "")
        payload    = cmd.get("payload") or {}

        if not isinstance(payload, dict):
            self._ack(command_id, False, "Payload not an object")
            return

        action = str(payload.get("action") or "").upper()

        # Hangos parancs detektálás – DeviceAgent.cpp:188 looksLikeAudioCommand
        is_audio = (
            action in _AUDIO_ACTIONS
            or bool(payload.get("url"))
            or bool(payload.get("text"))
        )
        if is_audio:
            print(f"[Agent] AUDIO command (action={action or '?'}) → ACK only "
                  "(snapclient handles stream)")
            if self._on_audio_command:
                try:
                    self._on_audio_command(payload)
                except Exception as e:
                    print(f"[Agent] on_audio_command hiba: {e}")
            self._ack(command_id, True, "")
            return

        # Vezérlő parancsok
        try:
            if action == "SET_VOLUME":
                vol = payload.get("volume")
                if not isinstance(vol, (int, float)):
                    self._ack(command_id, False, "No volume")
                    return
                vol_i = max(0, min(10, int(vol)))
                print(f"[Agent] SET_VOLUME → {vol_i}")
                if self._on_set_volume:
                    self._on_set_volume(vol_i)
                self._ack(command_id, True, "")

            elif action == "MUTE":
                muted = bool(payload.get("mute", True))
                print(f"[Agent] MUTE → {muted}")
                if self._on_mute:
                    self._on_mute(muted)
                self._ack(command_id, True, "")

            elif action == "REBOOT":
                print("[Agent] REBOOT")
                # ACK ELŐSZÖR, hogy a backend lássa a sikert. Csak utána
                # hívjuk a callback-et (ami valószínűleg sys.exit-tel zár).
                self._ack(command_id, True, "")
                if self._on_reboot:
                    self._on_reboot()

            elif action == "SHOW_MESSAGE":
                msg = str(payload.get("message") or "")
                if not msg:
                    self._ack(command_id, False, "No message")
                    return
                print(f"[Agent] SHOW_MESSAGE: {msg}")
                if self._on_show_message:
                    self._on_show_message(msg)
                self._ack(command_id, True, "")

            else:
                err = f"Unknown action: {action or '(empty)'}"
                print(f"[Agent] {err}")
                self._ack(command_id, False, err)

        except Exception as e:
            err = f"Exception: {e}"
            print(f"[Agent] execute hiba: {err}")
            self._ack(command_id, False, err)
