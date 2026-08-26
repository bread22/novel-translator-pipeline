from pathlib import Path

import pytest

from translator.web.routes import system


@pytest.mark.xfail(
    strict=True,
    reason="save_prompt writes directly to the destination and can leave a truncated file",
)
def test_prompt_write_interruption_preserves_previous_content(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "custom.md"
    target.write_text("old complete prompt", encoding="utf-8")

    def interrupted_write(path: Path, content: str, **_kwargs):
        path.open("w", encoding="utf-8").write(content[:5])
        raise OSError("simulated interruption")

    monkeypatch.setattr(system, "get_prompts_dir", lambda: tmp_path)
    monkeypatch.setattr(Path, "write_text", interrupted_write)

    with pytest.raises(OSError, match="simulated interruption"):
        system.save_prompt({"filename": "custom.md", "content": "new complete prompt"})

    assert target.read_text(encoding="utf-8") == "old complete prompt"
