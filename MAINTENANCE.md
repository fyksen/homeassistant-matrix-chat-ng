# Low-maintenance dependency updates

This repository is configured for **Renovate**, with tests and conservative
automatic merging. No private Matrix credentials or GitHub token need to be added
as CI secrets. Renovate handles the Home Assistant manifest directly, which avoids
keeping a second copy of its runtime requirements in a Python requirements file.

## One-time GitHub setup

1. Push these files to your repository's default branch and let **CI** run once.
2. Install the [Renovate GitHub app](https://github.com/apps/renovate) and grant it
   access to **only this repository**. The app installation must be authorized by
   the repository owner; adding a configuration file cannot grant that access.
3. Require the **`CI required`** status check on the default branch. Either use
   GitHub's branch-protection/ruleset UI or install GitHub CLI, authenticate, and run:

   ```sh
   gh auth login
   sh scripts/configure-github.sh
   ```

The helper creates protection only if the default branch is not already protected.
It never replaces existing branch protection. It also enables vulnerability alerts,
squash merging, and deletion of fully merged branches. If protection already exists,
add `CI required` to its checks manually. GitHub permissions/plan restrictions may
require using the repository settings UI instead.

You do **not** need to enable GitHub's separate “Allow auto-merge” setting: Renovate
does the merge itself only after checks pass (`platformAutomerge: false`). It will
respect existing approval requirements. If you require human approval for every PR,
that approval remains necessary—this configuration does not bypass it.

## What happens automatically

| Dependency source | Updated by Renovate |
| --- | --- |
| `Cargo.toml` and `Cargo.lock` | Rust dependencies and compatible lockfile refreshes |
| `manifest.json` → `requirements` | Actual integration runtime requirements |
| `requirements-test.txt` | pytest, async test helpers, Ruff and metadata-test tools |
| `Dockerfile` | Rust builder and Debian runtime image tags/digests |
| `.github/Dockerfile.tests` | Current Home Assistant test image |
| `rust-toolchain.toml` | Compiler used locally, in CI and in the bridge build |
| `.github/workflows/*.yaml` | GitHub Actions and CI tool image references |

- Routine updates are checked **weekly**, Monday before 06:00 UTC, and grouped
  to reduce PR noise. Only three dependency PRs are opened at once.
- New releases normally wait **three days** before updates are proposed. Images
  without release timestamps are not held indefinitely.
- Patch/minor updates, digest pins and compatible lockfile refreshes may merge
  **only when CI is green**. No automatic human approval or test bypass is enabled.
- The Matrix SDK and crypto test dependency are grouped together. The Rust compiler
  and builder image are grouped too, with consistency tests as a second safeguard.
- Major updates and **minor updates of pre-1.0 Rust crates** require review. For
  example, `matrix-sdk 0.19 → 0.20` is not treated as a safe automatic update.
- Security fixes can be proposed without the weekly schedule or release-age delay,
  but are deliberately left for review. OSV alerts and GitHub vulnerability alerts
  complement the Rust advisory scan.
- The Dependency Dashboard issue shows pending updates and failures. Failed
  update PRs remain unmerged rather than modifying application code automatically.

This updates the **repository**, not your running Home Assistant installation.
Publishing releases and deploying new integration/bridge versions remain explicit
maintainer operations.

## CI gates

Every push, PR and merge-queue change runs:

1. Rust tests, formatting and Clippy on the pinned compiler, including the real
   encrypted-message fixture and container-independent crypto/storage checks.
2. Python tests and lint against the **declared minimum HA version**, the
   Renovate-managed current version, and the moving **latest stable** release.
3. A production bridge image build and a non-root/no-network runtime smoke test.
4. Rust security advisories using `cargo-deny`, with no ignored vulnerabilities.
5. Strict Renovate configuration validation and GitHub Actions workflow linting.
6. **`CI required`**, which fails if any job fails, is cancelled or is skipped.

CI also runs weekly, Monday at 06:23 UTC, to find new Home Assistant incompatibilities
or published security advisories even if nobody has pushed code. GitHub schedules
are best-effort and may run late; GitHub may disable scheduled workflows after
extended repository inactivity. Watch workflow-failure notifications and the
Renovate dashboard for those cases.

There are no path filters that can accidentally skip required tests on a dependency
PR. Workflow permissions are read-only, checkout credentials are not persisted,
and no privileged `pull_request_target` workflow runs untrusted PR code.

## Run the same checks locally

```sh
cargo test --locked --all-targets
cargo fmt --check
cargo clippy --locked --all-targets -- -D warnings
sh scripts/test-python.sh
HA_TEST_IMAGE=ghcr.io/home-assistant/home-assistant:stable sh scripts/test-python.sh
docker build -t matrix-ng-bridge:ci .
sh scripts/test-bridge-container.sh matrix-ng-bridge:ci
```

Set `CONTAINER_ENGINE=podman` to use Podman for either shell helper. Test packages
are pinned and installed in a disposable image. Runtime packages are installed
**from the current integration manifest**, so a bot update is actually tested,
even if the HA image bundles an older package.

The HA minimum is intentionally not updated by Renovate. Raising support minimums
requires updating `hacs.json`, the `minimum` CI matrix step, and documentation
together. A regression test checks that the declared and tested versions agree.
