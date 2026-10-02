"""Bridge health and encryption-state coordinator."""

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import BridgeAuthError, BridgeError, MatrixBridge
from .const import DOMAIN

LOGGER = logging.getLogger(__name__)


class MatrixCoordinator(DataUpdateCoordinator[dict]):
    """Poll local health only; Matrix sync remains inside the Rust SDK."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, bridge: MatrixBridge) -> None:
        super().__init__(
            hass,
            LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(seconds=30),
        )
        self.bridge = bridge

    async def _async_update_data(self) -> dict:
        try:
            return await self.bridge.status()
        except BridgeAuthError as err:
            raise ConfigEntryAuthFailed("Invalid Matrix bridge API token") from err
        except BridgeError as err:
            raise UpdateFailed(f"Matrix bridge unavailable: {err}") from err
