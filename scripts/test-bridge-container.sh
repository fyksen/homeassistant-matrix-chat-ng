#!/bin/sh
set -eu
ENGINE="${CONTAINER_ENGINE:-docker}"
IMAGE="${1:-matrix-ng-bridge:ci}"
EXPECTED_USER="$("$ENGINE" image inspect --format '{{.Config.User}}' "$IMAGE")"
case "$EXPECTED_USER" in
  ''|root|root:*|0|0:*) echo "Bridge image must have a non-root default user" >&2; exit 1 ;;
esac

# Starts the compiled binary without credentials/network access. It must reach
# config validation and exit cleanly, not fail to load due to missing libraries.
if OUTPUT="$("$ENGINE" run --rm --network none "$IMAGE" 2>&1)"; then
  echo "Bridge unexpectedly started without its mandatory configuration" >&2
  exit 1
fi
case "$OUTPUT" in
  *'cannot read bridge configuration'*) echo "Non-root bridge runtime smoke test passed" ;;
  *) echo "Bridge failed before config validation: $OUTPUT" >&2; exit 1 ;;
esac
