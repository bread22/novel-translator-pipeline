from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from app.models import book_dir


def control_dir(root_books_dir: Path, book_id: str) -> Path:
    return book_dir(root_books_dir, book_id) / "control"


def stop_request_path(root_books_dir: Path, book_id: str) -> Path:
    return control_dir(root_books_dir, book_id) / "stop-request.json"


def request_stop(root_books_dir: Path, book_id: str, reason: str = "") -> dict:
    path = stop_request_path(root_books_dir, book_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"book": book_id, "requested_at": _now(), "reason": reason}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "status": "ok",
        "warnings": [],
        "summary": {"book": book_id, "stop_requested": True, "path": str(path)},
        "details": payload,
    }


def clear_stop(root_books_dir: Path, book_id: str) -> dict:
    path = stop_request_path(root_books_dir, book_id)
    existed = path.exists()
    if existed:
        path.unlink()
    return {
        "status": "ok",
        "warnings": [],
        "summary": {"book": book_id, "cleared": existed, "stop_requested": False},
        "details": {"path": str(path)},
    }


def stop_requested(root_books_dir: Path, book_id: str) -> bool:
    return stop_request_path(root_books_dir, book_id).exists()


def task_status(root_books_dir: Path, book_id: str) -> dict:
    path = stop_request_path(root_books_dir, book_id)
    payload = {}
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {"path": str(path), "invalid": True}
    return {
        "status": "warning" if path.exists() else "ok",
        "warnings": ["已请求停止，运行中的翻译会在当前批次结束后退出。"] if path.exists() else [],
        "summary": {"book": book_id, "stop_requested": path.exists()},
        "details": payload,
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
