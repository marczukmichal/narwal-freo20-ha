"""Time entities: when do-not-disturb starts and ends."""

from __future__ import annotations

from dataclasses import replace
from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import NarwalConfigEntry, NarwalCoordinator
from .entity import NarwalEntity
from .protocol import Setting
from .protocol.config import ValleyCharging

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NarwalConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    if coordinator.data.config is not None:
        entities: list[TimeEntity] = [NarwalDndTime(coordinator, "start"), NarwalDndTime(coordinator, "end")]
        if coordinator.static.features.supports("supportValleyCharging"):
            entities += [NarwalValleyTime(coordinator, "start"), NarwalValleyTime(coordinator, "end")]
        async_add_entities(entities)


class NarwalDndTime(NarwalEntity, TimeEntity):
    """Start or end of the do-not-disturb window, in the robot's local time."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: NarwalCoordinator, which: str) -> None:
        super().__init__(coordinator, f"do_not_disturb_{which}")
        self._attr_translation_key = f"do_not_disturb_{which}"
        self._which = which

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data.config is not None

    @property
    def native_value(self) -> time | None:
        if (config := self.coordinator.data.config) is None:
            return None
        return getattr(config.do_not_disturb, self._which)

    async def async_set_value(self, value: time) -> None:
        current = self.coordinator.data.config.do_not_disturb
        new = replace(current, **{self._which: value.replace(second=0, microsecond=0)})
        await self.coordinator.async_set_config(Setting.DO_NOT_DISTURB, new)


class NarwalValleyTime(NarwalEntity, TimeEntity):
    """Start or end of the off-peak charging window (robot's local time)."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: NarwalCoordinator, which: str) -> None:
        super().__init__(coordinator, f"valley_charging_{which}")
        self._attr_translation_key = f"valley_charging_{which}"
        self._which = which

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data.config is not None

    @property
    def native_value(self) -> time | None:
        if (config := self.coordinator.data.config) is None:
            return None
        return getattr(ValleyCharging.decode(config.value(Setting.VALLEY_CHARGING)), self._which)

    async def async_set_value(self, value: time) -> None:
        raw = self.coordinator.data.config.value(Setting.VALLEY_CHARGING)
        current = ValleyCharging.decode(raw)
        value = value.replace(second=0, microsecond=0)
        new = current.with_start(value) if self._which == "start" else current.with_end(value)
        await self.coordinator.async_set_config(Setting.VALLEY_CHARGING, new.encode(raw))
