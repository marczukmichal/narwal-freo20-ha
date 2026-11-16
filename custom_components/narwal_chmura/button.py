"""Start the room clean set up by the Narwal selects and room switches."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity

from .ustawienia import sprzataj_wybrane


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([SprzatajWybrane()])


class SprzatajWybrane(ButtonEntity):
    _attr_unique_id = "narwal_chmura_sprzataj_wybrane"
    _attr_name = "Narwal room clean start"
    _attr_icon = "mdi:play-circle-outline"

    async def async_press(self) -> None:
        await sprzataj_wybrane(self.hass)
