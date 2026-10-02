"""Diagnostics with credentials and identifiers redacted."""

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import MatrixConfigEntry


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: MatrixConfigEntry) -> dict:
    return async_redact_data(
        {"status": entry.runtime_data.data},
        {"user_id", "device_id", "room_id", "name"},
    )
