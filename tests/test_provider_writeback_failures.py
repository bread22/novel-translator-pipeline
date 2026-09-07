"""Provider failures must not partially publish book translations."""

import pytest

from translator.core.workspace import write_json
from translator.providers.translator import ProviderTranslator


@pytest.fixture
def adapter(tmp_path):
    manifest = tmp_path / "manifest.json"
    write_json(
        manifest,
        {
            "chapters": [
                {
                    "id": "c1",
                    "paragraphs": [
                        {"id": "p1", "source": "one", "translated": "old one"},
                        {"id": "p2", "source": "two", "translated": "old two"},
                    ],
                }
            ]
        },
    )
    return ProviderTranslator(novel_root=tmp_path / "vendor", manifest=manifest, config={}, config_path=tmp_path / "config.toml")


@pytest.mark.parametrize(
    "items,reason",
    [
        ([], "output_format"),
        ([{"id": "p1", "text": "one"}, {"id": "p1", "text": "duplicate"}], "output_format"),
        ([{"id": "p1", "text": "one"}, {"id": "unknown", "text": "unexpected"}], "output_format"),
        ([{"id": "p1", "text": "new one"}, {"id": "p2", "text": "  "}], "empty_translation"),
    ],
)
def test_invalid_provider_payload_preserves_manifest_bytes(adapter, monkeypatch, items, reason):
    before = adapter.manifest.read_bytes()
    monkeypatch.setattr(adapter, "_request", lambda *args: (items, {"status": "ok"}))
    response = adapter("fixture", "book", ["p1", "p2"], source_chars=6, max_tokens=100)
    assert response["status"] == "error"
    assert response["reason"] == reason
    assert adapter.manifest.read_bytes() == before


def test_missing_input_paragraph_fails_before_calling_provider(adapter, monkeypatch):
    before = adapter.manifest.read_bytes()

    def unexpected_call(*args):
        pytest.fail("provider called despite missing paragraph")

    monkeypatch.setattr(adapter, "_request", unexpected_call)
    with pytest.raises(ValueError, match="missing"):
        adapter("fixture", "book", ["missing"], source_chars=0, max_tokens=100)
    assert adapter.manifest.read_bytes() == before


def test_empty_request_and_missing_provider_are_explicit_results(adapter):
    assert adapter("fixture", "book", [], source_chars=0, max_tokens=100)["summary"]["translated"] == 0
    result = adapter.health_check("missing")
    assert result["status"] == "error"
    assert "missing" in result["error"]


def test_missing_config_policy_uses_vendor_fallback_then_builtin(adapter):
    builtin = adapter._system_prompt("fixture")
    assert "JSON" in builtin
    fallback = adapter.novel_root / "prompts" / "novel_translation_system.md"
    fallback.parent.mkdir(parents=True)
    fallback.write_text("fixture vendor policy", encoding="utf-8")
    assert adapter._system_prompt("fixture") == "fixture vendor policy"
