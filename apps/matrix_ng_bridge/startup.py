"""Supervisor bootstrap. Standard-library only; credentials never go to logs."""

from __future__ import annotations

import json
import os
import pwd
import secrets
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def private_write(path: Path, data: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as file:
        os.fchmod(file.fileno(), 0o600)
        file.write(data)


def prepare(options_path: Path, data_path: Path, runtime_path: Path) -> tuple[dict, str]:
    options = json.loads(options_path.read_text())
    user_id = options.get("user_id", "").strip()
    if not user_id:
        raise ValueError("Configure a full Matrix user ID before starting the app")
    data_path.mkdir(mode=0o700, parents=True, exist_ok=True)
    data_path.chmod(0o700)
    token_path = data_path / "api-token"
    try:
        descriptor = os.open(token_path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor) as file:
            token = file.read().strip()
    except FileNotFoundError:
        token = secrets.token_urlsafe(48)
        descriptor = os.open(
            token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(descriptor, "w") as file:
            file.write(token)
    if len(token) < 32 or not token.isascii() or any(char.isspace() for char in token):
        raise ValueError("Invalid stored API token; restore the app data backup")
    rooms = options.get("rooms", [])
    if not isinstance(rooms, list) or any(not isinstance(room, str) for room in rooms):
        raise ValueError("rooms must be a list of internal room IDs")
    config = {
        "user_id": user_id,
        "homeserver": options.get("homeserver", "").strip() or None,
        "password": options.get("password") or None,
        "recovery_key": options.get("recovery_key") or None,
        "api_token": token,
        "rooms": rooms,
        "auto_rooms": not bool(rooms),
        "listen_for_commands": options.get("listen_for_commands", True),
        "storage_path": str(data_path / "sdk"),
        "listen": "0.0.0.0:8099",
    }
    runtime_path.mkdir(mode=0o700, parents=True, exist_ok=True)
    private_write(runtime_path / "config.json", json.dumps(config))
    return config, token


def request_json(url: str, token: str, body: dict | None = None) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )

    # Tokens must never be forwarded to another host by a redirect.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise urllib.error.HTTPError(req.full_url, code, "Redirect refused", headers, fp)

    with urllib.request.build_opener(NoRedirect()).open(request, timeout=10) as response:
        return json.load(response)


def register_discovery(supervisor_url: str, supervisor_token: str, api_token: str) -> str:
    info = request_json(f"{supervisor_url}/addons/self/info", supervisor_token)["data"]
    hostname = info.get("hostname") or info["slug"].replace("_", "-")
    payload = {
        "service": "matrix_ng",
        "config": {"host": hostname, "port": 8099, "api_token": api_token},
    }
    result = request_json(f"{supervisor_url}/discovery", supervisor_token, payload)
    if result.get("result") != "ok":
        raise ValueError("Supervisor did not accept discovery")
    return result["data"]["uuid"]


def main() -> int:
    data_root = Path("/data")
    _, api_token = prepare(
        data_root / "options.json", data_root / "matrix-ng", Path("/run/matrix-ng")
    )
    account = pwd.getpwnam("matrix")
    # Parent traversal only; /data/options.json remains Supervisor-owned.
    data_root.chmod(0o711)
    for directory, directories, names in os.walk(data_root / "matrix-ng", followlinks=False):
        for path in [Path(directory), *(Path(directory) / name for name in directories + names)]:
            if path.is_symlink():
                raise ValueError("App data must not contain symbolic links")
            os.chown(path, account.pw_uid, account.pw_gid)
    for path in (Path("/run/matrix-ng"), Path("/run/matrix-ng/config.json")):
        os.chown(path, account.pw_uid, account.pw_gid)
    env = os.environ.copy()
    env.pop("SUPERVISOR_TOKEN", None)
    env["MATRIX_NG_CONFIG"] = "/run/matrix-ng/config.json"
    child = subprocess.Popen(["gosu", "matrix", "matrix-ng-bridge"], env=env)
    stopping = False

    def stop(signum, frame):
        nonlocal stopping
        if not stopping:
            print("Stop requested by Home Assistant; shutting down the bridge", flush=True)
        stopping = True
        if child.poll() is None:
            child.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    registered = False
    started = time.monotonic()
    last_attempt = 0.0
    warned = False
    while child.poll() is None and not stopping:
        # Login, initial sync and recovery take a few seconds; check quietly until then.
        if not registered and time.monotonic() - last_attempt >= 5:
            last_attempt = time.monotonic()
            try:
                status = request_json("http://127.0.0.1:8099/v1/status", api_token)
                if status.get("ready"):
                    register_discovery(
                        "http://supervisor", os.environ["SUPERVISOR_TOKEN"], api_token
                    )
                    registered = True
                    print("Matrix NG ready; connection registered with Home Assistant", flush=True)
            except (OSError, ValueError, KeyError):
                if not warned and time.monotonic() - started >= 60:
                    warned = True
                    print(
                        "Still waiting for the bridge to finish syncing or for the Supervisor; "
                        "retrying in the background",
                        flush=True,
                    )
        time.sleep(0.5)
    try:
        result = child.wait(timeout=30)
        if not stopping:
            print(f"Bridge exited unexpectedly (exit code {result}); see messages above", flush=True)
        return 0 if stopping else result
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()
        return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # Supervisor options can contain secrets: no exception values/tracebacks.
        print(
            "Matrix NG app startup failed; check configuration and app-data permissions",
            file=sys.stderr,
        )
        sys.exit(1)
