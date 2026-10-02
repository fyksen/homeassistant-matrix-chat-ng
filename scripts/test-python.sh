#!/bin/sh
set -eu
# Pinned test/runtime dependencies are installed in a disposable test image.
# HA_TEST_IMAGE may override the Renovate-managed default to test another HA release.
ENGINE="${CONTAINER_ENGINE:-docker}"
IMAGE="matrix-ng-python-tests:local"
if [ -n "${HA_TEST_IMAGE:-}" ]; then
  "$ENGINE" build --pull -f .github/Dockerfile.tests -t "$IMAGE" \
    --build-arg "HOME_ASSISTANT_IMAGE=$HA_TEST_IMAGE" .
else
  "$ENGINE" build --pull -f .github/Dockerfile.tests -t "$IMAGE" .
fi
"$ENGINE" run --rm \
  -v "$(pwd):/work" -w /work \
  "$IMAGE"
