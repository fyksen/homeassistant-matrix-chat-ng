"""Availability, service errors, coordinator auth and diagnostics."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import homeassistant  # noqa: F401
import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed
from test_api import STATUS

from custom_components.matrix_ng.api import BridgeAuthError, BridgeConnectionError, BridgeError
from custom_components.matrix_ng.coordinator import MatrixCoordinator
from custom_components.matrix_ng.diagnostics import async_get_config_entry_diagnostics
from custom_components.matrix_ng.notify import MatrixNotify


def entity(status=None, update_success=True):
    coordinator = SimpleNamespace(
        data=deepcopy(STATUS if status is None else status),
        last_update_success=update_success,
        bridge=SimpleNamespace(send_message=AsyncMock()),
    )
    entry = SimpleNamespace(
        runtime_data=coordinator, data={"room_id": "!room"}, unique_id="test-room", title="Test"
    )
    return MatrixNotify(entry)


def test_notify_availability():
    assert entity().available
    assert not entity(update_success=False).available
    for field in ["ready", "joined", "encrypted"]:
        data = deepcopy(STATUS)
        if field == "ready":
            data[field] = False
        else:
            data["rooms"][0][field] = False
        assert not entity(data).available


async def test_notify_sends_title_and_message():
    notify = entity()
    await notify.async_send_message("Hello", "Title")
    notify.coordinator.bridge.send_message.assert_awaited_once_with("!room", "Hello", "Title")


async def test_notify_reports_send_error():
    notify = entity()
    notify.coordinator.bridge.send_message.side_effect = BridgeError("room_not_encrypted")
    with pytest.raises(HomeAssistantError, match="room_not_encrypted"):
        await notify.async_send_message("Hello")


@pytest.mark.parametrize(
    "error,expected",
    [
        (BridgeAuthError("invalid_auth"), ConfigEntryAuthFailed),
        (BridgeConnectionError("cannot_connect"), UpdateFailed),
    ],
)
async def test_coordinator_errors(error, expected):
    coordinator = SimpleNamespace(bridge=SimpleNamespace(status=AsyncMock(side_effect=error)))
    with pytest.raises(expected):
        await MatrixCoordinator._async_update_data(coordinator)


async def test_diagnostics_redacts_identifiers_without_credentials():
    entry = SimpleNamespace(runtime_data=SimpleNamespace(data=deepcopy(STATUS)))
    result = await async_get_config_entry_diagnostics(None, entry)
    assert result["status"]["user_id"] == "**REDACTED**"
    assert result["status"]["device_id"] == "**REDACTED**"
    assert result["status"]["rooms"][0]["room_id"] == "**REDACTED**"
    assert "api_token" not in str(result)
    assert result["status"]["rooms"][0]["encrypted"] is True
