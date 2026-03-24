# player/api_client.py
#
# Native player API kliens – JWT nélkül, MAC alapú provisioning
# Aktiválás után deviceKey-jel csatlakozik a WS-re (mint ESP32)
#
# Változások:
#   • fetch_snap_port(device_key) – tenant snapserver port lekérése (pl. 1801)
#   • get_device_id(device_key)   – saját eszköz UUID lekérése (targeting-hez)
#   • save_device_id / get_cached_device_id – lokális cache
#   • Dead code eltávolítva a fetch_tenant_name végéről

import json
import uuid
import hashlib
import platform
import threading
import urllib.request
import urllib.error
from pathlib import Path
from typing  import Optional

from config import API_BASE, get_data_dir

# ── Hardware ID (MAC cím alapú) ───────────────────────────────────────────────
def get_hardware_id() -> str:
    """
    MAC cím alapú hardware ID.
    Formátum: "aa:bb:cc:dd:ee:ff"
    Ha nem sikerül lekérni, UUID alapú fallback (elmentve).
    """
    cache = get_data_dir() / "hardware_id.txt"
    if cache.exists():
        hid = cache.read_text().strip()
        if hid:
            return hid

    try:
        mac_int = uuid.getnode()
        if (mac_int >> 40) & 1:
            raise ValueError("Multicast bit set – szoftveres MAC")
        mac = ":".join(f"{(mac_int >> (8*i)) & 0xff:02x}" for i in range(5, -1, -1))
    except Exception:
        mac = str(uuid.uuid4())

    cache.write_text(mac)
    return mac

# ── Short ID (WP-XXXXXXXX) ────────────────────────────────────────────────────
def get_short_id(hardware_id: str) -> str:
    """MAC cím → determinisztikus WP-XXXXXXXX azonosító."""
    h = hashlib.sha256(hardware_id.encode()).hexdigest()[:8].upper()
    return f"WP-{h}"

# ── Device Key ────────────────────────────────────────────────────────────────
def get_or_create_device_key() -> str:
    """
    Első indításkor random UUID generálás + mentés.
    Ezzel a kulccsal csatlakozik a WS-re aktiválás után.
    """
    key_file = get_data_dir() / "device_key.txt"
    if key_file.exists():
        key = key_file.read_text().strip()
        if key:
            return key
    key = str(uuid.uuid4())
    key_file.write_text(key)
    return key

def get_device_key_hash(device_key: str) -> str:
    """bcrypt hash, fallback sha256 ha nincs bcrypt."""
    try:
        import bcrypt
        return bcrypt.hashpw(device_key.encode(), bcrypt.gensalt()).decode()
    except ImportError:
        return hashlib.sha256(device_key.encode()).hexdigest()

# ── Singleton értékek ─────────────────────────────────────────────────────────
HARDWARE_ID = get_hardware_id()
SHORT_ID    = get_short_id(HARDWARE_ID)
DEVICE_KEY  = get_or_create_device_key()
CLIENT_ID   = SHORT_ID   # kompatibilitás a sync_client.py-val

# ── HTTP helper ───────────────────────────────────────────────────────────────
def _request(method: str, path: str, body: Optional[dict] = None,
             timeout: int = 8, device_key: Optional[str] = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if device_key:
        headers["x-device-key"] = device_key

    data = json.dumps(body).encode() if body is not None else None
    req  = urllib.request.Request(
        f"{API_BASE}{path}", data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.reason}")

# ── Native Player Provisioning ────────────────────────────────────────────────
def provision() -> str:
    """
    Regisztrálja az eszközt a backenddel.
    Visszatér: "active" vagy "pending"
    """
    device_key_hash = get_device_key_hash(DEVICE_KEY)
    try:
        resp = _request("POST", "/devices/native/provision", {
            "hardwareId":    HARDWARE_ID,
            "deviceKeyHash": device_key_hash,
            "shortId":       SHORT_ID,
            "platform":      platform.system().lower(),
            "version":       "1.0.0",
            "userAgent":     f"{platform.system()} {platform.release()}",
        })
        # Ha a backend visszaadja a device UUID-t, mentsük el
        device_id = resp.get("deviceId")
        if device_id:
            save_device_id(device_id)
        return resp.get("status", "pending")
    except Exception as e:
        print(f"[API] Provisioning hiba: {e}")
        return "pending"

def poll_status() -> str:
    """Ellenőrzi hogy az eszköz aktív-e már."""
    try:
        resp = _request("GET", f"/devices/native/status/{HARDWARE_ID}")
        # Státusz ellenőrzéskor is elmentjük a device ID-t ha megérkezett
        device_id = resp.get("deviceId")
        if device_id:
            save_device_id(device_id)
        return resp.get("status", "pending")
    except Exception:
        return "pending"

# ── Device ID cache (targeting-hez) ──────────────────────────────────────────
def get_cached_device_id() -> Optional[str]:
    """Lokálisan cachelt device UUID visszaadása."""
    p = get_data_dir() / "device_id.txt"
    if p.exists():
        return p.read_text().strip() or None
    return None

def save_device_id(device_id: str) -> None:
    """Device UUID mentése lokálisan."""
    (get_data_dir() / "device_id.txt").write_text(device_id)

def get_device_id(device_key: str) -> Optional[str]:
    """
    Saját eszköz UUID lekérése a backendről (targeting-hez).
    Először a lokális cache-t nézi, ha nincs → /devices/native/info lekérdezés.
    Az UUID-t a backend az aktiváláskor rendeli az eszközhöz.
    """
    # Lokális cache elsőbbsége (provision / poll_status már elmenthette)
    cached = get_cached_device_id()
    if cached:
        return cached

    # Backend lekérés: /devices/native/info visszaadja a deviceId-t is
    try:
        req = urllib.request.Request(
            f"{API_BASE}/devices/native/info",
            headers={"x-device-key": device_key},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
            device_id = data.get("deviceId")
            if device_id:
                save_device_id(device_id)
                print(f"[API] Device ID lekérve: {device_id}")
            return device_id
    except Exception as e:
        print(f"[API] get_device_id hiba: {e}")
        return None

# ── Snap port lekérése ────────────────────────────────────────────────────────
def fetch_snap_port(device_key: str) -> Optional[int]:
    """
    Tenant-specifikus Snapcast szerver port lekérése.
    Pl. Demo suli → 1801, Ilosvai → 1800, Bárczay → 1802.
    Endpoint: GET /devices/native/snap-port
    Header: x-device-key
    """
    try:
        req = urllib.request.Request(
            f"{API_BASE}/devices/native/snap-port",
            headers={"x-device-key": device_key},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
            port = data.get("snapPort")
            if port:
                print(f"[API] Snap port: {port}")
                return int(port)
            print("[API] Snap port hiányzik a válaszból")
            return None
    except Exception as e:
        print(f"[API] fetch_snap_port hiba: {e}")
        return None

# ── Bell lekérés ──────────────────────────────────────────────────────────────
def fetch_bells(device_key: str) -> list:
    """Csengetési rend lekérése – device key auth headerrel."""
    try:
        req = urllib.request.Request(
            f"{API_BASE}/bells/today",
            headers={"Content-Type": "application/json",
                     "x-device-key": device_key},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
            return data.get("bells", [])
    except Exception as e:
        print(f"[API] fetchBells hiba: {e}")
        return []

# ── Tenant info ───────────────────────────────────────────────────────────────
def fetch_tenant_name(device_key: str) -> Optional[str]:
    """Visszaadja az intézmény nevét device key alapján."""
    try:
        req = urllib.request.Request(
            f"{API_BASE}/devices/native/info",
            headers={"x-device-key": device_key},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
            # Device ID-t is mentsük ha visszajön
            device_id = data.get("deviceId")
            if device_id:
                save_device_id(device_id)
            return data.get("tenantName")
    except Exception as e:
        print(f"[API] fetch_tenant_name hiba: {e}")
        return None