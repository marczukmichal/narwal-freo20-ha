"""Diagnostics download."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .coordinator import NarwalConfigEntry
from .map_objects import dirt_list, furniture_list, obstacle_list

TO_REDACT = {"token", "device_id", "binded_uuid"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: NarwalConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    data = coordinator.data
    static = coordinator.static
    status = asdict(data.status)
    status["activity"] = str(data.status.activity)
    status["task"] = getattr(data.status.task, "name", data.status.task)
    mi = data.map
    return async_redact_data(
        {
            "entry": dict(entry.data),
            "connected": coordinator.robot.connected,
            "device": asdict(static.device),
            "firmware": asdict(static.firmware) if static.firmware else None,
            "features": {"enabled": static.features.enabled, "other": static.features.other},
            "status": status,
            "working": asdict(data.working) if data.working else None,
            "live": {
                "path_points": len(coordinator.live.path),
                "track_points": len(coordinator.live.track),
                "track_tail": [list(p) for p in coordinator.live.track[-3:]],
                "map_trajectory_points": len(mi.trajectory) if mi else None,
                "map_trajectory_sample": [list(p) for p in (mi.trajectory[:4] + mi.trajectory[-4:])] if mi else None,
                "map_trajectory_nan": sum(1 for x, _ in mi.trajectory if x != x) if mi else None,
                "map_trajectory_range": [min((x for x, _ in mi.trajectory if x == x), default=None),
                                         max((x for x, _ in mi.trajectory if x == x), default=None),
                                         min((y for _, y in mi.trajectory if y == y), default=None),
                                         max((y for _, y in mi.trajectory if y == y), default=None)] if mi else None,
                "border": [mi.border_left, mi.border_bottom, mi.width, mi.height] if mi else None,
                "map_trajectory_raw_bytes": mi.trajectory_raw_len if mi else None,
                "robot": asdict(coordinator.live.robot) if coordinator.live.robot else None,
                "zone_id": coordinator.live.zone_id,
                "clean_started": coordinator.live.clean_started,
            },
            "config": {
                "fields": data.config.raw_fields,
                "messages": {k: v.hex() for k, v in data.config.raw_messages.items()},
            }
            if data.config
            else None,
            "consumables": asdict(data.consumables) if data.consumables else None,
            "map": {
                "ref": mi.ref,
                "size": [mi.width, mi.height],
                "resolution_mm": mi.resolution_mm,
                "rooms": [{"id": r.id, "name": r.name} for r in mi.rooms],
                "doors": len(mi.doors),
                "carpets": len(mi.carpets),
                "forbiddens": len(mi.forbiddens),
                "station": asdict(mi.station) if mi.station else None,
                "furniture": furniture_list(mi, data.names),
                "obstacles": obstacle_list(mi, data.names),
                "dirt": dirt_list(mi, data.names),
            }
            if mi
            else None,
        },
        TO_REDACT,
    )
