"""Remote driving from the camera view, as the Narwal app does it.

Payloads read from the app (video control controller, app_protol):

* /telecontrol/set_manual_control_mode  Request{1 mode}: 0 off, 1 joystick,
  2 clean default, 3 mute, 4 normal, 5 strong, 6 deep, 7 super. Modes 3-7
  are "clean where the robot stands" at that suction.
* /telecontrol/velocity_control (publish, every 200 ms while a button is held)
  TelecontrolVelocity{1 linearVelocity int32, 2 angularVelocity int32};
  the app sends speed*20/100 forward/back and speed*17/100 turning, and
  0/0 on release.
* /telecontrol/point_navi  Request{5 points[] PoseData{1 location Point{1 x, 2 y float}}}
  in border-frame map cells. (An earlier attempt put the points in field 4,
  which is the exploreMode enum; that is a likely cause of the robot crash.)
* /task/cancel  Request{1 taskType: 3 navi} cancels the drive to a point.

Every movement needs an active camera session with video, like the app.
A watchdog sends 0/0 when no new move arrives within 0.6 s.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .zwierzak import _call, _robot, _wynik

ADDON = "http://local-narwal-kamera:8099/session"
TRYBY = {"wylacz": 0, "joystick": 1, "cicho": 3, "normalnie": 4, "mocno": 5, "dokladnie": 6, "max": 7}
WATCHDOG = 0.6
MAX_LINEAR = 20  # the app's value at 100 % speed
MAX_ANGULAR = 17
GRID_S = 10  # narwal_mapa SVG units per cell


def _w():
    from custom_components.narwal_local.protocol.pb import Writer  # noqa: PLC0415

    return Writer()


class Sterowanie:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._stop_handle: asyncio.TimerHandle | None = None

    async def _wideo_aktywne(self) -> None:
        try:
            async with async_get_clientsession(self.hass).post(
                ADDON, json={"action": "status"}, timeout=aiohttp.ClientTimeout(total=5)
            ) as r:
                st = await r.json() if r.status == 200 else {}
        except (aiohttp.ClientError, TimeoutError, ValueError):
            st = {}
        if not (st.get("active") and st.get("video") and st.get("status") == "connected"):
            raise HomeAssistantError("Driving works only while the video preview is on")

    async def tryb(self, nazwa: str) -> None:
        if nazwa not in TRYBY:
            raise HomeAssistantError("Unknown mode")
        if nazwa != "wylacz":
            await self._wideo_aktywne()
        else:
            await self._zero()
        res = _wynik(await _call(self.hass, "/telecontrol/set_manual_control_mode",
                                 bytes(_w().int32(1, TRYBY[nazwa], force=True))))
        if res != 1:
            raise HomeAssistantError(f"The robot rejected the drive mode (code {res})")

    async def _publish(self, linear: int, angular: int) -> None:
        robot = _robot(self.hass)
        try:
            if not robot.connected:
                await robot.connect()
            await robot.publish("/telecontrol/velocity_control",
                                bytes(_w().int32(1, linear).int32(2, angular)))
        except Exception as err:  # noqa: BLE001
            raise HomeAssistantError(f"The robot did not respond: {err}") from err

    async def _zero(self) -> None:
        if self._stop_handle:
            self._stop_handle.cancel()
            self._stop_handle = None
        try:
            await self._publish(0, 0)
        except HomeAssistantError:
            pass

    async def ruch(self, kierunek: str, predkosc: int) -> None:
        if kierunek == "stop":
            await self._zero()
            return
        await self._wideo_aktywne()
        p = max(10, min(100, int(predkosc)))
        lin = round(MAX_LINEAR * p / 100)
        ang = round(MAX_ANGULAR * p / 100)
        v = {"przod": (lin, 0), "tyl": (-lin, 0), "lewo": (0, ang), "prawo": (0, -ang)}.get(kierunek)
        if v is None:
            raise HomeAssistantError("Unknown direction")
        await self._publish(*v)
        if self._stop_handle:
            self._stop_handle.cancel()
        self._stop_handle = self.hass.loop.call_later(
            WATCHDOG, lambda: self.hass.async_create_task(self._zero()))

    async def jedz_do(self, fx: float, fy: float) -> dict:
        """fx/fy: point on the narwal_mapa image (0..1), turned into map cells."""
        await self._wideo_aktywne()
        if not (0 <= fx <= 1 and 0 <= fy <= 1):
            raise HomeAssistantError("Point outside the map")
        try:
            geo = json.loads(await self.hass.async_add_executor_job(
                Path(self.hass.config.path(".storage", "narwal_mapa_geo.json")).read_text))
            H, ox, oy, vx, vy, vw, vh = geo
        except (OSError, ValueError, TypeError) as err:
            raise HomeAssistantError("No map geometry") from err
        px, py = (fx * vw + vx) / GRID_S, (fy * vh + vy) / GRID_S
        x, y = px - 0.5 + ox, H - py - 0.5 + oy
        point = _w().float_(1, x).float_(2, y)
        req = _w().message(5, _w().message(1, point))
        res = _wynik(await _call(self.hass, "/telecontrol/point_navi", bytes(req)))
        if res != 1:
            raise HomeAssistantError(f"The robot did not accept the target (code {res})")
        return {"x": round(x, 2), "y": round(y, 2)}

    async def anuluj(self) -> None:
        await self._zero()
        await _call(self.hass, "/task/cancel", bytes(_w().int32(1, 3, force=True)))
