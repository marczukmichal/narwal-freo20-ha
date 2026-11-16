"""Config flow: the user types email + password; only tokens are saved."""
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import HOSTS, NarwalChmuraApi, NarwalChmuraAuthError
from .const import DOMAIN

SCHEMA = vol.Schema({
    vol.Required("email"): str,
    vol.Required("password"): str,
    vol.Required("region", default="eu"): vol.In(list(HOSTS)),
})


class NarwalChmuraFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors: dict[str, str] = {}
        if user_input is not None:
            api = NarwalChmuraApi(async_get_clientsession(self.hass), user_input["region"])
            try:
                await api.login(user_input["email"], user_input["password"])
            except NarwalChmuraAuthError:
                errors["base"] = "invalid_auth"
            except Exception:  # noqa: BLE001
                errors["base"] = "cannot_connect"
            else:
                if self.source == config_entries.SOURCE_REAUTH:
                    return self.async_update_reload_and_abort(
                        self._get_reauth_entry(), data={"region": api.region, "tokens": api.tokens}
                    )
                await self.async_set_unique_id(api.tokens.get("uuid") or user_input["email"].lower())
                self._abort_if_unique_id_configured(
                    updates={"region": api.region, "tokens": api.tokens}
                )
                return self.async_create_entry(
                    title="Narwal Chmura",
                    data={"region": api.region, "tokens": api.tokens},
                )
        return self.async_show_form(step_id="user", data_schema=SCHEMA, errors=errors)

    async def async_step_reauth(self, entry_data):
        return await self.async_step_user()
