#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile

from verify_frontend_dist import verify_dist
from translator.version import __version__


ROOT = Path(__file__).resolve().parents[1]
RELEASE_ENTRIES = [
    "translator", "scripts", "schemas", "docs",
    "README.md", "CHANGELOG.md", "LICENSE", "pyproject.toml", "config.toml.example", ".env.example",
]


def copy_release_tree(package_root: Path) -> None:
    """Copy tracked release inputs plus the separately verified frontend build."""
    tracked = subprocess.run(
        ["git", "ls-files", "-z", "--", *RELEASE_ENTRIES],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout.decode().split("\0")
    for relative in (item for item in tracked if item):
        source = ROOT / relative
        destination = package_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    shutil.copytree(ROOT / "frontend" / "dist", package_root / "frontend" / "dist")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a source release archive containing a verified frontend/dist")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "release")
    parser.add_argument("--skip-frontend-build", action="store_true")
    args = parser.parse_args()
    if not args.skip_frontend_build:
        subprocess.run(["npm", "ci"], cwd=ROOT / "frontend", check=True, timeout=300)
        subprocess.run(["npm", "run", "build"], cwd=ROOT / "frontend", check=True, timeout=300)
    report = verify_dist(ROOT / "frontend" / "dist")
    if report["status"] != "ok":
        raise RuntimeError(f"frontend dist verification failed: {report['errors']}")
    version = __version__
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        package_root = Path(temporary) / f"novel-translator-pipeline-{version}"
        package_root.mkdir()
        copy_release_tree(package_root)
        archive_base = args.output_dir / package_root.name
        shutil.make_archive(str(archive_base), "gztar", root_dir=package_root.parent, base_dir=package_root.name)
        shutil.make_archive(str(archive_base), "zip", root_dir=package_root.parent, base_dir=package_root.name)
    print(Path(f"{archive_base}.tar.gz"))
    print(Path(f"{archive_base}.zip"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
