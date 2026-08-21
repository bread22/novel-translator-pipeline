#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from translator.providers.antigravity_bridge import (
    AntigravityBridge,
    build_prompt,
    extract_json_object,
    main,
    make_handler,
    provider_block_reason,
)

if __name__ == "__main__":
    raise SystemExit(main())
