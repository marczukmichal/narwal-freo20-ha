"""Authenticated, bounded PCM messages over HA's existing WebSocket."""
import aiohttp
import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.helpers.aiohttp_client import async_get_clientsession


@websocket_api.websocket_command({
    vol.Required('type'): 'narwal_cloud/audio',
    vol.Required('action'): vol.In(['listen_start', 'listen_stop', 'poll', 'talk_start', 'talk_stop', 'talk_pcm']),
    vol.Required('session_id'): vol.All(str, vol.Length(min=1, max=64)),
    vol.Optional('talk_id'): vol.All(str, vol.Length(max=64)),
    vol.Optional('seq'): vol.All(int, vol.Range(min=0)),
    vol.Optional('pcm'): vol.All(str, vol.Length(max=4300)),
})
@websocket_api.async_response
async def ws_audio(hass, connection, msg):
    if not connection.user.is_admin:
        connection.send_error(msg['id'], 'unauthorized', 'Administrators only')
        return
    body = {k: v for k, v in msg.items() if k not in ('id', 'type')}
    try:
        async with async_get_clientsession(hass).post(
            'http://local-narwal-kamera:8099/audio', json=body,
            # First audio start may wait for Narwal's cloud request (up to 20s).
            # PCM/stop remain short; stale microphone frames are never retried.
            timeout=aiohttp.ClientTimeout(total=25 if msg['action'] in ('listen_start', 'talk_start') else 2),
        ) as response:
            if response.status != 200:
                try:
                    detail = (await response.json()).get('error')
                except (aiohttp.ContentTypeError, ValueError):
                    detail = None
                connection.send_error(msg['id'], 'audio_unavailable', detail or 'Audio unavailable or the microphone is busy. Connect again.')
                return
            connection.send_result(msg['id'], await response.json())
    except (aiohttp.ClientError, TimeoutError):
        connection.send_error(msg['id'], 'audio_disconnected', 'Audio connection lost')


def setup_audio(hass):
    websocket_api.async_register_command(hass, ws_audio)
