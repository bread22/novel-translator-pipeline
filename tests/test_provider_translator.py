from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.provider_translator import ProviderTranslator


class _Response:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self.payload = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.status = status

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


def _manifest(path: Path, *, previous: str = "") -> None:
    path.write_text(
        json.dumps(
            {
                "id": "book",
                "chapters": [
                    {
                        "id": "c1",
                        "title": "Chapter",
                        "paragraphs": [
                            {"id": "p1", "source": "前文の短い段落です。", "translated": previous},
                            {"id": "p2", "source": "現在の短い段落です。", "translated": ""},
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


class ProviderTranslatorTests(unittest.TestCase):
    def _translator(self, manifest_path: Path) -> ProviderTranslator:
        return ProviderTranslator(novel_root=manifest_path.parent, manifest=manifest_path)

    def test_local_single_item_freeform_response_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest_path = Path(temporary) / "manifest.json"
            _manifest(manifest_path)
            response = {"choices": [{"message": {"content": "这不是 JSON 响应。"}, "finish_reason": "stop"}]}
            requests: list[dict] = []

            def fake_urlopen(request, timeout):
                requests.append(json.loads(request.data))
                return _Response(response)

            with patch("scripts.provider_translator.urlopen", side_effect=fake_urlopen):
                result = self._translator(manifest_path)(
                    "murasaki-local", "book", ["p2"], source_chars=10, max_tokens=8192
                )
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["format"], "plain_single_item")
            self.assertEqual(result["summary"]["translated"], 1)
            self.assertEqual(requests[0]["max_tokens"], 512)
            self.assertIn('"items"', requests[0]["messages"][0]["content"])
            local_payload = json.loads(requests[0]["messages"][1]["content"])
            self.assertEqual([item["id"] for item in local_payload["items"]], ["p2"])
            self.assertNotIn("context", local_payload)
            saved = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["chapters"][0]["paragraphs"][1]["translated"], "这不是 JSON 响应。")

    def test_truncated_json_response_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest_path = Path(temporary) / "manifest.json"
            _manifest(manifest_path)
            response = {
                "choices": [
                    {
                        "message": {"content": '{"items":[{"id":"p2","text":"译文"}]}'},
                        "finish_reason": "length",
                    }
                ]
            }
            with patch("scripts.provider_translator.urlopen", return_value=_Response(response)):
                result = self._translator(manifest_path)(
                    "murasaki-local", "book", ["p2"], source_chars=10, max_tokens=8192
                )
            self.assertEqual(result["status"], "error")
            self.assertEqual(result["reason"], "output_format")
            self.assertEqual(result["finish_reason"], "length")

    def test_repeated_response_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest_path = Path(temporary) / "manifest.json"
            _manifest(manifest_path)
            repeated = "这是一段足够长的重复译文句子，用于检测上下文污染。"
            response = {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"items": [{"id": "p2", "text": f"{repeated}\n{repeated}"}]},
                                ensure_ascii=False,
                            )
                        },
                        "finish_reason": "stop",
                    }
                ]
            }
            with patch("scripts.provider_translator.urlopen", return_value=_Response(response)):
                result = self._translator(manifest_path)(
                    "murasaki-local", "book", ["p2"], source_chars=10, max_tokens=8192
                )
            self.assertEqual(result["status"], "error")
            self.assertEqual(result["validation"]["kind"], "repeated_line")

    def test_previous_context_overlap_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest_path = Path(temporary) / "manifest.json"
            previous = "这是前文已经完成的一整段较长译文，用来验证污染内容不会回流到下一段翻译，也用于覆盖上下文重合校验。"
            _manifest(manifest_path, previous=previous)
            response = {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"items": [{"id": "p2", "text": f"{previous} 当前译文。"}]},
                                ensure_ascii=False,
                            )
                        },
                        "finish_reason": "stop",
                    }
                ]
            }
            with patch("scripts.provider_translator.urlopen", return_value=_Response(response)):
                result = self._translator(manifest_path)(
                    "murasaki-local", "book", ["p2"], source_chars=10, max_tokens=8192
                )
            self.assertEqual(result["status"], "error")
            self.assertEqual(result["validation"]["kind"], "previous_context_overlap")

    def test_valid_json_response_is_written(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest_path = Path(temporary) / "manifest.json"
            _manifest(manifest_path)
            response = {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"items": [{"id": "p2", "text": "当前段落的合法译文。"}]},
                                ensure_ascii=False,
                            )
                        },
                        "finish_reason": "stop",
                    }
                ]
            }
            with patch("scripts.provider_translator.urlopen", return_value=_Response(response)):
                result = self._translator(manifest_path)(
                    "murasaki-local", "book", ["p2"], source_chars=10, max_tokens=8192
                )
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["summary"]["translated"], 1)
            saved = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["chapters"][0]["paragraphs"][1]["translated"], "当前段落的合法译文。")

    def test_local_request_is_split_before_context_overflow(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest_path = Path(temporary) / "manifest.json"
            source = "日" * 7000
            manifest_path.write_text(
                json.dumps({"id": "book", "chapters": [{"id": "c1", "paragraphs": [{"id": "p1", "source": source, "translated": ""}]}]}, ensure_ascii=False),
                encoding="utf-8",
            )
            requests: list[dict] = []

            def fake_urlopen(request, timeout):
                body = json.loads(request.data)
                requests.append(body)
                local_payload = json.loads(body["messages"][1]["content"])
                response_items = [
                    {"id": item["id"], "text": "译" * len(item["text"])}
                    for item in local_payload["items"]
                ]
                return _Response({"choices": [{"message": {"content": json.dumps({"items": response_items}, ensure_ascii=False)}, "finish_reason": "stop"}]})

            with patch("scripts.provider_translator.urlopen", side_effect=fake_urlopen):
                result = self._translator(manifest_path)(
                    "murasaki-local", "book", ["p1"], source_chars=len(source), max_tokens=8192
                )
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["split"], "single_item_halves")
            self.assertEqual(len(requests), 2)
            self.assertTrue(all(body["max_tokens"] <= 8192 for body in requests))
            self.assertTrue(all(len(body["messages"][1]["content"]) < 8192 for body in requests))


if __name__ == "__main__":
    unittest.main()
