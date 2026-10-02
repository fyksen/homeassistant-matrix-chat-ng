# Low-maintenance dependency updates

This repository is configured for **Renovate**, with automatic PR creation and
CI-gated merging for **all dependency updates**. No private Matrix credentials or GitHub token need to be added
as CI secrets. Renovate handles the Home Assistant manifest directly, which avoids
keeping a second copy of its runtime requirements in a Python requirements file.

## One-time GitHub setup

1. Push these files to your repository's default branch and let **CI** run once.
2. Install the [Renovate GitHub app](https://github.com/apps/renovate) and grant it
   access to **only this repository**. The app installation must be authorized by
   the repository owner; adding a configuration file cannot grant that access.
   In the Mend Developer Portal, ensure the repository uses **Interactive mode**,
   not Silent mode. Silent mode previews updates without creating GitHub PRs or
   issues and can display all updates as awaiting approval. This hosted setting
   cannot be overridden by the repository configuration. See
   [Mend's onboarding-mode documentation](https://docs.renovatebot.com/mend-hosted/hosted-apps-config/#onboarding-behavior).
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

- Renovate can create PRs and merge **at any time**, on its next hosted run.
  There is no dashboard-approval requirement, weekly creation window or release-age
  waiting period. Missing release timestamps therefore do not block updates.
- Updates are grouped to reduce PR noise. Up to three dependency PRs are opened at
  once; additional updates are handled automatically as those PRs merge.
- **All dependency updates**—pins, digests, patch/minor/major versions, pre-1.0 Rust
  minor versions, lockfile maintenance and security fixes—may merge **only when CI
  is green**. No per-update human approval or test bypass is configured.
- The Matrix SDK and crypto test dependency are grouped together. The Rust compiler
  and builder image are grouped too, with consistency tests as a second safeguard.
- This is an intentionally hands-off policy: even `matrix-sdk 0.19 → 0.20` may merge
  if the tests pass. Tests cannot guarantee that every runtime regression or
  supply-chain issue will be detected. There is no three-day release maturity buffer.
- OSV alerts and GitHub vulnerability alerts complement the Rust advisory scan.
- The Dependency Dashboard issue shows pending updates and failures. Failed
  update PRs remain unmerged rather than modifying application code automatically.

If an update genuinely breaks the API or fails tests, code changes are still needed
before it can merge. “Fully automated” means no routine approval clicks; it does
not bypass failing checks or automatically repair arbitrary application code.

The free Mend-hosted service normally scans active repositories every **four hours**;
the repository's `at any time` schedule does not make that service run continuously.
A manual **Run job** after changing the config/mode can start the first scan sooner.
You should not need to select individual updates or use **Create/Rebase** normally.

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
