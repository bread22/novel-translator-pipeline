from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from translator.providers.translator import ProviderTranslator


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

            with patch("translator.providers.openai_provider.urlopen", side_effect=fake_urlopen):
                result = self._translator(manifest_path)(
                    "lmstudio", "book", ["p2"], source_chars=10, max_tokens=8192
                )
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["format"], "single_plain_text")
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
            with patch("translator.providers.openai_provider.urlopen", return_value=_Response(response)):
                result = self._translator(manifest_path)(
                    "lmstudio", "book", ["p2"], source_chars=10, max_tokens=8192
                )
            self.assertEqual(result["status"], "error")
            self.assertEqual(result["reason"], "output_format")
            self.assertEqual(result["finish_reason"], "length")

    def test_repeated_response_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest_path = Path(temporary) / "manifest.json"
            _manifest(manifest_path)
            repeated_line = "这是一个很长很长的重复行，用来测试生成模型是否陷入了重复输出循环。"
            repeated_text = f"{repeated_line}\\n{repeated_line}"
            response = {
                "choices": [
                    {
                        "message": {"content": f'{{"items":[{{"id":"p2","text":"{repeated_text}"}}]}}'},
                        "finish_reason": "stop",
                    }
                ]
            }
            with patch("translator.providers.openai_provider.urlopen", return_value=_Response(response)):
                result = self._translator(manifest_path)(
                    "lmstudio", "book", ["p2"], source_chars=10, max_tokens=8192
                )
            self.assertEqual(result["status"], "error")
            self.assertEqual(result["reason"], "output_format")
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
            with patch("translator.providers.openai_provider.urlopen", return_value=_Response(response)):
                result = self._translator(manifest_path)(
                    "lmstudio", "book", ["p2"], source_chars=10, max_tokens=8192
                )
            self.assertEqual(result["status"], "error")
            self.assertEqual(result["reason"], "output_format")
            self.assertEqual(result["validation"]["kind"], "previous_context_overlap")

    def test_valid_json_response_is_written(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest_path = Path(temporary) / "manifest.json"
            _manifest(manifest_path)
            response = {
                "choices": [
                    {
                        "message": {"content": '{"items":[{"id":"p2","text":"正常的译文。"}]}'},
                        "finish_reason": "stop",
                    }
                ]
            }
            with patch("translator.providers.openai_provider.urlopen", return_value=_Response(response)):
                result = self._translator(manifest_path)(
                    "lmstudio", "book", ["p2"], source_chars=10, max_tokens=8192
                )
            self.assertEqual(result["status"], "ok")
            saved = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["chapters"][0]["paragraphs"][1]["translated"], "正常的译文。")

    def test_legal_source_text_reference_is_written(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest_path = Path(temporary) / "manifest.json"
            _manifest(manifest_path)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["chapters"][0]["paragraphs"][1]["source"] = "変体仮名で「くじり」と書いてあった。"
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            translated = "用变体假名写着“くじり”二字。"
            response = {
                "choices": [{
                    "message": {
                        "content": json.dumps(
                            {"items": [{"id": "p2", "text": translated}]},
                            ensure_ascii=False,
                        ),
                    },
                    "finish_reason": "stop",
                }]
            }
            with patch("translator.providers.openai_provider.urlopen", return_value=_Response(response)):
                result = self._translator(manifest_path)(
                    "lmstudio", "book", ["p2"], source_chars=20, max_tokens=8192
                )

            self.assertEqual(result["status"], "ok")
            saved = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["chapters"][0]["paragraphs"][1]["translated"], translated)

    def test_local_request_is_split_before_context_overflow(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manifest_path = Path(temporary) / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "id": "book",
                        "chapters": [
                            {
                                "id": "c1",
                                "title": "Chapter",
                                "paragraphs": [
                                    {"id": "p1", "source": "A" * 6000, "translated": ""},
                                    {"id": "p2", "source": "B" * 6000, "translated": ""},
                                ],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            calls: list[list[str]] = []

            def fake_urlopen(request, timeout):
                data = json.loads(request.data)
                ids = [item["id"] for item in json.loads(data["messages"][1]["content"])["items"]]
                calls.append(ids)
                items = [{"id": item_id, "text": f"translated-{item_id}"} for item_id in ids]
                return _Response({"choices": [{"message": {"content": json.dumps({"items": items})}, "finish_reason": "stop"}]})

            with patch("translator.providers.openai_provider.urlopen", side_effect=fake_urlopen):
                result = self._translator(manifest_path)(
                    "lmstudio", "book", ["p1", "p2"], source_chars=12000, max_tokens=8192
                )
            self.assertEqual(result["status"], "ok")
            self.assertEqual(calls, [["p1"], ["p2"]])
            saved = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["chapters"][0]["paragraphs"][0]["translated"], "translated-p1")
            self.assertEqual(saved["chapters"][0]["paragraphs"][1]["translated"], "translated-p2")


if __name__ == "__main__":
    unittest.main()
