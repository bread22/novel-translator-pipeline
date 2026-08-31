# Novel Translator

Novel Translator 是一个参考 [A.T.T MZ](https://github.com/yexi-by/att-mz) 工作流构建的 Agent 友好型小说翻译工具，面向 `.epub` 和 `.txt` 小说文件。它会先把小说注册到本地工作区，拆成稳定段落 ID，再调用 OpenAI 兼容接口批量翻译，最后导出译文。

## 快速上手

### 适合什么

- 翻译 EPUB 或 TXT 小说。
- 让 Agent 按命令注册书籍、查看文本范围、分批翻译、检查质量、导出成品。
- 中断后继续翻译，已完成段落会保存在 `data/books/<书籍ID>/manifest.json`。

### 直接告诉 Agent

如果你要把整本小说交给 Agent 处理，可以直接复制下面这段，把文件路径和输出格式改成自己的：

```text
请使用当前项目的 Novel Translator 流程翻译这本小说：<小说文件路径>。

要求：
1. 如果本机还没有项目，先执行 `git clone https://github.com/OYcedar/novel-translator.git`，然后 `cd novel-translator`；如果已经有项目，先进入项目目录并 `git pull` 获取最新代码。
2. 先阅读 README 和 skills/novel-translator/SKILL.md，严格按项目 CLI 流程执行。
3. 如果是 EPUB，先运行 inspect-epub 检查结构风险，再 add-book 注册；如果是 TXT，直接 add-book。
4. 注册后依次执行 text-scope、analyze-book、export-terminology，并整理术语表；术语表导入后检查 terminology-status。
5. 长篇小说先 summarize-context，再运行 translation-plan。
6. 先用 translate --max-batches 1 试翻，检查 run-report 和 quality-report；确认没有严重问题后再全量翻译。
7. 有失败批次必须 retry-failed；有质量问题时使用 export-quality-fix / import-manual-translations 或 review-translations / apply-review-fixes 修复。
8. 占位符、HTML 标签、脚注锚点、术语译名必须保留一致，不要翻译或删除占位符。
9. 最终运行 validate-export 和 quality-report；没有 error 后导出 <txt|epub>，并用 package-delivery 生成交付包。
10. 每一步都输出关键 JSON summary，并在结束时汇总书籍 ID、翻译进度、失败批次数、质量报告结果和交付目录。
```

### 1. 准备配置

复制配置示例：

```bash
cp setting.example.toml setting.toml
```

填写 OpenAI 兼容接口。推荐把密钥放在环境变量里，`setting.toml` 只引用变量名：

```bash
export OPENAI_BASE_URL="https://<模型服务地址>/v1"
export OPENAI_API_KEY="<API Key>"
export OPENAI_MODEL="<模型名>"
```

```toml
[llm]
base_url = "$OPENAI_BASE_URL"
api_key = "$OPENAI_API_KEY"
model = "$OPENAI_MODEL"
timeout = 600
```

也可以使用项目专用变量 `NOVEL_TRANSLATOR_BASE_URL`、`NOVEL_TRANSLATOR_API_KEY` 和 `NOVEL_TRANSLATOR_MODEL`。不要把真实 API Key 提交到仓库或交付报告里。

先检查环境：

```bash
python3 main.py --agent-mode check --json
```

### 2. 注册小说

EPUB 建议先检查内部结构，再注册：

```bash
python3 main.py --agent-mode inspect-epub --path ./novel.epub --json
python3 main.py --agent-mode add-book --path ./novel.epub --json
python3 main.py --agent-mode text-scope --book <书籍ID> --json
```

TXT 可以直接注册：

```bash
python3 main.py --agent-mode add-book --path ./novel.txt --json
python3 main.py --agent-mode text-scope --book <书籍ID> --json
```

### 3. 译前准备

先生成项目画像和术语表，长篇小说再生成章节上下文：

```bash
python3 main.py --agent-mode analyze-book --book <书籍ID> --json
python3 main.py --agent-mode export-terminology --book <书籍ID> --output-dir ./workspace --json
python3 main.py --agent-mode import-terminology --book <书籍ID> --input ./workspace/terminology/glossary.json --json
python3 main.py --agent-mode summarize-context --book <书籍ID> --json
python3 main.py --agent-mode translation-plan --book <书籍ID> --json
```

### 4. 翻译与检查

先小批量试翻，确认质量后再全量翻译：

```bash
python3 main.py --agent-mode translate --book <书籍ID> --max-batches 1 --json
python3 main.py --agent-mode translate --book <书籍ID> --workers 200 --rpm 200 --json
python3 main.py --agent-mode run-report --book <书籍ID> --json
python3 main.py --agent-mode retry-failed --book <书籍ID> --json
python3 main.py --agent-mode quality-report --book <书籍ID> --json
```

### 5. 导出或交付

导出单个译本：

```bash
python3 main.py --agent-mode validate-export --book <书籍ID> --format epub --json
python3 main.py --agent-mode delivery-check --book <书籍ID> --format epub --json
python3 main.py --agent-mode export --book <书籍ID> --format epub --output ./translated.epub --json
python3 main.py --agent-mode export --book <书籍ID> --format txt --output ./translated.txt --json
```

生成包含译本、报告、术语和元数据的交付包：

```bash
python3 main.py --agent-mode package-delivery --book <书籍ID> --output-dir ./delivery --format epub --json
python3 main.py --agent-mode verify-delivery --manifest ./delivery/delivery-manifest.json --json
```

`delivery` 目录可以移动或压缩后再校验；manifest 同时记录相对路径和 SHA256，适合交付给他人复核。manifest 损坏、字段缺失、路径越界或文件被改动时，`verify-delivery` 会返回结构化 `error`。

## 详细说明

### 常用命令

| 命令 | 备注 |
| --- | --- |
| `python3 main.py --agent-mode version --json` | 输出工具版本、Git 提交、分支、Python 版本和命令数量，适合问题排查和交付记录。 |
| `python3 main.py --agent-mode check --json` | 一次运行项目聚合质量门禁，包含健康检查、命令清单、内置自测和敏感信息扫描。 |
| `python3 main.py --agent-mode check --strict --json` | 发布或交付前硬门槛，会把任何 warning 升级为 error。 |
| `python3 main.py --agent-mode doctor --json` | 输出项目健康报告，检查配置、LLM 字段、Python 版本、CI、Skill、命令数量和可选依赖。 |
| `python3 main.py --agent-mode commands --json` | 输出机器可读 CLI 命令清单，便于 Agent 自查当前能力。 |
| `python3 main.py --agent-mode self-test --json` | 运行内置 TXT/EPUB 冒烟测试，不需要模型或外部小说样本。 |
| `python3 main.py --agent-mode secret-scan --json` | 扫描已跟踪文件中的疑似密钥、私钥和误跟踪的本地配置。 |
| `python3 main.py --agent-mode inspect-epub --path ./novel.epub --json` | 注册 EPUB 前检查内部结构、目录、spine、重复文本和格式风险。 |
| `python3 main.py --agent-mode add-book --path ./novel.epub --json` | 注册 EPUB/TXT 小说，返回后续命令使用的 `<书籍ID>`。 |
| `python3 main.py --agent-mode list --json` | 查看本地已注册书籍。 |
| `python3 main.py --agent-mode text-scope --book <书籍ID> --json` | 查看章节和段落范围，确认拆分是否合理。 |
| `python3 main.py --agent-mode analyze-book --book <书籍ID> --json` | 生成译前项目画像，统计对话比例、重复文本、术语密度和 EPUB 风险。 |
| `python3 main.py --agent-mode export-terminology --book <书籍ID> --output-dir ./workspace --json` | 导出术语候选和上下文，供人工或 Agent 填写译名。 |
| `python3 main.py --agent-mode import-terminology --book <书籍ID> --input ./workspace/terminology/glossary.json --json` | 导入审定后的术语表。 |
| `python3 main.py --agent-mode terminology-status --book <书籍ID> --json` | 检查术语冲突、空译名和术语数量。 |
| `python3 main.py --agent-mode prepare-agent-workspace --book <书籍ID> --output-dir ./workspace --json` | 导出 Agent 工作区，包含文本范围、术语和质量报告。 |
| `python3 main.py --agent-mode validate-agent-workspace --book <书籍ID> --workspace ./workspace --json` | 校验 Agent 工作区文件和段落 ID 是否匹配当前书籍。 |
| `python3 main.py --agent-mode audit-coverage --book <书籍ID> --json` | 审计翻译覆盖率、未译段落和可导出格式。 |
| `python3 main.py --agent-mode export-translation-outline --book <书籍ID> --output ./outline.json --format compact --json` | 导出去重后的翻译 Outline；compact 适合发送给模型，`location_map` 留本地展开重复段落。 |
| `python3 main.py --agent-mode convert-translation-outline-result --outline ./outline.json --input ./outline-result.json --output ./manual.json --json` | 把模型返回的 Outline 译文转换成 `import-manual-translations` 可导入的手动译文表。 |
| `python3 main.py --agent-mode summarize-context --book <书籍ID> --json` | 生成章节摘要，长篇小说建议翻译前执行。 |
| `python3 main.py --agent-mode context-status --book <书籍ID> --json` | 检查章节上下文是否齐全。 |
| `python3 main.py --agent-mode translation-plan --book <书籍ID> --json` | 根据画像、术语和上下文生成 Agent 执行建议。 |
| `python3 main.py --agent-mode translate --book <书籍ID> --max-batches 1 --workers 200 --rpm 200 --json` | 小批量试翻；确认质量后再去掉 `--max-batches 1` 全量翻译。 |
| `python3 main.py --agent-mode run-report --book <书籍ID> --json` | 查看批次成功/失败、字符数、记忆命中和限速记录。 |
| `python3 main.py --agent-mode export-run-report --book <书籍ID> --output ./run-report.md --json` | 导出 Markdown 运行报告，适合交付或人工复盘。 |
| `python3 main.py --agent-mode failed-batches --book <书籍ID> --json` | 列出失败批次和对应段落 ID。 |
| `python3 main.py --agent-mode retry-failed --book <书籍ID> --json` | 只重试当前仍未翻译的失败段落。 |
| `python3 main.py --agent-mode review-translations --book <书籍ID> --mode risk --json` | 审校风险段落，只生成建议，不直接覆盖译文。 |
| `python3 main.py --agent-mode export-review-report --book <书籍ID> --review-id <审校ID> --output ./review.md --json` | 将审校 JSON 导出为 Markdown 报告。 |
| `python3 main.py --agent-mode snapshot --book <书籍ID> --name before-final --json` | 创建译文快照；大规模修复前建议先保存。 |
| `python3 main.py --agent-mode translation-memory-status --book <书籍ID> --json` | 查看翻译记忆数量和当前术语 hash 下的可复用数量。 |
| `python3 main.py --agent-mode translation-status --book <书籍ID> --json` | 查看总段落、已译、待译和进度。 |
| `python3 main.py --agent-mode quality-report --book <书籍ID> --json` | 检查未译、源文残留、术语、占位符、风格和 EPUB 风险。 |
| `python3 main.py --agent-mode export-pending-translations --book <书籍ID> --output ./pending.json --json` | 导出未译段落，适合人工补译。 |
| `python3 main.py --agent-mode export-quality-fix --book <书籍ID> --output ./quality-fix.json --json` | 导出质量问题段落，供人工填写修复译文。 |
| `python3 main.py --agent-mode import-manual-translations --book <书籍ID> --input ./manual.json --json` | 导入人工译文；未知段落 ID 会失败。 |
| `python3 main.py --agent-mode reset-translations --book <书籍ID> --input ./reset.json --json` | 精确清空坏译文，输入为段落 ID 数组或对象数组。 |
| `python3 main.py --agent-mode verify-feedback-text --book <书籍ID> --input ./feedback.txt --json` | 按读者反馈文本反查原文/译文段落。 |
| `python3 main.py --agent-mode export-epub-risk-report --book <书籍ID> --output ./epub-risk.md --json` | 导出 EPUB 标记风险报告，发布前用于人工复核。 |
| `python3 main.py --agent-mode run-pipeline --book <书籍ID> --json` | 执行快照、分析、计划、上下文、翻译、重试、质量和审校流水线。 |
| `python3 main.py --agent-mode package-delivery --book <书籍ID> --output-dir ./delivery --format epub --json` | 生成交付包，包含译本、交付门槛报告、质量报告、运行报告、术语和元数据；`--format` 省略时跟随源书格式。 |
| `python3 main.py --agent-mode verify-delivery --manifest ./delivery/delivery-manifest.json --json` | 校验交付包 manifest 中记录的相对路径、文件大小和 SHA256，确认交付文件未被改动。 |
| `python3 main.py --agent-mode validate-export --book <书籍ID> --format epub --json` | 导出前检查；最终交付不能有 error。 |
| `python3 main.py --agent-mode delivery-check --book <书籍ID> --format epub --json` | 聚合最终交付门槛，检查待译段落、失败批次、占位符、质量报告和导出风险。 |
| `python3 main.py --agent-mode export --book <书籍ID> --format txt --output ./translated.txt --json` | 导出 TXT 译本。 |
| `python3 main.py --agent-mode export --book <书籍ID> --format epub --output ./translated.epub --json` | 导出 EPUB 译本；仅 EPUB 源书可用。 |

排查流程可先用 `--dry-run` 不调用模型：

```bash
python3 main.py --agent-mode translate --book <书籍ID> --dry-run --json
```

### Agent 工作流建议

项目内置了 Agent Skill：

```text
skills/novel-translator/SKILL.md
```

如果交给 Codex、Claude Code 或其他 Agent 执行整本翻译，让它读取这个 Skill，并按其中的命令契约完成术语、翻译、质量检查和导出流程。本仓库只提供 Skill 文件，不会自动安装到本机 Codex。

1. `check --json` 运行聚合质量门禁；如需定位问题，再单独运行 `doctor --json`、`commands --json`、`self-test --json` 或 `secret-scan --json`。
2. `add-book --path <小说文件> --json` 注册小说，记录返回的书籍 ID。
3. `text-scope --book <书籍ID> --json` 确认章节和段落数量。
4. `analyze-book --book <书籍ID> --json` 生成译前项目画像，检查对话比例、重复文本、术语密度和 EPUB 风险。
5. `export-terminology --book <书籍ID> --output-dir <工作区> --json` 导出术语候选和上下文。
6. 人工或 Agent 填写 `<工作区>/terminology/glossary.json` 里的 `target`，删除不需要的候选，统一人名、地名、组织名、能力名等译名。
7. `import-terminology --book <书籍ID> --input <工作区>/terminology/glossary.json --json` 导入术语表。
8. `terminology-status --book <书籍ID> --json` 确认没有冲突；空译名会作为 warning。
9. `prepare-agent-workspace --book <书籍ID> --output-dir <工作区> --json` 导出完整 Agent 工作区。
10. `validate-agent-workspace --book <书籍ID> --workspace <工作区> --json` 验收工作区。
11. `audit-coverage --book <书籍ID> --json` 查看覆盖范围和可导出格式。
12. 大型或重复段落多的书可用 `export-translation-outline --format compact` 生成去重 Outline；模型只返回 `{id, translated_lines}`，再用 `convert-translation-outline-result` 转为手动译文表，并先通过 `import-manual-translations` 校验导入。
13. 长篇小说先运行 `summarize-context --book <书籍ID> --json`，再用 `context-status` 确认章节上下文齐全。
14. `translation-plan --book <书籍ID> --json` 生成 Agent 执行计划。
15. `translate --book <书籍ID> --max-batches 1 --json` 先小批量试翻，必要时设置 `--workers`、`--rpm` 和 `--stop-on-warning`。
16. `run-report --book <书籍ID> --json` 检查批次运行记录；如有失败，先用 `retry-failed --book <书籍ID> --json` 重试。
17. `review-translations --book <书籍ID> --mode risk --json` 审校风险段落；确认修复后用 `apply-review-fixes` 导入。
18. `quality-report --book <书籍ID> --json` 检查未译、源语言残留、术语、占位符、风格和审校风险。
19. `package-delivery --book <书籍ID> --output-dir <交付目录> --json` 生成译本、报告、术语和元数据交付包。

### 术语表流程

术语表保存在：

```text
data/books/<书籍ID>/terms.json
```

导出的工作区结构：

```text
workspace/
  manifest.json
  terminology/
    glossary.json
    contexts/
      term-contexts.json
```

`glossary.json` 示例：

```json
{
  "terms": [
    {
      "source": "Alice",
      "target": "爱丽丝",
      "category": "name",
      "note": "主角",
      "occurrences": 12,
      "sample_ids": ["c0001-p00003"]
    }
  ]
}
```

翻译时，命中当前批次原文的术语会被注入模型请求的 `glossary` 字段。质量报告会检查：如果原文包含术语 `source`，译文必须包含对应 `target`。

### 长篇稳定翻译

工具会在每本书目录下维护三类长篇状态：

```text
data/books/<书籍ID>/
  memory.json
  context.json
  runs/
```

`memory.json` 是翻译记忆，按源文 hash 和当前术语 hash 复用译文；术语表变化后，旧记忆不会自动命中，避免旧译名污染新译名。可用这些命令查看或迁移：

```bash
python3 main.py --agent-mode translation-memory-status --book <书籍ID> --json
python3 main.py --agent-mode export-translation-memory --book <书籍ID> --output ./memory.json --json
python3 main.py --agent-mode import-translation-memory --book <书籍ID> --input ./memory.json --json
```

`context.json` 保存章节摘要。`summarize-context` 会优先使用当前 OpenAI 兼容配置生成模型摘要；如果缺依赖或调用失败，会 warning 并回落到抽取式摘要。`translate` 会把章节标题、章节摘要、前后段落、前文已译片段、相关术语和占位符一起发给模型。长篇小说建议先运行：

```bash
python3 main.py --agent-mode summarize-context --book <书籍ID> --json
python3 main.py --agent-mode context-status --book <书籍ID> --json
```

`runs/` 保存每次翻译运行的批次记录，包括批次 ID、段落 ID、请求时间、耗时、模型、错误和 warning。失败批次不会写入译文缓存，成功批次会增量保存。常用命令：

```bash
python3 main.py --agent-mode translate --book <书籍ID> --run-id first-pass --json
python3 main.py --agent-mode run-report --book <书籍ID> --json
python3 main.py --agent-mode failed-batches --book <书籍ID> --json
python3 main.py --agent-mode retry-failed --book <书籍ID> --json
python3 main.py --agent-mode request-stop --book <书籍ID> --reason "pause" --json
python3 main.py --agent-mode task-status --book <书籍ID> --json
python3 main.py --agent-mode clear-stop --book <书籍ID> --json
python3 main.py --agent-mode work-records --book <书籍ID> --json
python3 main.py --agent-mode work-records --book <书籍ID> --collect-log-dir ../logs --json
```

模型输出会进行批次级校验：缺少段落 ID 会让该批失败；未知 ID、空译文、占位符缺失和术语缺失会进入 warning 或质量报告。`quality-report` 还会为对话标点、风格问题和疑似直译输出 `reasons`，例如英文引号、英文句点、英文原词残留、译文异常过短和重复句子，便于 `review-translations --mode risk` 精确审校。需要完全绕过翻译记忆时，可传 `translate --no-memory`。并发翻译会在每个批次完成后立刻记录 run、保存译文和翻译记忆；检测到 HTTP 402 或 Insufficient Balance 时会停止继续派发新请求并取消未开始批次。`retry-failed` 只会重试当前仍未翻译的失败段落，历史失败中后来已成功的段落会自动跳过。长任务可用 `request-stop` 请求优雅中断，运行中的翻译会在当前批次结束或下一次限流等待时保存进度退出；继续前用 `clear-stop` 清除停止请求。

### 单本小说工作记录

每本小说可以有独立的过程记录目录，默认位于：

```text
../workspace/books/<书籍ID>/
  logs/
  reports/
  workspace/
  imports/
  delivery/
```

注册书籍时会自动初始化该目录。也可以手动执行：

```bash
python3 main.py --agent-mode work-records --book <书籍ID> --json
python3 main.py --agent-mode work-records --book <书籍ID> --collect-log-dir ../logs --json
python3 main.py --agent-mode work-records --book <书籍ID> --collect-file ../import_terms.json --json
```

`data/books/<书籍ID>/` 仍然保存核心翻译状态；`work-records` 目录用于收纳后台脚本、日志、外部术语表、质检报告、审校材料和交付包。

交付包会在 `reports/delivery-check.json` 保存生成当时的交付门槛报告，并在 `delivery-manifest.json` 写入 `generated_at`、`status`、`ready`、`errors`、`warnings`、`delivery_check_summary` 和关键文件的相对路径、`sha256` 校验信息，便于复盘待译段落、失败批次、占位符、导出风险和文件完整性。交付目录移动或解压到其他位置后，仍可运行 `verify-delivery --manifest <交付目录>/delivery-manifest.json --json` 校验文件未被改动。如果报告存在 blockers，`package-delivery` 会返回 `error`，交付包只能作为排查材料，不能称为最终版。

### Agent 工作区与人工修复

完整工作区会导出：

```text
workspace/
  manifest.json
  book-summary.json
  text-scope.json
  terminology/
    glossary.json
    contexts/term-contexts.json
  quality/latest-report.json
```

人工修复文件使用 JSON，不依赖 CSV/XLSX。`export-pending-translations` 导出未译段落，`export-quality-fix` 导出质量报告命中的段落；填写 `translated` 后用 `import-manual-translations` 导入。坏译文可用 `reset-translations --input reset.json` 精确清空，或明确传 `--all` 全量清空。

### 审校、快照和交付

成熟流水线会在翻译前后生成更多可追踪文件：

```text
data/books/<书籍ID>/
  analysis.json
  reviews/<审校ID>.json
  snapshots/<快照ID>/manifest.json
```

`run-pipeline` 会自动创建开始前快照，执行分析、计划、上下文检查、翻译、失败重试、质量检查和风险审校。默认不导出最终文件；需要一并导出时传 `--export txt|epub --output <文件>`。

审校默认只处理风险段落：

```bash
python3 main.py --agent-mode review-translations --book <书籍ID> --mode risk --json
python3 main.py --agent-mode export-review-report --book <书籍ID> --review-id <审校ID> --output ./review.md --json
```

审校不会直接覆盖译文。只有在审校 JSON 中填写 `approved_translation` 后，`apply-review-fixes` 才会写入，并且会拒绝未知段落、空译文和占位符缺失。

交付前硬门槛：

- `delivery-check` 不能是 `error`，且 `summary.ready` 必须为 `true`。
- `validate-export` 不能是 `error`。
- `run-report.summary.failed` 必须为 0。
- `quality-report.summary.placeholder_mismatch` 必须为 0。
- EPUB 有标记风险时，交付包必须包含 `epub-risk-report.md`。

交付包命令：

```bash
python3 main.py --agent-mode package-delivery --book <书籍ID> --output-dir ./delivery --format epub --json
```

### 占位符保护

翻译请求会把段落里的 `{name}`、`{{name}}`、`%s`、`%d`、HTML 标签和 `[#note]` 这类脚注锚点作为 `placeholders` 传给模型。译文必须原样保留这些占位符；`quality-report` 会用 `placeholder_mismatch` 报告缺失项。

### 译文质量配置

翻译请求会自动携带 `quality_profile`，包含 `style_guide`、`dialogue_style`、`requirements`、`avoid` 和 `self_check_passes`。默认要求模型忠实保留原文信息量、视角、语气、修辞、称谓关系、术语和占位符，同时避免逐词硬译、漏译、摘要式旁白和擅自弱化原文。可在 `setting.toml` 的 `[translation]` 中调整：

```toml
style_guide = "自然流畅的简体中文小说译文，忠实原意，避免生硬直译。"
dialogue_style = "符合中文网文/出版小说阅读习惯，称谓、语气和人物关系保持连续。"
style_sample_file = ""
style_sample_max_chars = 1200
quality_passes = 2
```

如果希望更贴近某本书或某个译者的中文节奏，可以把一小段已经认可的译文保存成文本文件，并把 `style_sample_file` 指向它。翻译请求会注入 `style_reference`，只要求模型参考叙事节奏、对话口吻、标点和句式密度，不会要求照抄样例内容。`doctor --json` 会提示样例文件是否缺失。

### 数据位置

- 注册书籍和译文缓存：`data/books`
- 原始文件副本：`data/books/<书籍ID>/source.epub` 或 `source.txt`
- 系统提示词：`prompts/novel_translation_system.md`

### 许可证

本项目使用 MIT License，详见 `LICENSE`。

### EPUB 说明

EPUB 导入会读取 OPF manifest/spine，支持 EPUB2 `toc.ncx`、EPUB3 `nav.xhtml`、`.xhtml`、`.html` 和 `.htm` 章节。导入时会为每个可翻译节点保存 `chapter_path`、`node_index`、`node_tag`、`node_id`、`node_class` 和 `text_hash`，导出时按节点定位回写，重复原文也能写回正确位置。

可先检查 EPUB 内部结构：

```bash
python3 main.py --agent-mode inspect-epub --path ./novel.epub --json
```

默认使用标准库解析；如果本机安装了可选依赖 `beautifulsoup4` / `lxml`，遇到坏 HTML 或非严格 XHTML 时会自动启用增强解析。可选安装：

```bash
python3 -m pip install ".[epub]"
```

配置项：

```toml
[epub]
parser = "auto"
include_non_linear_spine = false
preserve_outer_markup = true
warn_on_ruby = true
warn_on_duplicate_source = true
```

EPUB 导出会复制原始 EPUB，保留 CSS、图片和元数据，并替换 spine 章节中的译文节点。`ruby`、脚注链接、表格、代码块、图片文字等复杂结构会在 `quality-report` 的 `epub_markup_risk` 中提示；最终发布前建议运行 `validate-export`，并抽查 EPUB 阅读器效果。

### 开发与验证

本项目提供 GitHub Actions CI，会在 `main`、`perfect` 分支推送和 Pull Request 时运行编译、CLI 自省、测试和打包检查。本地开发可安装开发依赖后执行同样的核心验证：

```bash
python3 -m pip install -e ".[dev,epub]"
python3 -m compileall app tests
python3 main.py --agent-mode check --json
python3 -m pytest -q
python3 -m build
```

交付前可以额外运行 `python3 main.py --agent-mode check --strict --json`。严格模式会要求 Python 版本、模型配置、内置自测和敏感信息扫描都没有 warning。

### 特别感谢

本项目的流程设计、Agent 工作区思路和质量闭环设计参考了 [A.T.T MZ](https://github.com/yexi-by/att-mz)。特别感谢 A.T.T MZ 原作者 [yexi-by](https://github.com/yexi-by) 的开源项目与工作流启发。
