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
    return abs(a[0] - b[0]) < 1e-3 and abs(a[1] - b[1]) < 1e-3


def stitch(track: list[tuple[float, float]], first: int, points: Sequence[tuple[float, float]],
           limit: int) -> bool:
    """Write `points` into `track` at index `first`. Returns True on change."""
    if not points:
        return False
    before = (len(track), track[-1] if track else None)
    if first > 0 or not track:
        if first > len(track):
            gap = first - len(track)
            if gap > MAX_GAP:
                track.append(NAN)
            else:
                track.extend([NAN] * gap)
            track.extend(points)
        else:
            track[first:first + len(points)] = points
    else:
        # index 0 on a non-empty track: either the whole track again, or a
        # window without an index; find where it continues the old track
        if _same(track[0], points[0]) or len(points) >= len(track):
            track[:len(points)] = points
        else:
            at = next((i for i in range(len(track) - 1, -1, -1) if _same(track[i], points[0])), None)
            if at is None:
                track.append(NAN)
                track.extend(points)
            else:
                track[at:] = points
    if len(track) > limit:
        del track[: len(track) - limit]
    return (len(track), track[-1] if track else None) != before


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
