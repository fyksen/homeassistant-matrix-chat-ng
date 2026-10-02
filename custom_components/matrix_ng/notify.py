"""Modern Home Assistant notify entities for encrypted Matrix rooms."""

from homeassistant.components.notify import NotifyEntity, NotifyEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import MatrixConfigEntry
from .api import BridgeError
from .const import CONF_ROOM_ID, DOMAIN
from .coordinator import MatrixCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MatrixConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create one notification target per config entry."""
    async_add_entities([MatrixNotify(entry)])


class MatrixNotify(CoordinatorEntity[MatrixCoordinator], NotifyEntity):
    """Send plaintext to the local bridge, which encrypts before Matrix upload."""

    _attr_has_entity_name = True
    _attr_name = "Messages"
    _attr_icon = "mdi:message-lock"
    _attr_supported_features = NotifyEntityFeature.TITLE

    def __init__(self, entry: MatrixConfigEntry) -> None:
        super().__init__(entry.runtime_data)
        self._room_id = entry.data[CONF_ROOM_ID]
        self._attr_unique_id = entry.unique_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id)},
            name=entry.title,
            manufacturer="Matrix.org",
            model="matrix-rust-sdk encrypted room",
        )

    @property
    def available(self) -> bool:
        """Hide the send target if sync is stale or encryption is unavailable."""
        data = self.coordinator.data
        return bool(
            super().available
            and data
            and data["ready"]
            and any(
                room["room_id"] == self._room_id and room["joined"] and room["encrypted"]
                for room in data["rooms"]
            )
        )

    async def async_send_message(self, message: str, title: str | None = None) -> None:
        """Send a notification to the configured encrypted room."""
        try:
            await self.coordinator.bridge.send_message(self._room_id, message, title)
        except BridgeError as err:
            raise HomeAssistantError(f"Encrypted Matrix send failed: {err}") from err
