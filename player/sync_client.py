# schoollive_player/sync_client.py
#
# SyncCast WebSocket kliens – ugyanaz a protokoll mint VirtualPlayer.tsx
# PREPARE → READY_ACK → PLAY → lejátszás

import json
import time
import asyncio
import threading
import urllib.request
from api_client import SHORT_ID
from typing   import Optional, Callable
from config   import WS_URL, API_BASE

try:
    import websockets
    WS_AVAILABLE = True
except ImportError:
    WS_AVAILABLE = False

# ── Időszinkron ────────────────────────────────────────────────────────────────
class ClockSync:
    def __init__(self):
        self._offset_ms = 0.0   # szerveridő - lokális idő

    def sync(self) -> None:
        samples = []
        for _ in range(6):
            try:
                t0 = time.monotonic()
                resp = urllib.request.urlopen(
                    f"{API_BASE}/time", timeout=3
                )
                t1   = time.monotonic()
                data = json.loads(resp.read())
                rtt_ms = (t1 - t0) * 1000
                if rtt_ms < 300:
                    server_now = data["now"]
                    local_now  = time.time() * 1000
                    samples.append((server_now - local_now - rtt_ms / 2, rtt_ms))
            except Exception:
                pass
            time.sleep(0.1)

        if samples:
            samples.sort(key=lambda x: x[1])
            best = [s[0] for s in samples[:4]]
            best.sort()
            self._offset_ms = best[len(best) // 2]
            print(f"[ClockSync] offset={self._offset_ms:.1f}ms")

    def server_now_ms(self) -> float:
        return time.time() * 1000 + self._offset_ms


# ── SyncCast kliens ────────────────────────────────────────────────────────────
class SyncClient:
    def __init__(self,
                 on_prepare:   Callable[[dict], None],
                 on_play:      Callable[[dict], None],
                 on_immediate: Callable[[dict], None],
                 on_connected: Optional[Callable] = None,
                 on_disconnected: Optional[Callable] = None,
                 device_key: Optional[str] = None):
        self._on_prepare      = on_prepare
        self._on_play         = on_play
        self._on_immediate    = on_immediate
        self._on_connected    = on_connected
        self._on_disconnected = on_disconnected
        self._device_key      = device_key   # JWT helyett device key (mint ESP32)
        self._ws              = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._running         = False
        self.clock            = ClockSync()
        self._reconnect_delay = 3

    def start(self) -> None:
        if not WS_AVAILABLE:
            print("[SyncClient] websockets csomag nem elérhető")
            return
        self._running = True
        self._thread  = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)

    def send_ack(self, command_id: str, buffer_ms: int) -> None:
        if self._ws and self._loop:
            msg = json.dumps({
                "type":      "READY_ACK",
                "commandId": command_id,
                "deviceId":  SHORT_ID,
                "bufferMs":  buffer_ms,
                "readyAt":   self._iso_now(),
            })
            asyncio.run_coroutine_threadsafe(
                self._ws.send(msg), self._loop
            )

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._connect_loop())

    async def _connect_loop(self) -> None:
        while self._running:
            # Device key auth (mint ESP32) – JWT nélkül
            if self._device_key:
                url = f"{WS_URL}?deviceKey={self._device_key}"
            else:
                await asyncio.sleep(5)
                continue
            try:
                async with websockets.connect(
                    url,
                    ping_interval=25,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    self._ws = ws
                    print("[SyncClient] Csatlakozva")

                    # Időszinkron háttérben
                    asyncio.get_event_loop().run_in_executor(
                        None, self.clock.sync
                    )

                    if self._on_connected:
                        self._on_connected()

                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                        except Exception:
                            continue
                        self._handle(msg)

            except websockets.exceptions.ConnectionClosedError as e:
                if e.code == 4010:
                    # Saját magunk váltottuk le (másik példány) – várunk hosszabbat
                    print(f"[SyncClient] 4010 – replaced, újrapróbálás 10s múlva")
                    await asyncio.sleep(10)
                else:
                    print(f"[SyncClient] WS hiba: {e}")
            except Exception as e:
                print(f"[SyncClient] WS hiba: {e}")
            finally:
                self._ws = None
                if self._on_disconnected:
                    self._on_disconnected()

            if not self._running:
                break
            await asyncio.sleep(self._reconnect_delay)

    def _handle(self, msg: dict) -> None:
        if msg.get("type") == "HELLO":
            # Durva időszinkron HELLO alapján
            try:
                server_now = int(msg["serverNowMs"])
                local_now  = time.time() * 1000
                self.clock._offset_ms = server_now - local_now
            except Exception:
                pass
            return

        phase = msg.get("phase")
        if phase == "PREPARE":
            self._on_prepare(msg)
            return
        if phase == "PLAY":
            self._on_play(msg)
            return

        # Azonnali broadcast (BELL, STOP_PLAYBACK, SYNC_BELLS stb.)
        if msg.get("action"):
            self._on_immediate(msg)

    @staticmethod
    def _iso_now() -> str:
        import datetime
        return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"