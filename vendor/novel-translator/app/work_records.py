from __future__ import annotations

from pathlib import Path
import shutil

from app.models import Book


RECORD_SUBDIRS = ("logs", "reports", "workspace", "imports", "delivery")


def init_work_records(book: Book, records_root: Path) -> dict:
    record_dir = records_root / book.id
    for subdir in RECORD_SUBDIRS:
        (record_dir / subdir).mkdir(parents=True, exist_ok=True)
    readme = record_dir / "README.md"
    if not readme.exists():
        readme.write_text(_readme(book), encoding="utf-8")
    return _report(book, record_dir, copied=[])


def collect_external_logs(book: Book, records_root: Path, log_dir: Path, pattern: str = "translate*") -> dict:
    report = init_work_records(book, records_root)
    record_dir = Path(report["summary"]["record_dir"])
    copied = []
    if log_dir.exists():
        for path in sorted(log_dir.glob(pattern)):
            if path.is_file():
                target = record_dir / "logs" / path.name
                if path.resolve() != target.resolve():
                    shutil.copy2(path, target)
                    copied.append(str(target))
    return _report(book, record_dir, copied=copied)


def collect_files(book: Book, records_root: Path, paths: list[Path], subdir: str = "imports") -> dict:
    report = init_work_records(book, records_root)
    record_dir = Path(report["summary"]["record_dir"])
    target_dir = record_dir / subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    copied = []
    warnings = []
    for path in paths:
        if not path.exists() or not path.is_file():
            warnings.append(f"文件不存在或不是普通文件：{path}")
            continue
        target = target_dir / path.name
        if path.resolve() != target.resolve():
            shutil.copy2(path, target)
            copied.append(str(target))
    result = _report(book, record_dir, copied=copied)
    result["warnings"] = warnings
    result["status"] = "warning" if warnings else "ok"
    return result


def _report(book: Book, record_dir: Path, copied: list[str]) -> dict:
    return {
        "status": "ok",
        "warnings": [],
        "summary": {
            "book": book.id,
            "record_dir": str(record_dir),
            "copied": len(copied),
        },
        "details": {
            "subdirs": {subdir: str(record_dir / subdir) for subdir in RECORD_SUBDIRS},
            "copied": copied,
        },
    }


def _readme(book: Book) -> str:
    return "\n".join(
        [
            f"# {book.title}",
            "",
            f"- 书籍 ID：`{book.id}`",
            f"- 类型：`{book.source_type}`",
            f"- 源文件：`{book.source_file}`",
            "",
            "## 目录约定",
            "",
            "- `logs/`：后台脚本、标准输出、错误输出。",
            "- `reports/`：运行报告、质量报告、EPUB 风险报告、审校报告。",
            "- `workspace/`：Agent 工作区、人工修复表、临时上下文文件。",
            "- `imports/`：外部术语表、翻译记忆、人工导入文件。",
            "- `delivery/`：本书导出的交付包或中间交付文件。",
            "",
            "核心翻译状态仍以项目内 `data/books/<书籍ID>/` 为准，本目录用于收纳过程记录和交付材料。",
            "",
        ]
    )
