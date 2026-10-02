"""Test connection errors and the encrypted-room selector."""

from copy import deepcopy
from unittest.mock import AsyncMock, patch

import homeassistant  # noqa: F401
import pytest
from test_api import STATUS

from custom_components.matrix_ng.api import BridgeAuthError, BridgeConnectionError
from custom_components.matrix_ng.config_flow import MatrixConfigFlow
from custom_components.matrix_ng.const import CONF_API_TOKEN, CONF_BRIDGE_URL


@pytest.mark.parametrize(
    "exception,error",
    [
        (BridgeAuthError("invalid_auth"), "invalid_auth"),
        (BridgeConnectionError("cannot_connect"), "cannot_connect"),
        (ValueError("invalid URL"), "invalid_url"),
    ],
)
async def test_connection_errors(exception, error):
    flow = MatrixConfigFlow()
    with patch.object(flow, "_connect", AsyncMock(side_effect=exception)):
        result = await flow.async_step_user(
            {CONF_BRIDGE_URL: "http://localhost", CONF_API_TOKEN: "token"}
        )
    assert result["type"] == "form"
    assert result["errors"] == {"base": error}


async def test_only_encrypted_joined_rooms_are_offered():
    status = deepcopy(STATUS)
    status["rooms"].extend(
        [
            {"room_id": "!plain", "name": "Plaintext", "joined": True, "encrypted": False},
            {"room_id": "!invite", "name": "Invited", "joined": False, "encrypted": True},
        ]
    )
    flow = MatrixConfigFlow()
    with patch.object(flow, "_connect", AsyncMock(return_value=({}, status))):
        result = await flow.async_step_user({})
    assert result["step_id"] == "room"
    selector = list(result["data_schema"].schema.values())[0]
    assert selector.config["options"] == [{"value": "!room", "label": "Test"}]


async def test_no_encrypted_rooms_aborts():
    status = deepcopy(STATUS)
    status["rooms"] = []
    flow = MatrixConfigFlow()
    with patch.object(flow, "_connect", AsyncMock(return_value=({}, status))):
        result = await flow.async_step_user({})
    assert result["type"] == "abort"
    assert result["reason"] == "no_encrypted_rooms"
