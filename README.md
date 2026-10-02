# Matrix NG for Home Assistant

[![CI](https://github.com/fyksen/homeassistant-matrix-chat-ng/actions/workflows/ci.yaml/badge.svg)](https://github.com/fyksen/homeassistant-matrix-chat-ng/actions/workflows/ci.yaml)

Send notifications to **end-to-end encrypted Matrix rooms** using the official
[Matrix Rust SDK](https://github.com/matrix-org/matrix-rust-sdk) (`matrix-sdk 0.19.1`).

## Quick install on Home Assistant OS

Both parts install from custom repositories, so no HACS or app-store listing submission is needed.

1. In HACS, open **⋮ → Custom repositories** and add
   `https://github.com/fyksen/homeassistant-matrix-chat-ng` with type **Integration**.
   Download **Matrix NG**, then restart Home Assistant.
2. Add the app repository:

   [![Open your Home Assistant instance and show the add app repository dialog with this repository URL pre-filled.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Ffyksen%2Fhomeassistant-matrix-chat-ng%23apps)

   Manual URL: `https://github.com/fyksen/homeassistant-matrix-chat-ng#apps`
3. Install **Matrix NG Bridge**, enter the dedicated bot's Matrix ID and password
   (plus an existing recovery key if possible), and start it.
4. Accept the discovered **Matrix NG** integration and select an encrypted room.
   The connection URL and API token are provisioned automatically.
5. Add commands under **Settings → Devices & services → Matrix NG → Configure**.

Releases ship prebuilt `amd64` and `aarch64` images (x86 PCs, 64-bit Raspberry Pi,
Home Assistant Green), so nothing compiles on the device. Matching releases produce
normal Home Assistant and HACS update notifications. Matrix session and encryption
state lives in the app's persistent `/data` and is captured by cold backups; back up
both the app and Home Assistant configuration together.

There are two parts:

```text
Home Assistant automation
  → matrix_ng custom integration (Python, async HTTP)
  → local authenticated Rust bridge (matrix-rust-sdk + SQLite)
  → encrypted Matrix event → room members
```

The bridge is a **required separate process/container**, not a Python crypto
library. This keeps Rust compilation out of Home Assistant and preserves Matrix
device identity and encryption keys across Home Assistant restarts. Home Assistant
2026.9 or later is the supported baseline; tested with 2026.9.3.

## Features

- UI configuration and one modern `notify` entity per encrypted room.
- `matrix_ng.send_message` action with optional event-ID response.
- Official-style word, Python-regex and emoji-reaction commands firing
  `matrix_command` events, with explicit sender allowlisting.
- Password login, homeserver discovery, and importing an **existing** recovery key.
- Persistent Matrix session, device, cross-signing identity, and encrypted SQLite
  crypto store. No fresh device on each restart.
- Supports serverless room IDs (Matrix room version 12) and older room IDs.
- Strict room allowlist; refuses unjoined, unknown-encryption and plaintext rooms.
- Bearer-authenticated bridge, bounded message sizes, health polling, redacted
  diagnostics, and API-token reauthentication.
- No automatic room joining, invitation acceptance, identity reset, recovery-key
  rotation, or backup reset.

## 1. Start the Rust bridge

Install Docker with Compose, then from this directory:

```sh
mkdir -p secrets data
chmod 700 secrets data
cp config.example.json secrets/matrix-config.json
chmod 600 secrets/matrix-config.json
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
```

Edit `secrets/matrix-config.json` and fill in:

| Field | Meaning |
| --- | --- |
| `user_id` | Full account ID, e.g. `@homeassistant:example.org` |
| `homeserver` | HTTPS client API base URL; omit to use discovery from the user ID |
| `password` | Required on first login; removable after a successful login |
| `recovery_key` | Existing account recovery key or passphrase; optional, but recommended to verify this device and import existing encryption secrets |
| `api_token` | Random token of at least 32 printable ASCII characters; use the same token in HA |
| `rooms` | Allowlist of internal IDs, not room links or `#aliases` |
| `listen_for_commands` | Opt-in incoming command stream, default `false` |
| `storage_path` | `/data` in the container |
| `listen` | `0.0.0.0:8099` in the container; Compose exposes it only on host loopback |

The account must **already be joined** to each room. Join or accept invitations
with Element or another Matrix client first. A wrong recovery key fails startup;
the bridge never tries to fix this by resetting your account's encryption identity.
If no recovery key is supplied, sending encrypted events still works, but the new
device may need manual verification from another client.

Run the container with your host UID/GID so it can read a mode-600 secret and write
the mode-700 data directory:

```sh
export MATRIX_NG_UID="$(id -u)" MATRIX_NG_GID="$(id -g)"
docker compose up -d --build
docker compose logs -f matrix-bridge
```

Persist those variables in `.env` if needed. The build downloads and compiles the
Rust SDK and may take several minutes. Bind mounts are intentionally used so data
and secrets remain owned by your host user. Rootless Podman can also run the bridge
and Home Assistant; the test helper supports `CONTAINER_ENGINE=podman`.

### Running without Docker

With a current stable Rust toolchain and C build tools:

```sh
cargo build --locked --release
MATRIX_NG_CONFIG="$PWD/secrets/matrix-config.json" ./target/release/matrix-ng-bridge
```

For a native process, change `storage_path` to an absolute writable directory and
set `listen` to `127.0.0.1:8099`. A process lock prevents two bridges from using
the same store.

## 2. Install the Home Assistant custom integration

Copy `custom_components/matrix_ng` into your Home Assistant configuration:

```text
/config/custom_components/matrix_ng/
```

Restart Home Assistant. Then **Settings → Devices & services → Add integration →
Matrix NG**:

1. Enter the bridge URL and API token.
2. Select a room. Only joined, encrypted allowlisted rooms appear.

For Home Assistant Container using host networking on the same machine, the URL
is `http://127.0.0.1:8099`. In a shared container network, use a service hostname,
e.g. `http://matrix-bridge:8099`. `127.0.0.1` inside an isolated container does
**not** mean the host. Home Assistant OS needs the bridge on another reachable
host/container; this project does not yet include a Supervisor add-on.

Compose's default loopback binding is intentional. To reach the bridge from another
machine, configure a private network binding and preferably an HTTPS reverse
proxy. Do not expose this API to the public Internet.

## 3. Send messages from automations

Use the entity ID shown in Home Assistant (the exact generated ID depends on room
name and entity registry):

```yaml
action: notify.send_message
target:
  entity_id: notify.your_room_messages
data:
  title: Home Assistant
  message: "The garage door is open."
```

Or use the integration action:

```yaml
action: matrix_ng.send_message
data:
  message: "The garage door is open."
  title: Home Assistant
response_variable: matrix_result
```

The optional response is `{event_id: "$…", encrypted: true}`. `room_id` can
override the configured room, but the bridge's allowlist still applies. If multiple
entries are loaded, specify `entry_id` using the action editor's config-entry
selector.

Messages are plain text inside the encrypted event. Titles are prepended with a
blank line. The combined body is limited to 16,000 **UTF-8 bytes**. Attachments,
arbitrary incoming-message forwarding, Markdown/HTML, and interactive verification
are not implemented. Configured incoming commands are supported as described below.

### Retry semantics

Python does not automatically retry failed sends, since an HTTP timeout can occur
after the homeserver has accepted the event. Supply a stable `transaction_id`
when intentionally retrying the **same message**. Use a new ID for a new message.
IDs allow ASCII letters, digits, `_` and `-`, up to 128 bytes. Idempotency is scoped
to the Matrix session and room; it is not a durable job queue.

## Commands: react to incoming Matrix messages

Commands follow the [official Matrix integration's](https://www.home-assistant.io/integrations/matrix/)
`word`, `expression`, `reaction`, `name` and optional `rooms` configuration.
They are defined in **Home Assistant's `configuration.yaml`**, not the bridge's
credential file. Keep your existing Matrix NG UI entries for the room targets.

1. Set `"listen_for_commands": true` in the bridge JSON and restart/rebuild the bridge.
2. Add the following to Home Assistant's `configuration.yaml` and restart HA:

```yaml
matrix_ng:
  allowed_senders:
    - "@your_personal_account:example.org"
  commands:
    - word: status
      name: status
    - expression: 'My name is (?P<name>.*)'
      name: introduction
    - reaction: "👍"
      name: thumbsup
    - word: lights
      name: lights
      rooms:
        - "!YOUR_CONFIGURED_ENCRYPTED_ROOM_ID"
```

Each command needs **exactly one** trigger and a `name`:

- `word: status` matches `!status` and splits following arguments on whitespace.
  For `!status kitchen on`, `args` is `["kitchen", "on"]`. Words are case-sensitive.
- `expression` uses Python-compatible regular expressions, matches from the start
  of the message (`re.match` semantics), and returns named groups in `args`.
  Regex execution runs outside HA's event loop with a 50 ms matching timeout.
- `reaction: "👍"` matches that exact emoji and returns `args: {reaction: "👍"}`.
- `rooms` optionally restricts a command to particular **internal IDs**. Otherwise
  it applies to every room configured as a Matrix NG UI entry. The bridge allowlist
  still applies; this does not join new rooms or resolve aliases.

**Sender allowlisting is required when commands are configured.** No wildcard is
supported. Use a separate personal/controller account to issue commands: messages
from the bridge's own account are always ignored, including messages from another
device logged into that same account. Treat authorized commands as remote-control
access to any automations you connect to them.

### Automation example

```yaml
automation:
  - alias: "Matrix: respond to status"
    triggers:
      - trigger: event
        event_type: matrix_command
        event_data:
          command: status
    actions:
      - action: matrix_ng.send_message
        data:
          entry_id: "{{ trigger.event.data.entry_id }}"
          room_id: "{{ trigger.event.data.room }}"
          message: >-
            Received status request with arguments:
            {{ trigger.event.data.args | join(' ') }}
```

The event is named **`matrix_command`**, like the official integration, so existing
command/args-based triggers can be reused. If you also run the official integration,
filter on `entry_id` to distinguish Matrix NG events.

| Event field | Value |
| --- | --- |
| `command` / `name` | Configured command name (`name` is a compatibility alias) |
| `args` / `data` | Word arguments, regex named groups, or reaction dictionary |
| `sender` | Issuing user's full Matrix ID |
| `room` | Internal room ID |
| `event_id` | Command message ID; for reactions, the **reacted-to message** ID |
| `source_event_id` | Actual incoming event ID, including the reaction event ID |
| `thread_parent` | Thread root ID, or the message/target ID outside a thread |
| `entry_id` | Matrix NG config entry that received the command |

The Rust SDK decrypts text commands; **plaintext text messages are ignored even
inside encrypted rooms**. Edits, notices, emotes and redacted messages do not execute
text commands. Matrix reactions are normally **not encrypted**, even in encrypted
rooms; the reaction emoji and target ID may be visible to the homeserver. Reaction
commands still require an encrypted allowlisted room and an authorized sender.

### Live-only delivery and reconnect behavior

The authenticated `/v1/events` endpoint uses long polling. Its command-stream
buffer is bounded and **in-memory**; the bridge does not maintain a command journal
or log message bodies. Treat SDK state, HA event/recorder data, and their backups
as sensitive: matched command arguments may be recorded by Home Assistant.
Initial sync history is skipped. A new HA listener or bridge restart starts at
the live end of the stream, without replaying earlier commands. Short connection
interruptions can catch up within the buffer; events older than **60 seconds**
are dropped. Duplicate event IDs are ignored. Overflow or a changed bridge stream
resets the cursor and skips buffered history rather than risking command replay.

This is intentionally **not guaranteed/durable command delivery**. Commands issued
while HA is stopped, during bridge startup, or after a long outage may be dropped.
Use idempotent automations and send a fresh command after reconnecting.

## Security and backups

- End-to-end encryption starts in the **Rust bridge**, not in the Python integration.
  Home Assistant → bridge carries message text and the bearer token. Use loopback,
  a trusted private container network, or HTTPS for that hop.
- The bridge uses TLS for homeserver communication and the SDK's normal key-sharing
  and trust checks; it never silently falls back to plaintext.
- Recovery imports existing cross-signing and backup secrets and signs this device
  using the existing identity. It does not rotate those secrets.
- `secrets/`, `data/`, `.env` and test credentials are excluded from source control.
  HA stores its bridge API token in `.storage/core.config_entries`, so protect HA
  backups as credentials too.
- Back up the **entire data directory while the bridge is stopped**. It contains
  `session.json`, an encrypted SQLite store, and the store passphrase. Since the
  passphrase lives beside the database, disk/filesystem and backup protection are
  still necessary. The access token is not encrypted in `session.json`.
- After successful recovery, you may remove `password` and `recovery_key` from the
  configuration. The next startup restores the existing device and keys. Keep the
  data directory; deleting it creates a new device and loses local keys.
- Rotate credentials shared in chats before production use. Do not reset a Matrix
  recovery key casually: follow your client's recovery-key rotation procedure.

## Troubleshooting

| Error | What to check |
| --- | --- |
| `invalid_auth` | HA's token must match `api_token`; reload/reauthenticate after rotation |
| `not_ready` | Initial sync has not finished, or the last successful sync is over 120 seconds old |
| No rooms offered | Check allowlist, membership and room encryption; restart bridge after changing config |
| `room_not_encrypted` | Room is plaintext; this is intentionally refused |
| `matrix_send_failed` | Membership, send permission, homeserver connectivity or SDK key-sharing/trust failure |
| Recovery failed | Confirm the recovery key belongs to the configured account and that secret storage exists |
| Permission denied | Match Compose UID/GID to ownership of `secrets/` and `data/` |
| Commands entry not ready | Update the bridge, enable `listen_for_commands`, then restart it |
| A command does not fire | Check `allowed_senders`, configured room, exact trigger and 60-second age limit; the bot cannot command itself |

## Development and tests

```sh
cargo test --locked
cargo fmt --check
cargo clippy --locked --all-targets -- -D warnings
sh scripts/test-python.sh
# Or: CONTAINER_ENGINE=podman sh scripts/test-python.sh
```

The Python suite runs in the Home Assistant container and tests protocol validation,
auth errors, encrypted-room filtering, redirects, message sizes and safe failure
handling. Rust tests cover auth, configuration, request validation and secure
persistent storage. CI does **not** use a real Matrix account or send real messages.

See `TESTING.md` for the live Home Assistant/Matrix verification performed during
development.

### Automatic maintenance

Renovate updates Rust/Python dependencies, Home Assistant requirements, container
images and GitHub Actions. All dependency updates, including major versions and
security fixes, can merge after CI passes without approval clicks. CI tests the supported minimum and current/latest HA
versions and also runs weekly to catch upstream changes.

See **[MAINTENANCE.md](MAINTENANCE.md)** for the one-time GitHub app installation
and required-check setup. After that, dependency upkeep should mostly be hands-off.

To check an already-configured disposable HA instance:

```sh
python3 scripts/smoke-test.py \
  --bridge-config /path/to/private/matrix-config.json \
  --ha-token-file /path/to/private/ha-auth.json \
  --entity-id notify.your_room_messages
```

The HA auth file is JSON containing an `access_token` from a long-lived token or
the test instance's login. Add `--send` to intentionally post **two** test messages.
Add `--session-file /path/to/private/data/session.json` to inspect the resulting
raw event on the homeserver. No messages are sent by default.

For a decryption check, **stop the bridge** and run:

```sh
MATRIX_NG_CONFIG=/path/to/private/matrix-config.json \
  cargo run --locked --example verify_event -- '!ROOM_ID' '$EVENT_ID'
```

This uses the existing SDK crypto store and prints the decrypted test message;
do not run it against messages whose content should not appear in terminal output.
