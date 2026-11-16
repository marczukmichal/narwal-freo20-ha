"""Image entity with the Narwal map (SVG)."""

from __future__ import annotations

from datetime import timedelta
import logging
import math
from pathlib import Path
import zlib

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.util import dt as dt_util

import json

from .mapa_svg import project_pins, render_svg
from .local_map import local_snapshot
from .pinezki import map_pins

_LOGGER = logging.getLogger(__name__)
SCAN = timedelta(seconds=2)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Add the map entity."""
    async_add_entities([NarwalMapaImage(hass)])


class NarwalMapaImage(ImageEntity):
    """Map drawn from the local Narwal integration data."""

    _attr_content_type = "image/svg+xml"
    _attr_name = "Vacuum map"
    _attr_unique_id = "narwal_map_svg"
    _attr_icon = "mdi:floor-plan"
    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant) -> None:
        """Prepare the entity."""
        super().__init__(hass)
        self._svg: bytes | None = None
        self._signature: tuple | None = None
        self._busy = False
        self._failed = False
        # last map on disk: after an HA restart there is a picture before the robot wakes
        self._cache = Path(hass.config.path(".storage", "narwal_map.svg"))
        self._geo_file = Path(hass.config.path(".storage", "narwal_map_geo.json"))
        self._geo: list | None = None
        self._pin_sig: tuple | None = None

    def _load_cache(self) -> bytes | None:
        try:
            return self._cache.read_bytes()
        except OSError:
            return None

    def _save_cache(self, data: bytes) -> None:
        try:
            self._cache.write_bytes(data)
        except OSError:
            _LOGGER.debug("Could not save the map to %s", self._cache)

    @property
    def available(self) -> bool:
        """Available once at least one map was drawn."""
        return self._svg is not None

    async def async_added_to_hass(self) -> None:
        """Start following map changes."""
        await super().async_added_to_hass()
        cached = await self.hass.async_add_executor_job(self._load_cache)
        try:
            self._geo = json.loads(await self.hass.async_add_executor_job(self._geo_file.read_text))
        except (OSError, ValueError):
            self._geo = None
        if cached and self._svg is None:
            self._svg = cached
            self._attr_image_last_updated = dt_util.utcnow()
            self.async_write_ha_state()
        self.async_on_remove(async_track_time_interval(self.hass, self._tick, SCAN))
        self.hass.async_create_task(self._tick(None))

    def _state(self):
        """Robot state from the narwal coordinator (or None)."""
        for entry in self.hass.config_entries.async_entries("narwal"):
            if getattr(entry, "disabled_by", None) is not None:
                continue
            coordinator = getattr(entry, "runtime_data", None)
            data = getattr(coordinator, "data", None)
            if data is not None and getattr(data, "map_data", None) is not None:
                return data
        return None

    @callback
    def _snapshot(self):
        """Collect drawing data and its signature (to skip redraws without changes)."""
        # The old `narwal` entry can be disabled after migrating to
        # narwal_local. Reading only that entry leaves the SVG on disk frozen.
        local = None
        for entry in self.hass.config_entries.async_entries("narwal_local"):
            if getattr(entry, "disabled_by", None) is not None:
                continue
            local = local_snapshot(getattr(entry, "runtime_data", None))
            if local is not None:
                break
        if local is not None:
            m, robot, trail, metadata = local
            trail_signature = tuple((x, y) if math.isfinite(x) and math.isfinite(y) else None
                                    for x, y in trail)
            return self._snapshot_with_map(m, robot, trail, metadata, trail_signature)
        state = self._state()
        if state is None:
            return None
        m = state.map_data
        if not getattr(m, "compressed_map", None) or not m.width or not m.height:
            return None
        robot = None
        display = getattr(state, "map_display_data", None)
        if display is not None and not getattr(state, "is_docked", True):
            try:
                pos = display.to_grid_coords(m.resolution, m.origin_x, m.origin_y)
            except Exception:  # noqa: BLE001 - another integration, its format may change
                pos = None
            if pos and 0 <= pos[0] < m.width and 0 <= pos[1] < m.height:
                robot = (round(pos[0], 1), round(pos[1], 1), None)
        trail = []
        if display is not None:
            for x, y in display.trajectory_render_points():
                gx, gy = x - m.origin_x, y - m.origin_y
                if math.isfinite(gx) and math.isfinite(gy) and 0 <= gx < m.width and 0 <= gy < m.height:
                    trail.append((gx, gy))
                elif trail and math.isfinite(trail[-1][0]):
                    trail.append((float("nan"), float("nan")))
        metadata = {"map_source": "narwal", "robot_docked": bool(state.is_docked),
                    "robot_position_status": "last_known" if robot else "unavailable",
                    "robot_position": {"x": robot[0], "y": robot[1]} if robot else None}
        return self._snapshot_with_map(m, robot, trail, metadata,
                                       getattr(display, "trajectory_signature", ()))

    def _snapshot_with_map(self, m, robot, trail, metadata, trail_signature):
        """Shared signature and photo markers for either map provider."""
        pins = map_pins(self.hass.states.get("sensor.narwal_przeszkody"),
                        getattr(m, "clean_started", None), getattr(m, "live_obstacles", None))
        signature = (
            tuple((p["id"], p["x"], p["y"]) for p in pins),
            zlib.crc32(m.compressed_map),
            m.width,
            m.height,
            m.origin_x,
            m.origin_y,
            m.dock_x,
            m.dock_y,
            tuple((r.room_id, r.name, r.room_sub_type) for r in m.rooms),
            tuple(
                (o.id, o.type_id, o.center_x, o.center_y, o.width, o.height, o.angle)
                for o in m.obstacles
            ),
            zlib.crc32(repr((m.raw.get("40"), m.raw.get("26"), m.raw.get("11"), m.raw.get("9"), m.raw.get("50"))).encode()),
            robot,
            trail_signature,
            tuple((key, repr(value)) for key, value in metadata.items()),
        )
        return m, robot, trail, pins, signature, metadata

    async def _tick(self, _now) -> None:
        """Redraw the map if something changed."""
        if self._busy:
            return
        try:
            snap = self._snapshot()
        except Exception:  # noqa: BLE001
            if not self._failed:
                _LOGGER.exception("Could not read map data from the narwal integration")
                self._failed = True
            return
        if snap is None:
            self._pins_from_geo()
            return
        m, robot, trail, pins, signature, metadata = snap
        if signature == self._signature:
            return
        self._busy = True
        try:
            pins_out: list = []
            svg = await self.hass.async_add_executor_job(
                lambda: render_svg(m, robot, trail, pins=pins, pins_out=pins_out))
        except Exception:  # noqa: BLE001
            if not self._failed:
                _LOGGER.exception("Could not draw the Narwal map")
                self._failed = True
            return
        finally:
            self._busy = False
        if not svg:
            return
        self._failed = False
        self._signature = signature
        self._svg = svg.encode("utf-8")
        aspect = next((p["_aspect"] for p in pins_out if "_aspect" in p), None)
        geo = next((p["_geo"] for p in pins_out if "_geo" in p), None)
        if geo:
            self.hass.data["narwal_map_geo"] = geo
        if geo and geo != self._geo:
            self._geo = geo
            await self.hass.async_add_executor_job(self._geo_file.write_text, json.dumps(geo))
        self._attr_extra_state_attributes = {
            **metadata,
            "proporcje": aspect,
            "przeszkody": [p for p in pins_out if "_aspect" not in p and "_strefy" not in p],
            "strefy_zakazane": next((p["_strefy"] for p in pins_out if "_strefy" in p), []),
            "wirtualne_sciany": next((p["_sciany"] for p in pins_out if "_strefy" in p), []),
        }
        self._pin_sig = tuple((p["id"], p["x"], p["y"]) for p in pins)
        self._attr_image_last_updated = dt_util.utcnow()
        self.async_write_ha_state()
        if robot is None:
            await self.hass.async_add_executor_job(self._save_cache, self._svg)

    @callback
    def _pins_from_geo(self) -> None:
        """Without a fresh map: place pins with the remembered geometry."""
        if not self._geo or self._svg is None:
            return
        pins = map_pins(self.hass.states.get("sensor.narwal_przeszkody"), None, None)
        sig = tuple((p["id"], p["x"], p["y"]) for p in pins)
        if sig == self._pin_sig and (self._attr_extra_state_attributes or {}).get("map_source") == "cache":
            return
        self._pin_sig = sig
        aspect, out = project_pins(self._geo, pins)
        self._attr_extra_state_attributes = {
            "proporcje": aspect, "przeszkody": out, "map_source": "cache",
            "robot_docked": None, "robot_position": None,
            "robot_position_status": "unavailable",
        }
        self.async_write_ha_state()

    async def async_image(self) -> bytes | None:
        """Return the current SVG."""
        return self._svg
