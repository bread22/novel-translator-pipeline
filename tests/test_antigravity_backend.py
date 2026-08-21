from __future__ import annotations

import json
import unittest

from translator.providers.antigravity_bridge import build_prompt, extract_json_object, provider_block_reason


class AntigravityBackendTests(unittest.TestCase):
    def test_extracts_plain_and_fenced_json(self) -> None:
        payload = '{"items":[{"id":"p1","text":"译文"}]}'
        self.assertEqual(extract_json_object(payload)["items"][0]["id"], "p1")
        self.assertEqual(extract_json_object(f"说明\n```json\n{payload}\n```\n")["items"][0]["text"], "译文")

    def test_extracts_wrapped_json(self) -> None:
        payload = '{"items":[{"id":"p1","text":"译文"}]}'
        self.assertEqual(extract_json_object('{"response": ' + json_quote(payload) + '}')["items"][0]["id"], "p1")

    def test_prompt_keeps_system_and_user_messages(self) -> None:
        prompt = build_prompt([{"role": "system", "content": "系统"}, {"role": "user", "content": "输入"}])
        self.assertIn("--- SYSTEM ---", prompt)
        self.assertIn("--- USER ---", prompt)
        self.assertIn("输入", prompt)

    def test_detects_provider_content_filter(self) -> None:
        self.assertEqual(provider_block_reason("The prompt contains sensitive words"), "content_filter")
        self.assertEqual(provider_block_reason('{"items": []}'), "")


def json_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
