#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from translator.review.reviewer import cli_main, review_book

if __name__ == "__main__":
    cli_main()
