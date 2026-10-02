"""Keep dependency automation, supported versions and repository metadata coherent."""

import json
import re
import tomllib
from pathlib import Path

import pytest
import yaml
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]


def test_matrix_sdk_and_crypto_test_dependency_are_updated_together():
    cargo = tomllib.loads((ROOT / "Cargo.toml").read_text())
    assert (
        cargo["dependencies"]["matrix-sdk"]["version"]
        == cargo["dev-dependencies"]["matrix-sdk-crypto"]
    )


def test_compiler_and_docker_builder_major_minor_match():
    version = tomllib.loads((ROOT / "rust-toolchain.toml").read_text())["toolchain"]["channel"]
    image_version = re.search(
        r"^FROM rust:([\d.]+)-", (ROOT / "Dockerfile").read_text(), re.MULTILINE
    )[1]
    assert version.split(".")[:2] == image_version.split(".")[:2]


def test_minimum_ha_ci_image_matches_declared_support():
    minimum = json.loads((ROOT / "hacs.json").read_text())["homeassistant"]
    workflow = yaml.load((ROOT / ".github/workflows/ci.yaml").read_text(), Loader=yaml.BaseLoader)
    step = next(
        step
        for step in workflow["jobs"]["home-assistant"]["steps"]
        if step.get("if") == "matrix.target == 'minimum'"
    )
    assert step["env"]["HA_TEST_IMAGE"] == f"ghcr.io/home-assistant/home-assistant:{minimum}"


def test_english_translation_matches_strings():
    base = ROOT / "custom_components/matrix_ng"
    assert json.loads((base / "strings.json").read_text()) == json.loads(
        (base / "translations/en.json").read_text()
    )


def test_managed_ha_test_image_is_not_hidden_in_renovates_ignored_test_directory():
    dockerfile = ROOT / ".github/Dockerfile.tests"
    assert dockerfile.is_file()
    assert (
        "ARG HOME_ASSISTANT_IMAGE=ghcr.io/home-assistant/home-assistant:" in dockerfile.read_text()
    )
    assert ".github/Dockerfile.tests" in (ROOT / "scripts/test-python.sh").read_text()


def test_runtime_requirements_are_pinned_and_not_duplicated_in_test_requirements():
    manifest = json.loads((ROOT / "custom_components/matrix_ng/manifest.json").read_text())
    runtime = [Requirement(item) for item in manifest["requirements"]]
    tests = [
        Requirement(line)
        for line in (ROOT / "requirements-test.txt").read_text().splitlines()
        if line and not line.startswith("#")
    ]
    for requirement in runtime + tests:
        assert re.fullmatch(r"==[\w.]+", str(requirement.specifier))
    assert {item.name.lower() for item in runtime}.isdisjoint({item.name.lower() for item in tests})


def test_renovate_handles_every_dependency_source_and_does_not_skip_tests():
    config = json.loads((ROOT / "renovate.json").read_text())
    assert set(config["enabledManagers"]) >= {
        "cargo",
        "dockerfile",
        "github-actions",
        "homeassistant-manifest",
        "pip_requirements",
        "rust-toolchain",
    }
    assert config["ignoreTests"] is False
    assert config["platformAutomerge"] is False
    assert config["lockFileMaintenance"]["enabled"] is True
    assert config["vulnerabilityAlerts"]["automerge"] is False


def test_breaking_updates_do_not_automerge():
    rules = json.loads((ROOT / "renovate.json").read_text())["packageRules"]
    assert any(
        rule.get("matchUpdateTypes") == ["major"] and rule.get("automerge") is False
        for rule in rules
    )
    assert any(
        rule.get("matchCurrentVersion") == "<1.0.0"
        and rule.get("matchUpdateTypes") == ["minor"]
        and rule.get("automerge") is False
        for rule in rules
    )


def test_required_ci_gate_includes_every_job_and_does_not_allow_failure():
    workflow = yaml.load((ROOT / ".github/workflows/ci.yaml").read_text(), Loader=yaml.BaseLoader)
    jobs = workflow["jobs"]
    assert set(jobs["required"]["needs"]) == set(jobs) - {"required"}
    assert jobs["required"]["name"] == "CI required"
    assert jobs["required"]["if"] == "always()"
    for job in jobs.values():
        assert "continue-on-error" not in job
    assert "schedule" in workflow["on"]
    assert "pull_request_target" not in workflow["on"]
    assert workflow["permissions"] == {"contents": "read"}


@pytest.mark.parametrize(
    "path",
    [
        "Cargo.toml",
        "custom_components/matrix_ng/manifest.json",
        "config.example.json",
        "renovate.json",
        "hacs.json",
    ],
)
def test_metadata_is_parseable(path):
    text = (ROOT / path).read_text()
    if path.endswith(".toml"):
        tomllib.loads(text)
    else:
        json.loads(text)
