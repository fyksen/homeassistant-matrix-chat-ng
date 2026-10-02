"""Supervisor token provisioning, discovery and UI command setup without real secrets."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import homeassistant  # noqa: F401
import pytest
from homeassistant.helpers.service_info.hassio import HassioServiceInfo
from test_api import STATUS

from custom_components.matrix_ng.config_flow import MatrixConfigFlow, MatrixOptionsFlow

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "app_startup", ROOT / "apps/matrix_ng_bridge/startup.py"
)
startup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(startup)


def test_api_token_survives_restart_and_password_is_only_in_private_runtime_config(tmp_path):
    options = tmp_path / "options.json"
    options.write_text(
        json.dumps({"user_id": "@bot:example.org", "password": "dummy-password", "rooms": []})
    )
    data = tmp_path / "data"
    runtime = tmp_path / "run"
    config, token = startup.prepare(options, data, runtime)
    assert startup.prepare(options, data, runtime)[1] == token
    assert len(token) >= 32
    assert config["auto_rooms"] is True
    assert config["storage_path"] == str(data / "sdk")
    assert not (data / "config.json").exists()
    assert (data / "api-token").stat().st_mode & 0o777 == 0o600
    assert (runtime / "config.json").stat().st_mode & 0o777 == 0o600


def test_explicit_rooms_do_not_enable_automatic_allowlisting(tmp_path):
    options = tmp_path / "options.json"
    options.write_text(json.dumps({"user_id": "@bot:example.org", "rooms": ["!room"]}))
    config, _ = startup.prepare(options, tmp_path / "data", tmp_path / "run")
    assert config["auto_rooms"] is False
    assert config["rooms"] == ["!room"]


def test_discovery_payload_contains_private_connection_but_no_matrix_credentials():
    with patch.object(
        startup,
        "request_json",
        side_effect=[
            {"data": {"hostname": "abc-matrix-ng-bridge"}},
            {"result": "ok", "data": {"uuid": "discovery-id"}},
        ],
    ) as request:
        assert (
            startup.register_discovery("http://supervisor", "supervisor-secret", "bridge-secret")
            == "discovery-id"
        )
    assert request.call_args_list[1].args == (
        "http://supervisor/discovery",
        "supervisor-secret",
        {
            "service": "matrix_ng",
            "config": {"host": "abc-matrix-ng-bridge", "port": 8099, "api_token": "bridge-secret"},
        },
    )


async def test_supervisor_discovery_skips_manual_token_entry():
    flow = MatrixConfigFlow()
    flow.hass = SimpleNamespace()
    info = HassioServiceInfo(
        config={"host": "abc-matrix-ng-bridge", "port": 8099, "api_token": "secret"},
        name="Matrix NG",
        slug="abc_matrix_ng_bridge",
        uuid="discovery-id",
    )
    with (
        patch.object(
            flow,
            "_connect",
            AsyncMock(
                return_value=(
                    {"bridge_url": "http://abc-matrix-ng-bridge:8099", "api_token": "secret"},
                    STATUS,
                )
            ),
        ),
        patch.object(flow, "_async_current_entries", return_value=[]),
    ):
        result = await flow.async_step_hassio(info)
    assert result["step_id"] == "hassio_confirm"
    assert flow._connection["supervisor_addon"] == "abc_matrix_ng_bridge"
    room = await flow.async_step_hassio_confirm({})
    assert room["step_id"] == "room"


@pytest.mark.parametrize(
    "host,port", [("evil/path", 8099), ("user@host", 8099), ("host", "8099"), ("host", 0)]
)
async def test_invalid_discovery_is_rejected(host, port):
    flow = MatrixConfigFlow()
    info = HassioServiceInfo(
        config={"host": host, "port": port, "api_token": "secret"},
        name="App",
        slug="app",
        uuid="id",
    )
    result = await flow.async_step_hassio(info)
    assert result["type"] == "abort"


def entry(options=None):
    return SimpleNamespace(
        data={"room_id": "!room", "bridge_url": "http://bridge:8099", "api_token": "secret"},
        options=options or {},
    )


async def test_add_command_via_ui_saves_options_and_sender_allowlist():
    flow = MatrixOptionsFlow(entry())
    with patch.object(flow, "_check_enabled", AsyncMock()):
        result = await flow.async_step_add_command(
            {
                "enabled": True,
                "allowed_senders": ["@alice:example.org"],
                "name": "status",
                "trigger_type": "word",
                "trigger": "status",
            }
        )
    assert result["type"] == "create_entry"
    assert result["data"]["command_settings"] == {
        "enabled": True,
        "allowed_senders": ["@alice:example.org"],
        "commands": [{"name": "status", "word": "status", "rooms": ["!room"]}],
    }


async def test_ui_command_requires_authorized_sender():
    flow = MatrixOptionsFlow(entry())
    result = await flow.async_step_add_command(
        {
            "enabled": True,
            "allowed_senders": [],
            "name": "status",
            "trigger_type": "word",
            "trigger": "status",
        }
    )
    assert result["errors"] == {"base": "invalid_commands"}


async def test_remove_command_keeps_other_settings():
    flow = MatrixOptionsFlow(
        entry(
            {
                "other": "unchanged",
                "command_settings": {
                    "enabled": True,
                    "allowed_senders": ["@alice:example.org"],
                    "commands": [{"name": "one", "word": "one"}, {"name": "two", "reaction": "👍"}],
                },
            }
        )
    )
    result = await flow.async_step_remove_command({"commands": ["0"]})
    assert result["data"]["other"] == "unchanged"
    assert [cmd["name"] for cmd in result["data"]["command_settings"]["commands"]] == ["two"]
