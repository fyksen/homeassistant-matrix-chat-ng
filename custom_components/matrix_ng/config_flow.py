"""UI setup: connect to the bridge, then select an encrypted room."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    BridgeAuthError,
    BridgeConnectionError,
    BridgeError,
    BridgeProtocolError,
    MatrixBridge,
    normalize_url,
)
from .const import CONF_API_TOKEN, CONF_BRIDGE_URL, CONF_ROOM_ID, DOMAIN


def connection_schema(defaults: dict | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_BRIDGE_URL, default=defaults.get(CONF_BRIDGE_URL, "http://127.0.0.1:8099")
            ): TextSelector(),
            vol.Required(CONF_API_TOKEN): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD)
            ),
        }
    )


class MatrixConfigFlow(ConfigFlow, domain=DOMAIN):
    """No login credentials or recovery key are stored in Home Assistant."""

    VERSION = 1

    async def _connect(self, user_input: dict[str, Any]) -> tuple[dict, dict]:
        data = {
            CONF_BRIDGE_URL: normalize_url(user_input[CONF_BRIDGE_URL]),
            CONF_API_TOKEN: user_input[CONF_API_TOKEN],
        }
        bridge = MatrixBridge(
            async_get_clientsession(self.hass), data[CONF_BRIDGE_URL], data[CONF_API_TOKEN]
        )
        status = await bridge.status()
        if not status["ready"]:
            raise BridgeError("not_ready")
        return data, status

    @staticmethod
    def _error(err: Exception) -> str:
        if isinstance(err, BridgeAuthError):
            return "invalid_auth"
        if isinstance(err, BridgeConnectionError):
            return "cannot_connect"
        if isinstance(err, BridgeProtocolError):
            return "invalid_response"
        if isinstance(err, ValueError):
            return "invalid_url"
        return "not_ready"

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            try:
                self._connection, self._status = await self._connect(user_input)
            except (BridgeError, ValueError) as err:
                errors["base"] = self._error(err)
            else:
                return await self.async_step_room()
        return self.async_show_form(
            step_id="user", data_schema=connection_schema(user_input), errors=errors
        )

    async def async_step_room(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        rooms = {
            room["room_id"]: room["name"]
            for room in self._status["rooms"]
            if room["joined"] and room["encrypted"]
        }
        if not rooms:
            return self.async_abort(reason="no_encrypted_rooms")
        if user_input is not None and user_input[CONF_ROOM_ID] in rooms:
            room_id = user_input[CONF_ROOM_ID]
            unique = f"{self._connection[CONF_BRIDGE_URL]}|{self._status['user_id']}|{room_id}"
            await self.async_set_unique_id(unique)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=rooms[room_id],
                data={**self._connection, CONF_ROOM_ID: room_id},
            )
        return self.async_show_form(
            step_id="room",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ROOM_ID): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                {"value": room_id, "label": name} for room_id, name in rooms.items()
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors = {}
        if user_input is not None:
            try:
                data, status = await self._connect(user_input)
                unique = f"{data[CONF_BRIDGE_URL]}|{status['user_id']}|{entry.data[CONF_ROOM_ID]}"
                if unique != entry.unique_id:
                    return self.async_abort(reason="wrong_account")
            except (BridgeError, ValueError) as err:
                errors["base"] = self._error(err)
            else:
                return self.async_update_reload_and_abort(entry, data_updates=data)
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=connection_schema(entry.data),
            errors=errors,
        )
