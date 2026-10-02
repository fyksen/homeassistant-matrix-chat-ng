# Verification record

Tested on **2026-10-02**, Linux x86-64, Rust 1.99.0, Matrix Rust SDK 0.19.1,
and Home Assistant **2026.9.3**.

## Automated checks

- **8 Rust tests passed**, including mock-homeserver tests proving that plaintext,
  unjoined and non-allowlisted rooms do not cause any `/send/` request, and that
  stale sync prevents sending.
- **38 Python tests passed** in the Home Assistant container. Coverage includes
  protocol shape and encryption flags, bearer auth, URL validation, redirect
  rejection, message-size checks, non-retrying sends, UI encrypted-room filtering,
  entity availability, auth reauthentication errors and diagnostic redaction.
- `cargo fmt --check`, `cargo clippy --locked --all-targets -- -D warnings`,
  Ruff lint and formatting checks passed.
- The multi-stage `Dockerfile` built successfully. Its non-root runtime image
  restored a native-created SDK store and sent encrypted notifications.

Docker was installed but its system daemon was not running. Container testing used
**rootless Podman**, including the Home Assistant image and the bridge Dockerfile;
the system Docker daemon was not enabled or modified.

## Live account and room tests

Using only the account and test room authorized for this task:

1. Logged in once and saved a persistent device/session.
2. Imported the supplied existing recovery key. The SDK reported this device as
   **verified with cross-signing**; the existing identity was not reset.
3. Confirmed the account was already joined to the encrypted test room. The room
   has a serverless room-version-12-style ID.
4. Configured the integration through Home Assistant's config-flow API, equivalent
   to the two UI steps: bridge connection and encrypted-room selection.
5. Sent via `notify.send_message` and `matrix_ng.send_message`. Tested both the
   native bridge and the built bridge container: **four test messages total**.
6. Retried each integration-action test with the same transaction ID; the same
   event ID was returned, so retries did not add duplicate messages.
7. Retrieved the raw resulting events directly from the homeserver and checked:
   - Event type was `m.room.encrypted`.
   - Algorithm was `m.megolm.v1.aes-sha2`.
   - The raw content had **no plaintext `body`**.
8. Stopped the bridge and decrypted a sent event using `examples/verify_event.rs`
   and the persistent Rust SDK crypto store. The decrypted title/message matched
   the test message. This is a sender-side decryption check; another user's client
   was not independently inspected.
9. Removed the password and recovery key from the private test configuration and
   restarted. The same device ID and verified identity were restored, with the
   encrypted room still available.
10. Restarted Home Assistant and checked entry/entity persistence and restoration
    of the last-notified timestamp.
11. Verified live HTTP rejection of an incorrect bearer token (`401`) and a room
    outside the allowlist (`403`). Neither attempted message was posted.

## Private test state

No account password, recovery key, access token or API token is embedded in project
source or documentation. Test state is outside the project in:

```text
/tmp/opencode/matrix-ng-live/
```

That directory is mode `700`; configuration/session credential files are mode
`600`. The password and recovery key have been removed from the test configuration.
The directory still contains access tokens, the SDK crypto store/passphrase and
the disposable Home Assistant instance, so it must be treated as sensitive.

Temporary containers are named `matrix-ng-bridge-test` and `matrix-ng-ha-test`.
They are stopped after verification; the state is retained so the test device's
keys are not discarded. This directory is temporary storage, not a production
backup: move/back up the complete crypto store securely if reusing this device.

## Command extension (0.2.0)

Additional verification on 2026-10-02:

- **84 tests passed: 14 Rust and 70 Python**, with Rust/Clippy and Ruff checks clean.

- The Rust SDK consumed a **genuinely Megolm-encrypted** incoming command in a
  controlled mock-homeserver test, using a generated group session and imported
  room key. The decoded body and thread root were forwarded correctly.
- The same test checked reaction delivery (emoji, target ID, source reaction ID
  and thread root), while plaintext text and bot-issued messages were excluded.
- Python tests cover word arguments, named regex groups, reaction arguments,
  room restrictions, required sender ACLs, timeout handling, stale/future events,
  deduplication, cursor reset/reconnect, and reauthentication.
- A test fired a command through a **real Home Assistant event bus**, checking
  `matrix_command`, `command`, `args` and thread metadata.
- Home Assistant 2026.9.3 loaded the YAML command configuration and background
  listener against the updated native bridge and existing verified device.
- One additional encrypted test message was posted to the authorized real test
  room to check that bot-issued commands do not enter the command stream. This
  brings the development total to **five real test messages**.
- An authorized command issued by a **second real Matrix account** was not tested:
  only the bot account's credentials were supplied. Incoming decryption and command
  delivery are verified with the controlled encrypted fixture and HA event-bus tests,
  rather than claiming independent live-user verification.

## Dependency automation and CI

Maintenance setup was verified on 2026-10-02:

- **92 Python tests** passed against HA **2026.9.0**, **2026.9.3** and the current
  `stable` image, which reported **2026.9.4**. Runtime requirements came from the
  integration manifest rather than being assumed from the base image.
- `cargo test --locked --all-targets` passed **14 bridge tests and 2 example/storage
  tests** on the pinned Rust 1.99.0 toolchain. Formatting and Clippy checks passed.
- The production Dockerfile built successfully with the pinned toolchain. The
  no-network smoke test verified a working binary and non-root default user.
- `cargo-deny 0.20.2 check advisories` reported **advisories ok**.
- Renovate **44.132.2** validated `renovate.json` in strict mode.
- A local extraction dry run on an isolated committed snapshot detected all six
  configured managers and the current HA test-image dependency. No remote PRs or
  branches were created; GitHub action lookups require the installed app's token.
- Actionlint **1.7.7** accepted the GitHub Actions workflow.
- Shell syntax checks and `git diff --check` passed.

GitHub app installation, branch protection and actual bot-created PR/merge behavior
require the repository owner's authorization and were **not changed or exercised
remotely** in this session. Follow `MAINTENANCE.md` once these changes are pushed.
The GitHub setup helper deliberately refuses to overwrite existing branch protection.

## Fully automatic dependency policy

After enabling the GitHub app and branch protection, the policy was changed to
automatic PR creation and CI-gated merging for **all** updates, including major,
pre-1.0 minor, security, pin/digest and lockfile updates. Dashboard approval, the
weekly creation window and release-age waiting period were removed.

- **93 Python tests** pass on minimum, pinned-current and latest-stable HA images.
- Strict Renovate validation and Ruff checks pass.
- Regression tests require automatic creation/merging and prohibit manual approval
  overrides while preserving `ignoreTests: false` and `platformAutomerge: false`.
- GitHub confirmed the existing main-branch CI run passed, `CI required` remained
  mandatory, and no human PR-review requirement was configured.
- The Mend portal's actual mode was not accessible in this session. If it is using
  hosted Silent mode (`dryRun=lookup`), the repository owner must switch it to
  Interactive mode once. A repository config cannot override that hosted setting.

This policy attempts potentially breaking updates automatically; failed CI still
prevents merging and may require an application-code fix.

## Not verified

- An independent recipient's Element/other Matrix client.
- Home Assistant OS/Supervisor (no add-on is included).
- ARM64 or other architectures, other homeservers, delegated OIDC-only login,
  expired/revoked Matrix sessions, long-duration network failures or load testing.
- Attachments, arbitrary incoming-message forwarding, HTML/Markdown and
  interactive verification are not implemented. Configured incoming commands
  are supported in version 0.2.0.
