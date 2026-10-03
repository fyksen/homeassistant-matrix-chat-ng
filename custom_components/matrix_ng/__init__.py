"""Encrypted Matrix notifications backed by the matrix-rust-sdk."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import BridgeError, MatrixBridge
from .commands import SETTINGS_SCHEMA, CommandListener, CommandMatcher
from .const import (
    CONF_API_TOKEN,
    CONF_BRIDGE_URL,
    CONF_COMMAND_SETTINGS,
    CONF_ROOM_ID,
    DOMAIN,
    SERVICE_SEND_MESSAGE,
)
from .coordinator import MatrixCoordinator

type MatrixConfigEntry = ConfigEntry[MatrixCoordinator]

PLATFORMS = [Platform.NOTIFY]
CONFIG_SCHEMA = vol.Schema({vol.Optional(DOMAIN): SETTINGS_SCHEMA}, extra=vol.ALLOW_EXTRA)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the action once, independent of entry reloads."""
    hass.data[DOMAIN] = CommandMatcher(SETTINGS_SCHEMA(config.get(DOMAIN, {})))

    async def send_message(call: ServiceCall) -> dict:
        entries = [
            entry
            for entry in hass.config_entries.async_entries(DOMAIN)
            if entry.state is ConfigEntryState.LOADED
        ]
        if entry_id := call.data.get("entry_id"):
            entries = [entry for entry in entries if entry.entry_id == entry_id]
        if len(entries) != 1:
            raise HomeAssistantError("Select one loaded Matrix NG config entry using entry_id")
        entry = entries[0]
        try:
            return await entry.runtime_data.bridge.send_message(
                call.data.get(CONF_ROOM_ID, entry.data[CONF_ROOM_ID]),
                call.data["message"],
                call.data.get("title"),
                call.data.get("transaction_id"),
                call.data.get("display_name"),
            )
        except BridgeError as err:
            raise HomeAssistantError(f"Encrypted Matrix send failed: {err}") from err

    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_MESSAGE,
        send_message,
        schema=vol.Schema(
            {
                vol.Required("message"): cv.string,
                vol.Optional("title"): cv.string,
                vol.Optional(CONF_ROOM_ID): cv.string,
                vol.Optional("entry_id"): cv.string,
                vol.Optional("transaction_id"): cv.string,
                vol.Optional("display_name"): cv.string,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: MatrixConfigEntry) -> bool:
    """Load the notify entity after contacting the bridge."""
    bridge = MatrixBridge(
        async_get_clientsession(hass),
        entry.data[CONF_BRIDGE_URL],
        entry.data[CONF_API_TOKEN],
    )
    coordinator = MatrixCoordinator(hass, entry, bridge)
    await coordinator.async_config_entry_first_refresh()
    if CONF_COMMAND_SETTINGS in entry.options:
        options = entry.options[CONF_COMMAND_SETTINGS]
        settings = SETTINGS_SCHEMA(
            {
                "commands": options.get("commands", []) if options.get("enabled", False) else [],
                "allowed_senders": options.get("allowed_senders", []),
            }
        )
        matcher = CommandMatcher(settings)
    else:
        matcher = hass.data[DOMAIN]
    if matcher.commands and coordinator.data.get("commands_enabled") is not True:
        raise ConfigEntryNotReady("Update the Rust bridge and set listen_for_commands to true")
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    if matcher.commands:
        listener = CommandListener(hass, entry, bridge, matcher, coordinator.data["user_id"])
        entry.async_create_background_task(hass, listener.run(), "Matrix NG command listener")
    return True


async def async_reload_entry(hass: HomeAssistant, entry: MatrixConfigEntry) -> None:
    """Apply GUI command changes and cancel/restart the entry-owned listener."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: MatrixConfigEntry) -> bool:
    """Unload entities and their coordinator listeners."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
