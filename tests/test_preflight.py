from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts.codex_review import check_reviewer
from scripts.preflight import PreflightError, run_preflight
from scripts.provider_translator import ProviderTranslator


class _Response:
    def __init__(self, payload: dict) -> None:
        self.payload = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.status = 200

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


class PreflightTests(unittest.TestCase):
    def test_provider_health_check_checks_model_and_real_completion(self) -> None:
        responses = iter(
            [
                _Response({"data": [{"id": "murasaki-14b-v0.2"}]}),
                _Response(
                    {
                        "choices": [
                            {
                                "message": {"content": json.dumps({"items": [{"id": "__healthcheck__", "text": "测试"}]})},
                                "finish_reason": "stop",
                            }
                        ]
                    }
                ),
            ]
        )
        with tempfile.TemporaryDirectory() as temporary:
            translator = ProviderTranslator(novel_root=Path(temporary), manifest=Path(temporary) / "manifest.json")
            with patch("scripts.provider_translator.urlopen", side_effect=lambda request, timeout: next(responses)):
                result = translator.health_check("murasaki-local", timeout=3)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["model"], "murasaki-14b-v0.2")

    def test_provider_health_check_rejects_missing_model(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            translator = ProviderTranslator(novel_root=Path(temporary), manifest=Path(temporary) / "manifest.json")
            with patch("scripts.provider_translator.urlopen", return_value=_Response({"data": []})):
                result = translator.health_check("murasaki-local", timeout=3)
        self.assertEqual(result["status"], "error")
        self.assertIn("not listed", result["error"])

    def test_reviewer_health_check_requires_valid_output(self) -> None:
        def fake_run(command, **_kwargs):
            output_path = Path(command[command.index("-o") + 1])
            output_path.write_text('{"ok": true}', encoding="utf-8")
            return Mock(returncode=0, stdout="", stderr="")

        with patch("scripts.codex_review.shutil.which", return_value="/usr/bin/codex"), patch("scripts.codex_review.subprocess.run", side_effect=fake_run):
            result = check_reviewer(timeout=3)
        self.assertEqual(result["status"], "ok")

    def test_preflight_reports_all_provider_failures(self) -> None:
        translator = Mock()
        translator.health_check.side_effect = [
            {"name": "translator:gemini", "status": "error", "error": "bridge down"},
            {"name": "translator:murasaki-local", "status": "ok"},
        ]
        with patch("scripts.preflight.check_reviewer", return_value={"name": "reviewer", "status": "error", "error": "codex down"}):
            with self.assertRaises(PreflightError) as context:
                run_preflight(translator, timeout=3)
        report = context.exception.report
        self.assertEqual(report["status"], "error")
        self.assertEqual([item["status"] for item in report["checks"]], ["error", "error", "ok"])
        self.assertEqual(translator.health_check.call_count, 2)

    def test_preflight_uses_selected_opencode_roles(self) -> None:
        translator = Mock()
        translator.health_check.return_value = {"name": "translator:opencode", "status": "ok"}
        with patch("scripts.preflight.check_reviewer", return_value={"name": "opencode:reviewer", "status": "ok"}) as reviewer:
            report = run_preflight(
                translator,
                timeout=3,
                primary_provider="opencode",
                fallback_provider="opencode",
                reviewer_backend="opencode",
            )
        self.assertEqual(report["status"], "ok")
        reviewer.assert_called_once_with(timeout=3, backend="opencode")
        translator.health_check.assert_called_once_with("opencode", timeout=3)


if __name__ == "__main__":
    unittest.main()
