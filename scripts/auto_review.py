from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.novel_translator_tool import NOVEL_TRANSLATOR_ROOT, call_novel_translator


ARTIFACTS = ROOT / "artifacts"
SCHEMA = ROOT / "schemas" / "review-output.schema.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Review Novel Translator output with Codex CLI")
    parser.add_argument("--book", required=True)
    parser.add_argument("--mode", choices=["risk", "all"], default="risk")
    parser.add_argument("--apply", action="store_true", help="apply only high-confidence mechanical fixes")
    parser.add_argument("--chunk-size", type=int, default=30)
    return parser.parse_args()


def run_codex(input_path: Path, output_path: Path) -> None:
    model = os.environ.get("CODEX_MODEL", "gpt-5.6-sol")
    effort = os.environ.get("CODEX_REASONING_EFFORT", "low")
    prompt = f"""
审校 Novel Translator 的译文分片。
工作目录：{ROOT}
输入 JSON：{input_path}
请读取该文件，逐条对照 source 和 translated，检查：漏译、误译、重复、错别字、术语、人名、称谓、人称、标点、日文残留和明显中文病句。

规则：
- 只修改译文，不总结或改写剧情。
- source 是事实基准；不要凭空添加信息。
- 只有机械性、明显且置信度 >= 0.9 的修复才设置 auto_apply=true。
- 涉及语义取舍、风格偏好、成人描写措辞、人物动机或不确定的改写，设置 auto_apply=false。
- approved_translation 在 auto_apply=false 时保持空字符串。
- 严格输出符合 {SCHEMA} 的 JSON，不要 Markdown。
""".strip()
    command = [
        "codex",
        "exec",
        "--ephemeral",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--model",
        model,
        "-c",
        f'model_reasoning_effort="{effort}"',
        "--output-schema",
        str(SCHEMA),
        "-o",
        str(output_path),
        "-C",
        str(ROOT),
        prompt,
    ]
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"Codex review failed ({result.returncode}):\n{result.stderr}\n{result.stdout}")


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
        run_codex(input_path, output_path)
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        reviews.extend(payload.get("items", []))

    review_path = ARTIFACTS / "review-results.json"
    review_path.write_text(json.dumps({"book": args.book, "items": reviews}, ensure_ascii=False, indent=2), encoding="utf-8")
    fixes = [
        item for item in reviews
        if item.get("auto_apply") is True
        and float(item.get("confidence", 0)) >= 0.9
        and str(item.get("approved_translation", "")).strip()
    ]
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
