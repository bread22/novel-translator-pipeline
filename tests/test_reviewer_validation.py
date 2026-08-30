from __future__ import annotations

import unittest
from typing import Any

from translator.review.reviewer import (
    approved_fixes,
    has_hangul,
    has_japanese_kana,
    has_masking_symbol,
    has_target_script_residue,
    normalize_target_punctuation,
    validate_chapter_review_payload,
    verify_applied_fixes,
)


class ReviewerObjectiveValidationTests(unittest.TestCase):
    def test_has_japanese_kana_detection(self) -> None:
        self.assertTrue(has_japanese_kana("スチュワーデス・夕子と可奈子"))
        self.assertTrue(has_japanese_kana("夕子と可奈子"))
        self.assertFalse(has_japanese_kana("空姐·夕子与可奈子"))
        self.assertFalse(has_japanese_kana("空乘·夕子与可奈子"))
        self.assertFalse(has_japanese_kana("永井龙儿"))
        self.assertFalse(has_japanese_kana("中文・标题"))
        self.assertEqual(normalize_target_punctuation("嫂子・玲子"), "嫂子·玲子")

    def test_reject_hallucinated_kana_policy_violation_when_text_is_pure_chinese(self) -> None:
        current_translations = {
            "c0001-p00001": "空姐·夕子与可奈子",
        }
        # Reviewer hallucinating that '空姐' is katakana and marking policy_violation
        fixes: list[dict[str, Any]] = [
            {
                "id": "c0001-p00001",
                "category": "policy_violation",
                "severity": "critical",
                "confidence": 0.95,
                "reason": "译文残留日文假名：空姐·夕子与可奈子 中的 '空姐' 为日式片假名词，应译为中文表达。",
                "replacement": "空乘·夕子与可奈子",
                "auto_apply": True,
            }
        ]
        approved = approved_fixes(fixes, current_translations=current_translations)
        self.assertEqual(len(approved), 0, "Hallucinated kana violation on pure Chinese must be rejected")

    def test_detect_and_approve_hangul_cleanup(self) -> None:
        current = {"p1": "第二章 美歌子老师的内衣·心跳加速的兰제里小偷"}
        fix = {
            "id": "p1", "category": "policy_violation", "severity": "critical",
            "confidence": 0.99, "reason": "译文残留外文字符：兰제里",
            "replacement": "第二章 美歌子老师的内衣·令人心动的内衣小偷",
            "auto_apply": False,
        }
        self.assertTrue(has_hangul(current["p1"]))
        self.assertTrue(has_target_script_residue(current["p1"]))
        approved = approved_fixes([fix], current_translations=current)
        self.assertEqual([item["id"] for item in approved], ["p1"])
        self.assertTrue(approved[0]["auto_apply"])

    def test_reject_and_mark_hangul_replacement_invalid(self) -> None:
        fix = {
            "id": "p1", "category": "policy_violation", "severity": "critical",
            "confidence": 0.99, "reason": "译文残留外文字符",
            "replacement": "第二章·兰제里小偷", "auto_apply": True,
        }
        self.assertEqual(approved_fixes([fix], current_translations={"p1": "ランジェリー小偷"}), [])
        payload = {
            "checked_ids": ["p1"], "fixes": [fix],
        }
        normalized = validate_chapter_review_payload(payload, {"p1"})
        self.assertFalse(normalized["fixes"][0]["auto_apply"])
        self.assertEqual(normalized["fixes"][0]["apply_state"], "blocked")
        self.assertIn("target_script_residue", normalized["fixes"][0]["validation_errors"])
        self.assertEqual(normalized["fixes"][0]["invalid_reason"], "target_script_residue")

    def test_approve_real_kana_policy_violation_when_text_actually_has_kana(self) -> None:
        current_translations = {
            "c0001-p00001": "スチュワーデス·夕子与可奈子",
        }
        fixes: list[dict[str, Any]] = [
            {
                "id": "c0001-p00001",
                "category": "policy_violation",
                "severity": "critical",
                "confidence": 0.95,
                "reason": "译文残留未翻译假名スチュワーデス",
                "replacement": "空乘·夕子与可奈子",
                "auto_apply": True,
            }
        ]
        approved = approved_fixes(fixes, current_translations=current_translations)
        self.assertEqual(len(approved), 1)
        self.assertEqual(approved[0]["replacement"], "空乘·夕子与可奈子")

    def test_reject_fix_when_replacement_contains_japanese_kana(self) -> None:
        current_translations = {
            "c0001-p00001": "スチュワーデス·夕子与可奈子",
        }
        fixes: list[dict[str, Any]] = [
            {
                "id": "c0001-p00001",
                "category": "policy_violation",
                "severity": "critical",
                "confidence": 0.95,
                "reason": "译文残留未翻译假名",
                "replacement": "キャビンアテンダント·夕子与可奈子",
                "auto_apply": True,
            }
        ]
        approved = approved_fixes(fixes, current_translations=current_translations)
        self.assertEqual(len(approved), 0, "Replacement containing kana must be rejected")

    def test_skip_no_op_fix_when_replacement_equals_current_text(self) -> None:
        current_translations = {
            "c0001-p00001": "空乘·夕子与可奈子",
        }
        fixes: list[dict[str, Any]] = [
            {
                "id": "c0001-p00001",
                "category": "terminology",
                "severity": "minor",
                "confidence": 0.95,
                "reason": "术语统一",
                "replacement": "空乘·夕子与可奈子",
                "auto_apply": True,
            }
        ]
        approved = approved_fixes(fixes, current_translations=current_translations)
        self.assertEqual(len(approved), 0, "Identical replacement should be skipped")

    def test_legitimate_objective_fixes_still_approved(self) -> None:
        current_translations = {
            "c0001-p00001": "田中老师走了进来。",
        }
        fixes: list[dict[str, Any]] = [
            {
                "id": "c0001-p00001",
                "category": "mistranslation",
                "severity": "major",
                "confidence": 0.92,
                "reason": "原文为高桥先生，译成了田中老师",
                "replacement": "高桥老师走了进来。",
                "auto_apply": True,
            }
        ]
        approved = approved_fixes(fixes, current_translations=current_translations)
        self.assertEqual(len(approved), 1)
        self.assertEqual(approved[0]["replacement"], "高桥老师走了进来。")

    def test_approve_masking_symbol_cleanup_but_reject_masked_replacement(self) -> None:
        current = {"p1": "术语×残留"}
        good = {"id": "p1", "category": "policy_violation", "severity": "critical", "confidence": 0.95,
                "reason": "原文伏字/遮掩符号未还原", "replacement": "完整术语", "auto_apply": True}
        bad = {**good, "replacement": "术语○残留"}
        self.assertTrue(has_masking_symbol(current["p1"]))
        self.assertEqual([x["id"] for x in approved_fixes([good], current_translations=current)], ["p1"])
        self.assertEqual(approved_fixes([bad], current_translations=current), [])

    def test_clear_is_disabled_even_with_consensus(self) -> None:
        fix = {"id": "p1", "category": "addition", "severity": "major", "confidence": 0.98,
               "reason": "与上一段完全重复", "replacement": "", "operation": "clear",
               "auto_apply": True, "consensus": True}
        approved = approved_fixes([fix], current_translations={"p1": "重复译文"})
        self.assertEqual(approved, [])


if __name__ == "__main__":
    unittest.main()
