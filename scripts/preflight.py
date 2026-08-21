#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from translator.core.novel_tool import NOVEL_TRANSLATOR_ROOT
from translator.pipeline.chapter_pipeline import manifest_path
from translator.pipeline.preflight import PreflightError, run_preflight
from translator.providers.translator import ProviderTranslator

__all__ = [
    "PreflightError",
    "run_preflight",
]


def main() -> int:
    translator = ProviderTranslator(novel_root=NOVEL_TRANSLATOR_ROOT, manifest=ROOT / "data" / "manifest.json")
    try:
        report = run_preflight(translator)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except PreflightError as exc:
        print(json.dumps(exc.report, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
