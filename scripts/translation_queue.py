#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from translator.pipeline.queue import (
    TranslationQueue,
    display_name,
    ensure_book,
    log,
    main,
    novel_call,
    output_complete,
    registered_books,
    requested_book_id,
    run_pipeline,
    sha256,
    translation_status,
)

if __name__ == "__main__":
    raise SystemExit(main())
