from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.novel_translator_tool import NOVEL_TRANSLATOR_ROOT, call_novel_translator
from scripts.codex_review import run_codex_review
from scripts.book_pipeline import approved_fixes


ARTIFACTS = ROOT / "artifacts"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Review Novel Translator output with Codex CLI")
    parser.add_argument("--book", required=True)
    parser.add_argument("--mode", choices=["risk", "all"], default="risk")
    parser.add_argument("--apply", action="store_true", help="apply only high-confidence mechanical fixes")
    parser.add_argument("--chunk-size", type=int, default=30)
    return parser.parse_args()


def load_book_manifest(book: str) -> tuple[Path, dict[str, Any]]:
    manifest = NOVEL_TRANSLATOR_ROOT / "data" / "books" / book / "manifest.json"
    if not manifest.exists():
        raise FileNotFoundError(f"Novel Translator manifest not found: {manifest}")
    return manifest, json.loads(manifest.read_text(encoding="utf-8"))


def make_items(manifest: dict[str, Any], mode: str) -> list[dict[str, Any]]:
    paragraphs = [p for chapter in manifest.get("chapters", []) for p in chapter.get("paragraphs", [])]
    translated = [p for p in paragraphs if str(p.get("translated", "")).strip()]
    if mode == "all":
        selected = translated
    else:
        selected = [p for p in translated if any(char in str(p.get("translated", "")) for char in "くけこさしすせそたちつてと")]
    return [
        {
            "id": p["id"],
            "source": p.get("source", ""),
            "translated": p.get("translated", ""),
        }
        for p in selected
    ]


def main() -> int:
    args = parse_args()
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    snapshot = call_novel_translator("snapshot", "--book", args.book, "--name", "before-auto-review")
    quality = call_novel_translator("quality-report", "--book", args.book)
    _, manifest = load_book_manifest(args.book)
    items = make_items(manifest, args.mode)
    print(json.dumps({"snapshot": snapshot["summary"], "quality": quality["summary"], "review_items": len(items)}, ensure_ascii=False))
    if not items:
        return 0

    reviews: list[dict[str, Any]] = []
    for start in range(0, len(items), args.chunk_size):
        chunk = items[start : start + args.chunk_size]
        input_path = ARTIFACTS / f"review-input-{start:05d}.json"
        output_path = ARTIFACTS / f"review-output-{start:05d}.json"
        input_path.write_text(json.dumps({"book": args.book, "items": chunk}, ensure_ascii=False, indent=2), encoding="utf-8")
        run_codex_review(input_path, output_path)
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        reviews.extend(payload.get("items", []))

    review_path = ARTIFACTS / "review-results.json"
    review_path.write_text(json.dumps({"book": args.book, "items": reviews}, ensure_ascii=False, indent=2), encoding="utf-8")
    fixes = approved_fixes(reviews)
    fix_path = ARTIFACTS / "approved-fixes.json"
    fix_path.write_text(json.dumps({"book": args.book, "items": fixes}, ensure_ascii=False, indent=2), encoding="utf-8")
    result: dict[str, Any] = {"review": str(review_path), "candidate_fixes": len(fixes), "applied": False}
    if args.apply and fixes:
        applied = call_novel_translator("apply-review-fixes", "--book", args.book, "--input", str(fix_path))
        result["applied"] = applied["summary"]
        result["quality_after"] = call_novel_translator("quality-report", "--book", args.book)["summary"]
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
