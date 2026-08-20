from __future__ import annotations

import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "schemas" / "review-output.schema.json"
WINDOW_SCHEMA = ROOT / "schemas" / "window-review-output.schema.json"
CHAPTER_SCHEMA = ROOT / "schemas" / "chapter-review-output.schema.json"
GLOBAL_SCHEMA = ROOT / "schemas" / "global-consistency-output.schema.json"


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
- `items` 必须覆盖输入中的每一个段落 ID，即使该段没有问题，也要返回空 issues 和空 approved_translation。
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


def run_codex_window_review(input_path: Path, output_path: Path, autonomous: bool = False) -> None:
    model = os.environ.get("CODEX_MODEL", "gpt-5.6-sol")
    effort = os.environ.get("CODEX_REASONING_EFFORT", "low")
    prompt = f"""
审校输入 JSON 中的全部译文段落。这是多个连续翻译 batch 合并成的审阅窗口。
输入 JSON：{input_path}

要求：
- 逐条读取并检查 items 中的每个段落；必须把每个输入段落 ID 放入 checked_ids。
- 检查漏译、误译、重复、术语、人名、称谓、人称、标点、日文残留、中文病句和跨段落指代。
- issues 只输出确实存在问题的段落；没有问题的段落只进入 checked_ids，不要放入 issues。
- glossary 是已有术语表；term_updates 只收录后续分片可复用的词。
- 明确的错别字、漏译、重复、术语不一致、明显误译和确定的中文病句，在置信度 >= 0.9 时提供 approved_translation。
- {"全自动模式下，置信度 >= 0.9 且有明确修复文本的项目设置 auto_apply=true。" if autonomous else "涉及语义取舍、风格偏好或不确定改写时，approved_translation 为空且 auto_apply=false。"}
- 不要重写没有问题的段落，不要总结剧情。
- 严格输出符合 {WINDOW_SCHEMA} 的 JSON，不要 Markdown。
""".strip()
    command = [
        "codex", "exec", "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only",
        "--model", model, "-c", f'model_reasoning_effort="{effort}"',
        "--output-schema", str(WINDOW_SCHEMA), "-o", str(output_path), "-C", str(ROOT), prompt,
    ]
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"Codex window review failed ({result.returncode}):\n{result.stderr}\n{result.stdout}")


def run_codex_chapter_review(input_path: Path, output_path: Path, autonomous: bool = False) -> None:
    model = os.environ.get("CODEX_MODEL", "gpt-5.6-sol")
    effort = os.environ.get("CODEX_REASONING_EFFORT", "low")
    prompt = f"""
你是日译中小说审阅者。对输入 JSON 中的整章译文做章节级一致性审阅。
输入 JSON：{input_path}

- 只报告会导致读者误解原文的实质错误，不做文学润色。
- 不报告纯风格偏好、轻微措辞差异、可接受的自然化、标点偏好或普通敬称差异。
- 必须检查 items 中的每个段落，并把全部 ID 且不重复地写入 checked_ids。
- 重点检查人物身份和关系、主客体、代词指代、漏译、擅自添加、术语固定译法、事实冲突、时间顺序、跨段落动作关系和明显改变的强度。
- 当无法确定问题是否改变原意时，不要输出 fix。

- fixes 只输出确实存在且属于 critical 或 major 的问题，category 只能使用 Schema 中的枚举。
- replacement 必须是完整段落译文，而不是局部片段；不确定时为空字符串且 auto_apply=false。
- {"全自动模式下，客观问题且置信度 >= 0.9 的 fix 设置 auto_apply=true。" if autonomous else "语义取舍或不确定改写设置 auto_apply=false。"}
- glossary_delta 只收录后文仍有价值的人名、别名、组织、地点、特殊术语和固定称谓，不收录普通词或一次性短语。
- memory_delta 只收录会影响后续章节翻译的人物、关系、别名、重要事实和持续状态。
- chapter_state 只保存本章摘要和会影响后续理解的重要变化。
- 如果没有问题，fixes、glossary_delta 和 memory_delta 都返回空数组。
严格输出符合 {CHAPTER_SCHEMA} 的 JSON，不要 Markdown。
""".strip()
    command = [
        "codex", "exec", "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only",
        "--model", model, "-c", f'model_reasoning_effort="{effort}"',
        "--output-schema", str(CHAPTER_SCHEMA), "-o", str(output_path), "-C", str(ROOT), prompt,
    ]
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"Codex chapter review failed ({result.returncode}):\n{result.stderr}\n{result.stdout}")


def run_codex_global_consistency_review(input_path: Path, output_path: Path) -> None:
    model = os.environ.get("CODEX_MODEL", "gpt-5.6-sol")
    effort = os.environ.get("CODEX_REASONING_EFFORT", "low")
    prompt = f"""
对输入 JSON 中的全书状态做一次轻量一致性审阅。
输入 JSON：{input_path}

- 必须把输入中的每个 chapter_id 写入 checked_chapters，且不得重复或添加未知章节。
- 只检查 glossary、book_memory、章节摘要之间的事实、人物关系、时间线和术语冲突。
- 不重新审阅全文，不做文学润色，不因为不同章节的正常措辞差异而报告问题。
- conflicts 只输出有证据的冲突；recommendations 只给出后续人工或定向章节复核建议。
严格输出符合 {GLOBAL_SCHEMA} 的 JSON，不要 Markdown。
""".strip()
    command = [
        "codex", "exec", "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only",
        "--model", model, "-c", f'model_reasoning_effort="{effort}"',
        "--output-schema", str(GLOBAL_SCHEMA), "-o", str(output_path), "-C", str(ROOT), prompt,
    ]
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"Codex global consistency review failed ({result.returncode}):\n{result.stderr}\n{result.stdout}")
