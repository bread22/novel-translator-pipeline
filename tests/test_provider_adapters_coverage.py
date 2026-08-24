from __future__ import annotations

import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from translator.providers import antigravity, codex, opencode, registry
from translator.providers.antigravity import AntigravityProvider
from translator.providers.codex import CodexProvider


PAYLOAD = {"items": [{"id": "p1", "source": "hello"}]}
TRANSLATION = json.dumps({"items": [{"id": "p1", "text": "你好"}]})


def test_antigravity_prompt_process_health_translate_and_review(tmp_path: Path, monkeypatch) -> None:
    provider = AntigravityProvider("agy", {"agy": "agy", "model": "m", "effort": "", "timeout": 3})
    prompt = antigravity.build_prompt([{"role": "user", "content": [{"text": "hello"}]}])
    assert "USER" in prompt and "hello" in prompt
    monkeypatch.setattr(antigravity.shutil, "which", lambda _binary: None)
    with pytest.raises(RuntimeError):
        provider._run_agy("prompt")
    assert provider.health_check()["status"] == "error"

    schema = tmp_path / "schema.json"
    schema.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(antigravity.shutil, "which", lambda _binary: "/bin/agy")
    monkeypatch.setattr(antigravity.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout='{"ok":true}', stderr=""))
    assert provider._run_agy("prompt", schema_path=schema) == '{"ok":true}'
    assert provider.health_check()["status"] == "ok"

    monkeypatch.setattr(provider, "_run_agy", lambda *_args, **_kwargs: '{"ok":false}')
    assert provider.health_check()["status"] == "error"
    monkeypatch.setattr(provider, "_run_agy", lambda *_args, **_kwargs: TRANSLATION)
    items, meta = provider.translate(PAYLOAD, "system", 100)
    assert items[0]["text"] == "你好" and meta["status"] == "ok"
    monkeypatch.setattr(provider, "_run_agy", lambda *_args, **_kwargs: '{"items":[{"id":"wrong","text":"x"}]}')
    assert provider.translate(PAYLOAD, "system", 100)[1]["reason"] == "output_format"
    monkeypatch.setattr(provider, "_run_agy", lambda *_args, **_kwargs: "not json")
    assert provider.translate(PAYLOAD, "system", 100)[1]["reason"] == "output_format"
    monkeypatch.setattr(provider, "_run_agy", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("process")))
    assert provider.translate(PAYLOAD, "system", 100)[1]["reason"] == "process"
    monkeypatch.setattr(provider, "_run_agy", lambda *_args, **_kwargs: '{"checked_ids":[],"fixes":[]}')
    assert provider.review("chapter", {}, schema)["fixes"] == []


def test_antigravity_process_failures(monkeypatch) -> None:
    provider = AntigravityProvider("agy", {})
    monkeypatch.setattr(antigravity.shutil, "which", lambda _binary: "/bin/agy")
    monkeypatch.setattr(antigravity.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=2, stdout="", stderr="bad"))
    with pytest.raises(RuntimeError, match="execution failed"):
        provider._run_agy("prompt")


def _codex_runner(payload: object, *, returncode: int = 0):
    def run(command, **_kwargs):
        output_path = Path(command[command.index("-o") + 1])
        if payload is not None:
            output_path.write_text(json.dumps(payload) if not isinstance(payload, str) else payload, encoding="utf-8")
        return SimpleNamespace(returncode=returncode, stdout="out", stderr="err")
    return run


def test_codex_health_translate_and_review(tmp_path: Path, monkeypatch) -> None:
    provider = CodexProvider("codex", {"binary": "codex", "model": "m", "reasoning_effort": "low"})
    monkeypatch.setattr(codex.shutil, "which", lambda _binary: None)
    assert provider.health_check()["status"] == "error"
    with pytest.raises(RuntimeError):
        provider._executable()

    monkeypatch.setattr(codex.shutil, "which", lambda _binary: "/bin/codex")
    monkeypatch.setattr(codex.subprocess, "run", _codex_runner({"ok": True}))
    assert provider.health_check()["status"] == "ok"
    monkeypatch.setattr(codex.subprocess, "run", _codex_runner(None, returncode=1))
    assert provider.health_check()["status"] == "error"
    monkeypatch.setattr(codex.subprocess, "run", _codex_runner("invalid"))
    assert provider.health_check()["status"] == "error"

    monkeypatch.setattr(codex.subprocess, "run", _codex_runner({"items": [{"id": "p1", "text": "你好"}]}))
    items, meta = provider.translate(PAYLOAD, "system", 100)
    assert items and meta["status"] == "ok"
    monkeypatch.setattr(codex.subprocess, "run", _codex_runner({"items": [{"id": "wrong", "text": "x"}]}))
    assert provider.translate(PAYLOAD, "system", 100)[1]["reason"] == "output_format"
    monkeypatch.setattr(codex.subprocess, "run", _codex_runner(None, returncode=1))
    assert provider.translate(PAYLOAD, "system", 100)[1]["reason"] == "process"

    schema = tmp_path / "schema.json"
    schema.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(codex.subprocess, "run", _codex_runner({"checked_ids": []}))
    assert provider.review("chapter", {}, schema)["checked_ids"] == []
    monkeypatch.setattr(codex.subprocess, "run", _codex_runner(None, returncode=2))
    with pytest.raises(RuntimeError, match="review failed"):
        provider.review("chapter", {}, schema)


def test_codex_health_timeout(monkeypatch) -> None:
    provider = CodexProvider("codex", {})
    monkeypatch.setattr(codex.shutil, "which", lambda _binary: "/bin/codex")
    monkeypatch.setattr(codex.subprocess, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("x", 1)))
    assert "timed out" in provider.health_check(timeout=1)["error"]


def test_registry_explicit_and_inferred_provider_types() -> None:
    configs = {
        "open": {"type": "http", "base_url": "http://x", "model": "m"},
        "agy": {"type": "antigravity"},
        "oc": {"type": "opencode"},
        "cx": {"type": "codex"},
        "auto_agy": {"agy": "agy"},
        "auto_oc": {"binary": "opencode"},
        "auto_cx": {"binary": "codex"},
        "auto_http": {"base_url": "http://x", "model": "m"},
    }
    cfg = {"providers": configs}
    expected = {
        "open": "OpenAIProvider", "agy": "AntigravityProvider", "oc": "OpenCodeProvider", "cx": "CodexProvider",
        "auto_agy": "AntigravityProvider", "auto_oc": "OpenCodeProvider", "auto_cx": "CodexProvider", "auto_http": "OpenAIProvider",
    }
    for name, class_name in expected.items():
        assert type(registry.get_provider(name, cfg)).__name__ == class_name
    with pytest.raises(ValueError):
        registry.get_provider("missing", cfg)
    with pytest.raises(ValueError):
        registry.get_provider("unknown", {"providers": {"unknown": {}}})


def test_opencode_helpers_and_health(monkeypatch) -> None:
    events = '\n'.join([
        'noise',
        '{"type":"text","text":"A"}',
        '{"type":"message.part","part":{"text":"B"}}',
        '{"type":"assistant","content":[{"text":"C"}]}',
    ])
    assert opencode._event_text(events) == "ABC"
    assert opencode.parse_json_object('prefix {"ok":true} suffix') == {"ok": True}
    with pytest.raises(ValueError):
        opencode.parse_json_object("none")
    monkeypatch.setattr(opencode, "run_json", lambda *_args, **_kwargs: {"ok": True})
    monkeypatch.setattr(opencode, "model_for", lambda _role: "model")
    assert opencode.check()["status"] == "ok"
    monkeypatch.setattr(opencode, "run_json", lambda *_args, **_kwargs: {"ok": False})
    assert opencode.check()["status"] == "error"
    monkeypatch.setattr(opencode, "run_json", lambda *_args, **_kwargs: (_ for _ in ()).throw(opencode.OpenCodeError("bad")))
    assert opencode.check()["status"] == "error"


def test_opencode_prompt_and_provider_paths(tmp_path: Path, monkeypatch) -> None:
    success = SimpleNamespace(returncode=0, stdout='{"type":"text","text":"{\\"items\\":[{\\"id\\":\\"p1\\",\\"text\\":\\"ok\\"}]}"}\n', stderr="")
    monkeypatch.setattr(opencode.subprocess, "run", lambda *_args, **_kwargs: success)
    assert "items" in opencode.run_prompt("translate", binary="opencode", model="m", agent="a", max_retries=1)
    with pytest.raises(ValueError):
        opencode.run_prompt("x", timeout=0, binary="opencode")

    blocked = SimpleNamespace(returncode=1, stdout="content policy", stderr="")
    monkeypatch.setattr(opencode.subprocess, "run", lambda *_args, **_kwargs: blocked)
    with pytest.raises(opencode.OpenCodeError) as exc:
        opencode.run_prompt("x", binary="opencode", max_retries=1)
    assert exc.value.reason == "content_filter"
    empty = SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(opencode.subprocess, "run", lambda *_args, **_kwargs: empty)
    with pytest.raises(opencode.OpenCodeError) as exc:
        opencode.run_prompt("x", binary="opencode", max_retries=1)
    assert exc.value.reason == "output_format"

    provider = opencode.OpenCodeProvider("oc", {"binary": "opencode", "model": "m", "agent": "a"})
    monkeypatch.setattr(opencode, "run_prompt", lambda *_args, **_kwargs: '{"ok":true}')
    assert provider.health_check()["status"] == "ok"
    monkeypatch.setattr(opencode, "run_prompt", lambda *_args, **_kwargs: TRANSLATION)
    assert provider.translate(PAYLOAD, "system", 100)[1]["status"] == "ok"
    schema = tmp_path / "schema.json"
    schema.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(opencode, "run_prompt", lambda *_args, **_kwargs: '{"checked_ids":[]}')
    assert provider.review("chapter", {}, schema)["checked_ids"] == []

    monkeypatch.setattr(opencode, "run_prompt", lambda *_args, **_kwargs: (_ for _ in ()).throw(opencode.OpenCodeError("blocked", reason="content_filter")))
    assert provider.translate(PAYLOAD, "system", 100)[1]["status"] == "blocked"
    monkeypatch.setattr(opencode, "run_prompt", lambda *_args, **_kwargs: "not json")
    assert provider.translate(PAYLOAD, "system", 100)[1]["reason"] == "output_format"
    monkeypatch.setattr(opencode, "time", SimpleNamespace(sleep=lambda _seconds: None))
    with pytest.raises(ValueError):
        provider.review("chapter", {}, schema)


def test_opencode_retries_timeout(monkeypatch) -> None:
    monkeypatch.setattr(opencode.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        opencode.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("opencode", 1)),
    )
    with pytest.raises(opencode.OpenCodeError) as exc:
        opencode.run_prompt("x", binary="opencode", timeout=1, max_retries=2)
    assert exc.value.reason == "timeout"
