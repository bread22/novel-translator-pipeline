from __future__ import annotations

import unittest
from pathlib import Path
import tempfile

from scripts.config import ROOT, load_config
from scripts.opencode_backend import model_for


class ConfigTests(unittest.TestCase):
    def test_roles_reference_named_providers(self) -> None:
        config = load_config()
        self.assertEqual(
            config["roles"],
            {
                "primary_translator": "antigravity",
                "fallback_translator": "lmstudio",
                "reviewer": "opencode",
            },
        )
        self.assertTrue(set(config["roles"].values()) <= set(config["providers"]))
        self.assertNotIn("primary", config["providers"])
        self.assertNotIn("murasaki_local", config["providers"])

    def test_reviewer_model_comes_from_opencode_provider(self) -> None:
        config = load_config()
        self.assertEqual(config["providers"]["opencode"]["model"], "opencode/muse-spark-1.2-contributor-free")
        self.assertEqual(model_for("reviewer"), config["providers"]["opencode"]["model"])

    def test_schema_rejects_legacy_provider_role(self) -> None:
        source = (ROOT / "config.toml").read_text(encoding="utf-8")
        invalid = source.replace('fallback_translator = "lmstudio"', 'fallback_translator = "murasaki-local"', 1)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            path.write_text(invalid, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "murasaki-local"):
                load_config(path)


if __name__ == "__main__":
    unittest.main()
