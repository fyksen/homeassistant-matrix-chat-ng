#!/bin/sh
set -eu
# Dependencies are installed only in this disposable test container.
ENGINE="${CONTAINER_ENGINE:-docker}"
"$ENGINE" run --rm --entrypoint sh \
  -v "$(pwd):/work" -w /work \
  ghcr.io/home-assistant/home-assistant:2026.9.3 \
  -c 'pip install --quiet pytest pytest-asyncio pytest-aiohttp ruff && python -m pytest -q && ruff check custom_components tests && ruff format --check custom_components tests'
