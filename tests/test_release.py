"""Release versions, manifests and assets are consistent and safe to retry."""

import hashlib
import importlib.util
import json
import shutil
import zipfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release_tools", ROOT / "scripts/release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
SHA = "a" * 40


def test_release_plan_first_release_auto_patch_and_explicit_minor():
    assert release.plan_version("0.3.0", [], SHA)["version"] == "0.3.0"
    assert release.plan_version("0.3.0", [{"tag_name": "v0.3.2"}], SHA)["version"] == "0.3.3"
    assert release.plan_version("0.4.0", [{"tag_name": "v0.3.2"}], SHA)["version"] == "0.4.0"


def test_completed_release_is_not_published_twice_and_draft_is_retryable():
    data = {"tag_name": "v0.3.1", "body": f"Source commit: {SHA}"}
    assert release.plan_version("0.3.0", [data], SHA)["should_release"] is False
    assert release.plan_version("0.3.0", [{**data, "draft": True}], SHA) == {
        "should_release": True,
        "version": "0.3.1",
    }
    assert (
        release.plan_version("0.3.0", [{**data, "draft": True, "body": "other source"}], SHA)[
            "version"
        ]
        == "0.3.2"
    )


@pytest.mark.parametrize("value", ["latest", "0.3", "0.3.0;echo bad", "v0.3.0", "00.3.0"])
def test_versions_cannot_inject_shell_or_use_mutable_tags(value):
    with pytest.raises(ValueError):
        release.semver(value)


def test_version_stamp_and_integration_archive(tmp_path):
    for name in (
        "VERSION",
        "Cargo.toml",
        "Cargo.lock",
        "Dockerfile",
        "custom_components/matrix_ng/manifest.json",
        "apps/matrix_ng_bridge/config.yaml",
    ):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    release.stamp(tmp_path, "0.3.42")
    assert (tmp_path / "VERSION").read_text().strip() == "0.3.42"
    assert (
        json.loads((tmp_path / "custom_components/matrix_ng/manifest.json").read_text())["version"]
        == "0.3.42"
    )
    assert (
        yaml.safe_load((tmp_path / "apps/matrix_ng_bridge/config.yaml").read_text())["version"]
        == "0.3.42"
    )
    release.make_assets(tmp_path, tmp_path / "dist")
    with zipfile.ZipFile(tmp_path / "dist/matrix_ng.zip") as archive:
        assert "manifest.json" in archive.namelist()
        assert json.loads(archive.read("manifest.json"))["version"] == "0.3.42"
    assert (
        (tmp_path / "dist/SHA256SUMS")
        .read_text()
        .startswith(hashlib.sha256((tmp_path / "dist/matrix_ng.zip").read_bytes()).hexdigest())
    )


def test_release_workflow_requires_successful_same_repo_main_push_ci():
    workflow = yaml.load(
        (ROOT / ".github/workflows/release.yaml").read_text(), Loader=yaml.BaseLoader
    )
    gate = workflow["jobs"]["prepare"]["if"]
    assert "conclusion == 'success'" in gate
    assert "event == 'schedule'" in gate
    assert "event == 'workflow_dispatch'" in gate
    assert "default_branch" in gate
    assert "head_repository.full_name == github.repository" in gate
    assert workflow["permissions"] == {"contents": "read"}
    assert "pull_request_target" not in workflow["on"]
    matrix = workflow["jobs"]["images"]["strategy"]["matrix"]["include"]
    assert {item["arch"] for item in matrix} == {"amd64", "aarch64"}
    assert any(item["runner"] == "ubuntu-24.04-arm" for item in matrix)


def test_release_is_skipped_when_only_docs_tests_or_workflow_changed():
    prior = {"tag_name": "v0.3.0", "body": f"Source commit: {SHA}"}
    other_sha = "b" * 40
    skip_files = ["README.md", "tests/test_api.py", ".github/workflows/ci.yaml"]
    assert release.plan_release("0.3.0", [prior], other_sha, skip_files)["should_release"] is False
    assert (
        release.plan_release("0.3.0", [prior], other_sha, ["src/main.rs"])["should_release"] is True
    )


def test_ci_schedule_is_monthly_not_weekly():
    workflow = yaml.load((ROOT / ".github/workflows/ci.yaml").read_text(), Loader=yaml.BaseLoader)
    assert workflow["on"]["schedule"][0]["cron"] == "0 6 1 * *"


def test_source_versions_are_consistent():
    import tomllib

    version = (ROOT / "VERSION").read_text().strip()
    assert tomllib.loads((ROOT / "Cargo.toml").read_text())["package"]["version"] == version
    assert (
        json.loads((ROOT / "custom_components/matrix_ng/manifest.json").read_text())["version"]
        == version
    )
    assert (
        yaml.safe_load((ROOT / "apps/matrix_ng_bridge/config.yaml").read_text())["version"]
        == version
    )
