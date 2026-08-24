#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from translator.version import __version__
from translator.web.app import create_app


def check_versions(root: Path = ROOT) -> dict[str, object]:
    frontend = json.loads((root / "frontend" / "package.json").read_text(encoding="utf-8"))["version"]
    lock = json.loads((root / "frontend" / "package-lock.json").read_text(encoding="utf-8"))
    lock_root = lock["packages"][""]["version"]
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    heading = re.search(r"^## \[([^]]+)]", changelog, re.MULTILINE)
    changelog_version = heading.group(1) if heading else None
    readme = (root / "README.md").read_text(encoding="utf-8")
    badge = re.search(r"version-([0-9]+\.[0-9]+\.[0-9]+)-blue", readme)
    readme_version = badge.group(1) if badge else None
    app = create_app(static_dir=root / ".missing-dist-for-version-check")
    health_route = next(route for route in app.routes if getattr(route, "path", None) == "/health")
    health_version = health_route.endpoint()["version"]
    values = {
        "source": __version__,
        "frontend": frontend,
        "frontend_lock": lock_root,
        "openapi": app.version,
        "health": health_version,
        "changelog": changelog_version,
        "readme": readme_version,
    }
    return {"status": "ok" if len(set(values.values())) == 1 else "error", "versions": values}


def main() -> int:
    report = check_versions()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
