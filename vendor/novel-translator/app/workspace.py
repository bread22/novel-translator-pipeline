from __future__ import annotations

from pathlib import Path
import json

from app.config import QualityConfig
from app.manual import audit_coverage_report
from app.models import Book
from app.quality import quality_report
from app.terminology import (
    Term,
    export_terminology_workspace,
    term_from_dict,
    validate_terms,
)


def prepare_agent_workspace(root_books_dir: Path, book: Book, terms: list[Term], quality_config: QualityConfig, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    terminology_details = export_terminology_workspace(book, output_dir, terms)

    book_summary_path = output_dir / "book-summary.json"
    text_scope_path = output_dir / "text-scope.json"
    quality_dir = output_dir / "quality"
    quality_dir.mkdir(parents=True, exist_ok=True)
    quality_path = quality_dir / "latest-report.json"

    _write_json(
        book_summary_path,
        {
            "id": book.id,
            "title": book.title,
            "source_type": book.source_type,
            "chapters": len(book.chapters),
            "paragraphs": len(book.paragraphs),
            "source_file": book.source_file,
            "coverage": audit_coverage_report(book, terms)["summary"],
        },
    )
    _write_json(
        text_scope_path,
        {
            "book": book.id,
            "chapters": [
                {
                    "id": chapter.id,
                    "title": chapter.title,
                    "index": chapter.index,
                    "paragraphs": [
                        {
                            "id": paragraph.id,
                            "index": paragraph.index,
                            "source": paragraph.source,
                            "translated": paragraph.translated,
                        }
                        for paragraph in chapter.paragraphs
                    ],
                }
                for chapter in book.chapters
            ],
        },
    )
    _write_json(quality_path, quality_report(book, quality_config, terms))

    manifest = {
        "book": book.id,
        "title": book.title,
        "workflow": "novel_translation",
        "files": {
            "book_summary": str(book_summary_path),
            "text_scope": str(text_scope_path),
            "terminology_glossary": terminology_details["glossary"],
            "terminology_contexts": terminology_details["contexts"],
            "quality_latest_report": str(quality_path),
        },
    }
    manifest_path = output_dir / "manifest.json"
    _write_json(manifest_path, manifest)
    return {
        "status": "ok",
        "warnings": [],
        "summary": {
            "book": book.id,
            "workspace": str(output_dir),
            "paragraphs": len(book.paragraphs),
            "terms": len(terms),
        },
        "details": {"manifest": str(manifest_path), "files": manifest["files"]},
    }


def validate_agent_workspace(book: Book, workspace: Path) -> dict:
    errors: list[dict[str, str]] = []
    warnings: list[str] = []
    required = [
        workspace / "manifest.json",
        workspace / "book-summary.json",
        workspace / "text-scope.json",
        workspace / "terminology" / "glossary.json",
        workspace / "terminology" / "contexts" / "term-contexts.json",
        workspace / "quality" / "latest-report.json",
    ]
    loaded: dict[str, object] = {}
    for path in required:
        if not path.exists():
            errors.append({"code": "workspace_file_missing", "message": f"缺少文件：{path}"})
            continue
        try:
            loaded[str(path)] = json.loads(path.read_text(encoding="utf-8"))
        except Exception as error:
            errors.append({"code": "workspace_json_invalid", "message": f"{path} JSON 无法读取：{error}"})

    glossary_path = workspace / "terminology" / "glossary.json"
    if glossary_path.exists():
        try:
            raw = json.loads(glossary_path.read_text(encoding="utf-8"))
            items = raw.get("terms", raw if isinstance(raw, list) else [])
            terms = [term_from_dict(item) for item in items if isinstance(item, dict)]
            term_errors, term_warnings = validate_terms(terms)
            errors.extend({"code": "terminology_invalid", "message": message} for message in term_errors)
            warnings.extend(term_warnings)
        except Exception as error:
            errors.append({"code": "terminology_invalid", "message": f"术语表无法读取：{error}"})

    text_scope = loaded.get(str(workspace / "text-scope.json"))
    if isinstance(text_scope, dict):
        known_ids = {paragraph.id for paragraph in book.paragraphs}
        scope_ids = {
            str(paragraph.get("id", ""))
            for chapter in text_scope.get("chapters", [])
            if isinstance(chapter, dict)
            for paragraph in chapter.get("paragraphs", [])
            if isinstance(paragraph, dict)
        }
        missing = known_ids - scope_ids
        extra = scope_ids - known_ids
        if missing:
            errors.append({"code": "workspace_scope_missing_ids", "message": f"text-scope 缺少 {len(missing)} 个当前书籍段落 ID"})
        if extra:
            errors.append({"code": "workspace_scope_extra_ids", "message": f"text-scope 包含 {len(extra)} 个未知段落 ID"})

    status = "error" if errors else ("warning" if warnings else "ok")
    return {
        "status": status,
        "errors": errors,
        "warnings": warnings,
        "summary": {
            "book": book.id,
            "workspace": str(workspace),
            "required_files": len(required),
            "valid_files": len([path for path in required if path.exists()]),
        },
        "details": {},
    }


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
