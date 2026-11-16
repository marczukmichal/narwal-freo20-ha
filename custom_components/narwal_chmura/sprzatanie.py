"""Room cleaning with full settings over the one local connection.

The Odkurzacz window used to start room cleans through the Narwal Flow
integration (narwal.clean_rooms). Flow and narwal_local both hold a
websocket to the robot, and the robot keeps only one per address, so they
kicked each other off every second or two. This module builds the same
/clean/start_clean request that Flow sends and sends it through narwal_local.

StartClean_Request { 1 CleanTask { 1 mapId, 2 CleanItem[], 3 TaskOption {}, 5 taskType } }
CleanItem  { 1 ZoneOption { 1 zoneType 1 (room), 2 roomId }, 2 CleanParam, 3 order }
CleanParam { 1 mode, 2 fanLevel, 3 mopStrength, 4 mopHumidity,
             5 sweepTime, 6 mopTime, 7 sweepMopSyncTime, 8 overlapLevel (route) }

taskType is the work mode (1 vacuum, 2 mop, 3 vacuum then mop, 4 both);
CleanParam.mode is 2, 3, 5, 4 for the same modes. The pass count goes in
the pass field(s) of the mode. `zgodnosc` checks that the bytes match Flow's
own builder without talking to the robot.
"""
from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .zwierzak import _call, _wynik

# dashboard option -> (taskType / WorkMode, CleanParam.mode, pass fields)
TRYB = {
    "vacuum": (1, 2, (5,)),
    "mop": (2, 3, (6,)),
    "vacuum_then_mop": (3, 5, (5, 6)),
    "vacuum_and_mop": (4, 4, (7,)),
}
# The shared Flow enum also contains SUPER=5. That is not an extra suction
# choice on this Freo 20: its app exposes Smart and MUTE/NORMAL/STRONG/DEEP.
SSANIE = {"ai": 0, "quiet": 1, "standard": 2, "strong": 3, "super_powerful": 4}
WODA = {"dry": 1, "normal": 2, "wet": 3}
MOP = {"normal": 1, "high": 2}
TRASA = {"standard": 1, "meticulous": 2}


def _w():
    from custom_components.narwal_local.protocol.pb import Writer  # noqa: PLC0415

    return Writer()


def _coordinator(hass: HomeAssistant):
    for entry in hass.config_entries.async_entries("narwal_local"):
        coordinator = getattr(entry, "runtime_data", None)
        if coordinator is not None and getattr(coordinator, "data", None) is not None:
            return coordinator
    raise HomeAssistantError("No local connection to the robot (narwal_local integration)")


def zbuduj(map_id: int, pokoje: list[int], tryb: str, ssanie: str, woda: str,
           mop: str, przejazdy: int, trasa: str | None) -> bytes:
    if tryb not in TRYB or ssanie not in SSANIE or woda not in WODA or mop not in MOP:
        raise HomeAssistantError("Unknown cleaning setting")
    if trasa is not None and trasa not in TRASA:
        raise HomeAssistantError("Unknown coverage setting")
    if not 1 <= przejazdy <= 3 or not pokoje:
        raise HomeAssistantError("Invalid rooms or number of cleaning cycles")
    task_type, mode, pass_tags = TRYB[tryb]
    # Same field order as Flow: 1-4, the route, then the pass fields; zeros included.
    fields = {1: mode, 2: SSANIE[ssanie], 3: MOP[mop], 4: WODA[woda]}
    if trasa is not None:
        fields[8] = TRASA[trasa]
    for tag in pass_tags:
        fields[tag] = przejazdy
    task = _w().int32(1, map_id, force=True)
    for order, room in enumerate(pokoje, start=1):
        param = _w()
        for tag in fields:
            param.int32(tag, fields[tag], force=True)
        item = (_w().message(1, _w().int32(1, 1, force=True).int32(2, room, force=True))
                .message(2, param).int32(3, order, force=True))
        task.message(2, item)
    task.message(3, _w())
    task.int32(5, task_type, force=True)
    return bytes(_w().message(1, task))


def _parametry(hass: HomeAssistant, data: dict) -> tuple[int, list[int]]:
    c = _coordinator(hass)
    status, mapa = c.data.status, c.data.map
    if mapa is None:
        raise HomeAssistantError("The robot did not report its map")
    znane = [r.id for r in mapa.rooms]
    pokoje = data["rooms"]
    if pokoje in ("all", ["all"]):
        pokoje = znane
    pokoje = [int(p) for p in pokoje]
    if obce := [p for p in pokoje if p not in znane]:
        raise HomeAssistantError(f"Unknown rooms: {obce}")
    return status.map_id, pokoje


async def sprzataj(hass: HomeAssistant, data: dict) -> None:
    map_id, pokoje = _parametry(hass, data)
    req = zbuduj(map_id, pokoje, data["mode"], data["suction"], data["water"],
                 data["mop_strength"], int(data["passes"]), data.get("route"))
    res = _wynik(await _call(hass, "/clean/start_clean", req))
    if res != 1:
        raise HomeAssistantError(f"The robot did not start cleaning (code {res})")


def zgodnosc() -> dict:
    """Compare our bytes with Flow's builder for every setting combination.
    Nothing is sent to the robot."""
    from custom_components.narwal.narwal_client import client as flow  # noqa: PLC0415
    from custom_components.narwal.narwal_client.const import (  # noqa: PLC0415
        CleaningRoute, FanLevel, MopHumidity, MopStrengthLevel, WorkMode,
    )

    fake = object.__new__(flow.NarwalClient)
    wm = {"vacuum": WorkMode.VACUUM, "mop": WorkMode.MOP,
          "vacuum_then_mop": WorkMode.VACUUM_THEN_MOP, "vacuum_and_mop": WorkMode.VACUUM_AND_MOP}
    sprawdzone, rozne = 0, []
    for tryb in TRYB:
        for ssanie, fan in SSANIE.items():
            for woda in WODA:
                for mop in MOP:
                    for przejazdy in (1, 2, 3):
                        for trasa in (None, "standard", "meticulous"):
                            for pokoje in ([2], [1, 3, 5], [4, 2, 1, 5, 3]):
                                nasze = zbuduj(7, pokoje, tryb, ssanie, woda, mop, przejazdy, trasa)
                                ich = fake._build_start_clean_payload(
                                    pokoje, 7, work_mode=wm[tryb], fan=FanLevel(fan),
                                    water=MopHumidity(WODA[woda]), mop_strength=MopStrengthLevel(MOP[mop]),
                                    passes=przejazdy, route=None if trasa is None else CleaningRoute(TRASA[trasa]))
                                sprawdzone += 1
                                if nasze != ich and len(rozne) < 5:
                                    rozne.append({"tryb": tryb, "ssanie": ssanie, "woda": woda, "mop": mop,
                                                  "przejazdy": przejazdy, "trasa": trasa, "pokoje": pokoje,
                                                  "nasze": nasze.hex(), "flow": ich.hex()})
    return {"sprawdzone": sprawdzone, "rozne": rozne}
