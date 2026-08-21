from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from translator.core.config import load_config, setting


class ConfigTests(unittest.TestCase):
    def test_roles_reference_named_providers(self) -> None:
        config = load_config()
        self.assertIn(config["roles"]["primary_translator"], config["providers"])
        self.assertIn(config["roles"]["fallback_translator"], config["providers"])
        self.assertIn(config["roles"]["reviewer"], config["providers"])

    def test_reviewer_model_comes_from_opencode_provider(self) -> None:
        config = load_config()
        self.assertEqual(config["roles"]["reviewer"], "opencode")
        self.assertEqual(
            setting(config, "providers.opencode.model", "OPENCODE_MODEL"),
            config["providers"]["opencode"]["model"],
        )

    def test_schema_rejects_legacy_provider_role(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config_file = Path(temporary) / "invalid.toml"
            config_file.write_text(
                """
[paths]
output_root = "output"
translation_policy = "docs/prompts/translation-policy.md"

[roles]
primary_translator = "gemini"
fallback_translator = "lmstudio"
reviewer = "opencode"

[providers.lmstudio]
base_url = "http://127.0.0.1:1234/v1"
model = "murasaki-14b-v0.2"
api_key = "lm-studio"
context_tokens = 8192

[providers.antigravity]
base_url = "http://127.0.0.1:1235/v1"
api_key = "antigravity"
host = "127.0.0.1"
port = 1235
agy = "agy"
model = "gemini-3.7-flash"
effort = "low"
timeout = 600
concurrency = 1
context_tokens = 1048576

[providers.opencode]
binary = "opencode"
model = "opencode/muse-spark-1.2-contributor-free"
agent = ""
timeout = 600

[providers.codex]
binary = "codex"
model = ""
reasoning_effort = ""
timeout = 600

[pipeline]
max_cycles = 1000
max_chapter_batches = 1000
primary_batch_max_chars = 4000
max_provider_split_depth = 8
translation_max_tokens = 8192
health_check_timeout = 60
layout = "preserve"

[queue]
source_root = "source"
max_cycles = 1000
apply = true
autonomous = true
finalize = true
stop_on_error = true
""",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_config(config_file)


if __name__ == "__main__":
    unittest.main()
