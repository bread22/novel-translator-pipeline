from __future__ import annotations

import json
from pathlib import Path
import threading

import pytest

from translator.core import config
from translator.core.job_control import CancellationToken, JobCancelled, PauseGate
from translator.core.state_migrations import migrate_queue_state_v1
from translator.core.workspace import merge_chapter_state, merge_memory_delta, merge_term_updates, normalize_book_memory_v2, write_json


def test_schema_validator_exercises_composition_and_constraints() -> None:
    schema = {
        "type": "object",
        "required": ["value", "items"],
        "additionalProperties": False,
        "properties": {
            "value": {"type": "string", "minLength": 3, "pattern": "^[A-Z]", "enum": ["ABC"]},
            "items": {"type": "array", "minItems": 2, "maxItems": 3, "items": {"type": "integer", "minimum": 1, "maximum": 5}},
        },
        "allOf": [{"type": "object"}],
    }
    errors = config._schema_errors({"value": "x", "items": [0, 9, 2, 3], "extra": True}, schema, schema)
    assert any("字符串过短" in error for error in errors)
    assert any("元素数量大于" in error for error in errors)
    assert any("schema 未定义" in error for error in errors)
    assert config._schema_errors("x", {"oneOf": [{"type": "string"}, {"type": "string"}]}, {})
    assert config._schema_errors(1, {"type": "string"}, {})
    with pytest.raises(ValueError):
        config._resolve_ref({}, "external")
    with pytest.raises(ValueError):
        config._resolve_ref({"x": 1}, "#/x")


def test_config_env_helpers_and_role_validation(tmp_path: Path, monkeypatch) -> None:
    env = tmp_path / ".env"
    config.write_env_keys({"VALID_KEY": "value"}, env)
    assert config.read_env_keys(env) == {"VALID_KEY": "value"}
    with pytest.raises(ValueError):
        config.write_env_keys({"NOT-VALID": "x"}, env)
    monkeypatch.setenv("FALLBACK_TRANSLATORS", "a, b")
    assert config.fallback_translators_names({}) == ["a", "b"]
    monkeypatch.setenv("DUAL_REVIEW", "yes")
    assert config.dual_review_enabled({})
    monkeypatch.setenv("OVERRIDE", "env")
    assert config.setting({"x": "file"}, "x", "OVERRIDE") == "env"
    with pytest.raises(KeyError):
        config.config_value({}, "missing.value")

    base = config.load_config()
    for role, value in (
        ("primary_translator", "missing"),
        ("reviewer", "missing"),
        ("secondary_reviewer", "missing"),
        ("fallback_translator", "missing"),
        ("secondary_fallback_translator", "missing"),
        ("fallback_reviewers", ["missing"]),
    ):
        candidate = json.loads(json.dumps(base))
        candidate["roles"][role] = value
        with pytest.raises(ValueError):
            config.validate_config_data(candidate)


def test_job_control_pause_resume_and_cancel() -> None:
    event = threading.Event()
    token = CancellationToken(event)
    assert token.event is event and not token.is_cancelled()
    token.cancel()
    with pytest.raises(JobCancelled):
        token.check()
    gate = PauseGate()
    assert gate.event.is_set()
    gate.pause()
    assert not gate.event.is_set()
    gate.resume()
    gate.wait(CancellationToken())


def test_migration_and_merge_edge_branches(tmp_path: Path) -> None:
    source = tmp_path / "queue.json"
    destination = tmp_path / "v2.json"
    source.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError):
        migrate_queue_state_v1(source, destination)
    source.write_text(json.dumps({"schema_version": 9, "items": {}}), encoding="utf-8")
    with pytest.raises(ValueError):
        migrate_queue_state_v1(source, destination)
    write_json(source, {"items": {"bad": "value", "unknown": {"status": "mystery"}}})
    report = migrate_queue_state_v1(source, destination)
    assert len(report["warnings"]) == 2

    normalized, stats = normalize_book_memory_v2({
        "entries": [{"key": "A", "value": "same"}],
        "characters": [None, {"name": "", "summary": ""}, {"name": "A", "summary": "same"}],
    })
    assert stats["modified"] == 1 and normalized["entries"][0]["key"] == "A"
    memory, summary = merge_memory_delta(
        {"entries": [{"key": "A", "value": "old", "confidence": 0.5}]},
        {"add": [{"key": "A", "value": "old", "confidence": 1, "note": "new"}], "update": "bad"},
        "c1",
    )
    assert summary["updated"] == 1 and summary["rejected"] == 1 and memory["entries"][0]["note"] == "new"
    glossary, term_summary = merge_term_updates(
        {"terms": [{"source": "A", "target": "old", "confidence": 0.5}]},
        [{"source": "A", "target": "old", "confidence": 1, "note": "note"}],
        "c1",
    )
    assert term_summary["confirmed"] == 1 and glossary["terms"][0]["note"] == "note"
    state = merge_chapter_state({"chapter_id": "old"}, {"summary": "new"}, chapter_id_kw="c1")
    assert state["chapter_id"] == "c1" and state["status"] == "reviewed"
