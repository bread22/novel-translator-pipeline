from __future__ import annotations

import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "schemas" / "review-output.schema.json"


def run_codex_review(input_path: Path, output_path: Path, autonomous: bool = False) -> None:
    model = os.environ.get("CODEX_MODEL", "gpt-5.6-sol")
    effort = os.environ.get("CODEX_REASONING_EFFORT", "low")
    prompt = f"""
审校 Novel Translator 的译文分片，并从本分片总结可复用的日译中术语。
输入 JSON：{input_path}
逐条对照 source 和 translated，检查漏译、误译、重复、错别字、术语、人名、称谓、人称、标点、日文残留和明显中文病句。

规则：
- glossary 是已有术语表；译文和新增术语不得与其冲突。
- 只修改译文，不总结或改写剧情；source 是事实基准。
- 明确的错别字、漏译、重复、术语不一致、明显误译和确定的中文病句，在置信度 >= 0.9 时直接提供 approved_translation。
- {"全自动模式下，不等待人工确认；所有置信度 >= 0.9 且有明确修复的 approved_translation 都设置 auto_apply=true。" if autonomous else "涉及语义取舍、风格偏好或不确定改写时，auto_apply=false 且 approved_translation 为空字符串。"}
- term_updates 只收录后续分片可复用且有明确原文对应的词，不收录整句或临时描述。
- 已有术语得到本分片支持时可以再次输出相同译法；发现冲突时保留更可靠的建议并在 note 说明。
- 严格输出符合 {SCHEMA} 的 JSON，不要 Markdown。
""".strip()
    command = [
        "codex", "exec", "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only",
        "--model", model, "-c", f'model_reasoning_effort="{effort}"',
        "--output-schema", str(SCHEMA), "-o", str(output_path), "-C", str(ROOT), prompt,
    ]
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"Codex review failed ({result.returncode}):\n{result.stderr}\n{result.stdout}")
