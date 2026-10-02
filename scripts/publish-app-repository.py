"""Publish only installable app metadata to the generated apps branch, never main."""

import os
import subprocess
import tempfile
from pathlib import Path


def files(root: Path) -> dict[str, bytes]:
    output = {"repository.yaml": (root / "repository.yaml").read_bytes()}
    source = root / "apps/matrix_ng_bridge"
    for path in sorted(source.rglob("*")):
        if path.is_file() and path.suffix in {".yaml", ".md", ".png"}:
            output[f"matrix_ng_bridge/{path.relative_to(source)}"] = path.read_bytes()
    return output


def main() -> None:
    root = Path.cwd()
    version = (root / "VERSION").read_text().strip()
    source = os.environ["SOURCE_SHA"]
    previous = subprocess.check_output(
        ["git", "ls-remote", "origin", "refs/heads/apps"], text=True
    ).split()
    parent = []
    if previous:
        subprocess.run(["git", "fetch", "origin", "apps"], check=True)
        parent = ["-p", previous[0]]
    with tempfile.TemporaryDirectory() as directory:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(directory) / "index")}
        subprocess.run(["git", "read-tree", "--empty"], env=env, check=True)
        for name, content in files(root).items():
            blob = (
                subprocess.check_output(["git", "hash-object", "-w", "--stdin"], input=content)
                .decode()
                .strip()
            )
            subprocess.run(
                ["git", "update-index", "--add", "--cacheinfo", f"100644,{blob},{name}"],
                env=env,
                check=True,
            )
        tree = subprocess.check_output(["git", "write-tree"], env=env, text=True).strip()
        commit = subprocess.check_output(
            ["git", "commit-tree", tree, *parent],
            input=f"Publish Matrix NG {version}\n\nSource commit: {source}\n",
            text=True,
        ).strip()
        # Fast-forward only. Preserve history and fail instead of overwriting a racing update.
        subprocess.run(["git", "push", "origin", f"{commit}:refs/heads/apps"], check=True)


if __name__ == "__main__":
    main()
