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


def test_chapter_review_prompt_uses_meaning_first_titles_and_allows_polishing() -> None:
    schema = Path(__file__).resolve().parents[1] / "schemas" / "chapter-review-output.schema.json"
    prompt = build_review_prompt(
        "chapter",
        {
            "translation_policy": "仅以本文件和 glossary 为准",
            "glossary": [],
            "items": [{"id": "title-1", "source": "レイザーレイプ!", "translated": "剃刀强暴！"}],
        },
        schema,
        autonomous=True,
    )
    assert "未经提供的出版社惯例、年代惯例、文库惯例" in prompt
    assert "透明的外来语不得默认音译" in prompt
    assert "レイプ` → 强暴/强奸" in prompt
    assert "ホテル` → 酒店" in prompt
    assert "ナイフ` → 刀" in prompt
    assert "セックス` → 性爱" in prompt
    assert "只有人名、品牌、虚构专名、无法自然意译的名称" in prompt
    assert "Reviewer 同时负责中文润色" in prompt
    assert "纯润色使用 `category: style`" in prompt
    assert "审阅顺序固定为：先做基础语义与中文自然度检查，再做风格润色" in prompt
    assert "字面看似中文、实际是日语词法直搬" in prompt
    assert "兄嫁（あによめ）" in prompt
    assert "嫂子`、`兄嫂` 或 `大嫂" in prompt
    assert "severity=major 只表示会造成实质意义错误" in prompt
    assert "置信度是基于证据的记录" in prompt
    assert "confidence 只能作为辅助记录，绝不能单独触发 auto_apply" in prompt
    assert "编辑偏好不进入任何客观错误类别" not in prompt


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


def test_translation_policy_covers_contextual_kinship_conversion() -> None:
    policy = (Path(__file__).resolve().parents[1] / "docs" / "prompts" / "france-shoin-90s-classic.md").read_text(encoding="utf-8")
    assert "日语亲属称谓必须转换为自然的简体中文亲属称谓" in policy
    assert "兄嫁（あによめ）" in policy
    assert "嫂子 / 兄嫂 / 大嫂" in policy
    assert "義弟" in policy and "小叔子 / 妻弟" in policy
