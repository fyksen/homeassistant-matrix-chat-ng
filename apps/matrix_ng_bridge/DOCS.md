# Matrix NG Bridge

This app runs the official Matrix Rust SDK. It requires the **Matrix NG custom
integration**, installed through HACS's **Custom repositories** (Integration type).
Neither the app repository nor the integration requires a listing submission.

1. Install the integration, then restart Home Assistant.
2. Configure this app with a dedicated Matrix user ID/password. An existing recovery
   key is recommended for device verification. An empty homeserver uses discovery.
3. Join the bot account to encrypted rooms using Element/another Matrix client.
4. Start the app. Home Assistant offers a discovered **Matrix NG** integration;
   confirm it and select a room. No API token or bridge URL needs to be copied.
5. Use the resulting `notify` entity or `matrix_ng.send_message` action. Use the
   integration's **Configure** options to add commands and authorized senders.

An empty room allowlist opts into **all already-joined encrypted rooms** at startup.
It never joins rooms, accepts invitations or enables encryption. For stricter scope,
enter internal room IDs in the app's allowlist. Plaintext rooms are always refused.

The account's own messages cannot trigger commands. A command sender must use a
different Matrix account and must be in the integration's allowed-senders list.

## Storage, backups and upgrades

The API token and complete SDK state live in `/data/matrix-ng`. They persist across
app upgrades/restarts and are included in app backups. Backups are **cold**: Supervisor
stops the app first so SQLite and session files are captured consistently.

After a successful login/recovery you can clear the password and recovery-key fields.
Keep the persistent data and include both this app and HA configuration in backups.
Restoring only one can lose a device identity or leave a stale connection token.

Uninstalling the app or deleting its data loses the local device/keys. Do not run
two copies against the same SDK store or casually downgrade across database migrations.

## Security

The app has no host-network port mapping, no hardware access, no Docker socket and
no write access to Home Assistant's configuration. The Python bootstrap reads
Supervisor options, then launches the Rust process as a non-root user. The bridge
does not receive the Supervisor token. Credentials are never logged.

The watchdog exposes only a readiness status. All message/command APIs require the
generated bearer token. Discovery transfers that token over the internal Supervisor
API, not through logs or a publicly reachable port.

Reactions are normally plaintext Matrix events, even in encrypted rooms. Text
commands must be decrypted encrypted events; sender restrictions are mandatory.

## Troubleshooting

- **No discovered integration:** install/restart the custom integration first,
  then restart this app. Check app logs for a discovery retry message.
- **No rooms offered:** join the bot to an encrypted room, then restart the app.
- **Login/recovery failed:** check credentials; the app deliberately does not reset
  encryption identity or automatically replace an invalid saved Matrix session.
- **Commands don't fire:** configure the integration's allowed senders/triggers,
  enable command reception here, and issue a fresh command from a different account.
