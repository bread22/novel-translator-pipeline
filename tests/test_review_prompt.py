from pathlib import Path

from translator.providers.base import build_review_prompt


def test_chapter_review_prompt_scans_existing_translated_fields_for_kana() -> None:
    schema = Path(__file__).resolve().parents[1] / "schemas" / "chapter-review-output.schema.json"
    prompt = build_review_prompt(
        "chapter",
        {"items": [{"id": "p1", "source": "原文", "translated": "译文"}]},
        schema,
        autonomous=True,
    )
    assert "逐字扫描平假名和片假名" in prompt
    assert "不得只检查 fixes.replacement" in prompt
    assert "source 字段本身是日文原文" in prompt
    assert "translated 字段" in prompt
    assert "policy_violation fix" in prompt
    assert "replacement 必须给出该段完整" in prompt


def test_chapter_review_prompt_distinguishes_masking_symbols_and_japanese_kanji_from_kana() -> None:
    schema = Path(__file__).resolve().parents[1] / "schemas" / "chapter-review-output.schema.json"
    prompt = build_review_prompt(
        "chapter",
        {"items": [{"id": "p1", "source": "SAMPLE", "translated": "TARGET×TERM"}]},
        schema,
        autonomous=True,
    )

    assert "日文汉字、伏字与遮掩符号" in prompt
    assert "○、●、×、＊、※、□等符号不是假名" in prompt
    assert "category=terminology、severity=major" in prompt
    assert "原文伏字/遮掩符号未还原" in prompt
    assert "category=policy_violation、severity=critical" in prompt
    assert "auto_apply=true 且 confidence>=0.95" in prompt
    assert "ambiguous_source" in prompt
    assert "日文风格词或不规范译名" in prompt
    assert "operation=clear" in prompt
    assert "双审一致" in prompt
