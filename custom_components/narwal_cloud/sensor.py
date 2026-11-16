"""sensor.narwal_przeszkody: obstacle photos from the latest clean that has any."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity


async def async_setup_entry(hass, entry, async_add_entities):
    czesci = entry.runtime_data.czesci
    async_add_entities([
        PrzeszkodySensor(entry.runtime_data.koordynator),
        CzesciSensor(czesci),
        HistoriaSensor(entry.runtime_data.historia, "liczba_sprzatan", "Narwal cleaning count", "mdi:counter", None),
        HistoriaSensor(entry.runtime_data.historia, "laczna_powierzchnia", "Narwal total cleaned area", "mdi:texture-box", "m²"),
        HistoriaSensor(entry.runtime_data.historia, "laczny_czas_h", "Narwal total cleaning time", "mdi:timer-outline", "h"),
        *(CzescSensor(czesci, kod) for kod, c in (czesci.data or {}).items() if c["pozostalo_h"] is not None),
    ])


class PrzeszkodySensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = False
    _attr_name = "Narwal obstacles"
    _attr_unique_id = "narwal_cloud_przeszkody"
    _attr_icon = "mdi:image-search"
    _attr_native_unit_of_measurement = "photos"

    @property
    def native_value(self):
        return len((self.coordinator.data or {}).get("lista") or [])

    @property
    def extra_state_attributes(self):
        d = self.coordinator.data or {}
        return {"sprzatanie": d.get("sprzatanie"), "przeszkody": d.get("lista") or [],
                "zwierzak": d.get("zwierzak") or []}


class CzesciSensor(CoordinatorEntity, SensorEntity):
    """All parts as in the app: how many need attention, the list in attributes."""

    _attr_has_entity_name = False
    _attr_name = "Narwal parts"
    _attr_unique_id = "narwal_cloud_czesci"
    _attr_icon = "mdi:wrench-clock"

    @property
    def native_value(self):
        d = self.coordinator.data
        if d is None:
            return None
        return sum(1 for c in d.values() if c["procent"] is not None and c["procent"] <= 10)

    @property
    def extra_state_attributes(self):
        return {"czesci": list((self.coordinator.data or {}).values())}


class CzescSensor(CoordinatorEntity, SensorEntity):
    """Remaining hours of one part (the number the app shows)."""

    _attr_has_entity_name = False
    _attr_icon = "mdi:timer-sand"
    _attr_native_unit_of_measurement = "h"
    _attr_state_class = "measurement"

    def __init__(self, coordinator, kod: str) -> None:
        super().__init__(coordinator)
        self.kod = kod
        self._attr_unique_id = f"narwal_cloud_czesc_{kod}"
        self._attr_name = f"Narwal part {coordinator.data[kod]['nazwa'].lower()}"

    @property
    def available(self) -> bool:
        return super().available and self.kod in (self.coordinator.data or {})

    @property
    def native_value(self):
        return self.coordinator.data[self.kod]["pozostalo_h"]

    @property
    def extra_state_attributes(self):
        c = self.coordinator.data[self.kod]
        return {k: c[k] for k in ("procent", "uzyte_h", "zywotnosc_h", "rodzaj", "opis")}

    @property
    def entity_picture(self):
        return (self.coordinator.data or {}).get(self.kod, {}).get("obrazek")


class HistoriaSensor(CoordinatorEntity, SensorEntity):
    """Totals from the app's cleaning records; the first one also carries the list."""

    _attr_has_entity_name = False
    _attr_state_class = "total_increasing"

    def __init__(self, coordinator, klucz: str, nazwa: str, ikona: str, jednostka: str | None) -> None:
        super().__init__(coordinator)
        self.klucz = klucz
        self._attr_name = nazwa
        self._attr_icon = ikona
        self._attr_unique_id = f"narwal_cloud_historia_{klucz}"
        self._attr_native_unit_of_measurement = jednostka

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get(self.klucz)

    @property
    def extra_state_attributes(self):
        if self.klucz != "liczba_sprzatan":
            return None
        d = self.coordinator.data or {}
        return {"sprzatania": d.get("lista") or [], "dni": d.get("dni") or []}
