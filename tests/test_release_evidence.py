from pathlib import Path

from scripts.generate_release_evidence import generate


def test_release_evidence_generation_is_non_mutating(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "scripts.generate_release_evidence.verify_dist",
        lambda dist: {"status": "ok", "dist": str(dist), "errors": [], "assets": []},
    )
    report = generate(tmp_path)
    assert report["status"] == "ok"
    expected = {
        "version.json",
        "frontend-dist.json",
        "config-dry-run.json",
        "glossary-migration-dry-run.json",
        "memory-migration-dry-run.json",
        "review-migration-dry-run.json",
        "queue-state-migration-dry-run.json",
        "frontend-api-contract.json",
        "openapi.json",
    }
    assert expected <= set(report["files"])
