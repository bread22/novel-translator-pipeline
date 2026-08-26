from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

from translator.core.novel_tool import NovelToolError, call_novel_translator, novel_translator_diagnostic


class NovelToolTests(unittest.TestCase):
    def test_dependency_diagnostic_is_structured(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            diagnostic = novel_translator_diagnostic(Path(temporary), Path(sys.executable))
        self.assertEqual(diagnostic["status"], "error")
        self.assertFalse(diagnostic["checks"]["main_py_exists"])
        self.assertIn("NOVEL_TRANSLATOR_ROOT", diagnostic["setup"])

    @unittest.skipIf(os.name == "nt", "POSIX process-group assertion")
    def test_timeout_terminates_the_process_group(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "main.py").write_text(
                "import pathlib, subprocess, sys, time\n"
                "child=subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
                "pathlib.Path('child.pid').write_text(str(child.pid))\n"
                "time.sleep(30)\n",
                encoding="utf-8",
            )
            with self.assertRaises(NovelToolError) as raised:
                call_novel_translator("fixture", novel_root=root, python_bin=Path(sys.executable), timeout=0.2)
            self.assertEqual(raised.exception.result.status, "timeout")
            child_pid = int((root / "child.pid").read_text(encoding="utf-8"))
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                stat_path = Path(f"/proc/{child_pid}/stat")
                try:
                    process_state = stat_path.read_text().split()[2]
                except OSError:
                    break
                if process_state == "Z":
                    break
                time.sleep(0.05)
            else:
                self.fail("timed-out child process is still running")


if __name__ == "__main__":
    unittest.main()
