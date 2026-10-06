# AGENTS.md — Matrix NG

Compact guide for OpenCode sessions working in this repo.

## Project layout

- `src/` — Rust bridge (`matrix-ng-bridge`). Entrypoint `src/main.rs`; HTTP API in `src/api.rs`, config in `src/config.rs`, Matrix events in `src/events.rs`, crypto storage helpers in `src/storage.rs`.
- `custom_components/matrix_ng/` — Home Assistant custom integration (Python 3.13+). Manifest is the source of runtime requirements.
- `apps/matrix_ng_bridge/` — Home Assistant Supervisor app wrapper (`startup.py`) and app-repository metadata (`config.yaml`, `repository.yaml`).
- `scripts/` — Release/version tooling, Python/bridge/app smoke tests, GitHub setup helper.
- `tests/` — Python integration tests only. Rust tests are inline (`#[cfg(test)]`) plus `examples/verify_event.rs`.

## Local verification

```sh
# Rust
rustup show  # pins to rust-toolchain.toml (currently 1.99.0)
cargo test --locked --all-targets
cargo fmt --check
cargo clippy --locked --all-targets -- -D warnings

# Python (runs inside a disposable Home Assistant container; also runs Ruff checks)
sh scripts/test-python.sh

# Specific HA version, e.g. latest stable
HA_TEST_IMAGE=ghcr.io/home-assistant/home-assistant:stable sh scripts/test-python.sh

# Container smoke tests
docker build -t matrix-ng-bridge:ci .
sh scripts/test-bridge-container.sh matrix-ng-bridge:ci
```

Use `CONTAINER_ENGINE=podman` with any of the shell helpers above.

## Python tooling conventions

- Ruff target is Python 3.13; line length 100.
- Integration runtime requirement lives only in `custom_components/matrix_ng/manifest.json` (`requirements` field); do not duplicate in `requirements-test.txt`.
- Python tests are pytest-asyncio; fixtures are in `tests/conftest.py`.

## Running the bridge locally

```sh
cargo build --locked --release
MATRIX_NG_CONFIG="$PWD/secrets/matrix-config.json" ./target/release/matrix-ng-bridge
```

The bridge reads `MATRIX_NG_CONFIG` or defaults to `/run/secrets/matrix-config.json`. Copy `config.example.json` to `secrets/matrix-config.json`, set mode `600`, and keep `secrets/` and `data/` mode `700`.

## Version stamping and releases

- Do not manually bump versions in `Cargo.toml`, `Cargo.lock`, `manifest.json`, `apps/matrix_ng_bridge/config.yaml`, or `Dockerfile`.
- The release script updates all of them consistently:
  ```sh
  python3 scripts/release.py stamp 0.4.0
  ```
- Releases run on a **monthly train** (1st of the month) and skip months where only docs, tests, or workflow files changed. See `MAINTENANCE.md`.

## CI gatekeeping

Required checks are grouped behind the `CI required` job. No path filters skip tests on dependency PRs. Jobs include:

- Rust tests, formatting, Clippy on the pinned toolchain.
- Python tests against HA `2026.9.0` (minimum), the Renovate-pinned current image, and `stable`.
- `amd64` and `aarch64` bridge + Supervisor app container builds and smoke tests.
- `cargo-deny` advisories.
- Strict Renovate config validation and actionlint.

## Common mistakes to avoid

- Do not treat the bridge as a Python library; encryption and persistence happen in the Rust process. The Python integration talks to it over HTTP.
- Do not configure incoming commands in the bridge JSON. Command matchers and `allowed_senders` belong in Home Assistant's `configuration.yaml` under `matrix_ng:`; the bridge only needs `"listen_for_commands": true`.
- Do not join rooms or accept invitations from the bridge. The configured Matrix account must already be joined to each encrypted room.
- Never reset encryption identity or recovery keys automatically. Recovery imports existing secrets; wrong keys fail startup.
- When changing the supported HA minimum, update `hacs.json`, the `minimum` CI matrix step in `.github/workflows/ci.yaml`, and any docs together.

## Where authoritative context lives

- `README.md` — install, usage, automation examples, troubleshooting.
- `TESTING.md` — manual verification record and known unverified areas.
- `MAINTENANCE.md` — Renovate/GitHub setup, release automation, CI gates.
- `config.example.json` — bridge configuration shape.
- `custom_components/matrix_ng/manifest.json` — integration metadata and runtime dependency.
