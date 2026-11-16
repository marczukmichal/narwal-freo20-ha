"""Cleaning settings and room choice as entities, so Assist and automations can
start the same room clean the Odkurzacz window starts (what the Narwal Flow
integration offered as its selects, room switches and clean_rooms).

The values live in Home Assistant (restored after restarts); nothing is sent
to the robot until "Narwal clean selected rooms" is pressed.
"""
from __future__ import annotations

from homeassistant.core import HomeAssistant, callback

from . import sprzatanie

DOMAIN = "narwal_cloud"

# key, name, options (internal value, label), default
USTAWIENIA = (
    ("tryb", "Narwal room clean mode", [("vacuum_and_mop", "Vacuum and mop"), ("vacuum", "Vacuum"),
                                        ("mop", "Mop"), ("vacuum_then_mop", "Vacuum then mop")],
     "vacuum_and_mop"),
    ("ssanie", "Narwal room clean suction", [("ai", "Smart"), ("quiet", "Quiet"), ("standard", "Normal"),
                                             ("strong", "Strong"), ("super_powerful", "Super strong")], "standard"),
    ("woda", "Narwal room clean mopping humidity", [("dry", "Slightly dry"), ("normal", "Standard"),
                                                    ("wet", "High")], "normal"),
    # App "Customize Rooms": Vacuuming / Mopping / Vacuum & Mop Cycle.
    ("przejazdy", "Narwal room clean cycles", [("1", "1 cycle"), ("2", "2 cycles"), ("3", "3 cycles")], "1"),
    ("trasa", "Narwal room clean coverage precision", [("standard", "Standard"), ("meticulous", "Intensive")],
     "standard"),
)


def stan(hass: HomeAssistant) -> dict:
    return hass.data.setdefault(DOMAIN, {}).setdefault("_ustawienia", {"pokoje": {}, "wartosci": {}})


def pokoje_mapy(hass: HomeAssistant) -> list[tuple[int, str]]:
    for entry in hass.config_entries.async_entries("narwal_local"):
        c = getattr(entry, "runtime_data", None)
        mapa = getattr(getattr(c, "data", None), "map", None)
        if mapa is not None:
            return [(r.id, r.display_name) for r in mapa.rooms]
    return []


async def sprzataj_wybrane(hass: HomeAssistant) -> None:
    s = stan(hass)
    wybrane = [rid for rid, on in s["pokoje"].items() if on]
    w = {k: s["wartosci"].get(k, d) for k, _, _, d in USTAWIENIA}
    await sprzatanie.sprzataj(hass, {
        "rooms": wybrane or "all",
        "mode": w["tryb"], "suction": w["ssanie"], "water": w["woda"],
        # Freo 20's app has no mop intensity; Flow's field 3 is sent as normal.
        "mop_strength": "normal", "passes": int(w["przejazdy"]), "route": w["trasa"],
    })


@callback
def etykieta(opcje, wartosc):
    return next((lab for val, lab in opcje if val == wartosc), None)


# Restore old display labels without losing a saved preference after renaming.
# The removed level 5 is reduced to this model's supported highest level.
STARE_ETYKIETY: dict = {}


def odtworz_wartosc(klucz: str, opcje, zapisana: str) -> str | None:
    return next((val for val, label in opcje if label == zapisana),
                STARE_ETYKIETY.get(klucz, {}).get(zapisana))
