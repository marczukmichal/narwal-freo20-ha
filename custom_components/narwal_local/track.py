"""The cleaned track: stitching the robot's display_map windows, and storing it.

display_map carries RobotTrajectory (packed x and y floats) plus the index of
its first point in the whole track of the job. Windows overlap; writing each
one at its index rebuilds the full track the app draws, also after a
reconnect. Pure functions, so they can be tested without Home Assistant.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
import math

NAN = (float("nan"), float("nan"))
# gaps longer than this many points are not padded (a broken index)
MAX_GAP = 5000


def _same(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return all((not math.isfinite(x) and not math.isfinite(y)) or abs(x - y) < 1e-3
               for x, y in zip(a, b))


def stitch(track: list[tuple[float, float]], first: int | None,
           points: Sequence[tuple[float, float]], limit: int) -> bool:
    """Write an indexed window without shifting its absolute point indices.

    Only an absent index permits overlap matching. Index zero is a real
    prefix, including corrected points and gaps filled after reconnect.
    """
    if not points or first is not None and (first < 0 or first >= limit):
        return False
    if first is None:
        first = next((i for i in range(len(track) - 1, -1, -1)
                      if _same(track[i], points[0])), None)
        if first is None:
            first = len(track) + bool(track)
    if first - len(track) > MAX_GAP:
        return False  # never collapse a gap and corrupt every later index
    points = points[:max(0, limit - first)]
    if not points:
        return False
    old = track[first:first + len(points)]
    changed = len(old) != len(points) or any(not _same(a, b) for a, b in zip(old, points))
    if changed:
        track.extend([NAN] * max(0, first - len(track)))
        track[first:first + len(points)] = points
    return changed


def pack_points(points: Iterable[tuple[float, float]]) -> list:
    """JSON-safe: breaks become null."""
    return [None if not (math.isfinite(x) and math.isfinite(y)) else [round(x, 2), round(y, 2)]
            for x, y in points]


def unpack_points(data) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for p in data or ():
        if p is None:
            out.append(NAN)
        else:
            try:
                out.append((float(p[0]), float(p[1])))
            except (TypeError, ValueError, IndexError):
                continue
    return out


def job_start(timeline) -> float | None:
    """Robot time of the job's first event; the robot starts a fresh timeline
    with each job, so this identifies the job across restarts."""
    times = [e.time for e in timeline or () if getattr(e, "time", 0)]
    return min(times) if times else None
