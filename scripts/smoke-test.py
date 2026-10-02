#!/usr/bin/env python3
"""Check an already-configured test instance; sending requires explicit --send.

No credentials are embedded. HA token file contains JSON with an access_token.
Optional session file is the bridge's private session.json for wire verification.
"""

import argparse
import json
import urllib.parse
import urllib.request
from pathlib import Path
from uuid import uuid4


def request(url, token, data=None):
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=90) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bridge-config", type=Path, required=True)
    parser.add_argument("--bridge-url", default="http://127.0.0.1:8099")
    parser.add_argument("--ha-url", default="http://127.0.0.1:18123")
    parser.add_argument("--ha-token-file", type=Path, required=True)
    parser.add_argument("--entity-id", required=True)
    parser.add_argument("--entry-id")
    parser.add_argument("--session-file", type=Path)
    parser.add_argument("--send", action="store_true", help="Post two test messages to the room")
    args = parser.parse_args()
    config = json.loads(args.bridge_config.read_text())
    token = json.loads(args.ha_token_file.read_text())["access_token"]
    status = request(f"{args.bridge_url}/v1/status", config["api_token"])
    assert status["ready"], "Bridge is not ready"
    state = request(f"{args.ha_url}/api/states/{args.entity_id}", token)
    assert state["state"] != "unavailable", "Notify entity is unavailable"
    print("Bridge and Home Assistant notify entity are available.")
    if not args.send:
        return

    request(
        f"{args.ha_url}/api/services/notify/send_message",
        token,
        {"entity_id": args.entity_id, "message": "Matrix NG smoke test: encrypted notify entity."},
    )
    data = {
        "message": "Matrix NG smoke test: encrypted integration action.",
        "transaction_id": f"smoke-{uuid4().hex}",
    }
    if args.entry_id:
        data["entry_id"] = args.entry_id
    result = request(
        f"{args.ha_url}/api/services/matrix_ng/send_message?return_response", token, data
    )["service_response"]
    assert result["encrypted"] is True
    repeated = request(
        f"{args.ha_url}/api/services/matrix_ng/send_message?return_response", token, data
    )["service_response"]
    assert repeated["event_id"] == result["event_id"], "Idempotency check failed"
    print("Both send actions succeeded; repeated transaction returned the same event ID.")
    if args.session_file:
        session = json.loads(args.session_file.read_text())
        room_id = urllib.parse.quote(config["rooms"][0], safe="")
        event_id = urllib.parse.quote(result["event_id"], safe="")
        event = request(
            f"{session['homeserver'].rstrip('/')}/_matrix/client/v3/rooms/{room_id}/event/{event_id}",
            session["session"]["access_token"],
        )
        assert event["type"] == "m.room.encrypted"
        assert event["content"]["algorithm"] == "m.megolm.v1.aes-sha2"
        assert "body" not in event["content"]
        print("Homeserver event is Megolm-encrypted and contains no plaintext body.")
    print("Event ID:", result["event_id"])


if __name__ == "__main__":
    main()
