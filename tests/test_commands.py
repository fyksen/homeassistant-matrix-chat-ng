"""Command parsing, sender restrictions, replay protection and event-bus delivery."""

import asyncio
import time
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import homeassistant  # noqa: F401
import pytest
import voluptuous as vol
from homeassistant.core import HomeAssistant

from custom_components.matrix_ng.api import BridgeAuthError, BridgeConnectionError
from custom_components.matrix_ng.commands import SETTINGS_SCHEMA, CommandListener, CommandMatcher


def matcher(commands=None):
    return CommandMatcher(
        SETTINGS_SCHEMA(
            {
                "allowed_senders": ["@alice:example.org"],
                "commands": commands
                if commands is not None
                else [{"name": "status", "word": "status"}],
            }
        )
    )


def event(**changes):
    return {
        "sequence": 1,
        "kind": "message",
        "room_id": "!room",
        "sender": "@alice:example.org",
        "event_id": "$message",
        "source_event_id": "$message",
        "thread_parent": "$root",
        "timestamp": int(time.time() * 1000),
        "encrypted": True,
        "body": "!status kitchen on",
        **changes,
    }


def listener(command_matcher=None):
    hass = SimpleNamespace(
        bus=SimpleNamespace(async_fire=Mock()),
        async_add_executor_job=AsyncMock(side_effect=lambda func, value: func(value)),
    )
    entry = SimpleNamespace(data={"room_id": "!room"}, entry_id="entry", async_start_reauth=Mock())
    return CommandListener(
        hass,
        entry,
        SimpleNamespace(events=AsyncMock()),
        command_matcher or matcher(),
        "@bot:example.org",
    )


@pytest.mark.parametrize(
    "command",
    [
        {"name": "bad"},
        {"name": "bad", "word": "one", "reaction": "👍"},
        {"name": "bad", "word": "!status"},
        {"name": "bad", "word": "two words"},
        {"name": "", "word": "status"},
        {"name": "bad", "expression": "["},
        {"name": "bad", "reaction": ""},
        {"name": "bad", "word": "status", "rooms": ["#alias:example.org"]},
    ],
)
def test_invalid_command_config(command):
    with pytest.raises(vol.Invalid):
        matcher([command])


def test_sender_allowlist_is_required_and_no_wildcards():
    for settings in [
        {"commands": [{"name": "status", "word": "status"}]},
        {"commands": [{"name": "status", "word": "status"}], "allowed_senders": ["*"]},
    ]:
        with pytest.raises(vol.Invalid):
            SETTINGS_SCHEMA(settings)


def test_word_arguments_and_official_payload():
    result = matcher().match(event())[0]
    assert result == {
        "command": "status",
        "name": "status",
        "args": ["kitchen", "on"],
        "data": ["kitchen", "on"],
        "sender": "@alice:example.org",
        "room": "!room",
        "event_id": "$message",
        "source_event_id": "$message",
        "thread_parent": "$root",
    }


@pytest.mark.parametrize(
    "body", ["status", "!status_extra", "!STATUS", "", "hello !status", " !status"]
)
def test_words_match_exact_command_only(body):
    assert matcher().match(event(body=body)) == []


def test_regex_named_groups_use_match_not_search():
    command = matcher([{"name": "intro", "expression": r"My name is (?P<name>.*)"}])
    assert command.match(event(body="My name is Alice"))[0]["args"] == {"name": "Alice"}
    assert command.match(event(body="Hello. My name is Alice")) == []


def test_reaction_args_and_target_event_id():
    command = matcher([{"name": "thumbsup", "reaction": "👍"}])
    result = command.match(
        event(
            kind="reaction",
            reaction="👍",
            encrypted=False,
            event_id="$target",
            source_event_id="$reaction",
        )
    )[0]
    assert result["args"] == {"reaction": "👍"}
    assert result["event_id"] == "$target"
    assert result["source_event_id"] == "$reaction"
    assert command.match(event(kind="reaction", reaction="👎")) == []


def test_room_restriction_and_multiple_matches():
    command = matcher(
        [
            {"name": "status", "word": "status", "rooms": ["!room"]},
            {"name": "other", "word": "status", "rooms": ["!other"]},
            {"name": "pattern", "expression": r"!status (?P<args>.*)"},
        ]
    )
    assert [match["command"] for match in command.match(event())] == ["status", "pattern"]


def test_regex_timeout_is_bounded_and_safe():
    command = matcher([{"name": "slow", "expression": ".*"}])
    pattern = Mock()
    pattern.match.side_effect = TimeoutError
    command.commands[0]["pattern"] = pattern
    assert command.match(event()) == []
    pattern.match.assert_called_once_with("!status kitchen on", timeout=0.05)


@pytest.mark.parametrize(
    "changes",
    [
        {"sender": "@mallory:example.org"},
        {"sender": "@bot:example.org"},
        {"room_id": "!other"},
        {"encrypted": False},
        {"timestamp": 1},
        {"timestamp": int(time.time() * 1000) + 120_000},
    ],
)
async def test_unauthorized_stale_and_plaintext_events_are_ignored(changes):
    command_listener = listener()
    await command_listener.handle(event(**changes))
    command_listener.hass.bus.async_fire.assert_not_called()
    command_listener.hass.async_add_executor_job.assert_not_awaited()


async def test_duplicate_event_is_fired_only_once():
    command_listener = listener()
    incoming = event()
    await command_listener.handle(incoming)
    await command_listener.handle(deepcopy(incoming))
    command_listener.hass.bus.async_fire.assert_called_once()
    event_type, data = command_listener.hass.bus.async_fire.call_args.args
    assert event_type == "matrix_command"
    assert data["entry_id"] == "entry"


async def test_new_stream_skips_history_and_advances_cursor():
    command_listener = listener()
    command_listener.bridge.events.side_effect = [
        {"cursor": "epoch:1", "reset": True, "events": [event()]},
        {"cursor": "epoch:2", "reset": False, "events": [event(source_event_id="$new")]},
        asyncio.CancelledError(),
    ]
    with pytest.raises(asyncio.CancelledError):
        await command_listener.run()
    command_listener.hass.bus.async_fire.assert_called_once()
    assert command_listener.cursor == "epoch:2"


async def test_auth_error_starts_reauthentication_and_stops_listener():
    command_listener = listener()
    command_listener.bridge.events.side_effect = BridgeAuthError("invalid_auth")
    await command_listener.run()
    command_listener.entry.async_start_reauth.assert_called_once_with(command_listener.hass)


async def test_reconnect_retains_cursor():
    command_listener = listener()
    command_listener.cursor = "epoch:3"
    command_listener.bridge.events.side_effect = [
        BridgeConnectionError("cannot_connect"),
        asyncio.CancelledError(),
    ]
    with patch("custom_components.matrix_ng.commands.asyncio.sleep", AsyncMock()) as sleep:
        with pytest.raises(asyncio.CancelledError):
            await command_listener.run()
    sleep.assert_awaited_once_with(5)
    assert command_listener.cursor == "epoch:3"


async def test_real_home_assistant_event_bus(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    captured = asyncio.get_running_loop().create_future()
    unsubscribe = hass.bus.async_listen(
        "matrix_command", lambda received: captured.set_result(received)
    )
    entry = SimpleNamespace(data={"room_id": "!room"}, entry_id="entry")
    command_listener = CommandListener(hass, entry, None, matcher(), "@bot:example.org")
    try:
        await command_listener.handle(event())
        received = await asyncio.wait_for(captured, timeout=5)
        assert received.data["command"] == "status"
        assert received.data["args"] == ["kitchen", "on"]
        assert received.data["thread_parent"] == "$root"
    finally:
        unsubscribe()
        await hass.async_stop()
