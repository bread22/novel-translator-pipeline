#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

from translator.web.app import app


ROOT = Path(__file__).resolve().parents[1]
TYPE_FILE = ROOT / "frontend" / "src" / "types" / "api.ts"
SCHEMAS = (
    "BookSummary", "ChapterSummary", "ParagraphItem", "ChapterDetail",
    "TaskStatusResponse", "PipelineStartRequest", "GlossaryItem", "GlossaryResponse",
    "BookMemoryResponse", "PreflightProviderResult", "PreflightResponse", "QueueItem",
    "QueueStatusResponse", "EnqueueRequest",
)


def interface_fields(source: str, name: str) -> set[str]:
    match = re.search(rf"export interface {re.escape(name)}\s*\{{", source)
    if not match:
        raise ValueError(f"missing TypeScript interface: {name}")
    depth = 1
    fields: set[str] = set()
    for line in source[match.end():].splitlines():
        if depth == 1:
            field = re.match(r"^\s{2}([A-Za-z_][A-Za-z0-9_]*)\??:", line)
            if field:
                fields.add(field.group(1))
        depth += line.count("{") - line.count("}")
        if depth <= 0:
            break
    return fields


def check_contract() -> list[str]:
    source = TYPE_FILE.read_text(encoding="utf-8")
    schemas = app.openapi()["components"]["schemas"]
    failures: list[str] = []
    for name in SCHEMAS:
        backend = set(schemas[name].get("properties", {}))
        frontend = interface_fields(source, name)
        if backend != frontend:
            failures.append(
                f"{name}: missing={sorted(backend - frontend)}, extra={sorted(frontend - backend)}"
            )
    return failures


def main() -> int:
    failures = check_contract()
    if failures:
        print("Frontend/OpenAPI contract mismatch:")
        print("\n".join(f"- {failure}" for failure in failures))
        return 1
    print(f"Frontend/OpenAPI contract OK: {len(SCHEMAS)} interfaces")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
