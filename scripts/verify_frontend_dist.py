#!/usr/bin/env python3
from __future__ import annotations

import argparse
from html.parser import HTMLParser
import json
import mimetypes
from pathlib import Path
from urllib.parse import urlparse


class AssetParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.references: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag in {"script", "img", "source"} and values.get("src"):
            self.references.append(str(values["src"]))
        if tag == "link" and values.get("href") and values.get("rel") not in {"preconnect", "dns-prefetch"}:
            self.references.append(str(values["href"]))


def verify_dist(dist: Path) -> dict[str, object]:
    dist = dist.expanduser().resolve()
    index = dist / "index.html"
    errors: list[str] = []
    assets: list[dict[str, object]] = []
    if not index.is_file() or index.stat().st_size == 0:
        return {"status": "error", "dist": str(dist), "errors": ["index.html missing or empty"], "assets": []}
    parser = AssetParser()
    parser.feed(index.read_text(encoding="utf-8"))
    for reference in parser.references:
        parsed = urlparse(reference)
        if parsed.scheme or parsed.netloc or reference.startswith(("data:", "#")):
            continue
        target = (dist / parsed.path.lstrip("/")).resolve()
        if target != dist and dist not in target.parents:
            errors.append(f"asset escapes dist: {reference}")
            continue
        expected_mime, _ = mimetypes.guess_type(target.name)
        entry = {"reference": reference, "path": str(target), "mime": expected_mime}
        if not target.is_file():
            errors.append(f"missing asset: {reference}")
        elif target.stat().st_size == 0:
            errors.append(f"empty asset: {reference}")
        elif target.suffix in {".js", ".mjs"} and expected_mime not in {"text/javascript", "application/javascript"}:
            errors.append(f"unexpected JavaScript MIME: {reference} -> {expected_mime}")
        elif target.suffix == ".css" and expected_mime != "text/css":
            errors.append(f"unexpected CSS MIME: {reference} -> {expected_mime}")
        assets.append(entry)
    return {"status": "ok" if not errors else "error", "dist": str(dist), "errors": errors, "assets": assets}


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify every local asset referenced by frontend/dist/index.html")
    parser.add_argument("--dist", type=Path, default=Path(__file__).resolve().parents[1] / "frontend" / "dist")
    args = parser.parse_args()
    report = verify_dist(args.dist)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
