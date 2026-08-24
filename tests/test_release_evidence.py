from pathlib import Path

from scripts.generate_release_evidence import generate


def test_release_evidence_generation_is_non_mutating(tmp_path: Path) -> None:
    report = generate(tmp_path)
    assert report["status"] == "ok"
    expected = {
        "version.json",
        "frontend-dist.json",
        "config-dry-run.json",
        "glossary-migration-dry-run.json",
        "queue-state-migration-dry-run.json",
        "openapi.json",
    }
    assert expected <= set(report["files"])
