# player/app.py

import sys
import time
import threading
import datetime
from typing import Optional

import api_client    as api
import audio_manager as audio
import bell_calendar
from api_client       import DEVICE_KEY, SHORT_ID, HARDWARE_ID
from config           import load_settings, save_settings
from snapcast_manager import SnapcastManager
from sync_client      import SyncClient
from updater_client   import AutoUpdater
from system_volume    import set_system_volume, set_system_mute
from ui               import PlayerUI

BEACON_INTERVAL_S = 30
POLL_INTERVAL_S   = 5


class SchoolLiveApp:
    def __init__(self, ui: PlayerUI):
        self.ui        = ui
        self._settings = load_settings()
        self._bells:   list = []
        # A self._bells melyik napra érvényes – ld. Windows app.py.
        self._bells_date: Optional[datetime.date] = None
        self._status   = "provisioning"
        self._online   = False
        self._last_bell_key = ""
        self._device_id: Optional[str] = None
        self._snap_muted = False

        self._snap = SnapcastManager(
            on_connected    = self._on_snap_connected,
            on_disconnected = self._on_snap_disconnected,
            on_error        = self._on_snap_error,
        )

        self._ws = SyncClient(
            on_prepare      = self._on_prepare,
            on_play         = self._on_play,
            on_immediate    = self._on_immediate,
            on_connected    = self._on_ws_connected,
            on_disconnected = self._on_ws_disconnected,
            on_device_id    = self._on_ws_device_id,
            device_key      = DEVICE_KEY,
        )

        self._pending: dict = {}

        ui.on_volume_change = self._handle_volume
        self._volume = self._settings.get("volume", 7)
        ui.set_volume_display(self._volume)
        self._handle_volume(self._volume)

        # SET_VOLUME/MUTE/REBOOT/SHOW_MESSAGE mostantól a WS `_on_immediate`-en
        # érkeznek real-time (nem a korábbi 2s-es HTTP /devices/poll-on
        # keresztül) – ld. device_agent.py (törölve) helyett fent.

        self._updater = AutoUpdater(
            on_update_available = self._on_update_available,
            on_downloading      = self._on_update_downloading,
            on_ready_to_install = self._on_update_ready,
            on_error            = lambda e: print(f"[Update] {e}"),
        )

        self._boot()

    # ── Boot ──────────────────────────────────────────────────────────────────
    def _boot(self) -> None:
        threading.Thread(target=self._provision_loop, daemon=True).start()

    def _provision_loop(self) -> None:
        status = api.provision()
        self.ui.show_pending()

        if status == "active":
            print(f"[App] Már aktivált: {SHORT_ID}")
            self.ui.hide_pending()
            self._activate()
            return

        print(f"[App] Provisioning mód: {SHORT_ID}")
        while True:
            time.sleep(POLL_INTERVAL_S)
            status = api.poll_status()
            if status == "active":
                self.ui.hide_pending()
                self._activate()
                return
            api.provision()

    # ── Aktiválás ─────────────────────────────────────────────────────────────
    def _activate(self) -> None:
        print(f"[App] Aktiválva: {SHORT_ID}")
        self._status = "active"

        def _fetch_info():
            # Tenant neve
            name = api.fetch_tenant_name(DEVICE_KEY)
            if name:
                self.ui.set_institution(name)

            # Device ID (targeting)
            device_id = api.get_device_id(DEVICE_KEY)
            if device_id:
                self._device_id = device_id
                print(f"[App] Device ID: {self._device_id}")

            # Snap port lekérése – MAJD snap indítás
            snap_port = api.fetch_snap_port(DEVICE_KEY)
            if snap_port:
                print(f"[App] Snap port: {snap_port}")
                self._snap.set_port(snap_port)
            else:
                print("[App] ⚠ Snap port lekérés sikertelen, alapértelmezett 1704")

            if self._snap.available:
                self._snap.start()
                self.ui.set_snap_status("⏳ Snapcast csatlakozás...")
            else:
                self.ui.set_snap_status("🔊 Belső lejátszó (snapclient nélkül)")

        threading.Thread(target=_fetch_info, daemon=True).start()

        # WS SyncCast
        self._ws.start()

        # Háttér taskok
        threading.Thread(target=self._sync_bells,     daemon=True).start()
        threading.Thread(target=self._bell_tick_loop, daemon=True).start()
        threading.Thread(target=self._beacon_loop,    daemon=True).start()

        self._updater.start()

    # ── Snap callbacks ────────────────────────────────────────────────────────
    def _on_snap_connected(self) -> None:
        self.ui.set_snap_status("🔊 Snapcast csatlakozva")

    def _on_snap_disconnected(self) -> None:
        self.ui.set_snap_status("⚠ Snapcast lecsatlakozva")
        self.ui.hide_overlay()

    def _on_snap_error(self, msg: str) -> None:
        self.ui.set_snap_status(f"❌ {msg[:40]}")

    # ── WS callbacks ──────────────────────────────────────────────────────────
    def _on_ws_connected(self) -> None:
        self._online = True
        self.ui.set_online(True)

    def _on_ws_disconnected(self) -> None:
        self._online = False
        self.ui.set_online(False)

    def _on_ws_device_id(self, device_id: str) -> None:
        """HELLO deviceId – korábban csak a HTTP beacon válaszából ismertük
        meg. Csak egyszer mentjük el (idempotens, akárhányszor csatlakozunk
        újra)."""
        if not self._device_id:
            self._device_id = device_id
            api.save_device_id(device_id)
            print(f"[App] Device ID (HELLO): {device_id}")

    # ── PREPARE ───────────────────────────────────────────────────────────────
    def _on_prepare(self, msg: dict) -> None:
        command_id  = msg.get("commandId", "")
        action      = msg.get("action", "")
        url         = msg.get("url")
        snap_active = msg.get("snapcastActive", False)

        # Eszközönkénti célzás
        target_ids = msg.get("targetDeviceIds")
        if target_ids is not None and isinstance(target_ids, list):
            is_targeted = (self._device_id in target_ids) if self._device_id else True
            if not is_targeted and not self._snap_muted:
                print("[App] Eszköz nem célzott → snap mute ON")
                self._snap_muted = True
                self._snap.mute(True)
            elif is_targeted and self._snap_muted:
                print("[App] Eszköz célzott → snap mute OFF")
                self._snap_muted = False
                self._snap.mute(False)
        else:
            if self._snap_muted:
                self._snap_muted = False
                self._snap.mute(False)

        self._pending[command_id] = {
            "action":      action,
            "url":         url,
            "text":        msg.get("text"),
            "title":       msg.get("title"),
            "snap_active": snap_active,
        }
        self._ws.send_ack(command_id, 0)

    # ── PLAY ──────────────────────────────────────────────────────────────────
    def _on_play(self, msg: dict) -> None:
        command_id = msg.get("commandId", "")
        play_at_ms = msg.get("playAtMs")
        prepare    = self._pending.pop(command_id, None)
        if not prepare:
            return

        # PREPARE-en NEM célzott eszközön se snap-en, se lokálisan ne szóljon
        # a hang, és HUD se jelenjen meg. A `_snap_muted` flag-et a PREPARE
        # állítja be a `targetDeviceIds` alapján. (Egyezően az Android
        # `applyTargeting` skip-pel.)
        if self._snap_muted:
            print(f"[App] PLAY: nem-célzott eszköz (snap muted), skip")
            return

        server_now = self._ws.clock.server_now_ms()
        if play_at_ms:
            delay_ms = (play_at_ms - server_now)
            if delay_ms < -10_000:
                # 10 másodpercnél régebbi parancs – eldobjuk
                print(f"[App] PLAY stale ({-delay_ms:.0f}ms régen volt) → skip")
                return
            delay_ms = max(0, delay_ms)
        else:
            delay_ms = 0

        ctx = {
            "action":      prepare.get("action", ""),
            "url":         prepare.get("url"),
            "text":        prepare.get("text"),
            "title":       prepare.get("title"),
            "snap_usable": bool(
                prepare.get("snap_active", False)
                and self._snap.available
                and self._snap.connected
                and not self._snap_muted
            ),
            "volume":      self._volume,
            "delay_ms":    delay_ms,
        }

        def _execute(ctx=ctx):
            if ctx["delay_ms"] > 50:
                time.sleep(ctx["delay_ms"] / 1000)

            action      = ctx["action"]
            url         = ctx["url"]
            snap_usable = ctx["snap_usable"]
            volume      = ctx["volume"]

            print(f"[App] PLAY action={action} snap_usable={snap_usable} "
                  f"muted={self._snap_muted}")

            if action == "TTS":
                text = ctx.get("text", "")
                if text:
                    reading_ms = self._calc_reading_ms(text)
                    self.ui.show_message_overlay(text, reading_ms)
                if not snap_usable and url:
                    audio.play_url(url, volume / 10)

            elif action == "PLAY_URL":
                self.ui.show_radio_overlay("Iskolarádió")
                if not snap_usable and url:
                    audio.play_url(url, volume / 10)

            elif action == "BELL":
                self.ui.show_bell_banner(True)
                if not snap_usable and url:
                    sound_file = url.split("/")[-1]
                    audio.play_bell(sound_file, volume / 10,
                                    on_done=lambda: self.ui.show_bell_banner(False))
                else:
                    threading.Timer(3.0, lambda: self.ui.show_bell_banner(False)).start()

        threading.Thread(target=_execute, daemon=True).start()

    # ── Azonnali broadcast ────────────────────────────────────────────────────
    def _on_immediate(self, msg: dict) -> None:
        action      = msg.get("action", "")
        snap_active = msg.get("snapcastActive", False)
        snap_usable = (
            snap_active
            and self._snap.available
            and self._snap.connected
            and not self._snap_muted
        )

        # HUD-célzás: a NOW_PLAYING_INFO / immediate BELL / TTS / PLAY_URL
        # broadcast minden tenant-eszközre megy (a backend source:start
        # eventjén nincs targeting-lista). Az utolsó PREPARE célzása alapján
        # `_snap_muted` jelzi, hogy a kliens hallja-e a snap streamet. Ha
        # nem hallja, akkor HUD-ot sem mutatunk – egyezően az Android
        # kliens viselkedésével.
        if self._snap_muted and action in ("BELL", "TTS", "PLAY_URL", "NOW_PLAYING_INFO"):
            print(f"[App] {action}: snap muted (nem célzott) → HUD skip")
            return

        if action == "BELL":
            url = msg.get("url", "")
            now = datetime.datetime.now()
            key = f"{now.hour}:{now.minute}"
            if self._last_bell_key == key:
                return
            self._last_bell_key = key
            self.ui.show_bell_banner(True)
            if not snap_usable and url:
                sound_file = url.split("/")[-1]
                audio.play_bell(sound_file, self._volume / 10,
                                on_done=lambda: self.ui.show_bell_banner(False))
            else:
                threading.Timer(3.0, lambda: self.ui.show_bell_banner(False)).start()

        elif action == "TTS":
            url  = msg.get("url")
            text = msg.get("text", "")
            if text:
                reading_ms = self._calc_reading_ms(text)
                self.ui.show_message_overlay(text, reading_ms)
            if not snap_usable and url:
                audio.play_url(url, self._volume / 10)

        elif action == "PLAY_URL":
            self.ui.show_radio_overlay("Iskolarádió")
            url = msg.get("url")
            if not snap_usable and url:
                audio.play_url(url, self._volume / 10)

        elif action == "STOP_PLAYBACK":
            audio.stop()
            self.ui.hide_overlay()
            self.ui.show_bell_banner(False)
            if self._snap_muted:
                print("[App] STOP_PLAYBACK → snap unmute + restart")
                self._snap_muted = False
                self._snap.mute(False)
                self._snap.restart()

        elif action == "NOW_PLAYING_INFO":
            # Backend forrás-start event: az aktuálisan szóló forrás
            # nevének/típusának HUD-frissítése. A snap stream folyamatosan szól,
            # csak az UI-t frissítjük – nincs snap-restart, nincs mute.
            # (Targeting-gate fent: ha `_snap_muted` aktív, már visszatértünk.)
            title = msg.get("title") or ""
            job_type = msg.get("jobType") or ""
            source_type = msg.get("sourceType") or ""
            print(f"[App] NOW_PLAYING_INFO: {job_type} '{title}' (source={source_type})")
            try:
                if hasattr(self.ui, "show_now_playing"):
                    self.ui.show_now_playing(title, job_type)
                elif title and hasattr(self.ui, "show_radio_overlay"):
                    # Fallback a meglévő overlay-re a stream-szerű kijelzéshez
                    self.ui.show_radio_overlay(title, 0)
            except Exception as e:
                print(f"[App] NOW_PLAYING_INFO UI hiba: {e}")

        elif action == "SYNC_BELLS":
            threading.Thread(target=self._sync_bells, daemon=True).start()

        # ── Vezérlő parancsok (korábban csak a HTTP /devices/poll DeviceAgent
        # kapta meg ezeket – ld. device_agent.py _execute). A commandId jelenléte
        # esetén CMD_ACK-ot küldünk, hogy a backend ACKED-re állítsa a parancsot
        # (ne duplikálódjon, ha a kliens időközben újracsatlakozik).
        elif action == "SET_VOLUME":
            command_id = msg.get("commandId", "")
            vol = msg.get("volume")
            if not isinstance(vol, (int, float)):
                if command_id:
                    self._ws.send_cmd_ack(command_id, False, "No volume")
                return
            vol_i = max(0, min(10, int(vol)))
            print(f"[App] SET_VOLUME (WS) → {vol_i}")
            self._on_remote_set_volume(vol_i)
            if command_id:
                self._ws.send_cmd_ack(command_id, True)

        elif action == "MUTE":
            command_id = msg.get("commandId", "")
            muted = bool(msg.get("mute", True))
            print(f"[App] MUTE (WS) → {muted}")
            self._on_remote_mute(muted)
            if command_id:
                self._ws.send_cmd_ack(command_id, True)

        elif action == "REBOOT":
            command_id = msg.get("commandId", "")
            print("[App] REBOOT (WS)")
            # ACK ELŐSZÖR, hogy a backend lássa a sikert, mielőtt a kliens
            # kilép (ld. _on_remote_reboot).
            if command_id:
                self._ws.send_cmd_ack(command_id, True)
            self._on_remote_reboot()

        elif action == "SHOW_MESSAGE":
            command_id = msg.get("commandId", "")
            text = str(msg.get("message") or "")
            if not text:
                if command_id:
                    self._ws.send_cmd_ack(command_id, False, "No message")
                return
            print(f"[App] SHOW_MESSAGE (WS): {text}")
            self._on_remote_show_message(text)
            if command_id:
                self._ws.send_cmd_ack(command_id, True)

    # ── Csengetési rend ───────────────────────────────────────────────────────
    def _sync_bells(self) -> None:
        data = api.fetch_bells(DEVICE_KEY)
        if not data:
            self.ui.set_cache_status("⚠ Csengetési rend lekérés sikertelen")
            return

        bells = [] if data.get("isHoliday") else (data.get("bells") or [])
        self._bells      = bells
        self._bells_date = datetime.date.today()
        if bells:
            audio.prefetch_bells(bells)
            self.ui.set_bells(bells)
            self.ui.set_cache_status(f"🔔 {len(bells)} csengő betöltve")
        else:
            self.ui.set_cache_status("⚠ Csengetési rend üres (ünnepnap)" if data.get("isHoliday") else "⚠ Csengetési rend üres")

        # Teljes tanévnyi naptár mentése lokálisan – ld. Windows app.py.
        bell_calendar.save_full_year_calendar(data)

    def _bells_for_today(self) -> list:
        """A ma érvényes csengetési lista – napváltás esetén a lokálisan
        mentett teljes tanévnyi naptárból oldja fel. Ld. Windows app.py."""
        today = datetime.date.today()
        if self._bells_date == today:
            return self._bells

        bells, is_holiday = bell_calendar.resolve_bells_for_date(today)
        self._bells      = bells
        self._bells_date = today
        if is_holiday:
            print(f"[App] Naptár szerint ma ünnepnap/hétvége ({today.isoformat()}) – nincs csengetés")
        else:
            print(f"[App] Napváltás – naptárból feloldva: {len(bells)} csengő ({today.isoformat()})")
        return bells

    # ── Beacon loop ───────────────────────────────────────────────────────────
    #
    # WS-alapú beacon (korábban 30s-enkénti HTTP POST /devices/native/beacon
    # volt) – ugyanaz a payload, csak a SyncEngine WS "BEACON" handlere
    # dolgozza fel. A deviceId-t már nem a beacon válaszából tanuljuk meg,
    # hanem a HELLO üzenetből (ld. _on_ws_device_id).
    def _beacon_loop(self) -> None:
        while True:
            try:
                status_payload = {
                    "snapConnected": bool(self._snap.connected),
                    "wsOnline":      bool(self._online),
                    "hardwareId":    HARDWARE_ID,
                    "shortId":       SHORT_ID,
                    "platform":      "linux",
                    "appVersion":    "1.1.0",
                }
                self._ws.send_beacon(
                    volume=int(self._volume),
                    muted=bool(self._snap_muted),
                    firmware_version="1.1.0",
                    status_payload=status_payload,
                )
            except Exception:
                pass
            time.sleep(BEACON_INTERVAL_S)

    # ── Remote parancsok (DeviceAgent callback-ek) ────────────────────────────

    def _on_remote_set_volume(self, vol: int) -> None:
        """Backend SET_VOLUME parancs → UI + Snap volume frissítés (0..10)."""
        try:
            self.ui.set_volume_display(vol)
        except Exception:
            pass
        self._handle_volume(vol)

    def _on_remote_mute(self, muted: bool) -> None:
        """Backend MUTE parancs → snap mute toggle + OS master mute."""
        self._snap_muted = muted
        try:
            self._snap.mute(muted)
        except Exception as e:
            print(f"[App] remote mute hiba: {e}")
        # OS master mute is – ha a system mixer-ben le van halkítva, hiába
        # tartja a snap a saját puffer-gain-jét magasan, a hang nem hallatszik.
        set_system_mute(muted)

    def _on_remote_reboot(self) -> None:
        """Backend REBOOT parancs → kilépés (systemd / launcher visszahozza)."""
        print("[App] REBOOT parancs érkezett, kilépés...")
        time.sleep(1)  # várjuk meg, hogy a CMD_ACK WS-frame kimenjen
        sys.exit(0)

    def _on_remote_show_message(self, msg: str) -> None:
        """Backend SHOW_MESSAGE parancs → UI banner (best effort)."""
        try:
            if hasattr(self.ui, "show_banner"):
                self.ui.show_banner(msg)
            elif hasattr(self.ui, "show_message_overlay"):
                self.ui.show_message_overlay(msg, 10000)
            else:
                print(f"[App] SHOW_MESSAGE (UI nincs): {msg}")
        except Exception as e:
            print(f"[App] show_message hiba: {e}")

    def _bell_tick_loop(self) -> None:
        while True:
            time.sleep(5)
            if self._status != "active":
                continue
            bells = self._bells_for_today()
            if not bells:
                continue
            now = datetime.datetime.now()
            if now.second > 58:
                continue
            key = f"{now.hour}:{now.minute}"
            if self._last_bell_key == key:
                continue
            due = next(
                (b for b in bells
                 if b["hour"] == now.hour and b["minute"] == now.minute),
                None,
            )
            if not due:
                continue
            if not self._online:
                self._last_bell_key = key
                self.ui.show_bell_banner(True)
                audio.play_bell(
                    due["soundFile"], self._volume / 10,
                    on_done=lambda: self.ui.show_bell_banner(False),
                )

    # ── Hangerő ───────────────────────────────────────────────────────────────
    def _handle_volume(self, vol: int) -> None:
        self._volume = vol
        # 1) snapclient saját puffer-gain (app-szint, csak a snap stream-re hat)
        self._snap.set_volume(int(vol * 10))
        # 2) OS master mixer is mozogjon, hogy a "10/10" tényleg max hangerő
        #    legyen a fizikai kimeneten (pactl/amixer/nircmd, ha elérhető)
        set_system_volume(int(vol * 10))
        self._settings["volume"] = vol
        save_settings(self._settings)

    # ── Auto-update ───────────────────────────────────────────────────────────
    def _on_update_available(self, tag: str) -> None:
        self.ui.show_update_banner(f"Új verzió érhető el: {tag} – letöltés...")

    def _on_update_downloading(self, pct: int) -> None:
        self.ui.show_update_banner(f"Letöltés: {pct}%")

    def _on_update_ready(self) -> None:
        self.ui.show_update_banner(
            "Frissítés kész – kattints a telepítéshez",
            on_click=self._install_update,
        )

    def _install_update(self) -> None:
        self._updater.install_now()

    @staticmethod
    def _calc_reading_ms(text: str) -> int:
        chars = len(text.strip())
        return max(6000, min(30000, chars * 300))