"""Obstacle pins on the map: cloud photos or obstacles from the current job.

The cloud gets the cleaning record only after the job ends. Once the robot
starts a new job, pins from the previous one are hidden and the obstacles the
robot recognises now are shown (without photos) until the cloud record for
this job arrives.
"""

from __future__ import annotations

from datetime import datetime

# the cloud record carries the job start time; allow for clock differences
MARGIN_S = 300

# same names as the cloud photos (narwal_cloud/przeszkody.py)
NAZWY = {1: "Cable", 2: "Tissue", 3: "Pet waste", 4: "Liquid", 5: "Shoes", 6: "Solid dirt",
         7: "Socks", 8: "Plastic bag", 9: "Trash can", 10: "Obstacle",
         11: "Cannot be cleaned"}


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _record_start(st) -> float | None:
    when = st.attributes.get("sprzatanie") if st else None
    try:
        return datetime.fromisoformat(when).timestamp() if when else None
    except (TypeError, ValueError):
        return None


def map_pins(st, clean_started: float | None, live: list | None) -> list[dict]:
    """Pins to draw on the map."""
    record = _record_start(st)
    stale = clean_started is not None and (record is None or record < clean_started - MARGIN_S)
    if stale:
        return [{"id": f"live-{oid}", "x": x, "y": y, "etykieta": label,
                 "nazwa": NAZWY.get(label, "Obstacle"), "na_zywo": True}
                for oid, label, x, y in live or () if _num(x) and _num(y)]
    return [{"id": o["id"], "x": o["x"], "y": o["y"], "nazwa": o.get("nazwa"), "etykieta": o.get("etykieta")}
            for o in ((st.attributes.get("przeszkody") or []) if st else [])
            if _num(o.get("x")) and _num(o.get("y"))]
