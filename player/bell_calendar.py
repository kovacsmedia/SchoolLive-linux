# player/bell_calendar.py
#
# Teljes tanévnyi csengetési naptár – lokális tárolás + offline feloldás.
#
# A /bells/sync válasz (api_client.fetch_bells) a "ma" nézet mellett a teljes
# tanév (szept 1 - júl 1) sablonjait és naptár-kivételeit (ünnepnap / egyedi
# sablon adott napra) is tartalmazza. Ezt mentjük el lokálisan, hogy az
# eszköz akkor is helyesen ki tudja számolni BÁRMELYIK nap csengetési
# rendjét, ha napváltáskor (vagy hosszabb ideig) nincs internetkapcsolata –
# nem csak a legutóbb lekért "ma" listára van korlátozva.
#
# Ugyanaz a feloldási logika, mint a backend bell.scheduler.ts-ében és az
# ESP32 BellManager::resolveFullYearForDate()-jében:
#   1. Explicit naptár-kivétel erre a napra (ünnepnap vagy egyedi sablon)
#   2. Nincs kivétel → hétvégén nincs csengetés
#   3. Egyébként a default sablon

import json
import datetime
from pathlib import Path
from typing import Optional

from config import get_data_dir

_CALENDAR_FILE = "bell_calendar.json"


def save_full_year_calendar(data: dict) -> None:
    """A /bells/sync válasz releváns almezőit menti lokális JSON fájlba.

    Csendben kihagyja a mentést, ha a válasz nem tartalmazza az additív
    mezőket (régi backend, vagy /bells/sync hiba esetén üres dict) – ilyenkor
    a korábban mentett (vagy hiányzó) naptár marad érvényben."""
    full_year_version = data.get("fullYearVersion")
    if not full_year_version:
        return
    try:
        path = get_data_dir() / _CALENDAR_FILE
        payload = {
            "fullYearVersion":   full_year_version,
            "templates":         data.get("templates", []),
            "calendar":          data.get("calendar", []),
            "defaultTemplateId": data.get("defaultTemplateId"),
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
    except Exception as e:
        print(f"[BellCalendar] mentés hiba: {e}")


def _load_full_year_calendar() -> Optional[dict]:
    try:
        path = get_data_dir() / _CALENDAR_FILE
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[BellCalendar] betöltés hiba: {e}")
        return None


def resolve_bells_for_date(date: datetime.date):
    """Visszaadja a (bells, is_holiday) párt egy adott napra a lokálisan
    mentett tanévnyi naptár alapján. Ha nincs mentett naptár, vagy nem
    oldható fel (nincs default sablon sem), üres listát ad vissza.

    Visszatérési érték típusa: tuple[list, bool] – nincs explicit
    subscriptelt annotáció (PEP 585, csak Python 3.9+), hogy régebbi
    Python-nal futó telepítéseken se törjön."""
    cal = _load_full_year_calendar()
    if not cal:
        return [], False

    date_str = date.isoformat()
    templates: list = cal.get("templates") or []
    calendar_days: list = cal.get("calendar") or []
    default_template_id = cal.get("defaultTemplateId")

    target_template_id: Optional[str] = None
    has_calendar_entry = False

    for d in calendar_days:
        if d.get("date") != date_str:
            continue
        has_calendar_entry = True
        if d.get("isHoliday"):
            return [], True
        target_template_id = d.get("templateId")
        break

    if not has_calendar_entry:
        # Hétvégén (szombat=5, vasárnap=6) nincs csengetés, ha nincs
        # explicit naptár-kivétel – egyezően a backend online
        # bell.scheduler.ts viselkedésével.
        if date.weekday() >= 5:
            return [], True
        target_template_id = default_template_id

    if not target_template_id:
        return [], False

    for tpl in templates:
        if tpl.get("id") == target_template_id:
            return tpl.get("bells") or [], False

    return [], False
