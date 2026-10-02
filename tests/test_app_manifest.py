"""Supervisor app declares only required privileges and persistent/cold backups."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_app_is_private_network_only_with_cold_backup_and_watchdog():
    config = yaml.safe_load((ROOT / "apps/matrix_ng_bridge/config.yaml").read_text())
    assert config["arch"] == ["amd64", "aarch64"]
    assert config["boot"] == "auto"
    assert config["backup"] == "cold"
    assert config["discovery"] == ["matrix_ng"]
    assert config["watchdog"].endswith("/health")
    assert config["image"] == "ghcr.io/fyksen/homeassistant-matrix-chat-ng-app"
    for key in ("host_network", "docker_api", "full_access", "homeassistant_api"):
        assert not config.get(key, False)
    assert not config.get("map")
    assert not config.get("ports")
    assert config["schema"]["password"] == "password"
    assert config["schema"]["recovery_key"] == "password"


def test_custom_repository_and_brands_are_installable_without_hacs_listing():
    config = yaml.safe_load((ROOT / "repository.yaml").read_text())
    assert config["url"] == "https://github.com/fyksen/homeassistant-matrix-chat-ng"
    for path in ("custom_components/matrix_ng/brand/icon.png", "apps/matrix_ng_bridge/icon.png"):
        assert (ROOT / path).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
