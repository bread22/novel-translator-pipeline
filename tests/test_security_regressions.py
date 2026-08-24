from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import tomli_w

from translator.core.config import load_config, read_env_keys, write_env_key
from translator.core.workspace import safely_extract_epub
from translator.web.path_policy import resolve_under, validate_prompt_filename
from translator.web.routes.system import get_system_config, save_system_config


class SecurityRegressionTests(unittest.TestCase):
    def test_config_get_masks_secret(self) -> None:
        config = {
            "providers": {"p": {"type": "openai", "api_key": "$TEST_MASK_KEY"}},
            "roles": {},
        }
        with patch.dict(os.environ, {"TEST_MASK_KEY": "secret-value-1234"}, clear=False), patch(
            "translator.web.routes.system.load_config", return_value=copy.deepcopy(config)
        ):
            response = get_system_config()
        serialized = repr(response)
        self.assertNotIn("secret-value-1234", serialized)
        self.assertNotIn("api_key':", serialized)
        self.assertEqual(response["providers"]["p"]["api_key_ref"], "$TEST_MASK_KEY")
        self.assertTrue(response["providers"]["p"]["api_key_configured"])
        self.assertEqual(response["providers"]["p"]["api_key_preview"], "••••1234")

    def test_prompt_policy_rejects_escape_and_confusables(self) -> None:
        for value in ("../x.md", "/tmp/x.md", "a/b.md", "a\\b.md", "x\x00.md", "ｘ.md"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_prompt_filename(value)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertEqual(resolve_under(root, "valid.md"), root / "valid.md")
            with self.assertRaises(ValueError):
                resolve_under(root, "../outside.md")

    def test_dotenv_write_is_atomic_quoted_and_private(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ".env"
            write_env_key("TOKEN", " spaces # and 'quotes' ", path)
            self.assertEqual(read_env_keys(path)["TOKEN"], " spaces # and 'quotes' ")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_config_rolls_back_when_secret_commit_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "policy.md").write_text("# policy", encoding="utf-8")
            (root / "output").mkdir()
            candidate = copy.deepcopy(load_config())
            candidate["paths"] = {"output_root": "output", "translation_policy": "policy.md"}
            config_path = root / "config.toml"
            config_path.write_text(tomli_w.dumps(candidate), encoding="utf-8")
            baseline = hashlib.sha256(config_path.read_bytes()).hexdigest()
            candidate["providers"]["nemotron"]["api_key"] = "new-secret"
            with patch("translator.web.routes.system.get_config_path", return_value=config_path), patch(
                "translator.web.routes.system.write_env_keys", side_effect=OSError("fixture failure")
            ):
                with self.assertRaises(Exception):
                    save_system_config(candidate)
            self.assertEqual(hashlib.sha256(config_path.read_bytes()).hexdigest(), baseline)

    def test_zip_bomb_is_rejected_before_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = root / "bomb.epub"
            with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("huge.txt", b"A" * 20_000)
            with self.assertRaisesRegex(ValueError, "膨胀率"):
                safely_extract_epub(archive_path, root / "unpacked", max_compression_ratio=2)
            self.assertFalse((root / "unpacked" / "huge.txt").exists())


if __name__ == "__main__":
    unittest.main()
