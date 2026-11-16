"""Parts and consumables with their remaining hours, as in the app's
"Parts and Consumables" screen. The robot itself only reports which parts are
due (/consumable/get_consumable_info); the hours live in Narwal's cloud:

POST /consumables-management-app-server/v3/consumables/list {productId, deviceId}
 -> [{consumables_code, name, item_type (1 maintain, 2 replace), usage_duration,
      total_duration (seconds), progress_bar_switch (1 = has hours), icon}]

The part pictures (icon) sit on Narwal's public CDN, served as
application/octet-stream; they are copied once into www/narwal/czesci so the
window shows them from Home Assistant as normal PNGs.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

LIST_PATH = "/consumables-management-app-server/v3/consumables/list"
# What the app calls for robots with supportConsumablesOnCloud (Freo 20):
# NRConsumableActionService._resetCloudConsumable -> ResetConsumableRequester.
RESET_PATH = "/consumables-management-app-server/consumables/reset"

# Names as in the English app, by the cloud's part code (Freo 20); unknown codes keep the cloud name.
NAZWY = {
    "uFs1ZxFz8G": "Station dust bag",
    "TQjfy3YAtQ": "Dust box filter",
    "xwYhtWn3ko": "Track mop",
    "KBpp7EeVIt": "Self-cleaning mop scraper",
    "HMBE6UBuEl": "Station replaceable filter",
    "yPtGfPmPd6": "Floating main brush",
    "bY252SrptP": "Carpet brush cover",
    "9DRtob0Hbi": "Side brush",
    "bQekrFNEgI": "Sensors",
    "fSts92ZlA5": "Sponge filter",
    "zUMDqTyWmV": "Wheel",
}


class Czesci:
    def __init__(self, przeszkody) -> None:
        self.p = przeszkody  # shares the device lookup and the API session

    async def odswiez(self) -> dict[str, dict]:
        await self.p._device()  # noqa: SLF001
        res = await self.p.api.request(
            "POST", LIST_PATH,
            body={"productId": self.p.product_id, "deviceId": self.p.device_id},
            device_id=self.p.device_id, product_id=self.p.product_id,
        )
        if res.get("code") not in (0, None):
            raise RuntimeError(f"Narwal parts: {res.get('msg')}")
        out: dict[str, dict] = {}
        hass = self.p.hass
        folder = Path(hass.config.path("www", "narwal", "czesci"))
        for x in res.get("result") or []:
            total, used = x.get("total_duration") or 0, x.get("usage_duration") or 0
            godziny = bool(x.get("progress_bar_switch")) and total > 0
            left = max(total - used, 0)
            out[x["consumables_code"]] = {
                "nazwa": NAZWY.get(x["consumables_code"], x.get("name")),
                "nazwa_chmura": x.get("name"),
                "rodzaj": "wymiana" if x.get("item_type") == 2 else "konserwacja",
                "pozostalo_h": int(left // 3600) if godziny else None,
                "procent": round(100 * left / total) if godziny else None,
                "uzyte_h": round(used / 3600, 1) if godziny else None,
                "zywotnosc_h": round(total / 3600) if godziny else None,
                "opis": x.get("subtitle") or "",
                "kod": x["consumables_code"],
                "zeruj": bool(x.get("reset_btn_switch")),
                "obrazek": await self._obrazek(folder, x["consumables_code"], x.get("icon")),
            }
        return out

    async def zeruj(self, kod: str) -> None:
        """"Replaced / maintained": start the part's hours from zero, as the app's Reset."""
        await self.p._device()  # noqa: SLF001
        res = await self.p.api.request(
            "POST", RESET_PATH,
            body={"device_id": self.p.device_id, "product_id": self.p.product_id, "consumables_code": kod},
            device_id=self.p.device_id, product_id=self.p.product_id,
        )
        # An unknown code answers success with result False; only True means reset.
        if res.get("code") not in (0, None) or res.get("result") is not True:
            raise RuntimeError(f"Narwal did not reset the part: {res.get('msg')}")

    async def _obrazek(self, folder: Path, kod: str, url: str | None) -> str | None:
        """Local copy of the part picture; the file name changes with the URL."""
        if not url or not url.startswith("https://") or not kod.isalnum():
            return None
        nazwa = f"{kod}-{hashlib.sha1(url.encode()).hexdigest()[:8]}.png"
        plik = folder / nazwa
        hass = self.p.hass
        if not await hass.async_add_executor_job(plik.is_file):
            try:
                async with self.p.api._session.get(url, timeout=30) as r:  # noqa: SLF001
                    r.raise_for_status()
                    dane = await r.read()
                if not dane.startswith(b"\x89PNG"):
                    return None
                await hass.async_add_executor_job(lambda: folder.mkdir(parents=True, exist_ok=True))
                await hass.async_add_executor_job(plik.write_bytes, dane)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Part picture %s: %s", kod, err)
                return None
        return f"/local/narwal/czesci/{nazwa}"
