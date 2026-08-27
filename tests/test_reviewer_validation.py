from __future__ import annotations

import unittest
from typing import Any

from translator.review.reviewer import approved_fixes, has_japanese_kana


class ReviewerObjectiveValidationTests(unittest.TestCase):
    def test_has_japanese_kana_detection(self) -> None:
        self.assertTrue(has_japanese_kana("スチュワーデス・夕子と可奈子"))
        self.assertTrue(has_japanese_kana("夕子と可奈子"))
        self.assertFalse(has_japanese_kana("空姐·夕子与可奈子"))
        self.assertFalse(has_japanese_kana("空乘·夕子与可奈子"))
        self.assertFalse(has_japanese_kana("永井龙儿"))

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


if __name__ == "__main__":
    unittest.main()
