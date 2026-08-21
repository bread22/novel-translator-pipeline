from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from translator.providers.opencode import check, parse_json_object, run_prompt
from translator.providers.translator import ProviderTranslator
from translator.review.reviewer import run_chapter_review


class OpenCodeBackendTests(unittest.TestCase):
    def test_run_prompt_collects_json_text_events(self) -> None:
        stdout = "\n".join(
            [
                json.dumps({"type": "step_start"}),
                json.dumps({"type": "text", "part": {"type": "text", "text": "{\\\"ok\\\":"}}),
                json.dumps({"type": "text", "part": {"type": "text", "text": "true}"}}),
                json.dumps({"type": "step_finish"}),
            ]
        )
        with patch("translator.providers.opencode.executable", return_value="/usr/bin/opencode"), patch(
            "translator.providers.opencode.subprocess.run",
            return_value=Mock(returncode=0, stdout=stdout, stderr=""),
        ) as run:
            result = run_prompt("health", role="reviewer", timeout=3)
        self.assertEqual(result, '{\\"ok\\":true}')
        command = run.call_args.args[0]
        self.assertEqual(command[:3], ["/usr/bin/opencode", "run", "--format"])
        self.assertIn("--dir", command)

    def test_parse_json_object_accepts_fenced_output(self) -> None:
        self.assertEqual(parse_json_object("```json\n{\"ok\":true}\n```"), {"ok": True})
        self.assertEqual(
            parse_json_object('格式示例 {"wrong":true}\n最终结果 {"items":[]}'),
            {"items": []},
        )

    def test_health_check_requires_exact_json_response(self) -> None:
        with patch("translator.providers.opencode.run_json", return_value={"ok": True}):
            result = check(timeout=3, role="reviewer")
        self.assertEqual(result["status"], "ok")

    def test_provider_translator_uses_opencode_json_backend(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            translator = ProviderTranslator(novel_root=Path(temporary), manifest=Path(temporary) / "manifest.json")
            with patch(
                "translator.providers.opencode.run_prompt",
                return_value=json.dumps({"ok": True}),
            ) as run:
                result = translator.health_check("opencode", timeout=3)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(run.call_args.kwargs["role"], "reviewer")

    def test_chapter_review_writes_opencode_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_path = root / "input.json"
            output_path = root / "output.json"
            input_path.write_text(json.dumps({"items": [{"id": "p1", "source": "原文", "translated": "译文"}]}), encoding="utf-8")
            with patch(
                "translator.providers.opencode.run_prompt",
                return_value=json.dumps({"checked_ids": ["p1"], "fixes": [], "glossary_delta": {"add": [], "update": []}, "memory_delta": {"add": [], "update": []}, "chapter_state": {}}),
            ):
                run_chapter_review(input_path, output_path, backend="opencode")
            payload = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["checked_ids"], ["p1"])


if __name__ == "__main__":
    unittest.main()
