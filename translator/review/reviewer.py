from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any

from translator.core.config import load_config, setting
from translator.core.novel_tool import call_novel_translator
from translator.core.workspace import (
    BookWorkspace,
    empty_book_memory,
    merge_chapter_state,
    merge_memory_delta,
    merge_term_updates,
    novel_translator_terms,
    read_json,
    utc_now,
    write_json,
)
from translator.providers.opencode import check as check_opencode, parse_json_object, run_prompt


ROOT = Path(__file__).resolve().parents[2]
CHAPTER_SCHEMA = ROOT / "schemas" / "chapter-review-output.schema.json"
GLOBAL_SCHEMA = ROOT / "schemas" / "global-consistency-output.schema.json"

OBJECTIVE_CATEGORIES = {
    "mistranslation",
    "subject_object",
    "pronoun_reference",
    "omission",
    "addition",
    "terminology",
    "factual_conflict",
    "context_conflict",
    "policy_violation",
}
OBJECTIVE_SEVERITIES = {"critical", "major"}


def manifest_path(book: str) -> Path:
    from translator.core.novel_tool import NOVEL_TRANSLATOR_ROOT
    return NOVEL_TRANSLATOR_ROOT / "data" / "books" / book / "manifest.json"


def paragraph_map(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(paragraph["id"]): paragraph
        for chapter in manifest.get("chapters", [])
        for paragraph in chapter.get("paragraphs", [])
        if isinstance(paragraph, dict) and paragraph.get("id")
    }


def approved_fixes(items: list[dict[str, Any]], threshold: float = 0.9, *, autonomous: bool = False) -> list[dict[str, Any]]:
    approved: list[dict[str, Any]] = []
    for raw in items:
        item = dict(raw)
        replacement = str(item.get("replacement", "") or item.get("approved_translation", "")).strip()
        is_new_contract = "category" in item or "replacement" in item
        if is_new_contract and (
            str(item.get("category", "")) not in OBJECTIVE_CATEGORIES
            or str(item.get("severity", "")) not in OBJECTIVE_SEVERITIES
        ):
            continue
        if (autonomous or item.get("auto_apply") is True) and float(item.get("confidence", 0) or 0) >= threshold and replacement:
            item["approved_translation"] = replacement
            item["replacement"] = replacement
            approved.append(item)
    return approved


def missing_checked_ids(payload: dict[str, Any], expected_ids: set[str]) -> set[str]:
    checked = payload.get("checked_ids", []) if isinstance(payload, dict) else []
    return expected_ids - {str(item) for item in checked}


def validate_chapter_review_payload(payload: dict[str, Any], expected_ids: set[str]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("章节审阅结果必须是 JSON 对象")
    checked = payload.get("checked_ids")
    fixes = payload.get("fixes")
    glossary_delta = payload.get("glossary_delta")
    memory_delta = payload.get("memory_delta")
    chapter_state = payload.get("chapter_state")
    if not isinstance(checked, list) or not isinstance(fixes, list):
        raise ValueError("章节审阅结果必须包含 checked_ids 和 fixes 数组")
    if not isinstance(glossary_delta, dict) or not isinstance(memory_delta, dict) or not isinstance(chapter_state, dict):
        raise ValueError("章节审阅结果必须包含 glossary_delta、memory_delta 和 chapter_state 对象")
    checked_ids = [str(item) for item in checked]
    received = set(checked_ids)
    unknown = sorted(received - expected_ids)
    missing = sorted(expected_ids - received)
    duplicate_ids = sorted({item for item in checked_ids if checked_ids.count(item) > 1})
    fix_ids = {str(item.get("id", "")) for item in fixes if isinstance(item, dict)}
    unknown_fixes = sorted(fix_ids - expected_ids)
    details: list[str] = []
    if unknown:
        details.append(f"checked_ids 未知 ID：{', '.join(unknown)}")
    if missing:
        details.append(f"checked_ids 缺少 ID：{', '.join(missing)}")
    if duplicate_ids:
        details.append(f"checked_ids 重复 ID：{', '.join(duplicate_ids)}")
    if unknown_fixes:
        details.append(f"fixes 未知 ID：{', '.join(unknown_fixes)}")
    if details:
        raise ValueError("章节审阅结果段落不匹配；" + "；".join(details))
    return payload


def validate_global_consistency_payload(payload: dict[str, Any], expected_chapter_ids: set[str]) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("checked_chapters"), list):
        raise ValueError("全书一致性结果必须包含 checked_chapters 数组")
    checked = [str(item) for item in payload["checked_chapters"]]
    if len(checked) != len(set(checked)):
        raise ValueError("全书一致性结果包含重复章节 ID")
    unknown = sorted(set(checked) - expected_chapter_ids)
    missing = sorted(expected_chapter_ids - set(checked))
    if unknown or missing:
        details = []
        if unknown:
            details.append(f"未知章节 ID：{', '.join(unknown)}")
        if missing:
            details.append(f"缺少章节 ID：{', '.join(missing)}")
        raise ValueError("全书一致性结果覆盖范围不匹配；" + "；".join(details))
    if not isinstance(payload.get("conflicts"), list) or not isinstance(payload.get("recommendations"), list):
        raise ValueError("全书一致性结果必须包含 conflicts 和 recommendations 数组")
    return payload


def verify_applied_fixes(manifest: dict[str, Any], fixes: list[dict[str, Any]]) -> None:
    paragraphs = paragraph_map(manifest)
    mismatches: list[str] = []
    for fix in fixes:
        item_id = str(fix.get("id", ""))
        expected = str(fix.get("approved_translation", "") or fix.get("replacement", "")).strip()
        actual = str(paragraphs.get(item_id, {}).get("translated", "")).strip()
        if not item_id or not expected or actual != expected:
            mismatches.append(item_id or "<empty>")
    if mismatches:
        raise ValueError(f"应用修复后 manifest 未验证通过：{', '.join(mismatches)}")


def _selected_backend(backend: str | None = None) -> str:
    config = load_config()
    return (backend or setting(config, "roles.reviewer", "REVIEWER")).strip().casefold()


def _codex_model_effort() -> tuple[str, str]:
    config = load_config()
    return (
        setting(config, "providers.codex.model", "CODEX_MODEL"),
        setting(config, "providers.codex.reasoning_effort", "CODEX_REASONING_EFFORT"),
    )


def _codex_binary() -> str:
    return str(setting(load_config(), "providers.codex.binary", "CODEX_BIN"))


def check_reviewer(timeout: int = 60, *, backend: str | None = None) -> dict[str, Any]:
    selected = _selected_backend(backend)
    if selected == "opencode":
        return check_opencode(timeout=timeout, role="reviewer")
    if selected != "codex":
        return {"name": "reviewer", "status": "error", "error": f"unknown reviewer backend: {selected}"}
    return _check_codex_reviewer(timeout=timeout)


def _check_codex_reviewer(timeout: int = 60) -> dict[str, Any]:
    executable = shutil.which(_codex_binary())
    if not executable:
        return {"name": "reviewer", "status": "error", "error": "codex executable not found in PATH"}
    model, effort = _codex_model_effort()
    schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
        "additionalProperties": False,
    }
    try:
        with tempfile.TemporaryDirectory(prefix="reviewer-health-") as temporary:
            root = Path(temporary)
            schema_path = root / "schema.json"
            output_path = root / "result.json"
            schema_path.write_text(json.dumps(schema), encoding="utf-8")
            command = [
                executable,
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
                str(schema_path),
                "-o",
                str(output_path),
                "-C",
                str(ROOT),
                'Return exactly {"ok":true}. Do not include any other fields or text.',
            ]
            result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=timeout, check=False)
            if result.returncode != 0:
                return {
                    "name": "reviewer",
                    "status": "error",
                    "error": f"codex exited {result.returncode}: {(result.stderr or result.stdout)[-600:]}",
                }
            try:
                payload = json.loads(output_path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError) as exc:
                return {"name": "reviewer", "status": "error", "error": f"invalid health response: {exc}"}
            if not isinstance(payload, dict) or payload.get("ok") is not True:
                return {"name": "reviewer", "status": "error", "error": f"unexpected health response: {payload!r}"}
    except subprocess.TimeoutExpired:
        return {"name": "reviewer", "status": "error", "error": f"codex health check timed out after {timeout}s"}
    except OSError as exc:
        return {"name": "reviewer", "status": "error", "error": str(exc)}
    return {"name": "reviewer", "status": "ok", "model": model}


def _run_codex_chapter_review(input_path: Path, output_path: Path, autonomous: bool = False) -> None:
    model, effort = _codex_model_effort()
    prompt = f"""
你是日译中小说审阅者。对输入 JSON 中的整章译文做章节级一致性审阅。
输入 JSON：{input_path}

- 只报告会导致读者误解原文的实质错误，不做文学润色。
- 不报告纯风格偏好、轻微措辞差异、可接受的自然化、标点偏好或普通敬称差异。
- 必须检查 items 中的每个段落，并把全部 ID 且不重复地写入 checked_ids。
- 重点检查人物身份和关系、主客体、代词指代、漏译、擅自添加、术语固定译法、事实冲突、时间顺序、跨段落动作关系和明显改变的强度。
- 当无法确定问题是否改变原意时，不要输出 fix。

- fixes 只输出确实存在且属于 critical 或 major 的问题，category 只能使用 Schema 中的枚举。
- replacement 必须是完整段落译文，而不是局部片段；不确定时为空字符串且 auto_apply=false。
- {"全自动模式下，客观问题且置信度 >= 0.9 的 fix 设置 auto_apply=true。" if autonomous else "语义取舍或不确定改写设置 auto_apply=false。"}
- glossary_delta 只收录后文仍有价值的人名、别名、组织、地点、特殊术语和固定称谓，不收录普通词或一次性短语。
- memory_delta 只收录会影响后续章节翻译的人物、关系、别名、重要事实和持续状态。
- chapter_state 只保存本章摘要和会影响后续理解的重要变化。
- 如果没有问题，fixes、glossary_delta 和 memory_delta 都返回空数组。
严格输出符合 {CHAPTER_SCHEMA} 的 JSON，不要 Markdown。
""".strip()
    command = [
        _codex_binary(), "exec", "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only",
        "--model", model, "-c", f'model_reasoning_effort="{effort}"',
        "--output-schema", str(CHAPTER_SCHEMA), "-o", str(output_path), "-C", str(ROOT), prompt,
    ]
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"Codex chapter review failed ({result.returncode}):\n{result.stderr}\n{result.stdout}")


def _run_codex_global_consistency_review(input_path: Path, output_path: Path) -> None:
    model, effort = _codex_model_effort()
    prompt = f"""
对输入 JSON 中的全书状态做一次轻量一致性审阅。
输入 JSON：{input_path}

- 必须把输入中的每个 chapter_id 写入 checked_chapters，且不得重复或添加未知章节。
- 只检查 glossary、book_memory、章节摘要之间的事实、人物关系、时间线和术语冲突。
- 不重新审阅全文，不做文学润色，不因为不同章节的正常措辞差异而报告问题。
- conflicts 只输出有证据的冲突；recommendations 只给出后续人工或定向章节复核建议。
严格输出符合 {GLOBAL_SCHEMA} 的 JSON，不要 Markdown。
""".strip()
    command = [
        _codex_binary(), "exec", "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only",
        "--model", model, "-c", f'model_reasoning_effort="{effort}"',
        "--output-schema", str(GLOBAL_SCHEMA), "-o", str(output_path), "-C", str(ROOT), prompt,
    ]
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"Codex global consistency review failed ({result.returncode}):\n{result.stderr}\n{result.stdout}")


def _opencode_review_prompt(kind: str, input_payload: dict[str, Any], schema_path: Path, autonomous: bool) -> str:
    if kind == "chapter":
        instructions = """
这是章节级一致性审阅。
- 只报告会导致读者误解原文的实质错误，不做文学润色。
- 不报告纯风格偏好、轻微措辞差异、可接受的自然化、标点偏好或普通敬称差异。
- 必须检查 items 中的每个段落，并把全部 ID 且不重复地写入 checked_ids。
- 重点检查人物身份和关系、主客体、代词指代、漏译、擅自添加、术语固定译法、事实冲突、时间顺序、跨段落动作关系和明显改变的强度。
- 当无法确定问题是否改变原意时，不要输出 fix。
- fixes 只输出确实存在且属于 critical 或 major 的问题；replacement 必须是完整段落译文。
- glossary_delta 只收录后文仍有价值的人名、别名、组织、地点、特殊术语和固定称谓。
- memory_delta 只收录会影响后续章节翻译的人物、关系、别名、重要事实和持续状态。
- chapter_state 只保存本章摘要和会影响后续理解的重要变化。
""".strip()
    elif kind == "global":
        instructions = """
这是全书状态的一致性审阅。
- 必须把输入中的每个 chapter_id 写入 checked_chapters，且不得重复或添加未知章节。
- 只检查 glossary、book_memory、章节摘要之间的事实、人物关系、时间线和术语冲突。
- 不重新审阅全文，不做文学润色，不因为不同章节的正常措辞差异而报告问题。
- conflicts 只输出有证据的冲突；recommendations 只给出后续人工或定向章节复核建议。
""".strip()
    else:
        raise ValueError(f"未知 OpenCode reviewer 类型：{kind}")
    auto_rule = (
        "全自动模式下，所有置信度 >= 0.9 且有明确修复的项目设置 auto_apply=true。"
        if autonomous
        else "涉及语义取舍、风格偏好或不确定改写时，auto_apply=false 且 replacement 为空。"
    )
    schema = schema_path.read_text(encoding="utf-8")
    return f"""
你是日译中小说译文审阅者。只分析输入 JSON，不修改文件，不调用外部工具。
{instructions}
- {auto_rule}
- glossary 是已有术语表；不得与已有术语冲突。

严格只输出一个 JSON 对象，不要 Markdown、解释、推理或前后缀。
JSON Schema：
{schema}

输入 JSON：
{json.dumps(input_payload, ensure_ascii=False)}
""".strip()


def _run_opencode_review(kind: str, input_path: Path, output_path: Path, autonomous: bool = False) -> None:
    try:
        input_payload = json.loads(input_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"OpenCode reviewer input is invalid: {input_path}: {exc}") from exc
    schema_path = {
        "chapter": CHAPTER_SCHEMA,
        "global": GLOBAL_SCHEMA,
    }[kind]
    try:
        output = run_prompt(
            _opencode_review_prompt(kind, input_payload, schema_path, autonomous),
            role="reviewer",
            timeout=int(setting(load_config(), "providers.opencode.timeout", "OPENCODE_REVIEW_TIMEOUT")),
        )
        payload = parse_json_object(output)
    except (ValueError, RuntimeError) as exc:
        raise RuntimeError(f"OpenCode {kind} review failed: {exc}") from exc
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_chapter_review(input_path: Path, output_path: Path, autonomous: bool = False, *, backend: str | None = None) -> None:
    selected = _selected_backend(backend)
    if selected == "opencode":
        _run_opencode_review("chapter", input_path, output_path, autonomous)
        return
    if selected == "codex":
        _run_codex_chapter_review(input_path, output_path, autonomous)
        return
    raise ValueError(f"未知 reviewer backend：{selected}")


def run_global_consistency_review(input_path: Path, output_path: Path, *, backend: str | None = None) -> None:
    selected = _selected_backend(backend)
    if selected == "opencode":
        _run_opencode_review("global", input_path, output_path)
        return
    if selected == "codex":
        _run_codex_global_consistency_review(input_path, output_path)
        return
    raise ValueError(f"未知 reviewer backend：{selected}")


def review_book(
    *,
    book: str,
    name: str,
    output_root: Path,
    chapter_id: str | None = None,
    global_consistency: bool = False,
    translation_policy: Path | None = None,
    apply: bool = False,
    autonomous: bool = False,
    export: bool = False,
    reviewer: str | None = None,
) -> dict[str, Any]:
    workspace = BookWorkspace.at(output_root, name)
    workspace.initialize(book_id=book)
    manifest = read_json(manifest_path(book))
    all_chapters = manifest.get("chapters", [])
    chapters = all_chapters
    if chapter_id:
        chapters = [c for c in chapters if c.get("id") == chapter_id]
    if not chapters:
        raise ValueError("没有匹配的章节")

    snapshot = call_novel_translator("snapshot", "--book", book, "--name", "before-chapter-consistency")
    write_json(workspace.snapshots_dir / "before-chapter-consistency.json", snapshot)
    glossary = read_json(workspace.glossary_path, {"book": book, "terms": [], "conflicts": []})
    memory = read_json(workspace.book_memory_path, empty_book_memory(book))
    policy_path = translation_policy or ROOT / "docs" / "prompts" / "translation-policy.md"
    policy = policy_path.read_text(encoding="utf-8") if policy_path.exists() else ""
    results = []
    for chapter in chapters:
        items = [
            {"id": p["id"], "source": p.get("source", ""), "translated": p.get("translated", "")}
            for p in chapter.get("paragraphs", [])
            if str(p.get("translated", "")).strip()
        ]
        if not items:
            continue
        c_id = str(chapter["id"])
        input_path = workspace.reviews_dir / f"{c_id}-consistency-input.json"
        output_path = workspace.reviews_dir / f"{c_id}-consistency-output.json"
        previous_state = {}
        index = all_chapters.index(chapter)
        if index > 0:
            previous_id = str(all_chapters[index - 1].get("id", ""))
            previous_state = read_json(workspace.chapter_states_dir / f"{previous_id}.json", {}) or {}
        write_json(input_path, {
            "book": book,
            "chapter_id": c_id,
            "chapter_title": chapter.get("title", ""),
            "translation_policy": policy,
            "book_memory": memory,
            "previous_chapter_state": previous_state,
            "items": items,
            "glossary": glossary.get("terms", []),
        })
        run_chapter_review(input_path, output_path, autonomous=autonomous, backend=reviewer)
        review = read_json(output_path)
        if not isinstance(review, dict):
            raise ValueError(f"章节审阅结果不是 JSON 对象：{output_path}")
        expected = {item["id"] for item in items}
        for retry in range(1, 3):
            if not missing_checked_ids(review, expected):
                break
            retry_path = workspace.reviews_dir / f"{c_id}-consistency-retry-{retry:02d}.json"
            run_chapter_review(input_path, retry_path, autonomous=autonomous, backend=reviewer)
            review = read_json(retry_path)
        validate_chapter_review_payload(review, expected)
        fixes = approved_fixes(review["fixes"], autonomous=autonomous)
        fixes_path = workspace.reviews_dir / f"{c_id}-consistency-fixes.json"
        write_json(fixes_path, {"book": book, "items": fixes})
        applied_fixes = False
        if apply and fixes:
            applied_fixes = call_novel_translator("apply-review-fixes", "--book", book, "--input", str(fixes_path))
            verify_applied_fixes(read_json(manifest_path(book)), fixes)
        glossary, term_summary = merge_term_updates(
            glossary,
            review["glossary_delta"].get("add", []) + review["glossary_delta"].get("update", []),
            chunk_id=f"chapter-{c_id}",
        )
        memory, memory_summary = merge_memory_delta(memory, review["memory_delta"], chapter_id=c_id)
        chapter_state = merge_chapter_state(
            read_json(workspace.chapter_states_dir / f"{c_id}.json", {"chapter_id": c_id}),
            review["chapter_state"],
            chapter_id=c_id,
        )
        chapter_state.update({"status": "reviewed", "checked": len(expected), "fixes": len(fixes)})
        write_json(workspace.chapter_states_dir / f"{c_id}.json", chapter_state)
        results.append({
            "chapter_id": c_id,
            "checked": len(expected),
            "issues": len(review["fixes"]),
            "fixes": len(fixes),
            "applied": applied_fixes,
            "term_updates": term_summary,
            "memory_delta": memory_summary,
        })

    write_json(workspace.glossary_path, glossary)
    write_json(workspace.book_memory_path, memory)
    terms_path = workspace.data_dir / "novel-translator-terms.json"
    write_json(terms_path, novel_translator_terms(glossary))
    terminology = call_novel_translator("import-terminology", "--book", book, "--input", str(terms_path))
    quality = call_novel_translator("quality-report", "--book", book)
    report = {"book": book, "chapters": results, "terminology": terminology, "quality": quality, "updated_at": utc_now()}
    if global_consistency:
        chapter_states = []
        for chapter in chapters:
            c_id = str(chapter.get("id", ""))
            state = read_json(workspace.chapter_states_dir / f"{c_id}.json", None)
            if state:
                chapter_states.append(state)
        global_input = workspace.reviews_dir / "global-consistency-input.json"
        global_output = workspace.reviews_dir / "global-consistency-output.json"
        write_json(global_input, {
            "book": book,
            "chapter_ids": [str(c.get("id", "")) for c in chapters],
            "glossary": glossary,
            "book_memory": memory,
            "chapter_states": chapter_states,
        })
        run_global_consistency_review(global_input, global_output, backend=reviewer)
        global_review = read_json(global_output)
        expected_chapters = {str(c.get("id", "")) for c in chapters}
        global_review = validate_global_consistency_payload(global_review, expected_chapters)
        report["global_consistency"] = global_review
    write_json(workspace.reports_dir / "chapter-consistency.json", report)
    if export:
        output = workspace.root / f"{workspace.root.name}-中文.epub"
        validation = call_novel_translator("validate-export", "--book", book, "--format", "epub")
        exported = call_novel_translator("export", "--book", book, "--format", "epub", "--output", str(output), "--monolingual")
        epub_validation = call_novel_translator("validate-epub", "--path", str(output))
        report["export"] = {"output": str(output), "validation": validation, "exported": exported, "epub_validation": epub_validation}
        write_json(workspace.reports_dir / "chapter-consistency.json", report)
    return report


def cli_main() -> None:
    config = load_config()
    parser = argparse.ArgumentParser(description="Run chapter-level consistency review")
    parser.add_argument("--book", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--output-root", type=Path, default=ROOT / config["paths"]["output_root"])
    parser.add_argument("--chapter-id")
    parser.add_argument("--all", action="store_true", help="审阅 manifest 中的全部已翻译章节")
    parser.add_argument("--global-consistency", action="store_true", help="章节审阅后检查全书状态之间的一致性")
    parser.add_argument("--translation-policy", type=Path, default=ROOT / config["paths"]["translation_policy"])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--autonomous", action="store_true")
    parser.add_argument("--export", action="store_true")
    parser.add_argument(
        "--reviewer",
        default=setting(config, "roles.reviewer", "REVIEWER"),
        choices=["codex", "opencode"],
        help="审阅后端",
    )
    args = parser.parse_args()
    report = review_book(
        book=args.book,
        name=args.name,
        output_root=args.output_root,
        chapter_id=args.chapter_id,
        global_consistency=args.global_consistency,
        translation_policy=args.translation_policy,
        apply=args.apply,
        autonomous=args.autonomous,
        export=args.export,
        reviewer=args.reviewer,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    cli_main()
