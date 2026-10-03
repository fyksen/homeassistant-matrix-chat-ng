"""Regression tests for entry setup, listener lifetime and registered actions."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import homeassistant  # noqa: F401
import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError

import custom_components.matrix_ng as integration
from custom_components.matrix_ng.api import BridgeError
from custom_components.matrix_ng.commands import SETTINGS_SCHEMA, CommandMatcher


def config_entry(bridge=None):
    return SimpleNamespace(
        entry_id="entry-one",
        state=ConfigEntryState.LOADED,
        options={},
        async_on_unload=Mock(),
        add_update_listener=Mock(),
        data={"bridge_url": "http://localhost:8099", "api_token": "test-token", "room_id": "!room"},
        runtime_data=SimpleNamespace(bridge=bridge),
        async_create_background_task=Mock(
            side_effect=lambda hass, coroutine, name: coroutine.close()
        ),
    )


def home_assistant(entries=None, commands=None):
    settings = SETTINGS_SCHEMA(
        {
            "commands": commands or [],
            "allowed_senders": ["@alice:example.org"],
        }
    )
    return SimpleNamespace(
        data={"matrix_ng": CommandMatcher(settings)},
        services=SimpleNamespace(async_register=Mock()),
        config_entries=SimpleNamespace(
            async_entries=Mock(return_value=entries or []),
            async_forward_entry_setups=AsyncMock(),
            async_unload_platforms=AsyncMock(return_value=True),
        ),
    )


@pytest.mark.parametrize("commands_enabled", [None, False])
async def test_notify_only_entries_work_with_old_or_commands_disabled_bridge(commands_enabled):
    hass = home_assistant()
    entry = config_entry()
    coordinator = SimpleNamespace(
        async_config_entry_first_refresh=AsyncMock(),
        data={"commands_enabled": commands_enabled, "user_id": "@bot:example.org"},
    )
    with (
        patch.object(integration, "async_get_clientsession"),
        patch.object(integration, "MatrixBridge"),
        patch.object(integration, "MatrixCoordinator", return_value=coordinator),
    ):
        assert await integration.async_setup_entry(hass, entry)
    assert entry.runtime_data is coordinator
    hass.config_entries.async_forward_entry_setups.assert_awaited_once()
    entry.async_create_background_task.assert_not_called()


@pytest.mark.parametrize("commands_enabled", [None, False])
async def test_command_entries_require_bridge_opt_in(commands_enabled):
    hass = home_assistant(commands=[{"name": "status", "word": "status"}])
    coordinator = SimpleNamespace(
        async_config_entry_first_refresh=AsyncMock(),
        data={"commands_enabled": commands_enabled, "user_id": "@bot:example.org"},
    )
    with (
        patch.object(integration, "async_get_clientsession"),
        patch.object(integration, "MatrixBridge"),
        patch.object(integration, "MatrixCoordinator", return_value=coordinator),
    ):
        with pytest.raises(ConfigEntryNotReady, match="listen_for_commands"):
            await integration.async_setup_entry(hass, config_entry())
    hass.config_entries.async_forward_entry_setups.assert_not_awaited()


async def test_command_listener_is_owned_by_the_config_entry():
    hass = home_assistant(commands=[{"name": "status", "word": "status"}])
    entry = config_entry()
    coordinator = SimpleNamespace(
        async_config_entry_first_refresh=AsyncMock(),
        data={"commands_enabled": True, "user_id": "@bot:example.org"},
    )
    with (
        patch.object(integration, "async_get_clientsession"),
        patch.object(integration, "MatrixBridge"),
        patch.object(integration, "MatrixCoordinator", return_value=coordinator),
    ):
        assert await integration.async_setup_entry(hass, entry)
    entry.async_create_background_task.assert_called_once()
    assert entry.async_create_background_task.call_args.args[2] == "Matrix NG command listener"
    assert await integration.async_unload_entry(hass, entry)
    hass.config_entries.async_unload_platforms.assert_awaited_once_with(
        entry, integration.PLATFORMS
    )


async def registered_action(hass):
    assert await integration.async_setup(hass, {})
    return hass.services.async_register.call_args.args[2]


async def test_send_action_returns_event_id_and_uses_default_room():
    bridge = SimpleNamespace(
        send_message=AsyncMock(return_value={"event_id": "$sent", "encrypted": True})
    )
    entry = config_entry(bridge)
    hass = home_assistant([entry])
    send = await registered_action(hass)
    assert await send(SimpleNamespace(data={"message": "Hello"})) == {
        "event_id": "$sent",
        "encrypted": True,
    }
    bridge.send_message.assert_awaited_once_with("!room", "Hello", None, None, None)


async def test_send_action_requires_explicit_entry_when_multiple_are_loaded():
    first = config_entry(SimpleNamespace(send_message=AsyncMock()))
    second = config_entry(SimpleNamespace(send_message=AsyncMock()))
    second.entry_id = "entry-two"
    hass = home_assistant([first, second])
    send = await registered_action(hass)
    with pytest.raises(HomeAssistantError, match="entry_id"):
        await send(SimpleNamespace(data={"message": "Hello"}))
    await send(
        SimpleNamespace(
            data={
                "entry_id": "entry-two",
                "message": "Hello",
                "room_id": "!other",
                "transaction_id": "stable-id",
            }
        )
    )
    first.runtime_data.bridge.send_message.assert_not_awaited()
    second.runtime_data.bridge.send_message.assert_awaited_once_with(
        "!other", "Hello", None, "stable-id", None
    )


async def test_send_action_forwards_display_name():
    bridge = SimpleNamespace(
        send_message=AsyncMock(return_value={"event_id": "$sent", "encrypted": True})
    )
    entry = config_entry(bridge)
    hass = home_assistant([entry])
    send = await registered_action(hass)
    await send(SimpleNamespace(data={"message": "Hello", "display_name": "Kitchen Bot"}))
    bridge.send_message.assert_awaited_once_with("!room", "Hello", None, None, "Kitchen Bot")


async def test_send_action_preserves_safe_bridge_errors():
    bridge = SimpleNamespace(send_message=AsyncMock(side_effect=BridgeError("room_not_encrypted")))
    hass = home_assistant([config_entry(bridge)])
    send = await registered_action(hass)
    with pytest.raises(HomeAssistantError, match="room_not_encrypted"):
        await send(SimpleNamespace(data={"message": "Hello"}))
