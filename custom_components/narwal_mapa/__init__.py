"""App-style Narwal map as an image entity (SVG).

Reads the map grid, rooms, furniture, zones and the live robot position from
the `narwal_local` coordinator. Enable with `narwal_mapa:` in configuration.yaml.
"""

from __future__ import annotations

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import discovery
from homeassistant.helpers.typing import ConfigType

DOMAIN = "narwal_mapa"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Start the image platform."""
    hass.async_create_task(
        discovery.async_load_platform(hass, "image", DOMAIN, {}, config)
    )

    async def strefa(call: ServiceCall) -> None:
        """Add or delete a no-go zone or virtual wall drawn on the window's map.
        Corners come as fractions of the drawn map; they are turned back into
        the robot's world metres with the map the robot has now."""
        await _admin(hass, call)
        coordinator = _local(hass)
        mi = coordinator.data.map
        if call.data.get("usun") is not None:
            if call.data["rodzaj"] == "sciana":
                await coordinator.async_edit_virtual_walls(delete=[call.data["usun"]])
            else:
                await coordinator.async_edit_no_go_zones(delete=[call.data["usun"]])
            return
        geo = hass.data.get("narwal_mapa_geo")
        if not geo or mi is None:
            raise HomeAssistantError("No current robot map")
        f = call.data.get("f")
        if not f:
            raise HomeAssistantError("No coordinates")
        H, _ox, _oy, vx, vy, vw, vh = geo

        def swiat(fx: float, fy: float) -> tuple[float, float]:
            px, py = (fx * vw + vx) / 10, (fy * vh + vy) / 10  # S = 10 units per cell
            p = mi.cell_to_world(px - 0.5, H - py - 0.5)
            return p.x, p.y

        (x1, y1), (x2, y2) = swiat(f[0], f[1]), swiat(f[2], f[3])
        if call.data["rodzaj"] == "sciana":
            await coordinator.async_edit_virtual_walls(add=[(x1, y1, x2, y2)])
        else:
            from custom_components.narwal_local.protocol import ForbiddenType, NoGoZone  # noqa: PLC0415

            typ = {"no_entry": ForbiddenType.SWEEP_MOP, "no_vacuum": ForbiddenType.SWEEP,
                   "no_mop": ForbiddenType.MOP}[call.data["rodzaj"]]
            await coordinator.async_edit_no_go_zones(add=[NoGoZone(x1, y1, x2, y2, typ)])

    hass.services.async_register(DOMAIN, "strefa", strefa, schema=vol.Schema({
        vol.Required("rodzaj"): vol.In(["no_entry", "no_vacuum", "no_mop", "sciana"]),
        vol.Optional("f"): vol.All([vol.All(vol.Coerce(float), vol.Range(min=0, max=1))], vol.Length(min=4, max=4)),
        vol.Optional("usun"): vol.Coerce(int),
    }))
    return True


async def _admin(hass: HomeAssistant, call: ServiceCall) -> None:
    if call.context.user_id:
        user = await hass.auth.async_get_user(call.context.user_id)
        if user is None or not user.is_admin:
            raise HomeAssistantError("Administrators only")


def _local(hass: HomeAssistant):
    for entry in hass.config_entries.async_entries("narwal_local"):
        coordinator = getattr(entry, "runtime_data", None)
        if coordinator is not None and getattr(coordinator, "data", None) is not None:
            return coordinator
    raise HomeAssistantError("No local connection to the robot (narwal_local integration)")
