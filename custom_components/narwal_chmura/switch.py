"""Room choice for "Narwal clean selected rooms": one switch per room of
the robot's current map (none on = the whole home)."""
from __future__ import annotations

import asyncio

from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.restore_state import RestoreEntity

from .ustawienia import pokoje_mapy, stan


async def async_setup_entry(hass, entry, async_add_entities):
    if pokoje := pokoje_mapy(hass):
        async_add_entities([WyborPokoju(rid, nazwa) for rid, nazwa in pokoje])
        return

    # narwal_local may get the map only minutes after a restart (robot asleep);
    # wait for it in the background so HA's setup is not held up.
    async def czekaj():
        for _ in range(180):
            await asyncio.sleep(5)
            if pokoje := pokoje_mapy(hass):
                async_add_entities([WyborPokoju(rid, nazwa) for rid, nazwa in pokoje])
                return

    entry.async_create_background_task(hass, czekaj(), "narwal_chmura pokoje")


class WyborPokoju(SwitchEntity, RestoreEntity):
    _attr_should_poll = False
    _attr_icon = "mdi:floor-plan"

    def __init__(self, rid: int, nazwa: str) -> None:
        self.rid = rid
        self._attr_unique_id = f"narwal_chmura_pokoj_{rid}"
        self._attr_name = f"Narwal room clean include {nazwa}"

    @property
    def is_on(self) -> bool:
        return bool(stan(self.hass)["pokoje"].get(self.rid))

    async def async_added_to_hass(self) -> None:
        last = await self.async_get_last_state()
        stan(self.hass)["pokoje"][self.rid] = bool(last and last.state == "on")

    async def async_turn_on(self, **kwargs) -> None:
        stan(self.hass)["pokoje"][self.rid] = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        stan(self.hass)["pokoje"][self.rid] = False
        self.async_write_ha_state()
