"""Narwal cloud session for camera and obstacle photos.

Service narwal_cloud.zapytanie sends one request with the stored session
and returns the response with token-like fields removed (debug / discovery).
"""
from __future__ import annotations

import logging
import uuid

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from datetime import timedelta
from pathlib import Path

import aiohttp
from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import NarwalChmuraApi, NarwalChmuraAuthError
from .const import DOMAIN
from .czesci import Czesci
from .historia import Historia
from .przeszkody import Przeszkody
from .audio import setup_audio
from . import zwierzak
from .sterowanie import Sterowanie
from . import sprzatanie

_LOGGER = logging.getLogger(__name__)
MEDIA_INFO = "/iot-application-platform/app-product-media-server/v1/getDeviceInfo"
MEDIA_START = "/iot-application-platform/app-product-media-server/v1/channel/start"
SECRET_KEYS = {"token", "refresh_token", "refreshtoken", "auth-token", "password"}


def _redact(value):
    if isinstance(value, dict):
        return {k: ("<ukryte>" if k.lower() in SECRET_KEYS else _redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    return value


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    api = NarwalChmuraApi(async_get_clientsession(hass), entry.data["region"], entry.data["tokens"])
    try:
        if await api.ensure_fresh():
            hass.config_entries.async_update_entry(entry, data={**entry.data, "tokens": api.tokens})
    except NarwalChmuraAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err

    setup_audio(hass)
    entry.runtime_data = api
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = api

    przeszkody = Przeszkody(hass, api)

    async def _update():
        try:
            data = await przeszkody.odswiez()
        except NarwalChmuraAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(str(err)) from err
        hass.config_entries.async_update_entry(entry, data={**entry.data, "tokens": api.tokens})
        return data

    api.koordynator = DataUpdateCoordinator(
        hass, _LOGGER, config_entry=entry, name="narwal_przeszkody",
        update_method=_update, update_interval=timedelta(minutes=30),
    )
    await api.koordynator.async_config_entry_first_refresh()

    czesci = Czesci(przeszkody)

    async def _czesci():
        try:
            data = await czesci.odswiez()
        except NarwalChmuraAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(str(err)) from err
        hass.config_entries.async_update_entry(entry, data={**entry.data, "tokens": api.tokens})
        return data

    api.czesci = DataUpdateCoordinator(
        hass, _LOGGER, config_entry=entry, name="narwal_czesci",
        update_method=_czesci, update_interval=timedelta(hours=1),
    )
    await api.czesci.async_refresh()

    historia = Historia(przeszkody)

    async def _historia():
        try:
            return await historia.odswiez()
        except NarwalChmuraAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(str(err)) from err

    api.historia = DataUpdateCoordinator(
        hass, _LOGGER, config_entry=entry, name="narwal_historia",
        update_method=_historia, update_interval=timedelta(minutes=30),
    )
    await api.historia.async_refresh()
    if not hass.data[DOMAIN].get("_view"):
        hass.http.register_view(PrzeszkodaView(Path(hass.config.path(".storage", "narwal_przeszkody"))))
        hass.data[DOMAIN]["_view"] = True
    await hass.config_entries.async_forward_entry_setups(entry, ["sensor", "camera", "select", "switch", "button"])

    async def zapytanie(call: ServiceCall):
        before = dict(api.tokens)
        try:
            data = await api.request(
                call.data.get("method", "GET"),
                call.data["path"],
                body=call.data.get("body"),
                params=call.data.get("params"),
                device_id=call.data.get("device_id", ""),
                product_id=call.data.get("product_id", ""),
            )
        except NarwalChmuraAuthError as err:
            entry.async_start_reauth(hass)
            raise HomeAssistantError(f"Narwal session expired: {err}") from err
        if api.tokens != before:
            hass.config_entries.async_update_entry(entry, data={**entry.data, "tokens": api.tokens})
        return {"odpowiedz": _redact(data)}

    async def start_wideo(call: ServiceCall):
        """Return Agora session params for the dashboard card (admin only)."""
        if call.context.user_id:
            user = await hass.auth.async_get_user(call.context.user_id)
            if user is None or not user.is_admin:
                raise HomeAssistantError("Administrators only")
        did, pid = call.data.get("device_id"), call.data.get("product_id")
        if not did or not pid:
            try:
                await przeszkody._device()  # noqa: SLF001
            except RuntimeError as err:
                raise HomeAssistantError(str(err)) from err
            did, pid = did or przeszkody.device_id, pid or przeszkody.product_id
        try:
            info = await api.request("GET", MEDIA_INFO, params={"deviceId": did, "productId": pid},
                                     device_id=did, product_id=pid)
            app_id = (info.get("result") or {}).get("appId")
            if not app_id:
                raise HomeAssistantError(f"No appId: {info.get('msg')}")
            start = await api.request("POST", MEDIA_START, body={
                "deviceId": did, "productId": pid, "appId": app_id,
                "sessionID": uuid.uuid4().hex, "isGroup": False, "debugMode": False,
            }, device_id=did, product_id=pid)
        except NarwalChmuraAuthError as err:
            entry.async_start_reauth(hass)
            raise HomeAssistantError(f"Narwal session expired: {err}") from err
        res = start.get("result") or {}
        if start.get("code") != 0 or not res.get("channel"):
            raise HomeAssistantError(f"Narwal did not issue a session: {start.get('msg')}")
        hass.config_entries.async_update_entry(entry, data={**entry.data, "tokens": api.tokens})
        return {"appId": app_id, "channel": res["channel"], "token": res.get("token"),
                "uid": res.get("uid"), "key": res.get("securityKey"), "salt": res.get("salt"),
                "expires": res.get("tokenExpireTime"), "device_id": did, "product_id": pid, "password": (info.get("result") or {}).get("enablePassword")}

    async def camera_session(call: ServiceCall):
        """Manage viewer leases without exposing Narwal/Agora credentials."""
        if not call.context.user_id:
            raise HomeAssistantError("Login required")
        user = await hass.auth.async_get_user(call.context.user_id)
        if user is None or not user.is_admin:
            raise HomeAssistantError("Administrators only")
        try:
            async with async_get_clientsession(hass).post(
                "http://local-narwal-kamera:8099/session", json=dict(call.data),
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status == 410:
                    raise HomeAssistantError("Session expired. Start the preview again.")
                if response.status != 200:
                    raise HomeAssistantError("The camera add-on rejected the command")
                return await response.json()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise HomeAssistantError("Cannot reach the camera add-on") from err

    if not hass.services.has_service(DOMAIN, "camera_session"):
        hass.services.async_register(
            DOMAIN, "camera_session", camera_session,
            schema=vol.Schema({vol.Required("action"): vol.In(["start", "heartbeat", "stop", "status"]),
                               vol.Optional("session_id", default=""): str,
                               vol.Optional("mode"): vol.In(["video", "audio"])}),
            supports_response=SupportsResponse.ONLY,
        )

    async def refresh_obstacles(call: ServiceCall):
        """Refresh the latest obstacle photos on request."""
        await api.koordynator.async_request_refresh()

    if not hass.services.has_service(DOMAIN, "start_wideo"):
        hass.services.async_register(
            DOMAIN, "start_wideo", start_wideo,
            schema=vol.Schema({vol.Optional("device_id"): str, vol.Optional("product_id"): str}),
            supports_response=SupportsResponse.ONLY,
        )

    if not hass.services.has_service(DOMAIN, "zapytanie"):
        hass.services.async_register(
            DOMAIN, "zapytanie", zapytanie,
            schema=vol.Schema({
                vol.Required("path"): str,
                vol.Optional("method", default="GET"): vol.In(["GET", "POST"]),
                vol.Optional("body"): dict,
                vol.Optional("params"): dict,
                vol.Optional("device_id"): str,
                vol.Optional("product_id"): str,
            }),
            supports_response=SupportsResponse.ONLY,
        )
    async def _admin(call: ServiceCall) -> None:
        if call.context.user_id:
            user = await hass.auth.async_get_user(call.context.user_id)
            if user is None or not user.is_admin:
                raise HomeAssistantError("Administrators only")

    async def dzwieki(call: ServiceCall):
        return {"dzwieki": await zwierzak.lista_dzwiekow(hass)}

    async def odtworz_dzwiek(call: ServiceCall):
        await zwierzak.odtworz(hass, call.data["plik"])

    async def swiatlo_kamery(call: ServiceCall):
        await zwierzak.swiatlo_kamery(hass, call.data["jasnosc"], call.data.get("dioda", True))

    async def zeruj_czesc(call: ServiceCall):
        await _admin(call)
        kod = call.data["kod"]
        if kod not in (api.czesci.data or {}) or not api.czesci.data[kod].get("zeruj"):
            raise HomeAssistantError("This part cannot be reset")
        try:
            await czesci.zeruj(kod)
        except NarwalChmuraAuthError as err:
            entry.async_start_reauth(hass)
            raise HomeAssistantError(f"Narwal session expired: {err}") from err
        except RuntimeError as err:
            raise HomeAssistantError(str(err)) from err
        await api.czesci.async_request_refresh()

    async def szukaj_zwierzaka(call: ServiceCall):
        await _admin(call)
        await zwierzak.szukaj_zwierzaka(hass)

    ster = hass.data[DOMAIN].setdefault("_ster", Sterowanie(hass))

    async def steruj(call: ServiceCall):
        await _admin(call)
        a = call.data["akcja"]
        if a == "tryb":
            await ster.tryb(call.data.get("tryb", "wylacz"))
        elif a == "ruch":
            await ster.ruch(call.data.get("kierunek", "stop"), call.data.get("predkosc", 50))
        elif a == "jedz_do":
            return await ster.jedz_do(float(call.data["fx"]), float(call.data["fy"]))
        elif a == "anuluj":
            await ster.anuluj()
        return {}

    if not hass.services.has_service(DOMAIN, "steruj"):
        hass.services.async_register(DOMAIN, "steruj", steruj, schema=vol.Schema({
            vol.Required("akcja"): vol.In(["tryb", "ruch", "jedz_do", "anuluj"]),
            vol.Optional("tryb"): vol.In(["wylacz", "joystick", "cicho", "normalnie", "mocno", "dokladnie", "max"]),
            vol.Optional("kierunek"): vol.In(["przod", "tyl", "lewo", "prawo", "stop"]),
            vol.Optional("predkosc"): vol.All(int, vol.Range(min=10, max=100)),
            vol.Optional("fx"): vol.Coerce(float),
            vol.Optional("fy"): vol.Coerce(float),
        }), supports_response=SupportsResponse.OPTIONAL)

    async def sprzataj_pokoje(call: ServiceCall):
        await _admin(call)
        await sprzatanie.sprzataj(hass, dict(call.data))

    async def sprawdz_sprzatanie(call: ServiceCall):
        await _admin(call)
        return await hass.async_add_executor_job(sprzatanie.zgodnosc)

    if not hass.services.has_service(DOMAIN, "sprzataj_pokoje"):
        hass.services.async_register(DOMAIN, "sprzataj_pokoje", sprzataj_pokoje, schema=vol.Schema({
            vol.Required("rooms"): vol.Any("all", [vol.Any("all", vol.Coerce(int))]),
            vol.Required("mode"): vol.In(list(sprzatanie.TRYB)),
            vol.Required("suction"): vol.In(list(sprzatanie.SSANIE)),
            vol.Required("water"): vol.In(list(sprzatanie.WODA)),
            vol.Required("mop_strength"): vol.In(list(sprzatanie.MOP)),
            vol.Required("passes"): vol.All(vol.Coerce(int), vol.Range(min=1, max=3)),
            vol.Optional("route"): vol.In(list(sprzatanie.TRASA)),
            vol.Optional("entity_id"): object,
        }))
        hass.services.async_register(DOMAIN, "sprawdz_sprzatanie", sprawdz_sprzatanie,
                                     supports_response=SupportsResponse.ONLY)

    if not hass.services.has_service(DOMAIN, "dzwieki"):
        hass.services.async_register(DOMAIN, "dzwieki", dzwieki, supports_response=SupportsResponse.ONLY)
        hass.services.async_register(DOMAIN, "odtworz_dzwiek", odtworz_dzwiek,
                                     schema=vol.Schema({vol.Required("plik"): str}))
        hass.services.async_register(DOMAIN, "szukaj_zwierzaka", szukaj_zwierzaka)
        hass.services.async_register(DOMAIN, "swiatlo_kamery", swiatlo_kamery, schema=vol.Schema({
            vol.Required("jasnosc"): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
            vol.Optional("dioda", default=True): bool}))
        hass.services.async_register(DOMAIN, "zeruj_czesc", zeruj_czesc,
                                     schema=vol.Schema({vol.Required("kod"): vol.All(str, vol.Match(r"^[A-Za-z0-9]{4,32}$"))}))
    if not hass.services.has_service(DOMAIN, "refresh_obstacles"):
        hass.services.async_register(DOMAIN, "refresh_obstacles", refresh_obstacles)

    async def zdjecie_przeszkody(call: ServiceCall):
        try:
            return await przeszkody.na_zywo(call.data["id"], call.data["sprzatanie"])
        except ValueError as err:
            raise HomeAssistantError(str(err)) from err
        except Exception as err:
            _LOGGER.debug("Live obstacle photo failed: %s", type(err).__name__)
            raise HomeAssistantError("Could not load the photo from the robot. Try again.") from err

    if not hass.services.has_service(DOMAIN, "zdjecie_przeszkody"):
        hass.services.async_register(DOMAIN, "zdjecie_przeszkody", zdjecie_przeszkody,
            schema=vol.Schema({vol.Required("id"): vol.All(int, vol.Range(min=0, max=2**31-1)),
                               vol.Required("sprzatanie"): vol.All(vol.Coerce(float), vol.Range(min=1))}),
            supports_response=SupportsResponse.ONLY)

    async def przeszkody_sprzatania(call: ServiceCall):
        """Obstacle photos of one earlier clean (Historia), fetched on demand so
        the sensor attributes stay small."""
        try:
            return await przeszkody.sprzatanie(call.data["rekord"])
        except ValueError as err:
            raise HomeAssistantError(str(err)) from err

    if not hass.services.has_service(DOMAIN, "przeszkody_sprzatania"):
        hass.services.async_register(DOMAIN, "przeszkody_sprzatania", przeszkody_sprzatania,
                                     schema=vol.Schema({vol.Required("rekord"): vol.All(str, vol.Match(r"^\d{1,30}$"))}),
                                     supports_response=SupportsResponse.ONLY)
    return True


class PrzeszkodaView(HomeAssistantView):
    """Serve decrypted obstacle photos to logged-in users (signed paths work)."""

    url = "/api/narwal_cloud/przeszkoda/{rekord}/{plik}"
    name = "api:narwal_cloud:przeszkoda"
    requires_auth = True

    def __init__(self, base: Path) -> None:
        self.base = base

    async def get(self, request, rekord: str, plik: str):
        if not rekord.isdigit() or not plik.endswith(".jpg") or not plik[:-4].isdigit():
            return web.Response(status=404)
        path = self.base / rekord / plik
        if not path.is_file():
            return web.Response(status=404)
        return web.FileResponse(path, headers={"Cache-Control": "private, max-age=86400"})


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await hass.config_entries.async_unload_platforms(entry, ["sensor", "camera", "select", "switch", "button"])
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if not [k for k in hass.data.get(DOMAIN, {}) if k not in ("_view", "_ster", "_ustawienia")]:
        hass.services.async_remove(DOMAIN, "zapytanie")
        hass.services.async_remove(DOMAIN, "start_wideo")
        hass.services.async_remove(DOMAIN, "refresh_obstacles")
        hass.services.async_remove(DOMAIN, "przeszkody_sprzatania")
        hass.services.async_remove(DOMAIN, "zdjecie_przeszkody")
        hass.services.async_remove(DOMAIN, "camera_session")
        hass.services.async_remove(DOMAIN, "dzwieki")
        hass.services.async_remove(DOMAIN, "steruj")
        hass.services.async_remove(DOMAIN, "sprzataj_pokoje")
        hass.services.async_remove(DOMAIN, "sprawdz_sprzatanie")
        hass.services.async_remove(DOMAIN, "odtworz_dzwiek")
        hass.services.async_remove(DOMAIN, "szukaj_zwierzaka")
        hass.services.async_remove(DOMAIN, "swiatlo_kamery")
        hass.services.async_remove(DOMAIN, "zeruj_czesc")
    return True


async def async_remove_config_entry_device(hass, entry, device) -> bool:
    """Allow removing leftover devices from the device page."""
    return True
