from pathlib import Path

import pytest

from translator.web.routes import system


def test_prompt_write_interruption_preserves_previous_content(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "custom.md"
    target.write_text("old complete prompt", encoding="utf-8")

    def interrupted_replace(_source: Path, destination: Path):
        assert destination == target
        raise OSError("simulated interruption")

    monkeypatch.setattr(system, "get_prompts_dir", lambda: tmp_path)
    monkeypatch.setattr(system.os, "replace", interrupted_replace)

    with pytest.raises(OSError, match="simulated interruption"):
        system.save_prompt({"filename": "custom.md", "content": "new complete prompt"})

    assert target.read_text(encoding="utf-8") == "old complete prompt"
