"""Pet care: robot sounds for the dog/cat and pet search, over the local link.

Uses the connection that the narwal_local integration already holds
(port 9002 on the robot), so no cloud round trip is needed. Payloads come
from the Narwal app (app_protol):

* /voice/get_voice_list  Request{1 voiceType: 0 default, 1 tease pet, 2 user record}
  Response{1 repeated VoiceList{1 iconUrl, 2 infoType: 0 dog 1 cat 2 record 3 robot,
  3 repeated VoiceInfo{1 voiceText, 2 voiceResource, 3 extendInfo}}}
* /developer/play_voice  Request{4 resourceType: 2 file, 5 voiceResource}
* /pet/search_pet        Request{} (moves the robot: needs an explicit OK)
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

VOICE_DIR = "/userdata/usr/data/voice/"
_PLIK = re.compile(r"^/userdata/usr/data/voice/[A-Za-z0-9_]+\.mp3$")
ZWIERZE = {0: "pies", 1: "kot", 2: "nagranie", 3: "robot"}

# The app shows these phrases only as pictures (filenames in Chinese).
NAZWY = {
    "不可以这样": "Don't do that",
    "吃饭了没呀": "Have you eaten?",
    "过来我这边": "Come here",
    "过来一起玩": "Come and play",
    "你要乖点哦": "Be good",
    "你在干嘛呢": "What are you doing?",
    "你在哪里呢": "Where are you?",
    "玩个游戏吧": "Let's play a game",
    "又偷吃了吧": "Sneaking food again?",
    "偷吃没": "Did you sneak food?",
    "再这样揍你": "Do that again and you're in trouble",
    "你太棒了": "You're awesome",
    "撒个娇吧": "Come cuddle",
    "我想你啦": "I miss you",
}


def _robot(hass: HomeAssistant):
    for entry in hass.config_entries.async_entries("narwal_local"):
        coordinator = getattr(entry, "runtime_data", None)
        robot = getattr(coordinator, "robot", None)
        if robot is not None:
            return robot
    raise HomeAssistantError("No local connection to the robot (narwal_local integration)")


async def _call(hass: HomeAssistant, url: str, payload: bytes = b"") -> bytes:
    robot = _robot(hass)
    last = None
    for _ in range(2):  # a stale socket says "connection lost" once, then reconnects
        try:
            if not robot.connected:
                await robot.connect()
            return (await robot.call(url, payload, timeout=10)).payload
        except Exception as err:  # noqa: BLE001 - robot asleep, timeout, closed socket
            last = err
            try:
                await robot.close()
            except Exception:  # noqa: BLE001
                pass
    raise HomeAssistantError(f"The robot did not respond: {last}") from last


def _nazwa(icon_url: str, plik: str) -> str:
    stem = icon_url.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    fraza = stem.split("-", 1)[-1].strip()
    return NAZWY.get(fraza, fraza or plik)


def parse_voice_list(payload: bytes) -> list[dict]:
    from custom_components.narwal_local.protocol.pb import parse  # noqa: PLC0415

    out = []
    for grupa in parse(payload):
        if grupa.num != 1 or grupa.wtype != 2:
            continue
        typ = 0
        glosy = []
        for f in parse(grupa.data):
            if f.num == 2 and f.wtype == 0:
                typ = f.int
            elif f.num == 3 and f.wtype == 2:
                info = {g.num: g.data.decode("utf-8", "replace") for g in parse(f.data) if g.wtype == 2}
                plik = info.get(2, "")
                if _PLIK.match(plik):
                    glosy.append({"plik": plik, "nazwa": _nazwa(info.get(3, ""), info.get(1, "")),
                                  "ikona_url": info.get(3, "") if info.get(3, "").startswith("https://") else ""})
        glosy.sort(key=lambda g: g["plik"])
        out.extend({**g, "zwierze": ZWIERZE.get(typ, str(typ))} for g in glosy)
    return out


async def lista_dzwiekow(hass: HomeAssistant) -> list[dict]:
    """The list is fixed on the robot: keep the last good copy for when it sleeps."""
    from custom_components.narwal_local.protocol.pb import Writer  # noqa: PLC0415

    cache = Path(hass.config.path(".storage", "narwal_dzwieki.json"))
    try:
        lista = parse_voice_list(await _call(hass, "/voice/get_voice_list", bytes(Writer().varint(1, 1))))
    except HomeAssistantError:
        try:
            return json.loads(await hass.async_add_executor_job(cache.read_text))
        except (OSError, ValueError):
            raise HomeAssistantError("The robot is asleep and did not send the sound list") from None
    if lista:
        await _obrazki(hass, lista)
        await hass.async_add_executor_job(cache.write_text, json.dumps(lista, ensure_ascii=False))
    return lista


async def _obrazki(hass: HomeAssistant, lista: list[dict]) -> None:
    """The app shows each phrase as a picture on Narwal's public CDN; keep a
    local copy in www/narwal/zwierzak so the window loads it as a normal PNG."""
    import hashlib  # noqa: PLC0415

    from homeassistant.helpers.aiohttp_client import async_get_clientsession  # noqa: PLC0415

    folder = Path(hass.config.path("www", "narwal", "zwierzak"))
    session = async_get_clientsession(hass)
    for g in lista:
        url = g.pop("ikona_url", "")
        if not url.startswith("https://narwal-general-public."):
            continue
        nazwa = hashlib.sha1(url.encode()).hexdigest()[:16] + ".png"
        plik = folder / nazwa
        if not await hass.async_add_executor_job(plik.is_file):
            try:
                async with session.get(url, timeout=30) as r:
                    r.raise_for_status()
                    dane = await r.read()
                if not dane.startswith(b"\x89PNG"):
                    continue
                await hass.async_add_executor_job(lambda: folder.mkdir(parents=True, exist_ok=True))
                await hass.async_add_executor_job(plik.write_bytes, dane)
            except Exception:  # noqa: BLE001 - picture is optional
                continue
        g["ikona"] = f"/local/narwal/zwierzak/{nazwa}"


def _wynik(payload: bytes) -> int:
    from custom_components.narwal_local.protocol.pb import parse  # noqa: PLC0415

    return next((f.int for f in parse(payload) if f.num == 1 and f.wtype == 0), 0)


async def odtworz(hass: HomeAssistant, plik: str) -> None:
    from custom_components.narwal_local.protocol.pb import Writer  # noqa: PLC0415

    if not _PLIK.match(plik):
        raise HomeAssistantError("Unknown sound")
    res = _wynik(await _call(hass, "/developer/play_voice", bytes(Writer().varint(4, 2).string(5, plik))))
    if res != 1:
        raise HomeAssistantError(f"The robot did not play the sound (code {res})")


async def szukaj_zwierzaka(hass: HomeAssistant) -> None:
    res = _wynik(await _call(hass, "/pet/search_pet"))
    if res != 1:
        raise HomeAssistantError(f"The robot did not start the pet search (code {res})")


SWIATLO_MS = 30 * 60 * 1000  # light stays on at most 30 min


def _swiatlo_request(jasnosc: int) -> bytes:
    """PerformChoreography{1 Function{1 Statement{3 CustomAction{9 CameraFillLight{1 brightness}}}}}:
    the camera's fill light, as the app's laboratory sends it (0 turns it off)."""
    from custom_components.narwal_local.protocol.pb import Writer  # noqa: PLC0415

    swiatlo = Writer().message(3, Writer().message(9, Writer().int32(1, jasnosc, force=True)))
    funkcja = Writer().message(1, swiatlo)
    if jasnosc:
        # The robot resets the light when the choreography ends, so hold it with a
        # Wait {1 milliseconds}; a later request (off) replaces this one.
        funkcja.message(1, Writer().message(3, Writer().message(1, Writer().varint(1, SWIATLO_MS))))
    return bytes(Writer().message(1, funkcja))


async def swiatlo_kamery(hass: HomeAssistant, jasnosc: int) -> None:
    jasnosc = max(0, min(100, int(jasnosc)))
    res = _wynik(await _call(hass, "/telecontrol/perform_choreography", _swiatlo_request(jasnosc)))
    if res != 1:
        raise HomeAssistantError(f"The robot did not change the light (code {res})")

