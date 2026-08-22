from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from translator.pipeline.preflight import PreflightError, run_preflight
from translator.providers.translator import ProviderTranslator
from translator.review.reviewer import check_reviewer


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
        response = _Response(
            {
                "choices": [
                    {
                        "message": {"content": json.dumps({"items": [{"id": "__healthcheck__", "text": "测试"}]})},
                        "finish_reason": "stop",
                    }
                ]
            }
        )
        with tempfile.TemporaryDirectory() as temporary:
            translator = ProviderTranslator(novel_root=Path(temporary), manifest=Path(temporary) / "manifest.json")
            with patch("translator.providers.openai_provider.urlopen", return_value=response):
                result = translator.health_check("lmstudio", timeout=3)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["model"], "murasaki-14b-v0.2")

    def test_reviewer_health_check_requires_valid_output(self) -> None:
        def fake_run(command, **_kwargs):
            output_path = Path(command[command.index("-o") + 1])
            output_path.write_text('{"ok": true}', encoding="utf-8")
            return Mock(returncode=0, stdout="", stderr="")

        with patch("translator.providers.codex.shutil.which", return_value="/usr/bin/codex"), patch("translator.providers.codex.subprocess.run", side_effect=fake_run):
            result = check_reviewer(timeout=3, backend="codex")
        self.assertEqual(result["status"], "ok")

    def test_preflight_reports_all_provider_failures(self) -> None:
        translator = Mock()
        translator.health_check.side_effect = [
            {"name": "translator:opencode", "status": "error", "error": "opencode down"},
            {"name": "translator:antigravity", "status": "ok"},
            {"name": "translator:lmstudio", "status": "ok"},
        ]
        with patch("translator.pipeline.preflight.check_reviewer", return_value={"name": "reviewer", "status": "error", "error": "codex down"}):
            with self.assertRaises(PreflightError) as context:
                run_preflight(
                    translator,
                    timeout=3,
                    primary_translator="opencode",
                    fallback_translators=["antigravity", "lmstudio"],
                    reviewer="codex",
                )
        report = context.exception.report
        self.assertEqual(report["status"], "error")
        self.assertEqual([item["status"] for item in report["checks"]], ["error", "error", "ok", "ok"])
        self.assertEqual(translator.health_check.call_count, 3)

    def test_preflight_uses_selected_roles_and_fallbacks(self) -> None:
        translator = Mock()
        translator.health_check.return_value = {"name": "translator:opencode", "status": "ok"}
        with patch("translator.pipeline.preflight.check_reviewer", return_value={"name": "opencode:reviewer", "status": "ok"}) as reviewer:
            report = run_preflight(
                translator,
                timeout=3,
                primary_translator="opencode",
                fallback_translators=["opencode"],
                reviewer="opencode",
            )
        self.assertEqual(report["status"], "ok")
        reviewer.assert_called_once_with(timeout=3, backend="opencode")
        translator.health_check.assert_called_once_with("opencode", timeout=3)


if __name__ == "__main__":
    unittest.main()
