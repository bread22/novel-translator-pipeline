from pathlib import Path

from translator.providers.base import build_review_prompt


def test_chapter_review_prompt_scans_existing_translated_fields_for_kana() -> None:
    schema = Path(__file__).resolve().parents[1] / "schemas" / "chapter-review-output.schema.json"
    prompt = build_review_prompt(
        "chapter",
        {"items": [{"id": "p1", "source": "原文", "translated": "译文かな"}]},
        schema,
        autonomous=True,
    )
    assert "系统预检警报" in prompt
    assert "p1" in prompt
    assert "policy_violation" in prompt
    assert "Knowledge Extractor" in prompt


def test_knowledge_prompts() -> None:
    window_schema = Path(__file__).resolve().parents[1] / "schemas" / "knowledge-extractor-window.schema.json"
    prompt = build_review_prompt(
        "knowledge_window",
        {"items": [{"id": "p1", "source": "SAMPLE", "translated": "TARGET"}]},
        window_schema,
        autonomous=False,
    )
    assert "Knowledge Extractor" in prompt
    assert "rolling_context_delta" in prompt
    assert "knowledge_candidates" in prompt

