"""Draws the Narwal map as SVG in the app's style (pastel rooms, walls, furniture, zones).

Pure Python, no dependencies. The input is a MapData object from the `narwal`
(width, height, origin_x, origin_y, compressed_map, rooms, obstacles, raw, dock_x, dock_y).
"""

from __future__ import annotations

import math
import struct
import zlib
from typing import Any

S = 10  # SVG units per grid cell

INK = "#2b3467"  # walls and labels
ROOM_FILL = {  # by room type (RoomType)
    1: "#c5caf6",  # bedroom
    2: "#cfd0f5",
    3: "#cdeeea",  # living room
    4: "#ddd4f3",  # kitchen
    5: "#cfe3f8",  # bathroom
    6: "#cfe3f8",  # toilet
    8: "#f3e3cf",
    10: "#cbe5fa",  # corridor
}
ROOM_FALLBACK = ["#d6e4f7", "#e3d9f4", "#d3efe6", "#f2e2d2", "#e6e0f5"]
ROOM_NAMES_PL = {
    0: "Room", 1: "Bedroom", 2: "Bedroom 2", 3: "Living room", 4: "Kitchen", 5: "Bathroom",
    6: "Bathroom", 7: "Balcony", 8: "Dining room", 9: "Walk-in closet", 10: "Corridor", 11: "Study",
    12: "Kids room", 13: "Game room", 14: "Storage", 15: "Other",
}


def _f32(v: Any) -> float | None:
    if isinstance(v, float):
        return v
    if isinstance(v, int):
        try:
            return struct.unpack("<f", struct.pack("<I", v & 0xFFFFFFFF))[0]
        except struct.error:
            return None
    return None


def _as_list(v: Any) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _decode_grid(compressed: bytes, width: int, height: int) -> list[int]:
    data = b""
    for wbits in (47, 15, -15):
        try:
            data = zlib.decompress(compressed, wbits)
            break
        except zlib.error:
            continue
    pos = 0
    if data[:1] == b"\x0a":
        pos = 1
        while pos < len(data) and data[pos] & 0x80:
            pos += 1
        pos += 1
    out: list[int] = []
    n = len(data)
    while pos < n:
        val = 0
        shift = 0
        while pos < n:
            b = data[pos]
            pos += 1
            val |= (b & 0x7F) << shift
            shift += 7
            if not b & 0x80:
                break
        out.append(val)
    want = width * height
    if len(out) < want:
        out.extend([0] * (want - len(out)))
    return out[:want]


def _loops(cells: set[tuple[int, int]]) -> list[list[tuple[int, int]]]:
    """Outlines of a cell set as closed polylines along the grid edges."""
    nxt: dict[tuple[int, int], list[tuple[int, int]]] = {}

    def add(a, b):
        nxt.setdefault(a, []).append(b)

    for x, y in cells:
        if (x, y - 1) not in cells:
            add((x, y), (x + 1, y))
        if (x + 1, y) not in cells:
            add((x + 1, y), (x + 1, y + 1))
        if (x, y + 1) not in cells:
            add((x + 1, y + 1), (x, y + 1))
        if (x - 1, y) not in cells:
            add((x, y + 1), (x, y))
    loops = []
    while nxt:
        start = next(iter(nxt))
        loop = [start]
        cur = start
        prev_dir = None
        while True:
            outs = nxt[cur]
            if len(outs) == 1 or prev_dir is None:
                b = outs.pop()
            else:
                # where corners touch, turn so two islands are not joined
                def turn(c):
                    d = (c[0] - cur[0], c[1] - cur[1])
                    return prev_dir[0] * d[1] - prev_dir[1] * d[0]
                outs.sort(key=turn)
                b = outs.pop()
            if not outs:
                del nxt[cur]
            prev_dir = (b[0] - cur[0], b[1] - cur[1])
            cur = b
            if cur == start:
                break
            loop.append(cur)
        loops.append(loop)
    return loops


def _smooth(cells: set, rounds: int = 2) -> set:
    """3x3 majority filter: removes single bumps and holes, keeps straight walls."""
    for _ in range(rounds):
        cand = set(cells)
        for x, y in cells:
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    cand.add((x + dx, y + dy))
        nxt = set()
        for x, y in cand:
            n = sum((x + dx, y + dy) in cells for dx in (-1, 0, 1) for dy in (-1, 0, 1))
            if n >= 5:
                nxt.add((x, y))
        cells = nxt
    return cells


def _collinear(loop):
    out = []
    n = len(loop)
    for i in range(n):
        a, b, c = loop[i - 1], loop[i], loop[(i + 1) % n]
        if (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]) != 0:
            out.append(b)
    return out


def _area(loop) -> float:
    a = 0.0
    for i in range(len(loop)):
        x1, y1 = loop[i]
        x2, y2 = loop[(i + 1) % len(loop)]
        a += x1 * y2 - x2 * y1
    return a / 2


def _dp(points, tol):
    if len(points) < 3:
        return points
    (x1, y1), (x2, y2) = points[0], points[-1]
    dx, dy = x2 - x1, y2 - y1
    norm = math.hypot(dx, dy) or 1e-9
    best, idx = 0.0, 0
    for i in range(1, len(points) - 1):
        px, py = points[i]
        d = abs(dx * (py - y1) - dy * (px - x1)) / norm
        if d > best:
            best, idx = d, i
    if best <= tol:
        return [points[0], points[-1]]
    left = _dp(points[: idx + 1], tol)
    return left[:-1] + _dp(points[idx:], tol)


def _simplify(loop, tol):
    if tol <= 0 or len(loop) < 8:
        return loop
    # cut the loop at its two farthest points so DP has fixed ends
    a = 0
    b = max(range(len(loop)), key=lambda i: (loop[i][0] - loop[a][0]) ** 2 + (loop[i][1] - loop[a][1]) ** 2)
    first = _dp(loop[a : b + 1], tol)
    second = _dp(loop[b:] + [loop[a]], tol)
    return first[:-1] + second[:-1]


def _path(loops, tol=0.0, min_area=0.0) -> str:
    parts = []
    for loop in loops:
        if abs(_area(loop)) < min_area:
            continue
        pts = _simplify(loop, tol) if tol > 0 else _collinear(loop)
        if len(pts) < 3:
            continue
        parts.append("M" + "L".join(f"{x * S:.0f} {y * S:.0f}" for x, y in pts) + "Z")
    return "".join(parts)


# ── meble ────────────────────────────────────────────────────────────────────
# Local coordinates: centre at (0,0), w = X size, h = Y size (before rotation).

def _r(x, y, w, h, rx=1.0, extra=""):
    return f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{rx:.1f}" {extra}/>'


def _furniture(t: int, w: float, h: float) -> str:
    x0, y0 = -w / 2, -h / 2
    m = min(w, h)
    base = _r(x0, y0, w, h, min(12, m * 0.18))
    line = 'fill="none"'
    soft = 'class="fd"'
    if t in (1, 2, 3):  # bed: headboard on the -X side
        n = 2 if t == 2 else 1
        ph = (h - 0.16 * h) / n
        pillows = "".join(
            _r(x0 + w * 0.06, y0 + h * 0.08 + i * ph + ph * 0.06, w * 0.2, ph * 0.84, 8, soft) for i in range(n)
        )
        return base + pillows + f'<line x1="{x0 + w * 0.34:.1f}" y1="{y0:.1f}" x2="{x0 + w * 0.34:.1f}" y2="{y0 + h:.1f}" {line}/>'
    if t in (14, 15, 13, 16):  # sofa / fotel: oparcie po stronie -X
        arm = min(h * 0.16, w * 0.3)
        back = w * 0.28
        seat = _r(x0 + back, y0 + arm, w - back, h - 2 * arm, 6, soft)
        cushions = ""
        if t in (14, 15) and h > w * 1.4:
            cushions = f'<line x1="{x0 + back:.1f}" y1="0" x2="{x0 + w:.1f}" y2="0" {line}/>'
        return base + seat + cushions
    if t == 17:  # chair
        return base + _r(x0 + w * 0.3, y0 + h * 0.12, w * 0.58, h * 0.76, 5, soft)
    if t in (4, 6, 31):  # table / coffee table / desk
        return base + _r(x0 + m * 0.12, y0 + m * 0.12, w - m * 0.24, h - m * 0.24, 6, soft)
    if t in (5, 7):  # round table
        return f'<ellipse cx="0" cy="0" rx="{w / 2:.1f}" ry="{h / 2:.1f}"/>'
    if t == 8:  # szafka RTV
        return base + f'<line x1="{x0 + w * 0.5:.1f}" y1="{y0 + h * 0.08:.1f}" x2="{x0 + w * 0.5:.1f}" y2="{y0 + h * 0.92:.1f}" {line}/>'
    if t in (10, 11, 12):  # szafa / szafka
        return base + f'<line x1="{x0:.1f}" y1="0" x2="{x0 + w:.1f}" y2="0" {line}/>' if h >= w else \
            base + f'<line x1="0" y1="{y0:.1f}" x2="0" y2="{y0 + h:.1f}" {line}/>'
    if t == 28:  # toilet: cistern on the -X side
        return (_r(x0, y0 + h * 0.1, w * 0.3, h * 0.8, 4)
                + f'<ellipse cx="{x0 + w * 0.64:.1f}" cy="0" rx="{w * 0.36:.1f}" ry="{h * 0.4:.1f}"/>'
                + f'<ellipse cx="{x0 + w * 0.66:.1f}" cy="0" rx="{w * 0.2:.1f}" ry="{h * 0.22:.1f}" {soft}/>')
    if t == 22:  # pralka
        return base + f'<circle cx="0" cy="0" r="{m * 0.32:.1f}" {soft}/><circle cx="0" cy="0" r="{m * 0.18:.1f}" {line}/>'
    if t == 23:  # fridge
        return base + f'<line x1="{x0 + w * 0.38:.1f}" y1="{y0:.1f}" x2="{x0 + w * 0.38:.1f}" y2="{y0 + h:.1f}" {line}/>'
    if t == 20:  # miski
        r = min(w * 0.42, h * 0.21)
        return "".join(
            f'<circle cx="0" cy="{cy:.1f}" r="{r:.1f}"/><circle cx="0" cy="{cy:.1f}" r="{r * 0.55:.1f}" {soft}/>'
            for cy in (-h * 0.25, h * 0.25)
        )
    if t == 19:  # kuweta
        return base + _r(x0 + m * 0.16, y0 + m * 0.16, w - m * 0.32, h - m * 0.32, 10, soft)
    if t in (75, 74, 73):  # legowisko
        return (f'<ellipse cx="0" cy="0" rx="{w / 2:.1f}" ry="{h / 2:.1f}"/>'
                f'<ellipse cx="0" cy="0" rx="{w * 0.34:.1f}" ry="{h * 0.32:.1f}" {soft}/>')
    return base


def render_svg(m: Any, robot: tuple[float, float, float | None] | None = None,
               trail: list[tuple[float, float]] | None = None, names: dict[int, str] | None = None,
               pins: list[dict] | None = None, pins_out: list | None = None,
               robot_overlay: bool = False) -> str:
    """Returns the SVG. `robot` and `trail` are in grid coordinates (like dock_x/dock_y)."""
    W, H = int(m.width), int(m.height)
    grid = _decode_grid(m.compressed_map, W, H)
    ox, oy = m.origin_x, m.origin_y

    def P(wx: float, wy: float) -> tuple[float, float]:  # world -> image (cells)
        return (wx - ox + 0.5, H - (wy - oy) - 0.5)

    def G(gx: float, gy: float) -> tuple[float, float]:  # siatka → obraz
        return (gx + 0.5, H - gy - 0.5)

    rooms: dict[int, set] = {}
    floors: dict[int, set] = {}
    for i, val in enumerate(grid):
        if val in (0, 0x20, 0x28):
            continue
        rid = (val >> 8) & 0xFFF  # higher bits are flags
        if not rid:
            continue
        cell = (i % W, H - 1 - i // W)
        rooms.setdefault(rid, set()).add(cell)
        if not val & 0x10:
            floors.setdefault(rid, set()).add(cell)
    if not rooms:
        return ""
    xs = [c[0] for cells in rooms.values() for c in cells]
    ys = [c[1] for cells in rooms.values() for c in cells]
    pad = 4
    vx, vy = (min(xs) - pad) * S, (min(ys) - pad) * S
    vw, vh = (max(xs) - min(xs) + 1 + 2 * pad) * S, (max(ys) - min(ys) + 1 + 2 * pad) * S

    # obstacles with photos: positions as viewBox fractions (the HTML card draws them)
    if pins is not None and pins_out is not None:
        for pin in pins:
            px, py = P(pin["x"], pin["y"])
            fx, fy = (px * S - vx) / vw, (py * S - vy) / vh
            if 0 <= fx <= 1 and 0 <= fy <= 1:
                pins_out.append({**pin, "fx": round(fx, 4), "fy": round(fy, 4)})
        pins_out.append({"_aspect": round(vw / vh, 4), "_geo": [H, ox, oy, vx, vy, vw, vh]})

    info = {r.room_id: r for r in getattr(m, "rooms", [])}
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vx} {vy} {vw} {vh}" '
        f'font-family="-apple-system,BlinkMacSystemFont,Roboto,Helvetica,Arial,sans-serif">',
        "<style>"
        f".f{{fill:rgba(255,255,255,.62);stroke:#7d86b9;stroke-width:3;stroke-linejoin:round}}"
        f".f line,.f [fill=none]{{stroke:#7d86b9;stroke-width:2.5}}"
        ".fd{fill:rgba(125,134,185,.16);stroke:#7d86b9;stroke-width:2}"
        f".w{{fill:none;stroke:{INK};stroke-width:11;stroke-linejoin:round}}"
        ".z{fill:rgba(240,150,70,.10);stroke:#f0964a;stroke-width:5;stroke-dasharray:22 12;stroke-linejoin:round}"
        ".c{fill:rgba(90,120,255,.10);stroke:#6b86f5;stroke-width:4;stroke-dasharray:10 8;stroke-linejoin:round}"
        f".t{{fill:{INK};font-weight:650;text-anchor:middle;paint-order:stroke;stroke:rgba(255,255,255,.75);"
        "stroke-width:9;stroke-linejoin:round}"
        "</style>",
    ]

    # rooms: fill, reachable floor slightly darker (as in the app)
    walls = []
    labels = []
    clip = []
    for n, (rid, cells) in enumerate(sorted(rooms.items())):
        r = info.get(rid)
        rtype = getattr(r, "room_sub_type", 0)
        fill = ROOM_FILL.get(rtype) or ROOM_FALLBACK[n % len(ROOM_FALLBACK)]
        d = _path(_loops(_smooth(cells, 3)), min_area=12)
        if not d:
            continue
        out.append(f'<path d="{d}" fill="{fill}" fill-rule="evenodd"/>')
        clip.append(d)
        fl = floors.get(rid)
        if fl:
            fd = _path(_loops(_smooth(fl, 1)), min_area=6)
            out.append(f'<path d="{fd}" fill="{INK}" fill-opacity=".07" fill-rule="evenodd"/>')
        walls.append(f'<path class="w" d="{d}"/>')
        src = fl or cells
        cx = sum(c[0] for c in src) / len(src) + 0.5
        cy = sum(c[1] for c in src) / len(src) + 0.5
        name = (names or {}).get(rid) or getattr(r, "name", "") or ROOM_NAMES_PL.get(rtype, "Room")
        labels.append((cx, cy, name))

    raw = getattr(m, "raw", {}) or {}
    out.append('<clipPath id="rooms"><path d="' + "".join(clip) + '"/></clipPath><g clip-path="url(#rooms)">')

    # zones (field 40): rectangles from the app, pet zones / carpets
    for z in _as_list(raw.get("40")):
        pts = []
        for p in _as_list(z.get("1") if isinstance(z, dict) else None):
            x, y = _f32(p.get("1")), _f32(p.get("2"))
            if x is not None and y is not None:
                pts.append(P(x, y))
        if len(pts) >= 3:
            out.append('<polygon class="z" points="' + " ".join(f"{x * S:.0f},{y * S:.0f}" for x, y in pts) + '"/>')

    # obrys z pola 26 (np. dywanik)
    for c in _as_list(raw.get("26")):
        pts = [P(p.get("1", 0), p.get("2", 0)) for p in _as_list(c.get("2") if isinstance(c, dict) else None)
               if isinstance(p, dict)]
        if len(pts) >= 3:
            out.append('<polygon class="c" points="' + " ".join(f"{x * S:.0f},{y * S:.0f}" for x, y in pts) + '"/>')

    out.append("</g>")
    out.extend(walls)

    # no-go zones (field 9) and virtual walls (field 50), red as in the app
    stref_out, scian_out = [], []

    def frac(x, y):
        return round((x * S - vx) / vw, 4), round((y * S - vy) / vh, 4)

    for z in _as_list(raw.get("9")):
        pts = [P(x, y) for x, y in z.get("pts", [])]
        if len(pts) >= 3:
            out.append('<polygon points="' + " ".join(f"{x * S:.0f},{y * S:.0f}" for x, y in pts)
                       + '" fill="#e5484d" fill-opacity=".18" stroke="#e5484d" stroke-width="4" stroke-dasharray="10 6"/>')
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            stref_out.append({"id": z["id"], "typ": z.get("type"), "f": [*frac(min(xs), min(ys)), *frac(max(xs), max(ys))]})
    for w in _as_list(raw.get("50")):
        (x1, y1), (x2, y2) = P(*w["a"]), P(*w["b"])
        out.append(f'<line x1="{x1 * S:.0f}" y1="{y1 * S:.0f}" x2="{x2 * S:.0f}" y2="{y2 * S:.0f}" '
                   'stroke="#e5484d" stroke-width="8" stroke-linecap="round"/>')
        scian_out.append({"id": w["id"], "f": [*frac(x1, y1), *frac(x2, y2)]})
    if pins_out is not None:
        pins_out.append({"_strefy": stref_out, "_sciany": scian_out})

    # doors (field 11): a gap in the wall
    for dr in _as_list(raw.get("11")):
        pts = [p for p in _as_list(dr.get("2") if isinstance(dr, dict) else None) if isinstance(p, dict)]
        if len(pts) == 2:
            (x1, y1), (x2, y2) = (P(p.get("1", 0), p.get("2", 0)) for p in pts)
            out.append(f'<line x1="{x1 * S:.0f}" y1="{y1 * S:.0f}" x2="{x2 * S:.0f}" y2="{y2 * S:.0f}" '
                       'stroke="#eef0fb" stroke-width="12" stroke-linecap="butt"/>')

    # meble
    out.append('<g class="f">')
    for o in getattr(m, "obstacles", []) or []:
        cx, cy = P(o.center_x, o.center_y)
        w, h = max(o.width, 2) * S, max(o.height, 2) * S
        out.append(f'<g transform="translate({cx * S:.0f} {cy * S:.0f}) rotate({o.angle:.1f})">'
                   + _furniture(o.type_id, w, h) + "</g>")
    out.append("</g>")

    # Native trail: preserve segment breaks instead of drawing lines across rooms.
    for route, opacity in ((getattr(m, "background_trail", []), ".5"), (trail or [], "1")):
        segments, segment = [], []
        for x, y in route:
            if math.isfinite(x) and math.isfinite(y):
                segment.append(G(x, y))
            else:
                if len(segment) > 1:
                    segments.append(segment)
                segment = []
        if len(segment) > 1:
            segments.append(segment)
        for segment in segments:
            out.append(f'<polyline fill="none" stroke="#ffffff" stroke-opacity="{opacity}" stroke-width="5" '
                       'stroke-linejoin="round" stroke-linecap="round" points="'
                       + " ".join(f"{x * S:.0f},{y * S:.0f}" for x, y in segment) + '"/>')

    # napisy
    for cx, cy, name in labels:
        out.append(f'<text class="t" x="{cx * S:.0f}" y="{cy * S + 14:.0f}" font-size="40">{name}</text>')

    # stacja
    if m.dock_x is not None and m.dock_y is not None:
        dx, dy = G(m.dock_x, m.dock_y)
        out.append(f'<g transform="translate({dx * S:.0f} {dy * S:.0f})">'
                   '<ellipse cx="0" cy="18" rx="34" ry="16" fill="#6d7df0"/>'
                   '<rect x="-27" y="-34" width="54" height="56" rx="12" fill="#fff" stroke="#b9c0ea" stroke-width="3"/>'
                   '<rect x="-12" y="8" width="24" height="5" rx="2.5" fill="#b9c0ea"/></g>')

    # robot; with robot_overlay the HTML card draws it (smooth movement between readings)
    if robot and robot_overlay and pins_out is not None:
        rx, ry = G(robot[0], robot[1])
        pins_out.append({"_robot": {"fx": round((rx * S - vx) / vw, 5), "fy": round((ry * S - vy) / vh, 5),
                                    "r": round(30 / vw, 5)}})
    elif robot:
        rx, ry = G(robot[0], robot[1])
        out.append(f'<g transform="translate({rx * S:.0f} {ry * S:.0f})">'
                   '<circle r="30" fill="#6d7df0" fill-opacity=".25"/>'
                   f'<circle r="19" fill="#fff" stroke="{INK}" stroke-width="5"/>'
                   f'<circle r="6" fill="{INK}"/></g>')

    out.append("</svg>")
    return "".join(out)


def project_pins(geo: list, pins: list[dict]) -> tuple[float, list[dict]]:
    """Pin positions from the remembered geometry (robot asleep, no fresh map)."""
    H, ox, oy, vx, vy, vw, vh = geo
    out = []
    for pin in pins:
        px, py = pin["x"] - ox + 0.5, H - (pin["y"] - oy) - 0.5
        fx, fy = (px * S - vx) / vw, (py * S - vy) / vh
        if 0 <= fx <= 1 and 0 <= fy <= 1:
            out.append({**pin, "fx": round(fx, 4), "fy": round(fy, 4)})
    return round(vw / vh, 4), out
