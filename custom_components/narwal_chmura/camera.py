"""camera.narwal_kamera: live robot camera via the Narwal kamera add-on.

The add-on joins the Agora channel only while this camera is being watched,
so the robot camera is idle otherwise. The add-on is reachable only on the
internal Supervisor network; HA serves the stream to logged-in users.
"""
from __future__ import annotations

import aiohttp
from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.helpers.aiohttp_client import async_aiohttp_proxy_web, async_get_clientsession

ADDON = "http://local-narwal-kamera:8099"


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([NarwalKamera(hass)])


class NarwalKamera(Camera):
    _attr_name = "Narwal camera"
    _attr_unique_id = "narwal_chmura_kamera"
    _attr_icon = "mdi:robot-vacuum"
    _attr_supported_features = CameraEntityFeature.STREAM
    _attr_should_poll = False

    def __init__(self, hass) -> None:
        super().__init__()
        self.hass = hass
        self.content_type = "image/jpeg"

    async def async_camera_image(self, width=None, height=None):
        try:
            async with async_get_clientsession(self.hass).get(
                ADDON + "/frame.jpg", timeout=aiohttp.ClientTimeout(total=25)
            ) as r:
                return await r.read() if r.status == 200 else None
        except (aiohttp.ClientError, TimeoutError):
            return None

    async def handle_async_mjpeg_stream(self, request):
        session = async_get_clientsession(self.hass)
        stream = session.get(ADDON + "/stream", timeout=aiohttp.ClientTimeout(total=None, sock_read=30))
        return await async_aiohttp_proxy_web(self.hass, request, stream)
