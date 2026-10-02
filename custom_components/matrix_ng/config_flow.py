"""UI setup: connect to the bridge, then select an encrypted room."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.helpers.service_info.hassio import HassioServiceInfo
from yarl import URL

from .api import (
    BridgeAuthError,
    BridgeConnectionError,
    BridgeError,
    BridgeProtocolError,
    MatrixBridge,
    normalize_url,
)
from .commands import SETTINGS_SCHEMA
from .const import (
    CONF_API_TOKEN,
    CONF_BRIDGE_URL,
    CONF_COMMAND_SETTINGS,
    CONF_ROOM_ID,
    CONF_SUPERVISOR_ADDON,
    DOMAIN,
)


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

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return MatrixOptionsFlow(config_entry)

    async def async_step_hassio(self, discovery_info: HassioServiceInfo) -> ConfigFlowResult:
        """Trusted Supervisor discovery supplies the private endpoint and token."""
        try:
            host = discovery_info.config["host"]
            port = discovery_info.config["port"]
            token = discovery_info.config[CONF_API_TOKEN]
            if not isinstance(host, str) or not host or any(c in host for c in "/@?#"):
                raise ValueError("invalid discovery host")
            if type(port) is not int or not 1 <= port <= 65535 or not isinstance(token, str):
                raise ValueError("invalid discovery fields")
            self._connection, self._status = await self._connect(
                {
                    CONF_BRIDGE_URL: str(URL.build(scheme="http", host=host, port=port)),
                    CONF_API_TOKEN: token,
                }
            )
            self._connection[CONF_SUPERVISOR_ADDON] = discovery_info.slug
        except (KeyError, BridgeError, ValueError) as err:
            return self.async_abort(reason=self._error(err))
        existing = [
            entry
            for entry in self._async_current_entries()
            if entry.data.get(CONF_SUPERVISOR_ADDON) == discovery_info.slug
            and entry.data[CONF_BRIDGE_URL] == self._connection[CONF_BRIDGE_URL]
        ]
        if existing:
            # A restore can reconnect the same app. Never silently switch accounts.
            for entry in existing:
                expected = f"{self._connection[CONF_BRIDGE_URL]}|{self._status['user_id']}|{entry.data[CONF_ROOM_ID]}"
                if entry.unique_id != expected:
                    return self.async_abort(reason="wrong_account")
                self.hass.config_entries.async_update_entry(
                    entry, data={**entry.data, **self._connection}
                )
            return self.async_abort(reason="already_configured")
        self.context = {**self.context, "title_placeholders": {"name": discovery_info.name}}
        return await self.async_step_hassio_confirm()

    async def async_step_hassio_confirm(self, user_input: dict | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return await self.async_step_room()
        return self.async_show_form(step_id="hassio_confirm", data_schema=vol.Schema({}))

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
        # Adding another room from the same app must not require copying its token.
        if user_input is None and getattr(self, "hass", None) is not None:
            apps = {
                entry.data[CONF_SUPERVISOR_ADDON]: entry
                for entry in self._async_current_entries()
                if CONF_SUPERVISOR_ADDON in entry.data
            }
            if len(apps) == 1:
                existing = next(iter(apps.values()))
                try:
                    self._connection, self._status = await self._connect(existing.data)
                except (BridgeError, ValueError) as err:
                    errors["base"] = self._error(err)
                else:
                    self._connection[CONF_SUPERVISOR_ADDON] = existing.data[CONF_SUPERVISOR_ADDON]
                    return await self.async_step_room()
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


class MatrixOptionsFlow(OptionsFlow):
    """Commands can be managed without editing configuration.yaml."""

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        stored = entry.options.get(CONF_COMMAND_SETTINGS, {})
        self._settings = {
            "enabled": stored.get("enabled", False),
            "allowed_senders": list(stored.get("allowed_senders", [])),
            "commands": [dict(command) for command in stored.get("commands", [])],
        }

    async def async_step_init(self, user_input: dict | None = None) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init", menu_options=["add_command", "remove_command", "command_settings"]
        )

    async def _check_enabled(self, settings: dict) -> None:
        SETTINGS_SCHEMA(
            {"commands": settings["commands"], "allowed_senders": settings["allowed_senders"]}
        )
        if settings["enabled"] and settings["commands"]:
            bridge = MatrixBridge(
                async_get_clientsession(self.hass),
                self._entry.data[CONF_BRIDGE_URL],
                self._entry.data[CONF_API_TOKEN],
            )
            if (await bridge.status()).get("commands_enabled") is not True:
                raise BridgeError("commands_disabled")

    def _save(self, settings: dict) -> ConfigFlowResult:
        return self.async_create_entry(
            title="", data={**self._entry.options, CONF_COMMAND_SETTINGS: settings}
        )

    def _schema(self, adding: bool = False) -> vol.Schema:
        fields = {
            vol.Required(
                "enabled",
                default=self._settings["enabled"] if self._settings["commands"] else adding,
            ): BooleanSelector(),
            vol.Required(
                "allowed_senders", default=self._settings["allowed_senders"]
            ): TextSelector(TextSelectorConfig(multiple=True)),
        }
        if adding:
            fields.update(
                {
                    vol.Required("name"): TextSelector(),
                    vol.Required("trigger_type", default="word"): SelectSelector(
                        SelectSelectorConfig(
                            options=["word", "expression", "reaction"],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required("trigger"): TextSelector(),
                }
            )
        return vol.Schema(fields)

    async def async_step_add_command(self, user_input: dict | None = None) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            settings = {
                **self._settings,
                "enabled": user_input["enabled"],
                "allowed_senders": user_input["allowed_senders"],
                "commands": [
                    *self._settings["commands"],
                    {
                        "name": user_input["name"],
                        user_input["trigger_type"]: user_input["trigger"],
                        "rooms": [self._entry.data[CONF_ROOM_ID]],
                    },
                ],
            }
            try:
                await self._check_enabled(settings)
            except vol.Invalid:
                errors["base"] = "invalid_commands"
            except BridgeError as err:
                errors["base"] = (
                    "commands_disabled"
                    if str(err) == "commands_disabled"
                    else MatrixConfigFlow._error(err)
                )
            else:
                return self._save(settings)
        return self.async_show_form(
            step_id="add_command", data_schema=self._schema(adding=True), errors=errors
        )

    async def async_step_command_settings(self, user_input: dict | None = None) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            settings = {**self._settings, **user_input}
            try:
                await self._check_enabled(settings)
            except vol.Invalid:
                errors["base"] = "invalid_commands"
            except BridgeError as err:
                errors["base"] = (
                    "commands_disabled"
                    if str(err) == "commands_disabled"
                    else MatrixConfigFlow._error(err)
                )
            else:
                return self._save(settings)
        return self.async_show_form(
            step_id="command_settings", data_schema=self._schema(), errors=errors
        )

    async def async_step_remove_command(self, user_input: dict | None = None) -> ConfigFlowResult:
        if not self._settings["commands"]:
            return self.async_abort(reason="no_commands")
        if user_input is not None:
            indices = {int(index) for index in user_input["commands"]}
            return self._save(
                {
                    **self._settings,
                    "commands": [
                        command
                        for index, command in enumerate(self._settings["commands"])
                        if index not in indices
                    ],
                }
            )
        return self.async_show_form(
            step_id="remove_command",
            data_schema=vol.Schema(
                {
                    vol.Required("commands"): SelectSelector(
                        SelectSelectorConfig(
                            multiple=True,
                            options=[
                                {"value": str(index), "label": command["name"]}
                                for index, command in enumerate(self._settings["commands"])
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
        )
