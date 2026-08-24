from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any

from translator.core.workspace import write_json
from translator.web.models import PipelineStartRequest, QueueItem


QUEUE_V1_STATUS_MAP = {
    "pending": "pending",
    "running": "recovery_pending",
    "paused": "recovery_pending",
    "completed": "completed",
    "failed": "failed",
    "cancelled": "cancelled",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _backup_path(source: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    return source.with_name(f"{source.name}.v1.{stamp}.bak")


def migrate_queue_state_v1(
    source: Path,
    destination: Path,
    *,
    apply: bool = False,
    process_id: str | None = None,
) -> dict[str, Any]:
    """Build a v2 queue snapshot without mutating the v1 source file."""
    original = source.read_bytes()
    payload = json.loads(original)
    if not isinstance(payload, dict):
        raise ValueError("queue v1 state must be a JSON object")
    version = payload.get("schema_version", 1)
    if version not in {None, 1, "1", "1.0"}:
        raise ValueError(f"unsupported queue source schema: {version!r}")
    raw_items = payload.get("items", {})
    if not isinstance(raw_items, dict):
        raise ValueError("queue v1 items must be a JSON object")

    migrated_items: dict[str, dict[str, Any]] = {}
    status_counts: Counter[str] = Counter()
    warnings: list[dict[str, str]] = []
    now = _utc_now()
    for item_id, raw in raw_items.items():
        if not isinstance(raw, dict):
            warnings.append({"item_id": str(item_id), "warning": "non-object item skipped"})
            continue
        item_data = dict(raw)
        old_status = str(item_data.get("status", "pending"))
        new_status = QUEUE_V1_STATUS_MAP.get(old_status)
        if new_status is None:
            warnings.append({"item_id": str(item_id), "warning": f"unknown status {old_status!r}; mapped to failed"})
            new_status = "failed"
        status_counts[f"{old_status}->{new_status}"] += 1
        item_data.update(
            {
                "id": str(item_data.get("id") or item_id),
                "book_id": str(item_data.get("book_id") or item_id),
                "book_name": str(item_data.get("book_name") or item_data.get("book_id") or item_id),
                "options": item_data.get("options") or PipelineStartRequest(book_id=str(item_data.get("book_id") or item_id)).model_dump(),
                "status": new_status,
                "enqueued_at": str(item_data.get("enqueued_at") or now),
                "process_id": process_id,
            }
        )
        if old_status in {"running", "paused"}:
            item_data["recovery_reason"] = f"v1 migration: original status {old_status}"
            item_data["message"] = "迁移后等待恢复调度"
        try:
            validated_item = QueueItem.model_validate(item_data)
        except Exception as exc:
            warnings.append({"item_id": str(item_id), "warning": f"validation failed; item skipped: {exc}"})
            continue
        migrated_items[validated_item.id] = validated_item.model_dump()

    eligible = {item_id for item_id, item in migrated_items.items() if item["status"] in {"pending", "recovery_pending"}}
    pending_order: list[str] = []
    raw_order = payload.get("pending_order", [])
    if isinstance(raw_order, list):
        for item_id in raw_order:
            normalized = str(item_id)
            if normalized in eligible and normalized not in pending_order:
                pending_order.append(normalized)
    pending_order.extend(item_id for item_id in migrated_items if item_id in eligible and item_id not in pending_order)
    for index, item_id in enumerate(pending_order, start=1):
        migrated_items[item_id]["order_index"] = index
    for item_id, item in migrated_items.items():
        if item_id not in eligible:
            item["order_index"] = 0

    updated = {
        "schema_version": 2,
        "process_id": process_id,
        "is_paused": bool(payload.get("is_paused", True)),
        "concurrency": max(1, min(4, int(payload.get("concurrency", 1)))),
        "stop_on_error": bool(payload.get("stop_on_error", False)),
        "pending_order": pending_order,
        "items": migrated_items,
        "updated_at": now,
    }
    rendered = (json.dumps(updated, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    backup: Path | None = None
    if apply:
        backup = _backup_path(source)
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, backup)
        write_json(destination, updated)
    return {
        "source": str(source),
        "destination": str(destination),
        "mode": "apply" if apply else "dry-run",
        "source_preserved": source.exists() and source.read_bytes() == original,
        "backup": str(backup) if backup else None,
        "before_sha256": _sha256(original),
        "after_sha256": _sha256(rendered),
        "items_before": len(raw_items),
        "items_after": len(migrated_items),
        "pending_after": len(pending_order),
        "status_mappings": dict(sorted(status_counts.items())),
        "warnings": warnings,
        "payload": updated,
    }
