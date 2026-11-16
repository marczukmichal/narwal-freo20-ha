"""Cleaning history and statistics from Narwal's cloud, as in the app's
cleaning records and "robot journey" screens.

GET /sweeper-map-scheme-app-server/v2/work_record/all {deviceId, productId}
 -> [{start_time, end_time, duration (s), area (m²), task_type, error_code,
      clean_order, room_infos, ...}]
GET /clean-record-statistics-server/v2/robot-journey/getAllData {deviceId, productId}
 -> {area, cleanTimes, duration, dataList: [{day, area, cleanTimes, duration}]}
"""
from __future__ import annotations

from datetime import datetime, timezone

RECORDS_PATH = "/sweeper-map-scheme-app-server/v2/work_record/all"
STATS_PATH = "/clean-record-statistics-server/v2/robot-journey/getAllData"

# task_type is the work mode, as in sprzatanie.TRYB
TRYB = {1: "Vacuum", 2: "Mop", 3: "Vacuum then mop", 4: "Vacuum and mop"}


def _czas(ts) -> str | None:
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


class Historia:
    def __init__(self, przeszkody) -> None:
        self.p = przeszkody  # shares the device lookup and the API session

    async def _get(self, path: str):
        res = await self.p.api.request(
            "GET", path, params={"deviceId": self.p.device_id, "productId": self.p.product_id},
            device_id=self.p.device_id, product_id=self.p.product_id,
        )
        if res.get("code") not in (0, None):
            raise RuntimeError(f"Narwal historia: {res.get('msg')}")
        return res.get("result")

    async def odswiez(self) -> dict:
        await self.p._device()  # noqa: SLF001
        rekordy = await self._get(RECORDS_PATH) or []
        staty = await self._get(STATS_PATH) or {}
        lista = []
        for r in sorted(rekordy, key=lambda x: x.get("start_time") or 0, reverse=True)[:30]:
            lista.append({
                "rekord": str(r.get("record_id") or ""),
                "start": _czas(r.get("start_time")),
                "koniec": _czas(r.get("end_time")),
                "minuty": round((r.get("duration") or 0) / 60),
                "m2": round(r.get("area") or 0, 1),
                "tryb": TRYB.get(r.get("task_type"), "Cleaning"),
                "pokoje": len(r.get("clean_order") or []),
                "blad": r.get("error_code") or 0,
            })
        return {
            "lista": lista,
            "laczna_powierzchnia": round(staty.get("area") or 0, 1),
            "liczba_sprzatan": staty.get("cleanTimes") or len(rekordy),
            "laczny_czas_h": round((staty.get("duration") or 0) / 3600, 1),
            "dni": [{"dzien": d.get("day"), "m2": round(d.get("area") or 0, 1), "minuty": round((d.get("duration") or 0) / 60),
                     "sprzatania": d.get("cleanTimes") or 0} for d in (staty.get("dataList") or [])][-30:],
        }
