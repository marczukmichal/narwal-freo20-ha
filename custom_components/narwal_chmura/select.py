"""Cleaning settings for "Narwal clean selected rooms" (see ustawienia.py)."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.helpers.restore_state import RestoreEntity

from .ustawienia import USTAWIENIA, etykieta, odtworz_wartosc, stan


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([UstawienieSprzatania(hass, *u) for u in USTAWIENIA])


class UstawienieSprzatania(SelectEntity, RestoreEntity):
    _attr_should_poll = False
    _attr_icon = "mdi:robot-vacuum"

    def __init__(self, hass, klucz, nazwa, opcje, domyslna) -> None:
        self.klucz, self.opcje = klucz, opcje
        self._attr_unique_id = f"narwal_chmura_ustawienie_{klucz}"
        self._attr_name = nazwa
        self._attr_options = [lab for _, lab in opcje]
        stan(hass)["wartosci"].setdefault(klucz, domyslna)

    @property
    def current_option(self):
        return etykieta(self.opcje, stan(self.hass)["wartosci"][self.klucz])

    async def async_added_to_hass(self) -> None:
        last = await self.async_get_last_state()
        if last and (val := odtworz_wartosc(self.klucz, self.opcje, last.state)):
            stan(self.hass)["wartosci"][self.klucz] = val

    async def async_select_option(self, option: str) -> None:
        stan(self.hass)["wartosci"][self.klucz] = next(v for v, lab in self.opcje if lab == option)
        self.async_write_ha_state()
