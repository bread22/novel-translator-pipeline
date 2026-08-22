from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from translator.core.config import (
    fallback_translators_names,
    load_config,
    primary_translator_name,
    reviewer_name,
    setting,
)


class ConfigTests(unittest.TestCase):
    def test_roles_reference_named_providers(self) -> None:
        config = load_config()
        self.assertIn(primary_translator_name(config), config["providers"])
        for fb in fallback_translators_names(config):
            self.assertIn(fb, config["providers"])
        self.assertIn(reviewer_name(config), config["providers"])

    def test_multi_level_fallback_translators(self) -> None:
        config = load_config()
        fallbacks = fallback_translators_names(config)
        self.assertEqual(fallbacks, ["gemini", "mimo", "hy3"])

    def test_reviewer_configured_in_providers(self) -> None:
        config = load_config()
        self.assertIn(reviewer_name(config), config["providers"])
        self.assertIn("model", config["providers"][reviewer_name(config)])

    def test_schema_rejects_undefined_provider_role(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config_file = Path(temporary) / "invalid.toml"
            config_file.write_text(
                """
[paths]
output_root = "output"
translation_policy = "docs/prompts/translation-policy.md"

[roles]
primary_translator = "non_existent_provider"
fallback_translator = "lmstudio"
reviewer = "opencode"

[providers.lmstudio]
type = "openai"
base_url = "http://127.0.0.1:1234/v1"
model = "murasaki-14b-v0.2"
api_key = "lm-studio"
context_tokens = 8192

[providers.opencode]
type = "opencode"
binary = "opencode"
model = "opencode/muse-spark-1.2-contributor-free"
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
