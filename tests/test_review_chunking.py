from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from translator.review.reviewer import chunk_items_by_source_chars, run_chapter_review


def _items(lengths: list[int]) -> list[dict[str, str]]:
    return [
        {"id": f"p{index}", "source": "源" * length, "translated": f"译文{index}"}
        for index, length in enumerate(lengths, start=1)
    ]


def test_chunk_items_by_source_chars_keeps_paragraphs_intact() -> None:
    items = _items([400, 400, 400, 400, 400])
    chunks = chunk_items_by_source_chars(items, min_chars=1000, max_chars=1500)

    assert [[item["id"] for item in chunk] for chunk in chunks] == [
        ["p1", "p2", "p3"],
        ["p4", "p5"],
    ]
    assert all(item in sum(chunks, []) for item in items)


def test_chunk_items_by_source_chars_allows_a_single_oversized_paragraph() -> None:
    items = _items([1800, 200])
    chunks = chunk_items_by_source_chars(items, min_chars=1000, max_chars=1500)

    assert [[item["id"] for item in chunk] for chunk in chunks] == [["p1"], ["p2"]]


def test_run_chapter_review_sends_bilingual_context_and_backtracks_prior_context() -> None:
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        input_path = root / "c1-input.json"
        output_path = root / "c1-output.json"
        input_path.write_text(json.dumps({
            "book": "book",
            "chapter_id": "c1",
            "chapter_title": "第一章",
            "translation_policy": "政策",
            "book_memory": {},
            "previous_chapter_state": {},
            "glossary": [],
            "items": _items([400, 400, 400, 400, 400]),
        }, ensure_ascii=False), encoding="utf-8")
        calls: list[dict] = []

        def mock_execute(*args, **kwargs):
            payload = kwargs.get("input_payload") or args[1]
            calls.append(payload)
            target_ids = [item["id"] for item in payload.get("items", [])]
            if payload.get("review_mode") == "targeted_context_recheck":
                return {
                    "checked_ids": target_ids,
                    "fixes": [{
                        "id": target_ids[0],
                        "category": "context_conflict",
                        "severity": "major",
                        "confidence": 0.95,
                        "reason": "回溯复核确认",
                        "replacement": "回溯修复",
                        "auto_apply": True,
                    }],
                }
            result = {"checked_ids": target_ids, "fixes": []}
            if payload.get("context_before"):
                result["context_findings"] = [{
                    "id": payload["context_before"][0]["id"],
                    "category": "context_conflict",
                    "severity": "major",
                    "confidence": 0.9,
                    "reason": "后文揭示前文需要回看",
                    "evidence_ids": target_ids[:1],
                }]
            return result

        with patch("translator.review.reviewer._execute_review_with_fallbacks", side_effect=mock_execute):
            run_chapter_review(
                input_path,
                output_path,
                backend="mock",
                dual_review=False,
                chunk_min_chars=1000,
                chunk_max_chars=1500,
                context_before=1,
                context_after=1,
                backtrack_enabled=True,
            )

        normal_calls = [call for call in calls if call.get("review_mode") == "chapter_chunk"]
        targeted_calls = [call for call in calls if call.get("review_mode") == "targeted_context_recheck"]
        assert [[item["id"] for item in call["items"]] for call in normal_calls] == [
            ["p1", "p2", "p3"],
            ["p4", "p5"],
        ]
        assert normal_calls[0]["context_before"] == []
        assert [item["id"] for item in normal_calls[0]["context_after"]] == ["p4"]
        assert [item["id"] for item in normal_calls[1]["context_before"]] == ["p3"]
        assert [item["id"] for item in targeted_calls[0]["items"]] == ["p3"]
        assert targeted_calls[0]["items"][0]["source"]
        assert targeted_calls[0]["items"][0]["translated"]

        output = json.loads(output_path.read_text(encoding="utf-8"))
        assert output["checked_ids"] == ["p1", "p2", "p3", "p4", "p5"]
        assert [fix["id"] for fix in output["fixes"]] == ["p3"]
        assert output["review_diagnostics"]["chunking"]["mode"] == "source_chars"
        assert output["review_diagnostics"]["backtrack"]["rechecks"][0]["id"] == "p3"
