"""Deterministic versioning/assets for CI-gated releases. No external packages."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path


def semver(value: str) -> tuple[int, int, int]:
    if not re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", value):
        raise ValueError("Version must be a stable three-part semantic version")
    return tuple(int(part) for part in value.split("."))


def plan_version(base: str, releases: list[dict], source_sha: str) -> dict:
    base_version = semver(base)
    if not re.fullmatch(r"[a-f0-9]{40}", source_sha):
        raise ValueError("Invalid source commit")
    published = [
        release
        for release in releases
        if not release.get("draft") and not release.get("prerelease")
    ]
    if any(f"Source commit: {source_sha}" in (release.get("body") or "") for release in published):
        return {"should_release": False, "version": base}
    for release in releases:
        if release.get("draft") and f"Source commit: {source_sha}" in (release.get("body") or ""):
            version = release["tag_name"].removeprefix("v")
            semver(version)
            return {"should_release": True, "version": version}
    versions = []
    # Reserve draft/orphan-tag versions too: never reuse a version for different code.
    for release in releases:
        if release.get("prerelease"):
            continue
        try:
            versions.append(semver(release["tag_name"].removeprefix("v")))
        except (KeyError, ValueError):
            continue
    latest = max(versions, default=(-1, -1, -1))
    version = base_version if base_version > latest else (latest[0], latest[1], latest[2] + 1)
    return {"should_release": True, "version": ".".join(map(str, version))}


def replace(path: Path, pattern: str, replacement: str, expected: int | None = None) -> None:
    text, count = re.subn(pattern, replacement, path.read_text(), flags=re.MULTILINE)
    if not count or (expected is not None and count != expected):
        raise ValueError(f"Unexpected version layout: {path}")
    path.write_text(text)


def stamp(root: Path, version: str) -> None:
    semver(version)
    (root / "VERSION").write_text(version + "\n")
    replace(root / "Cargo.toml", r'^version = "[^\"]+"$', f'version = "{version}"', 1)
    replace(
        root / "Cargo.lock",
        r'(\[\[package\]\]\nname = "matrix-ng-bridge"\nversion = ")[^\"]+("(?:\n|$))',
        rf"\g<1>{version}\g<2>",
        1,
    )
    manifest_path = root / "custom_components/matrix_ng/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["version"] = version
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    replace(
        root / "apps/matrix_ng_bridge/config.yaml",
        r'^version: "[^\"]+"$',
        f'version: "{version}"',
        1,
    )
    replace(root / "Dockerfile", r"^ARG BUILD_VERSION=[^\n]+$", f"ARG BUILD_VERSION={version}", 2)


def make_assets(root: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    integration = root / "custom_components/matrix_ng"
    with zipfile.ZipFile(
        output / "matrix_ng.zip", "w", compression=zipfile.ZIP_DEFLATED
    ) as archive:
        for path in sorted(integration.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                archive.write(path, path.relative_to(integration))
    digest = hashlib.sha256((output / "matrix_ng.zip").read_bytes()).hexdigest()
    (output / "SHA256SUMS").write_text(f"{digest}  matrix_ng.zip\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    stamp_parser = commands.add_parser("stamp")
    stamp_parser.add_argument("version")
    stamp_parser.add_argument("--root", type=Path, default=Path.cwd())
    asset_parser = commands.add_parser("assets")
    asset_parser.add_argument("--root", type=Path, default=Path.cwd())
    asset_parser.add_argument("--output", type=Path, default=Path("dist"))
    plan_parser = commands.add_parser("plan")
    plan_parser.add_argument("--base", required=True)
    plan_parser.add_argument("--releases", type=Path, required=True)
    plan_parser.add_argument("--source-sha", required=True)
    args = parser.parse_args()
    if args.command == "stamp":
        stamp(args.root, args.version)
    elif args.command == "assets":
        make_assets(args.root, args.output)
    else:
        print(
            json.dumps(
                plan_version(args.base, json.loads(args.releases.read_text()), args.source_sha)
            )
        )


if __name__ == "__main__":
    main()
