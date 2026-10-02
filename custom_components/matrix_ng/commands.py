"""Official-style Matrix commands, with sender ACLs and bounded regex matching."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import deque
from typing import Any

import regex
import voluptuous as vol
from homeassistant.helpers import config_validation as cv

from .api import BridgeAuthError, BridgeError, MatrixBridge
from .const import CONF_ALLOWED_SENDERS, CONF_COMMANDS, EVENT_MATRIX_COMMAND

LOGGER = logging.getLogger(__name__)
TRIGGERS = ("word", "expression", "reaction")


def validate_command(command: dict) -> dict:
    """Exactly one trigger, and Python-regexp-compatible expression syntax."""
    if sum(key in command for key in TRIGGERS) != 1:
        raise vol.Invalid("Each command needs exactly one of word, expression or reaction")
    if not command["name"].strip():
        raise vol.Invalid("Command name must not be empty")
    if "word" in command and (
        not command["word"]
        or command["word"].startswith("!")
        or any(char.isspace() for char in command["word"])
    ):
        raise vol.Invalid("word must be one word, without the ! prefix")
    if "reaction" in command and not command["reaction"]:
        raise vol.Invalid("reaction must not be empty")
    if "expression" in command:
        try:
            re.compile(command["expression"])
            regex.compile(command["expression"], regex.VERSION0)
        except (re.error, regex.error) as err:
            raise vol.Invalid("Invalid Python regular expression") from err
    return command


COMMAND_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Required("name"): cv.string,
            vol.Optional("word"): cv.string,
            vol.Optional("expression"): cv.string,
            vol.Optional("reaction"): cv.string,
            vol.Optional("rooms", default=[]): vol.All(
                cv.ensure_list, [cv.matches_regex(r"^![^\s]+$")]
            ),
        }
    ),
    validate_command,
)


def validate_settings(settings: dict) -> dict:
    if settings[CONF_COMMANDS] and not settings[CONF_ALLOWED_SENDERS]:
        raise vol.Invalid("Matrix commands require an explicit allowed_senders list")
    return settings


SETTINGS_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Optional(CONF_COMMANDS, default=[]): [COMMAND_SCHEMA],
            vol.Optional(CONF_ALLOWED_SENDERS, default=[]): vol.All(
                cv.ensure_list, [cv.matches_regex(r"^@[^:\s]+:[^\s]+$")]
            ),
        }
    ),
    validate_settings,
)


class CommandMatcher:
    """Matching mirrors the official integration: words, re.match and reactions."""

    def __init__(self, settings: dict) -> None:
        self.commands = [dict(command) for command in settings[CONF_COMMANDS]]
        self.allowed_senders = set(settings[CONF_ALLOWED_SENDERS])
        for command in self.commands:
            if "expression" in command:
                command["pattern"] = regex.compile(command["expression"], regex.VERSION0)

    def match(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        results = []
        for command in self.commands:
            if command["rooms"] and event["room_id"] not in command["rooms"]:
                continue
            args = None
            if event["kind"] == "reaction":
                if command.get("reaction") == event["reaction"]:
                    args = {"reaction": event["reaction"]}
            elif "word" in command:
                pieces = event["body"].split()
                if (
                    pieces
                    and event["body"].startswith("!")
                    and pieces[0].lstrip("!") == command["word"]
                ):
                    args = pieces[1:]
            elif "pattern" in command:
                try:
                    match = command["pattern"].match(event["body"], timeout=0.05)
                except TimeoutError:
                    LOGGER.warning("Matrix command regex timed out; message ignored")
                    continue
                if match:
                    args = match.groupdict()
            if args is not None:
                results.append(
                    {
                        "command": command["name"],
                        "args": args,
                        # The official docs also describe these legacy aliases.
                        "name": command["name"],
                        "data": args,
                        "sender": event["sender"],
                        "room": event["room_id"],
                        "event_id": event["event_id"],
                        "source_event_id": event["source_event_id"],
                        "thread_parent": event["thread_parent"],
                    }
                )
        return results


class CommandListener:
    """One cancel-on-unload long-poll subscription per configured room."""

    def __init__(
        self, hass, entry, bridge: MatrixBridge, matcher: CommandMatcher, own_user_id: str
    ):
        self.hass = hass
        self.entry = entry
        self.bridge = bridge
        self.matcher = matcher
        self.own_user_id = own_user_id
        self.cursor: str | None = None
        self._seen: set[str] = set()
        self._seen_order: deque[str] = deque()

    async def handle(self, event: dict) -> None:
        """Authorize before regex processing or event emission."""
        now_ms = int(time.time() * 1000)
        if (
            event["room_id"] != self.entry.data["room_id"]
            or event["sender"] == self.own_user_id
            or event["sender"] not in self.matcher.allowed_senders
            or now_ms - event["timestamp"] > 60_000
            or event["timestamp"] > now_ms + 30_000
            or (event["kind"] == "message" and event["encrypted"] is not True)
            or event["source_event_id"] in self._seen
        ):
            return
        self._seen.add(event["source_event_id"])
        self._seen_order.append(event["source_event_id"])
        if len(self._seen_order) > 4096:
            self._seen.remove(self._seen_order.popleft())
        for data in await self.hass.async_add_executor_job(self.matcher.match, event):
            self.hass.bus.async_fire(
                EVENT_MATRIX_COMMAND, {**data, "entry_id": self.entry.entry_id}
            )

    async def run(self) -> None:
        failed = False
        while True:
            try:
                batch = await self.bridge.events(self.entry.data["room_id"], self.cursor)
                if failed:
                    LOGGER.info("Matrix command stream reconnected")
                    failed = False
                if batch["reset"]:
                    # New stream or queue overflow: do not execute replayed history.
                    self.cursor = batch["cursor"]
                    continue
                for event in batch["events"]:
                    await self.handle(event)
                self.cursor = batch["cursor"]
            except BridgeAuthError:
                self.entry.async_start_reauth(self.hass)
                return
            except BridgeError:
                if not failed:
                    LOGGER.warning("Matrix command stream unavailable; retrying in 5 seconds")
                    failed = True
                await asyncio.sleep(5)
