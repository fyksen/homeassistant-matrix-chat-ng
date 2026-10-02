"""Test the bridge protocol without logging in to a real Matrix account."""

from copy import deepcopy

import aiohttp
import homeassistant  # noqa: F401 -- install HA's voluptuous compatibility first
import pytest
from aiohttp import web

from custom_components.matrix_ng.api import (
    BridgeAuthError,
    BridgeError,
    BridgeProtocolError,
    MatrixBridge,
    normalize_url,
)

STATUS = {
    "protocol_version": 1,
    "ready": True,
    "user_id": "@bot:example.org",
    "device_id": "BOTDEVICE",
    "device_verified": True,
    "rooms": [{"room_id": "!room", "name": "Test", "joined": True, "encrypted": True}],
}


@pytest.mark.parametrize(
    "value",
    [
        "ftp://host",
        "http://user:password@host",
        "http://host?token=secret",
        "http://host#fragment",
        "host:8099",
        "http://host:bad",
        "https:///",
    ],
)
def test_rejects_unsafe_urls(value):
    with pytest.raises(ValueError):
        normalize_url(value)


def test_normalizes_url():
    assert normalize_url(" http://localhost:8099/ ") == "http://localhost:8099"
    assert normalize_url("https://host/matrix/") == "https://host/matrix"


async def make_bridge(aiohttp_server, handler):
    app = web.Application()
    app.router.add_route("*", "/v1/{method}", handler)
    server = await aiohttp_server(app)
    return str(server.make_url(""))


async def test_status_and_bearer_auth(aiohttp_server):
    async def handler(request):
        assert request.headers["Authorization"] == "Bearer private-token"
        return web.json_response(STATUS)

    url = await make_bridge(aiohttp_server, handler)
    async with aiohttp.ClientSession() as session:
        bridge = MatrixBridge(session, url, "private-token")
        assert await bridge.status() == STATUS


@pytest.mark.parametrize(
    "field,value",
    [
        ("protocol_version", 2),
        ("protocol_version", True),
        ("ready", "true"),
        ("rooms", {}),
        ("device_id", None),
        ("device_verified", "true"),
        ("user_id", None),
    ],
)
async def test_rejects_malformed_status(aiohttp_server, field, value):
    status = deepcopy(STATUS)
    status[field] = value

    async def handler(request):
        return web.json_response(status)

    url = await make_bridge(aiohttp_server, handler)
    async with aiohttp.ClientSession() as session:
        with pytest.raises(BridgeProtocolError):
            await MatrixBridge(session, url, "token").status()


async def test_rejects_string_encryption_flag(aiohttp_server):
    status = deepcopy(STATUS)
    status["rooms"][0]["encrypted"] = "false"

    async def handler(request):
        return web.json_response(status)

    url = await make_bridge(aiohttp_server, handler)
    async with aiohttp.ClientSession() as session:
        with pytest.raises(BridgeProtocolError):
            await MatrixBridge(session, url, "token").status()


async def test_send_and_explicit_transaction_id(aiohttp_server):
    async def handler(request):
        assert request.method == "POST"
        assert await request.json() == {
            "room_id": "!room",
            "message": "Hei, verden!",
            "title": "Test",
            "transaction_id": "test-123",
        }
        return web.json_response({"event_id": "$event", "encrypted": True})

    url = await make_bridge(aiohttp_server, handler)
    async with aiohttp.ClientSession() as session:
        result = await MatrixBridge(session, url, "token").send_message(
            "!room", "Hei, verden!", "Test", "test-123"
        )
        assert result == {"event_id": "$event", "encrypted": True}


@pytest.mark.parametrize(
    "payload",
    [
        {"event_id": "$event", "encrypted": False},
        {"event_id": "$event", "encrypted": "true"},
        {"event_id": "not-an-event", "encrypted": True},
        {"encrypted": True},
    ],
)
async def test_send_requires_encryption_confirmation(aiohttp_server, payload):
    async def handler(request):
        return web.json_response(payload)

    url = await make_bridge(aiohttp_server, handler)
    async with aiohttp.ClientSession() as session:
        with pytest.raises(BridgeProtocolError):
            await MatrixBridge(session, url, "token").send_message("!room", "hello")


@pytest.mark.parametrize(
    "message,error", [(" ", "empty_message"), ("ø" * 8001, "message_too_large")]
)
async def test_validates_message_without_network(message, error):
    async with aiohttp.ClientSession() as session:
        with pytest.raises(BridgeError, match=error):
            await MatrixBridge(session, "http://127.0.0.1:1", "token").send_message(
                "!room", message
            )


async def test_auth_failure(aiohttp_server):
    async def handler(request):
        return web.Response(status=401)

    url = await make_bridge(aiohttp_server, handler)
    async with aiohttp.ClientSession() as session:
        with pytest.raises(BridgeAuthError):
            await MatrixBridge(session, url, "token").status()


async def test_does_not_repeat_failed_sends_or_expose_server_text(aiohttp_server):
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        return web.json_response({"error": "password=secret"}, status=502)

    url = await make_bridge(aiohttp_server, handler)
    async with aiohttp.ClientSession() as session:
        with pytest.raises(BridgeError, match="^bridge_error$"):
            await MatrixBridge(session, url, "token").send_message("!room", "hello")
    assert calls == 1


async def test_does_not_follow_redirects(aiohttp_server):
    async def handler(request):
        raise web.HTTPFound("http://127.0.0.1:1/stolen-token")

    url = await make_bridge(aiohttp_server, handler)
    async with aiohttp.ClientSession() as session:
        with pytest.raises(BridgeProtocolError):
            await MatrixBridge(session, url, "token").status()


async def test_event_stream_query_and_validation(aiohttp_server):
    from test_commands import event

    payload = {"cursor": "epoch:2", "reset": False, "events": [event()]}

    async def handler(request):
        assert request.query == {"room_id": "!room", "wait": "20", "cursor": "epoch:1"}
        return web.json_response(payload)

    url = await make_bridge(aiohttp_server, handler)
    async with aiohttp.ClientSession() as session:
        bridge = MatrixBridge(session, url, "token")
        assert await bridge.events("!room", "epoch:1") == payload
        payload["events"][0]["encrypted"] = False
        with pytest.raises(BridgeProtocolError):
            await bridge.events("!room", "epoch:1")
