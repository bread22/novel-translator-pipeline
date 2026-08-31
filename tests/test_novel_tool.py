from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from translator.core.novel_tool import (
    NovelToolError,
    call_novel_translator,
    novel_translator_diagnostic,
    resolve_novel_translator_root,
)


class NovelToolTests(unittest.TestCase):
    def test_default_root_points_to_vendored_runtime(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            resolved = resolve_novel_translator_root()
        expected = Path(__file__).resolve().parents[1] / "vendor" / "novel-translator"
        self.assertEqual(resolved, expected.resolve())
        self.assertTrue((resolved / "app").is_dir())
        self.assertTrue((resolved / "LICENSE").is_file())

    def test_environment_root_overrides_vendored_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            override = Path(temporary)
            (override / "main.py").write_text("", encoding="utf-8")
            with patch.dict(os.environ, {"NOVEL_TRANSLATOR_ROOT": str(override)}):
                resolved = resolve_novel_translator_root()
        self.assertEqual(resolved, override.resolve())

    def test_dependency_diagnostic_is_structured(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            diagnostic = novel_translator_diagnostic(Path(temporary), Path(sys.executable))
        self.assertEqual(diagnostic["status"], "error")
        self.assertFalse(diagnostic["checks"]["main_py_exists"])
        self.assertIn("NOVEL_TRANSLATOR_ROOT", diagnostic["setup"])

    def test_unknown_operation_is_structured_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(NovelToolError) as raised:
                call_novel_translator("fixture", novel_root=root, python_bin=Path(sys.executable), timeout=0.2)
            self.assertEqual(raised.exception.result.status, "error")
            self.assertEqual(raised.exception.result.errors[0]["code"], "unsupported_operation")


if __name__ == "__main__":
    unittest.main()
