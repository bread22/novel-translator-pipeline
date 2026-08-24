from __future__ import annotations

import subprocess
import sys
import unittest


class ImportOrderTests(unittest.TestCase):
    def test_core_import_does_not_eagerly_construct_web_app(self) -> None:
        script = (
            "import sys; import translator.core.queue_manager; "
            "assert 'translator.web.app' not in sys.modules"
        )
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_web_factory_imports_in_fresh_interpreter(self) -> None:
        script = "from translator.web import create_app; assert create_app().title"
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
