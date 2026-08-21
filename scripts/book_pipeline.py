#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from translator.pipeline.chapter_pipeline import (
    IterativePipeline,
    failed_batch_count,
    main,
    manifest_path,
    newly_translated,
    paragraph_map,
    parse_args,
)
from translator.review.reviewer import (
    OBJECTIVE_CATEGORIES,
    OBJECTIVE_SEVERITIES,
    approved_fixes,
    missing_checked_ids,
    validate_chapter_review_payload,
    validate_global_consistency_payload,
    verify_applied_fixes,
)

if __name__ == "__main__":
    raise SystemExit(main())
