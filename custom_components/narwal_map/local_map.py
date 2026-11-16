"""Adapt the active Narwal Local map and live pose for the SVG renderer.

Narwal Local's origin is in metres. SVG overlays use border-frame cells,
so the renderer's origin must be border_left/border_bottom instead.
"""

from __future__ import annotations

import math
import time
from types import SimpleNamespace
import zlib

LIVE_POSITION_SECONDS = 15


def local_snapshot(coordinator, *, now: float | None = None):
    """Return renderer data without importing Home Assistant or sending commands."""
    data = getattr(coordinator, "data", None)
    mi = getattr(data, "map", None)
    if mi is None or not mi.robot_has_map or not mi.grid_raw:
        return None
    origin_x, origin_y = mi.border_left, mi.border_bottom
    station = mi.station
    m = SimpleNamespace(
        width=mi.width, height=mi.height, resolution=mi.resolution_mm,
        origin_x=origin_x, origin_y=origin_y,
        # grid_compressed may actually be uncompressed on some firmwares.
        compressed_map=zlib.compress(mi.grid_raw),
        dock_x=station.x - origin_x if station else None,
        dock_y=station.y - origin_y if station else None,
        rooms=[SimpleNamespace(room_id=r.id, name=r.name, room_sub_type=r.room_type)
               for r in mi.rooms],
        obstacles=[SimpleNamespace(id=f.id, type_id=int(f.type), center_x=f.x,
                                   center_y=f.y, width=f.width, height=f.height, angle=f.angle)
                   for f in mi.furniture],
        raw={
            "11": [{"2": [{"1": p.x, "2": p.y} for p in door]} for door in mi.doors],
            "26": [{"2": [{"1": p.x, "2": p.y} for p in c.points]} for c in mi.carpets],
            "40": [{"1": [{"1": float(p.x), "2": float(p.y)} for p in zone]}
                   for zone in getattr(mi, "custom_regions", ())],
            # no-go zones and virtual walls, border-frame cells like the rest
            "9": [{"id": fa.id, "type": int(fa.type), "pts": [(float(p.x), float(p.y)) for p in fa.polygon]}
                  for fa in getattr(mi, "forbiddens", ()) if getattr(fa, "mode", 0) == 0],
            "50": [{"id": w.id, "a": (float(w.start.x), float(w.start.y)), "b": (float(w.end.x), float(w.end.y))}
                   for w in getattr(mi, "virtual_walls", ())],
        },
    )
    live = coordinator.live
    # Obstacles seen during the current job, border frame like the cloud pins.
    m.live_obstacles = [(o.id, int(o.label), o.x, o.y)
                        for o in getattr(live, "obstacles", {}).values()]
    m.clean_started = getattr(live, "clean_started", None)
    connected = bool(getattr(coordinator, "last_update_success", False))
    activity = str(data.status.activity)
    docked = activity == "docked" if connected else None
    pose = live.robot
    received = getattr(live, "received_at", 0.0)
    age = max(0.0, (time.monotonic() if now is None else now) - received) if received else None
    robot = None
    if pose is not None and docked is not True:
        x, y = pose.x - origin_x, pose.y - origin_y
        if math.isfinite(x) and math.isfinite(y) and 0 <= x < mi.width and 0 <= y < mi.height:
            robot = (round(x, 1), round(y, 1), math.degrees(pose.theta))
    position_status = (
        "docked" if docked is True else
        "unavailable" if robot is None else
        "live" if connected and age is not None and age <= LIVE_POSITION_SECONDS else
        "last_known"
    )
    trail = []
    if docked is not True:
        for x, y in live.path:
            x, y = x - origin_x, y - origin_y
            if math.isfinite(x) and math.isfinite(y) and 0 <= x < mi.width and 0 <= y < mi.height:
                trail.append((x, y))
            elif trail and math.isfinite(trail[-1][0]):
                trail.append((float("nan"), float("nan")))
    metadata = {
        "map_source": "narwal_local",
        "robot_docked": docked,
        "robot_position_status": position_status,
        "robot_position": {"x": robot[0], "y": robot[1]} if robot else None,
    }
    return m, robot, trail, metadata
