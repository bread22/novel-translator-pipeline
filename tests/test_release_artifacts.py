from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

from scripts.verify_frontend_dist import verify_dist
from translator.core.paths import PathResolver


class ReleaseArtifactTests(unittest.TestCase):
    def test_frontend_verifier_checks_every_local_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            dist = Path(temporary)
            (dist / "assets").mkdir()
            (dist / "assets" / "app.js").write_text("console.log('ok')", encoding="utf-8")
            (dist / "assets" / "app.css").write_text("body{}", encoding="utf-8")
            (dist / "index.html").write_text(
                '<script src="/assets/app.js"></script><link rel="stylesheet" href="/assets/app.css">', encoding="utf-8"
            )
            self.assertEqual(verify_dist(dist)["status"], "ok")
            (dist / "assets" / "app.js").unlink()
            report = verify_dist(dist)
            self.assertEqual(report["status"], "error")
            self.assertIn("missing asset", str(report["errors"]))

    def test_paths_are_stable_from_an_arbitrary_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            resolver = PathResolver.for_config(base / "config.toml")
            previous = Path.cwd()
            try:
                os.chdir(base.parent)
                self.assertEqual(resolver.output_root({"paths": {"output_root": "output"}}), base / "output")
                self.assertEqual(resolver.source_root({"queue": {"source_root": "source"}}), base / "source")
            finally:
                os.chdir(previous)


if __name__ == "__main__":
    unittest.main()
