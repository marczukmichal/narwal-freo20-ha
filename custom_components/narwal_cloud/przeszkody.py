"""Obstacle photos from the Narwal cloud, decrypted locally in HA.

Key exchange (from the Narwal app): we send our RSA public key, the cloud
returns an AES-128 key wrapped with it (PKCS#1 v1.5), and with that key
(AES-128-ECB) the device IV (hex) and the device RSA private key (PEM).
Each photo (.jpg.nenc) = 256 bytes RSA(PKCS#1 v1.5, device key) holding an
AES-256 key, then AES-256-CBC(PKCS7) JPEG with the device IV.
Decrypted photos are kept under /config/.storage/narwal_przeszkody (not www).
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import padding as sympad
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .api import NarwalChmuraApi

_LOGGER = logging.getLogger(__name__)

KEY_PATH = "/iot-platform/iot-device-key-manage-server/v2/device/secret/key/get"
RECORDS_PATH = "/sweeper-map-scheme-app-server/v2/work_record/all"
DETAIL_PATH = "/clean-record-app-server/v1/clean-record/detail"
ALBUM_PATH = "/clean-record-app-server/v2/deviceFile/listMedia"
DEVICES_PATH = "/user-device-platform-server/device-info/getDeviceInfoList"

ETYKIETY = {
    0: "Unknown object", 1: "Cable", 2: "Tissue", 3: "Pet waste", 4: "Liquid",
    5: "Shoes", 6: "Solid dirt", 7: "Socks", 8: "Plastic bag", 9: "Trash can",
    10: "Obstacle", 11: "Cannot be cleaned", 12: "Fabric", 13: "None",
    14: "Floor texture", 15: "Pet", 16: "Ledge", 17: "Squat toilet",
    18: "Chair legs", 19: "Chair wheels", 20: "Mirror", 21: "Book", 22: "Stick",
    25: "Phone", 26: "Wallet", 27: "Keys", 28: "Banknote", 29: "Remote control",
    30: "Glasses", 31: "Watch", 32: "Card", 34: "Toy", 35: "Plant",
    36: "Fitness equipment", 37: "Pet toy", 38: "Dangerous object",
    39: "Dekoracja", 40: "Karton", 41: "Kot", 42: "Pies",
}
_NAME = re.compile(r"/(\d+)_\d+_(\d+)_(\d+)\.jpg\.nenc$")


def _aes_ecb(key: bytes, data: bytes) -> bytes:
    dec = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    raw = dec.update(data) + dec.finalize()
    unp = sympad.PKCS7(128).unpadder()
    return unp.update(raw) + unp.finalize()


class Przeszkody:
    def __init__(self, hass, api: NarwalChmuraApi) -> None:
        self.hass = hass
        self.api = api
        self.dir = Path(hass.config.path(".storage", "narwal_przeszkody"))
        self.device_id: str | None = None
        self.product_id: str | None = None
        self._dev_key = None
        self._iv: bytes | None = None
        self._photo_lock = asyncio.Lock()
        self.data: dict = {"lista": [], "sprzatanie": None}

    async def _device(self) -> None:
        if self.device_id:
            return
        res = await self.api.request("GET", DEVICES_PATH)
        devs = (res.get("result") or {}).get("deviceInfoList") or []
        if not devs:
            raise RuntimeError("No robot on the Narwal account")
        self.device_id, self.product_id = devs[0]["deviceId"], devs[0]["productId"]

    def _kw(self, params=None, body=None) -> dict:
        return {"params": params, "body": body,
                "device_id": self.device_id, "product_id": self.product_id}

    async def _klucz(self) -> None:
        if self._dev_key is not None:
            return
        own = await self.hass.async_add_executor_job(
            lambda: rsa.generate_private_key(public_exponent=65537, key_size=2048))
        pub = own.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        res = await self.api.request("POST", KEY_PATH, **self._kw(body={
            "productId": self.product_id, "deviceId": self.device_id,
            "keyName": 1, "publicKey": pub}))
        r = res.get("result") or {}
        if res.get("code") != 0 or not r.get("secretKeyCiphertext"):
            raise RuntimeError(f"Narwal did not issue the photo key: {res.get('msg')}")
        aes = own.decrypt(base64.b64decode(r["aesSecretCiphertext"]), padding.PKCS1v15())
        self._iv = bytes.fromhex(_aes_ecb(aes, base64.b64decode(r["ivCiphertext"])).decode()[:32])
        pem = _aes_ecb(aes, base64.b64decode(r["secretKeyCiphertext"]))
        self._dev_key = serialization.load_pem_private_key(pem, password=None)

    def _odszyfruj(self, data: bytes) -> bytes:
        key = self._dev_key.decrypt(data[:256], padding.PKCS1v15())
        dec = Cipher(algorithms.AES(key), modes.CBC(self._iv)).decryptor()
        raw = dec.update(data[256:]) + dec.finalize()
        unp = sympad.PKCS7(128).unpadder()
        out = unp.update(raw) + unp.finalize()
        if not out.startswith(b"\xff\xd8"):
            raise ValueError("not a JPEG")
        return out

    async def na_zywo(self, obstacle_id: int, job: float) -> dict:
        """Fetch only a recognised obstacle from this job, on the user's click."""
        from ..narwal_local.protocol.photo import MAX_PHOTO_BYTES, photo_request, photo_response
        from ..narwal_local.protocol.enums import ServiceResult
        from urllib.parse import urlparse

        coordinator = next((getattr(e, "runtime_data", None)
                            for e in self.hass.config_entries.async_entries("narwal_local")
                            if getattr(e, "disabled_by", None) is None), None)
        if coordinator is None:
            raise ValueError("No local connection to the robot")

        def cleaning() -> bool:
            return coordinator.data is not None and coordinator.data.status.in_clean_task

        # Outside a clean (manual driving, remote control) the robot still recognises
        # obstacles and keeps their photos, but there is no job to tie them to: fetch
        # the photo fresh each time, under a folder of its own.
        in_job = cleaning()

        def current():
            if not in_job:
                return not cleaning()
            return coordinator.live.clean_started == job and obstacle_id in coordinator.live.obstacles
        if not current():
            raise ValueError("This obstacle no longer belongs to the current cleaning")
        folder = self.dir / str(int((job if in_job else time.time()) * 1000))
        path = folder / f"{obstacle_id}.jpg"
        async with self._photo_lock:
            if not in_job or not await self.hass.async_add_executor_job(path.is_file):
                reply = await coordinator.robot.call("/map/get_vision_image", photo_request(obstacle_id), timeout=15)
                photo = photo_response(reply.payload)
                if photo.result != ServiceResult.PERFORM_SUCCESS:
                    raise ValueError("The robot has not shared a photo of this obstacle yet. Try again later.")
                data = photo.image
                if not data and photo.url:
                    url = urlparse(photo.url)
                    if url.scheme != "https" or not url.hostname or url.username or url.password:
                        raise ValueError("The robot returned an unsupported photo address")
                    # Fetch only the robot's existing photo, bounded in size. Never log signed URLs.
                    async with self.api._session.get(photo.url, timeout=20) as response:
                        response.raise_for_status()
                        data = await response.content.read(MAX_PHOTO_BYTES + 1)
                if not data or len(data) > MAX_PHOTO_BYTES:
                    raise ValueError("The robot did not return a valid photo")
                if not data.startswith(b"\xff\xd8"):
                    await self._device()
                    await self._klucz()
                    data = await self.hass.async_add_executor_job(self._odszyfruj, data)
                if not current():
                    raise ValueError("The cleaning changed while the photo was loading")
                await self.hass.async_add_executor_job(lambda: folder.mkdir(parents=True, exist_ok=True))
                await self.hass.async_add_executor_job(path.write_bytes, data)
        return {"sciezka": f"/api/narwal_cloud/przeszkoda/{folder.name}/{obstacle_id}.jpg"}

    async def odswiez(self) -> dict:
        data = await self._przeszkody()
        try:
            data["zwierzak"] = await self._zwierzak()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Pet photos: %s", err)
            data["zwierzak"] = (self.data or {}).get("zwierzak") or []
        self.data = data
        return data

    async def _zwierzak(self) -> list[dict]:
        """Pet photos the robot took while cleaning (the app's pet album).
        Plain JPEGs behind short-lived signed links, so they are kept locally."""
        res = await self.api.request("GET", ALBUM_PATH, **self._kw(params={
            "device_id": self.device_id, "product_id": self.product_id, "type": 0}))
        if res.get("code") != 0:
            raise RuntimeError(res.get("msg"))
        session = self.api._session  # noqa: SLF001
        lista = []
        for m in (res.get("result") or {}).get("media") or []:
            if "/pet/image/" not in (m.get("file_url") or ""):
                continue
            try:
                meta = json.loads(m.get("metadata") or "{}")
            except ValueError:
                meta = {}
            zadanie = str(meta.get("task_id") or "0")
            ts = int(m.get("shooting_time") or 0)
            if not zadanie.isdigit() or not ts:
                continue
            folder = self.dir / zadanie
            plik = folder / f"{ts}.jpg"
            if not plik.exists():
                try:
                    async with session.get(m["file_url"], timeout=30) as r:
                        jpg = await r.read()
                    if not jpg.startswith(b"\xff\xd8"):
                        raise ValueError("not a JPEG")
                    await self.hass.async_add_executor_job(lambda f=folder: f.mkdir(parents=True, exist_ok=True))
                    await self.hass.async_add_executor_job(plik.write_bytes, jpg)
                except Exception as err:  # noqa: BLE001
                    _LOGGER.warning("Pet photo %s: %s", ts, err)
                    continue
            lista.append({"nazwa": "Zwierzak", "czas": datetime.fromtimestamp(ts, timezone.utc).isoformat(),
                          "pokoj": meta.get("room_id"), "mapa": meta.get("map_id"),
                          "sciezka": f"/api/narwal_cloud/przeszkoda/{zadanie}/{ts}.jpg"})
        lista.sort(key=lambda x: x["czas"], reverse=True)
        return lista

    async def _przeszkody(self) -> dict:
        await self._device()
        res = await self.api.request("GET", RECORDS_PATH, **self._kw(
            params={"deviceId": self.device_id, "productId": self.product_id}))
        records = sorted(res.get("result") or [], key=lambda r: r.get("start_time") or 0, reverse=True)
        if not records:
            return {"lista": [], "sprzatanie": None}
        rec = records[0]
        return {"lista": await self.rekord(rec),
                "sprzatanie": datetime.fromtimestamp(rec.get("start_time") or 0, timezone.utc).isoformat(),
                "rekord": str(rec.get("record_id"))}

    async def sprzatanie(self, rekord: str) -> dict:
        """Photos of one cleaning record (the app's record detail), on request."""
        await self._device()
        res = await self.api.request("GET", RECORDS_PATH, **self._kw(
            params={"deviceId": self.device_id, "productId": self.product_id}))
        rec = next((r for r in res.get("result") or [] if str(r.get("record_id")) == str(rekord)), None)
        if rec is None:
            raise ValueError("No such cleaning in the Narwal cloud")
        return {"rekord": str(rekord),
                "sprzatanie": datetime.fromtimestamp(rec.get("start_time") or 0, timezone.utc).isoformat(),
                "lista": await self.rekord(rec)}

    async def rekord(self, rec: dict) -> list[dict]:
        """Download and decrypt the obstacle photos of one record (cached)."""
        rid = str(rec.get("record_id"))
        if not rid.isdigit():
            return []
        det = await self.api.request("GET", DETAIL_PATH, **self._kw(params={
            "clean_record_id": rid, "product_id": self.product_id, "device_id": self.device_id}))
        res = det.get("result") or {}
        imgs = (res.get("image_resources") or {}).get("obs_images") or []
        poz = {}
        for v in (res.get("vision_data") or {}).get("vision_obs") or []:
            c = (v.get("box") or {}).get("center") or {}
            if v.get("id") is not None and c.get("x") is not None:
                poz[str(v["id"])] = (round(float(c["x"]), 2), round(float(c.get("y", 0)), 2))
        if not imgs:
            return []
        await self._klucz()
        session = self.api._session  # noqa: SLF001
        folder = self.dir / rid
        await self.hass.async_add_executor_job(lambda: folder.mkdir(parents=True, exist_ok=True))
        lista = []
        for im in imgs:
            url = im.get("url") or ""
            m = _NAME.search(url)
            ts, label = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
            plik = folder / f"{im.get('id')}.jpg"
            if not plik.exists():
                try:
                    async with session.get(url, timeout=30) as r:
                        enc = await r.read()
                    jpg = await self.hass.async_add_executor_job(self._odszyfruj, enc)
                    await self.hass.async_add_executor_job(plik.write_bytes, jpg)
                except Exception as err:  # noqa: BLE001
                    _LOGGER.warning("Obstacle photo %s: %s", im.get("id"), err)
                    continue
            czas = datetime.fromtimestamp(ts / 1e9, timezone.utc).isoformat() if ts > 1e17 else None
            lista.append({"id": im.get("id"), "etykieta": label,
                          "nazwa": ETYKIETY.get(label, f"Object {label}"), "czas": czas,
                          "x": poz.get(str(im.get("id")), (None, None))[0],
                          "y": poz.get(str(im.get("id")), (None, None))[1],
                          "mapa": res.get("map_id") or rec.get("map_id"),
                          "sciezka": f"/api/narwal_cloud/przeszkoda/{rid}/{im.get('id')}.jpg"})
        lista.sort(key=lambda x: x["czas"] or "", reverse=True)
        return lista
