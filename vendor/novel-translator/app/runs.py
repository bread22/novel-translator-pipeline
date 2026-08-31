from __future__ import annotations

from pathlib import Path
import json
from datetime import datetime, timezone
from uuid import uuid4

from app.models import book_dir


def runs_dir(root_books_dir: Path, book_id: str) -> Path:
    return book_dir(root_books_dir, book_id) / "runs"


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]


def run_path(root_books_dir: Path, book_id: str, run_id: str) -> Path:
    return runs_dir(root_books_dir, book_id) / f"{run_id}.json"


def load_run(root_books_dir: Path, book_id: str, run_id: str) -> dict:
    path = run_path(root_books_dir, book_id, run_id)
    if not path.exists():
        return {"book": book_id, "run_id": run_id, "created_at": _now(), "batches": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_run(root_books_dir: Path, book_id: str, run: dict) -> Path:
    path = run_path(root_books_dir, book_id, str(run["run_id"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def record_batch(root_books_dir: Path, book_id: str, run_id: str, batch: dict) -> None:
    run = load_run(root_books_dir, book_id, run_id)
    run.setdefault("batches", []).append(batch)
    run["updated_at"] = _now()
    save_run(root_books_dir, book_id, run)


def run_report(root_books_dir: Path, book_id: str, pending_ids: set[str] | None = None) -> dict:
    runs = _load_all_runs(root_books_dir, book_id)
    batches = [batch for run in runs for batch in run.get("batches", [])]
    historical_failed = [batch for batch in batches if batch.get("status") == "failed"]
    failed = [_filter_failed_batch(batch, pending_ids) for batch in historical_failed]
    failed = [batch for batch in failed if batch is not None]
    succeeded = [batch for batch in batches if batch.get("status") == "succeeded"]
    input_chars = sum(int(batch.get("input_chars", 0) or 0) for batch in batches)
    output_chars = sum(int(batch.get("output_chars", 0) or 0) for batch in batches)
    memory_hits = sum(int(batch.get("memory_hits", 0) or 0) for batch in batches)
    rate_limited = sum(1 for batch in batches if batch.get("rate_limited"))
    return {
        "status": "ok" if not failed else "warning",
        "warnings": [f"存在 {len(failed)} 个失败批次"] if failed else [],
        "summary": {
            "book": book_id,
            "runs": len(runs),
            "batches": len(batches),
            "succeeded": len(succeeded),
            "failed": len(failed),
            "historical_failed": len(historical_failed),
            "input_chars": input_chars,
            "output_chars": output_chars,
            "memory_hits": memory_hits,
            "rate_limited": rate_limited,
            "estimated_tokens": sum(int(batch.get("estimated_tokens", 0) or 0) for batch in batches),
            "cost_estimate": round(sum(float(batch.get("cost_estimate", 0) or 0) for batch in batches), 6),
        },
        "details": {"runs": [{"run_id": run.get("run_id"), "batches": len(run.get("batches", []))} for run in runs[-20:]]},
    }


def failed_batches(root_books_dir: Path, book_id: str, pending_ids: set[str] | None = None) -> dict:
    runs = _load_all_runs(root_books_dir, book_id)
    failed = []
    for run in runs:
        for batch in run.get("batches", []):
            if batch.get("status") == "failed":
                filtered = _filter_failed_batch({**batch, "run_id": run.get("run_id")}, pending_ids)
                if filtered is not None:
                    failed.append(filtered)
    return {
        "status": "ok" if not failed else "warning",
        "warnings": [],
        "summary": {"book": book_id, "failed": len(failed)},
        "details": {"batches": failed[-100:]},
    }


def latest_failed_paragraph_ids(root_books_dir: Path, book_id: str, pending_ids: set[str] | None = None) -> list[str]:
    report = failed_batches(root_books_dir, book_id, pending_ids)
    ids = []
    seen = set()
    for batch in report["details"]["batches"]:
        for paragraph_id in batch.get("paragraph_ids", []):
            if pending_ids is not None and paragraph_id not in pending_ids:
                continue
            if paragraph_id not in seen:
                seen.add(paragraph_id)
                ids.append(paragraph_id)
    return ids


def export_run_report(root_books_dir: Path, book_id: str, output: Path) -> dict:
    report = run_report(root_books_dir, book_id)
    failed = failed_batches(root_books_dir, book_id)
    lines = [
        f"# Run Report: {book_id}",
        "",
        f"- Runs: {report['summary'].get('runs', 0)}",
        f"- Batches: {report['summary'].get('batches', 0)}",
        f"- Succeeded: {report['summary'].get('succeeded', 0)}",
        f"- Failed: {report['summary'].get('failed', 0)}",
        f"- Input chars: {report['summary'].get('input_chars', 0)}",
        f"- Output chars: {report['summary'].get('output_chars', 0)}",
        f"- Memory hits: {report['summary'].get('memory_hits', 0)}",
        "",
    ]
    if failed["details"]["batches"]:
        lines.append("## Failed Batches")
        lines.append("")
        for batch in failed["details"]["batches"]:
            lines.append(f"- `{batch.get('batch_id')}`: {batch.get('error', '')}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")
    return {"status": "ok", "warnings": [], "summary": {"book": book_id, "output": str(output)}, "details": report}


def _filter_failed_batch(batch: dict, pending_ids: set[str] | None) -> dict | None:
    if pending_ids is None:
        return batch
    paragraph_ids = [item for item in batch.get("paragraph_ids", []) if item in pending_ids]
    if not paragraph_ids:
        return None
    return {**batch, "paragraph_ids": paragraph_ids, "resolved_by_later_success": len(paragraph_ids) != len(batch.get("paragraph_ids", []))}


def _load_all_runs(root_books_dir: Path, book_id: str) -> list[dict]:
    directory = runs_dir(root_books_dir, book_id)
    if not directory.exists():
        return []
    runs = []
    for path in sorted(directory.glob("*.json")):
        try:
            runs.append(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    return runs


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
