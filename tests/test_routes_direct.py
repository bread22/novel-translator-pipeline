from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile

from fastapi import HTTPException
import pytest

from translator.core.workspace import BookWorkspace, read_json, write_json
from translator.web.models import (
    EnqueueRequest,
    ParagraphUpdateRequest,
    PipelineStartRequest,
    QueueClearRequest,
    QueueConfigUpdateRequest,
    QueueItemMoveRequest,
    QueueReorderRequest,
    RetranslateParagraphRequest,
    TaskStatusResponse,
)
from translator.web.routes import books, knowledge, queue, system, tasks


def _manifest() -> dict:
    return {
        "book": "book-1",
        "title": "Test Book",
        "source_type": "epub",
        "chapters": [{
            "id": "c1",
            "title": "Chapter",
            "paragraphs": [
                {"id": "p1", "source": "one", "translated": "一"},
                {"id": "p2", "source": "two", "translated": ""},
            ],
        }],
    }


def _task(status: str = "running") -> TaskStatusResponse:
    return TaskStatusResponse(task_id="t1", book_id="book-1", status=status)


def test_book_read_update_reset_delete_and_download_routes(tmp_path: Path, monkeypatch) -> None:
    manifest_file = tmp_path / "data" / "books" / "book-1" / "manifest.json"
    write_json(manifest_file, _manifest())
    output = tmp_path / "output"
    workspace = BookWorkspace.at(output, "Test Book")
    workspace.initialize(book_id="book-1")
    write_json(workspace.data_dir / "translation-provenance.json", {
        "items": {"p1": {"provider": "primary", "fallback_from": "fallback", "reason": "partial"}}
    })
    write_json(workspace.chapter_states_dir / "c1.json", {"summary": "summary"})
    monkeypatch.setattr(books, "manifest_path", lambda _book: manifest_file)
    monkeypatch.setattr(books, "get_output_root", lambda: output)
    monkeypatch.setattr(books, "NOVEL_TRANSLATOR_ROOT", tmp_path)

    summary = books.get_book("book-1")
    assert summary.total_paragraphs == 2 and summary.translated_paragraphs == 1
    assert books.list_chapters("book-1")[0].status == "translated"
    detail = books.get_chapter_detail("book-1", "c1")
    assert detail.chapter_summary == "summary"
    assert detail.paragraphs[0].status == "fallback_recovered"
    assert books.update_paragraph("book-1", "p2", ParagraphUpdateRequest(translated="二"))["status"] == "ok"
    assert read_json(manifest_file)["chapters"][0]["paragraphs"][1]["translated"] == "二"

    workspace.epub_path.write_bytes(b"epub")
    response = books.download_book_epub("book-1")
    assert Path(response.path) == workspace.epub_path
    monkeypatch.setattr(books.job_manager, "cancel_book_and_wait", lambda _book: True)
    monkeypatch.setattr(books, "call_novel_translator", lambda *_args: {"status": "ok"})
    reset = books.reset_book("book-1")
    assert reset["status"] == "ok"
    assert read_json(manifest_file)["chapters"][0]["paragraphs"][0]["translated"] == ""
    deleted = books.delete_book("book-1")
    assert deleted["status"] == "ok" and not workspace.root.exists()


def test_list_upload_and_export_book_routes(tmp_path: Path, monkeypatch) -> None:
    books_root = tmp_path / "data" / "books"
    manifest_file = books_root / "book-1" / "manifest.json"
    write_json(manifest_file, _manifest())
    output = tmp_path / "output"
    translated = tmp_path / "translated"
    monkeypatch.setattr(books, "NOVEL_TRANSLATOR_ROOT", tmp_path)
    monkeypatch.setattr(books, "get_output_root", lambda: output)
    monkeypatch.setattr(books, "manifest_path", lambda book_id: books_root / book_id / "manifest.json")
    monkeypatch.setattr(books, "load_config", lambda: {"queue": {"translated_root": str(translated)}})
    assert books.list_books()[0].id == "book-1"

    def novel_call(*args: str) -> dict:
        if args[0] == "add-book":
            uploaded_id = args[args.index("--id") + 1]
            write_json(books_root / uploaded_id / "manifest.json", {
                "book": uploaded_id, "title": "Upload", "source_type": "txt", "chapters": [],
            })
            return {"status": "warning", "returncode": 0, "warnings": ["fixture warning"], "summary": {"book": uploaded_id}}
        if args[0] == "export":
            target = Path(args[args.index("--output") + 1])
            target.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(target, "w") as archive:
                archive.writestr("mimetype", "application/epub+zip")
                archive.writestr("META-INF/container.xml", "<container/>")
            return {"status": "ok"}
        return {"status": "valid"}

    monkeypatch.setattr(books, "call_novel_translator", novel_call)
    class AsyncUpload:
        filename = "Upload.txt"

        def __init__(self) -> None:
            self.sent = False

        async def read(self, _size: int) -> bytes:
            if self.sent:
                return b""
            self.sent = True
            return b"hello"

    upload = AsyncUpload()
    uploaded = asyncio.run(books.upload_book(upload))
    assert uploaded.name == "Upload"
    exported = books.export_book("book-1", layout="preserve")
    assert exported["status"] == "exported"
    assert list(translated.glob("*.epub"))


def test_book_route_errors(tmp_path: Path, monkeypatch) -> None:
    missing = tmp_path / "missing.json"
    monkeypatch.setattr(books, "manifest_path", lambda _book: missing)
    for call in (
        lambda: books.get_book("missing"),
        lambda: books.list_chapters("missing"),
        lambda: books.get_chapter_detail("missing", "c1"),
        lambda: books.update_paragraph("missing", "p1", ParagraphUpdateRequest(translated="x")),
        lambda: books.download_book_epub("missing"),
    ):
        with pytest.raises(HTTPException) as exc:
            call()
        assert exc.value.status_code == 404


def test_system_config_prompt_and_preflight_routes(tmp_path: Path, monkeypatch) -> None:
    config = {
        "providers": {
            "primary": {"type": "openai", "model": "m", "api_key": "secret-key"},
            "idle": {"type": "codex", "model": "c"},
        },
        "roles": {"primary_translator": "primary", "fallback_translators": [], "reviewer": "primary"},
    }
    monkeypatch.setattr(system, "_load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr(system, "load_config", lambda *args, **kwargs: json.loads(json.dumps(config)))
    displayed = system.get_system_config()
    assert "api_key" not in displayed["providers"]["primary"]
    assert displayed["providers"]["primary"]["api_key_preview"] == "••••-key"

    prompts = tmp_path / "prompts"
    prompts.mkdir()
    monkeypatch.setattr(system, "get_prompts_dir", lambda: prompts)
    saved = system.save_prompt({"filename": "Custom Prompt", "content": "# Heading\ntext"})
    assert saved["id"] == "custom-prompt.md"
    assert system.list_prompts()[0]["name"] == "Heading"
    assert system.get_prompt_detail("custom-prompt.md")["content"].endswith("text")
    assert system.delete_prompt("custom-prompt.md")["status"] == "ok"
    with pytest.raises(HTTPException):
        system.save_prompt({"filename": "empty", "content": ""})

    class Provider:
        def __init__(self, name: str) -> None:
            self.name = name

        def health_check(self, timeout: int):
            return {"status": "ok" if self.name == "primary" else "failed", "model": self.name, "error": "offline"}

    monkeypatch.setattr(system, "create_provider", lambda name, _config: Provider(name))
    result = system.run_system_preflight()
    assert result.all_passed
    assert [item.provider for item in result.results] == ["primary", "idle"]
    assert result.results[1].status == "failed"


def test_task_routes_and_retranslate(tmp_path: Path, monkeypatch) -> None:
    manifest_file = tmp_path / "manifest.json"
    write_json(manifest_file, _manifest())
    monkeypatch.setattr(tasks, "manifest_path", lambda _book: manifest_file)
    monkeypatch.setattr(tasks.job_manager, "start_pipeline", lambda _request: _task())
    monkeypatch.setattr(tasks.job_manager, "pause_pipeline", lambda _id: _task("paused"))
    monkeypatch.setattr(tasks.job_manager, "resume_pipeline", lambda _id: _task())
    monkeypatch.setattr(tasks.job_manager, "stop_pipeline", lambda _id: _task("cancelled"))
    monkeypatch.setattr(tasks.job_manager, "get_task", lambda _id: _task())
    monkeypatch.setattr(tasks.job_manager, "list_tasks", lambda: [_task()])
    assert tasks.start_pipeline(PipelineStartRequest(book_id="book-1")).task_id == "t1"
    assert tasks.pause_pipeline("t1").status == "paused"
    assert tasks.resume_pipeline("t1").status == "running"
    assert tasks.stop_pipeline("t1").status == "cancelled"
    assert tasks.get_task_status("t1").book_id == "book-1"
    assert len(tasks.list_tasks()) == 1

    class Translator:
        def __init__(self, **_kwargs) -> None:
            pass

        def __call__(self, *_args, **_kwargs):
            payload = read_json(manifest_file)
            payload["chapters"][0]["paragraphs"][0]["translated"] = "重译"
            write_json(manifest_file, payload)
            return {"status": "ok"}

    monkeypatch.setattr(tasks, "ProviderTranslator", Translator)
    monkeypatch.setattr(tasks, "load_config", lambda: {"roles": {"primary_translator": "primary"}})
    result = tasks.retranslate_paragraph(RetranslateParagraphRequest(book_id="book-1", chapter_id="c1", paragraph_id="p1"))
    assert result["translated"] == "重译" and result["provider"] == "primary"


def test_queue_routes_delegate_and_raise(monkeypatch) -> None:
    status = SimpleNamespace(total_items=0)
    fake = SimpleNamespace(
        get_status=lambda: status,
        enqueue_batch=lambda **_kwargs: [],
        cancel_item=lambda _id: True,
        retry_item=lambda _id: object(),
        move_item=lambda _id, _direction: True,
        reorder=lambda _ids: None,
        pause_queue=lambda: None,
        resume_queue=lambda: None,
        clear=lambda **_kwargs: 0,
        update_config=lambda **_kwargs: None,
    )
    monkeypatch.setattr(queue, "job_manager", fake)
    assert queue.get_queue() is status
    assert queue.enqueue_items(EnqueueRequest(book_ids=["b"])) is status
    assert queue.cancel_queue_item("t") is status
    assert queue.retry_queue_item("t") is status
    assert queue.move_queue_item("t", QueueItemMoveRequest(direction="up")) is status
    assert queue.reorder_queue(QueueReorderRequest(item_ids=["t"])) is status
    assert queue.pause_queue() is status and queue.resume_queue() is status
    assert queue.clear_queue(QueueClearRequest(scope="completed")) is status
    assert queue.update_queue_config(QueueConfigUpdateRequest(concurrency=2)) is status
    with pytest.raises(HTTPException):
        queue.enqueue_items(EnqueueRequest(book_ids=[]))


def test_knowledge_memory_reports_and_invalid_review_warning(tmp_path: Path, monkeypatch) -> None:
    workspace = BookWorkspace.at(tmp_path, "Book")
    workspace.initialize(book_id="book")
    write_json(workspace.book_memory_path, {
        "characters": [{"name": "Legacy", "summary": "old"}],
        "entries": [
            {"key": "Alice", "value": "hero", "category": "character"},
            {"key": "Profiles", "value": "chapter characters", "category": "character_profile"},
            {"key": "Relations", "value": "character graph", "category": "relationship_graph"},
        ],
        "timeline": [{"event": "start"}],
    })
    write_json(workspace.reviews_dir / "c1-output.json", {"checked_ids": [], "unexpected": True})
    write_json(workspace.reviews_dir / "c1-glossary-extract-output.json", {
        "schema_version": "3.0",
        "checked_ids": ["p1"],
        "candidates": [],
    })
    write_json(workspace.reports_dir / "c1.json", {"checked_paragraphs": 2, "applied_fixes": 0})
    monkeypatch.setattr(knowledge, "get_workspace_for_book", lambda _book: workspace)

    memory = knowledge.get_book_memory("book")
    assert {item["name"] for item in memory.characters} == {"Legacy", "Alice", "Profiles", "Relations"}
    reports = knowledge.list_chapter_reports("book")
    assert [item["chapter_id"] for item in reports] == ["c1"]
    assert reports[0]["migration_warning"]
    review = knowledge.get_chapter_review("book", "c1")
    assert review["status"] == "ok" and review["migration_warning"]
    assert knowledge.get_chapter_review("book", "missing")["status"] == "not_found"


def test_report_explains_why_each_unapplied_fix_was_skipped(tmp_path: Path, monkeypatch) -> None:
    workspace = BookWorkspace.at(tmp_path, "Book")
    workspace.initialize(book_id="book")
    review = {
        "checked_ids": ["p1", "p2", "p3"],
        "fixes": [
            {"id": "p1", "category": "mistranslation", "severity": "major", "confidence": 0.95,
             "reason": "objective", "replacement": "修正", "auto_apply": True},
            {"id": "p2", "category": "style", "severity": "minor", "confidence": 0.8,
             "reason": "preference", "replacement": "建议", "auto_apply": False},
            {"id": "p3", "category": "policy_violation", "severity": "critical", "confidence": 0.99,
             "reason": "译文残留外文字符", "replacement": "第二章·兰제里小偷", "auto_apply": True},
        ],
        "glossary_delta": {"add": [], "update": [], "conflicts": []},
        "memory_delta": {"add": [], "update": [], "conflicts": []},
        "chapter_state": {},
    }
    write_json(workspace.reviews_dir / "c1-output.json", review)
    write_json(workspace.reports_dir / "c1.json", {
        "checked_paragraphs": 3, "reported_issues": 3, "applied_fixes": 1,
        "approved_fixes": [review["fixes"][0]], "applied": {"status": "ok"},
    })
    monkeypatch.setattr(knowledge, "get_workspace_for_book", lambda _book: workspace)

    fixes = knowledge.list_chapter_reports("book")[0]["fixes"]
    assert fixes[0]["applied"] is True and fixes[0]["not_applied_reason"] is None
    assert fixes[1]["applied"] is False
    assert "置信度 80% 低于 90% 自动修正门槛" in fixes[1]["not_applied_reason"]
    assert fixes[2]["applied"] is False
    assert fixes[2]["not_applied_reason"] == "建议译文仍含韩文字符，写回安全校验已拦截"
