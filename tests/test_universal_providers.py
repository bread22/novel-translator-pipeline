from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from translator.core.config import load_config
from translator.providers.antigravity import AntigravityProvider
from translator.providers.base import (
    extract_json_object,
    parse_translation_items,
    provider_block_reason,
    validate_translation_items,
)
from translator.providers.codex import CodexProvider
from translator.providers.opencode import OpenCodeProvider
from translator.providers.openai_provider import OpenAIProvider
from translator.providers.registry import get_provider
from translator.review.reviewer import run_chapter_review, run_global_consistency_review


class _MockHTTPResponse:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self.payload = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.status = status

    def __enter__(self) -> "_MockHTTPResponse":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


class UniversalProviderTests(unittest.TestCase):
    def test_provider_registry_instantiates_all_types(self) -> None:
        cfg = load_config()
        self.assertIsInstance(get_provider("antigravity", cfg), AntigravityProvider)
        self.assertIsInstance(get_provider("opencode", cfg), OpenCodeProvider)
        self.assertIsInstance(get_provider("lmstudio", cfg), OpenAIProvider)
        self.assertIsInstance(get_provider("codex", cfg), CodexProvider)
        self.assertIsInstance(get_provider("online_api", cfg), OpenAIProvider)

    def test_openai_provider_translate_and_review(self) -> None:
        provider = OpenAIProvider("custom_api", {
            "type": "openai",
            "base_url": "https://api.example.com/v1",
            "model": "deepseek-chat",
            "api_key": "sk-test",
            "context_tokens": 32768,
        })
        # Test translation
        trans_resp = _MockHTTPResponse({
            "choices": [{"message": {"content": json.dumps({"items": [{"id": "p1", "text": "在线模型译文"}]})}, "finish_reason": "stop"}]
        })
        with patch("translator.providers.openai_provider.urlopen", return_value=trans_resp):
            items, result = provider.translate(
                {"items": [{"id": "p1", "text": "原文"}]},
                "翻译系统提示词",
                max_tokens=1024,
            )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(items, [{"id": "p1", "text": "在线模型译文"}])

        # Test review
        rev_payload = {
            "checked_ids": ["p1"],
            "fixes": [],
        }
        rev_resp = _MockHTTPResponse({
            "choices": [{"message": {"content": json.dumps(rev_payload)}, "finish_reason": "stop"}]
        })
        with tempfile.TemporaryDirectory() as temp_dir:
            schema_file = Path(temp_dir) / "schema.json"
            schema_file.write_text("{}", encoding="utf-8")
            with patch("translator.providers.openai_provider.urlopen", return_value=rev_resp):
                output = provider.review("chapter", {"items": [{"id": "p1"}]}, schema_file)
        self.assertEqual(output["checked_ids"], ["p1"])

    def test_antigravity_provider_as_reviewer(self) -> None:
        provider = AntigravityProvider("antigravity", {
            "agy": "agy",
            "model": "gemini-3.7-flash",
            "effort": "low",
            "timeout": 60,
        })
        rev_payload = {
            "checked_ids": ["p1", "p2"],
            "fixes": [{"id": "p2", "category": "mistranslation", "severity": "major", "confidence": 0.95, "replacement": "AGY审阅修复"}],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_path = root / "input.json"
            output_path = root / "output.json"
            input_path.write_text(json.dumps({"items": [{"id": "p1", "source": "s1", "translated": "t1"}, {"id": "p2", "source": "s2", "translated": "t2"}]}), encoding="utf-8")

            with patch.object(provider, "_run_agy", return_value=json.dumps(rev_payload)):
                with patch("translator.review.reviewer.get_provider", return_value=provider):
                    run_chapter_review(input_path, output_path, backend="antigravity")

            saved = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["checked_ids"], ["p1", "p2"])
            self.assertEqual(saved["fixes"][0]["replacement"], "AGY审阅修复")

    def test_codex_provider_as_translator(self) -> None:
        provider = CodexProvider("codex", {
            "binary": "codex",
            "model": "gpt-5.6",
            "timeout": 60,
        })
        payload = {"items": [{"id": "p1", "text": "原文段落"}]}

        def fake_run(command, **_kwargs):
            out_idx = command.index("-o") + 1
            out_file = Path(command[out_idx])
            out_file.write_text(json.dumps({"items": [{"id": "p1", "text": "Codex高质量译文"}]}), encoding="utf-8")
            return Mock(returncode=0, stdout="", stderr="")

        with patch("translator.providers.codex.shutil.which", return_value="/usr/bin/codex"), patch("translator.providers.codex.subprocess.run", side_effect=fake_run):
            items, result = provider.translate(payload, "系统提示词", max_tokens=1024)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(items, [{"id": "p1", "text": "Codex高质量译文"}])


if __name__ == "__main__":
    unittest.main()
