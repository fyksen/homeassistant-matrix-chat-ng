"""Authenticated client for the Rust Matrix bridge (no Matrix crypto in Python)."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import aiohttp
from yarl import URL

from .const import MAX_MESSAGE_BYTES


class BridgeError(Exception):
    """Safe, non-secret error reported by the bridge."""


class BridgeAuthError(BridgeError):
    """Invalid bridge bearer token."""


class BridgeConnectionError(BridgeError):
    """Bridge could not be reached."""


class BridgeProtocolError(BridgeError):
    """Bridge returned an unsupported or malformed response."""


def normalize_url(value: str) -> str:
    """Validate URL without allowing credentials or query-string tokens."""
    try:
        url = URL(value.strip())
        if (
            url.scheme not in ("http", "https")
            or not url.host
            or url.user is not None
            or url.password is not None
            or url.query_string
            or url.fragment
        ):
            raise ValueError("invalid bridge URL")
        _ = url.port
    except (TypeError, ValueError) as err:
        raise ValueError("invalid bridge URL") from err
    return str(url).rstrip("/")


class MatrixBridge:
    """One shared aiohttp session, with no automatic message retries."""

    def __init__(self, session: aiohttp.ClientSession, url: str, token: str) -> None:
        self._session = session
        self.url = normalize_url(url)
        self._token = token

    async def _request(
        self,
        method: str,
        path: str,
        data: dict[str, Any] | None = None,
        *,
        params: dict[str, str | int] | None = None,
    ) -> dict[str, Any]:
        try:
            async with self._session.request(
                method,
                f"{self.url}/v1/{path}",
                headers={"Authorization": f"Bearer {self._token}"},
                json=data,
                params=params,
                timeout=aiohttp.ClientTimeout(
                    total=70 if method == "POST" else 30 if path == "events" else 15
                ),
                allow_redirects=False,
            ) as response:
                if response.status == 401:
                    raise BridgeAuthError("invalid_auth")
                try:
                    result = await response.json()
                except (ValueError, aiohttp.ContentTypeError) as err:
                    raise BridgeProtocolError("invalid_response") from err
                if not isinstance(result, dict):
                    raise BridgeProtocolError("invalid_response")
                if response.status >= 300:
                    # Never expose arbitrary server text (which may contain secrets).
                    code = result.get("error")
                    known = {
                        "room_not_allowed",
                        "not_ready",
                        "invalid_room_id",
                        "room_not_joined",
                        "room_not_encrypted",
                        "empty_message",
                        "message_too_large",
                        "invalid_transaction_id",
                        "matrix_send_failed",
                        "send_timeout",
                        "encryption_state_unavailable",
                        "encryption_not_confirmed",
                        "commands_disabled",
                    }
                    raise BridgeError(
                        code if isinstance(code, str) and code in known else "bridge_error"
                    )
                return result
        except (aiohttp.ClientError, TimeoutError) as err:
            raise BridgeConnectionError("cannot_connect") from err

    async def status(self) -> dict[str, Any]:
        """Check compatibility and validate all security-relevant fields."""
        result = await self._request("GET", "status")
        if (
            type(result.get("protocol_version")) is not int
            or result.get("protocol_version") != 1
            or not isinstance(result.get("ready"), bool)
            or not isinstance(result.get("user_id"), str)
            or not isinstance(result.get("device_id"), str)
            or not isinstance(result.get("device_verified"), bool)
            or not isinstance(result.get("rooms"), list)
        ):
            raise BridgeProtocolError("invalid_response")
        for room in result["rooms"]:
            if (
                not isinstance(room, dict)
                or not isinstance(room.get("room_id"), str)
                or not isinstance(room.get("name"), str)
                or not isinstance(room.get("joined"), bool)
                or not isinstance(room.get("encrypted"), bool)
            ):
                raise BridgeProtocolError("invalid_response")
        return result

    async def events(self, room_id: str, cursor: str | None = None) -> dict[str, Any]:
        """Long-poll a bounded live-only stream of SDK-decoded events."""
        params: dict[str, str | int] = {"room_id": room_id, "wait": 20}
        if cursor is not None:
            params["cursor"] = cursor
        result = await self._request("GET", "events", params=params)
        if (
            not isinstance(result.get("cursor"), str)
            or not result["cursor"]
            or not isinstance(result.get("reset"), bool)
            or not isinstance(result.get("events"), list)
            or len(result["events"]) > 1024
        ):
            raise BridgeProtocolError("invalid_response")
        for event in result["events"]:
            if (
                not isinstance(event, dict)
                or event.get("kind") not in ("message", "reaction")
                or any(
                    not isinstance(event.get(key), str)
                    for key in ("room_id", "sender", "event_id", "source_event_id", "thread_parent")
                )
                or type(event.get("sequence")) is not int
                or type(event.get("timestamp")) is not int
                or not isinstance(event.get("encrypted"), bool)
                or (
                    event["kind"] == "message"
                    and (
                        event["encrypted"] is not True
                        or not isinstance(event.get("body"), str)
                        or len(event["body"].encode("utf-8")) > MAX_MESSAGE_BYTES
                    )
                )
                or (event["kind"] == "reaction" and not isinstance(event.get("reaction"), str))
            ):
                raise BridgeProtocolError("invalid_response")
        return result

    async def send_message(
        self,
        room_id: str,
        message: str,
        title: str | None = None,
        transaction_id: str | None = None,
    ) -> dict[str, Any]:
        """Send once; transaction IDs allow callers to retry safely."""
        if not message.strip():
            raise BridgeError("empty_message")
        body = f"{title}\n\n{message}" if title else message
        if len(body.encode("utf-8")) > MAX_MESSAGE_BYTES:
            raise BridgeError("message_too_large")
        result = await self._request(
            "POST",
            "send",
            {
                "room_id": room_id,
                "message": message,
                "title": title,
                "transaction_id": transaction_id or uuid4().hex,
            },
        )
        if (
            result.get("encrypted") is not True
            or not isinstance(result.get("event_id"), str)
            or not result["event_id"].startswith("$")
        ):
            raise BridgeProtocolError("invalid_response")
        return result
