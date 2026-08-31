# CLI 命令契约

所有命令在 `<项目目录>` 执行，默认前缀：

```bash
python3 main.py --agent-mode <命令> ...
```

需要机器读取时加 `--json`。命令返回 JSON 的通用外层字段为：

- `status`: `ok`、`warning` 或 `error`
- `warnings`: 可继续但必须解释的风险
- `summary`: 当前阶段摘要
- `details`: 明细，可能较长
- `errors`: 失败时出现

## 环境与注册

| 命令 | 用途 | 成功判断 | 失败处理 |
| --- | --- | --- | --- |
| `version --json` | 输出工具版本、Git 提交、分支、Python 和命令数量 | `status` 为 `ok` | 问题排查或交付记录中附带该输出 |
| `check --json` | 运行聚合质量门禁：健康检查、命令清单、内置自测和敏感信息扫描 | `status` 不是 `error` 且 `summary.errors` 为 0 | 按 `details.steps` 定位失败步骤，再单独运行对应命令修复 |
| `check --strict --json` | 交付前硬门槛，任何 warning 都升级为 error | `status` 为 `ok` | 先处理 Python 版本、模型配置、内置自测或密钥扫描 warning |
| `doctor --json` | 检查项目根目录、配置文件和 Python 版本 | `status` 不是 `error` | 缺 `setting.toml` 时复制示例并填写；仅 dry-run 可继续 |
| `commands --json` | 输出当前 CLI 支持的命令、必填参数和 JSON 支持状态 | `details.commands` 可读且包含当前要执行的命令 | 如果文档命令不在清单里，以 `commands` 输出为准并停止使用旧命令 |
| `self-test --json` | 运行内置 TXT/EPUB 导入、导出和 EPUB 校验冒烟测试 | `status` 为 `ok` 且 `summary.errors` 为 0 | 失败时先修工具本身，不要开始真实翻译 |
| `secret-scan --json` | 扫描已跟踪文件中的疑似密钥、私钥和误跟踪本地配置 | `status` 为 `ok` 且 `summary.findings` 为 0 | 先移除敏感内容、轮换已泄露密钥，再继续 |
| `inspect-epub --path <EPUB文件> --json` | 检查 EPUB2/3、OPF、spine、nav/toc、重复文本和标记风险 | `summary.paragraph_count` 可解释 | 坏 HTML 且增强依赖不可用时，安装可选 EPUB 依赖或换源文件 |
| `add-book --path <小说文件> --json` | 注册 EPUB/TXT 小说 | `summary.book` 可用于后续命令 | 修路径或格式后重跑 |
| `add-book --path <小说文件> --title <标题> --id <ID> --json` | 用指定标题和 ID 注册小说 | `summary.book` 等于规范化 ID | ID 冲突时确认是否覆盖当前本地缓存 |
| `list --json` | 列出已注册小说 | `details.books` 可读 | 未注册时先 `add-book` |
| `text-scope --book <书籍ID> --json` | 查看章节和段落范围 | 段落数合理 | EPUB 章节缺失时先检查源文件 |
| `analyze-book --book <书籍ID> --json` | 生成译前项目画像 | `summary.paragraphs` 和风险统计可解释 | 先注册书籍后重跑 |
| `translation-plan --book <书籍ID> --json` | 生成 Agent 执行建议 | `details.actions` 可执行 | 按 warning 补术语或上下文 |

## 术语

| 命令 | 用途 | 成功判断 | 失败处理 |
| --- | --- | --- | --- |
| `export-terminology --book <书籍ID> --output-dir <工作区> --json` | 导出术语候选和上下文 | `details.glossary` 与 `details.contexts` 文件存在 | 删除不完整工作区后重跑 |
| `import-terminology --book <书籍ID> --input <glossary.json> --json` | 导入审查后的术语表 | `status` 为 `ok`，`summary.term_count` 可解释 | 修空 source、重复冲突或 JSON 结构后重跑 |
| `terminology-status --book <书籍ID> --json` | 查看术语表状态 | 无 `errors` | 冲突必须修；空译名 warning 必须解释 |

术语表输入必须是数组，或包含 `terms` 数组的对象。每项建议包含 `source`、`target`、`category`、`note`、`occurrences`、`sample_ids`。

## Agent 工作区与覆盖审计

| 命令 | 用途 | 成功判断 | 失败处理 |
| --- | --- | --- | --- |
| `prepare-agent-workspace --book <书籍ID> --output-dir <工作区> --json` | 导出 Agent 分析所需的工作区 | 工作区包含 manifest、book-summary、text-scope、terminology 和 quality 文件 | 删除不完整工作区后重跑 |
| `validate-agent-workspace --book <书籍ID> --workspace <工作区> --json` | 校验工作区结构和段落 ID | `status` 不是 `error` | 按 errors 修工作区或重新 prepare |
| `audit-coverage --book <书籍ID> --json` | 审计段落翻译覆盖和可导出格式 | 最终交付前 pending 应为 0 | pending 不为 0 时继续翻译或人工修复 |
| `summarize-context --book <书籍ID> --json` | 生成章节上下文摘要 | `summary.chapters` 等于章节数 | 长篇小说缺上下文时先补摘要 |
| `context-status --book <书籍ID> --json` | 检查章节上下文覆盖 | `status` 为 `ok` | 缺失章节摘要时重跑 summarize |

## 翻译与检查

| 命令 | 用途 | 成功判断 | 失败处理 |
| --- | --- | --- | --- |
| `translate --book <书籍ID> --max-batches 1 --json` | 小批量真实翻译 | `summary.batch_failed` 为 0，pending 下降 | 看 run-report 和质量报告，不盲目全量 |
| `translate --book <书籍ID> --json` | 继续翻译所有未译段落，默认使用翻译记忆 | pending 持续下降 | 停滞时检查模型配置、术语、失败批次或手动处理 |
| `translate --book <书籍ID> --workers <N> --rpm <N> --stop-on-warning --json` | 带并发配置、限速和 warning 闸门翻译 | run-report 记录 chars/token/限速字段 | 触发 warning 时先修质量问题 |
| `translate --book <书籍ID> --no-memory --json` | 绕过翻译记忆强制请求模型 | 用于术语大改后的重译 | 成本更高，先确认用户意图 |
| `translate --book <书籍ID> --dry-run --json` | 不调用模型，把原文写入译文字段验证流程 | 只用于测试流程 | 不得当真实译文交付 |
| `translation-memory-status --book <书籍ID> --json` | 查看翻译记忆数量和当前术语 hash 可复用数量 | `summary.reusable_entries` 可解释 | 术语变更后可复用数降低是正常现象 |
| `export-translation-memory --book <书籍ID> --output <文件> --json` | 导出翻译记忆 | 输出文件存在 | 用于迁移或备份 |
| `import-translation-memory --book <书籍ID> --input <文件> --json` | 导入翻译记忆 | `summary.imported` 可解释 | 修 JSON 或缺字段后重跑 |
| `run-report --book <书籍ID> --json` | 查看翻译运行和批次统计 | 最终交付前 failed 应为 0 | 有失败批次先 retry-failed 或人工修复 |
| `failed-batches --book <书籍ID> --json` | 列出失败批次明细 | `summary.failed` 可解释 | 结合错误原因修配置或重试 |
| `retry-failed --book <书籍ID> --json` | 只重试失败批次对应段落 | batch_failed 下降或 pending 下降 | 仍失败时导出人工修复表 |
| `run-pipeline --book <书籍ID> --json` | 执行快照、分析、计划、上下文、翻译、重试、质量和审校流水线 | `details.steps` 全部可解释 | warning 必须进入修复或说明 |
| `export-run-report --book <书籍ID> --output <报告.md> --json` | 导出 Markdown 运行报告 | 输出文件存在 | 用于交付包或人工复核 |
| `translation-status --book <书籍ID> --json` | 查看总数、已译、待译和进度 | 数量可解释 | pending 不下降时排查翻译请求 |
| `quality-report --book <书籍ID> --json` | 检查未译、源文残留、术语不一致和占位符缺失 | 最终交付前应为 `ok` | 按 details 修译文、术语或占位符 |

## 人工修复与反馈

| 命令 | 用途 | 成功判断 | 失败处理 |
| --- | --- | --- | --- |
| `export-pending-translations --book <书籍ID> --output <文件> --json` | 导出未译段落 | 输出 JSON 的 `items` 数量可解释 | pending 很多时优先继续模型翻译 |
| `export-quality-fix --book <书籍ID> --output <文件> --json` | 导出质量问题段落 | 输出 JSON 包含 reasons 和 translated 字段 | 只填写 translated，不改 id/source |
| `import-manual-translations --book <书籍ID> --input <文件> --json` | 导入人工译文 | `summary.imported` 可解释 | 未知段落 ID 会整体失败，修输入后重跑 |
| `reset-translations --book <书籍ID> --input <文件> --json` | 精确清空坏译文 | `summary.reset` 可解释 | 输入 ID 不存在会整体失败 |
| `reset-translations --book <书籍ID> --all --json` | 全量清空译文 | 用户明确要求完整重译时才用 | 不要和 `--input` 同时用 |
| `verify-feedback-text --book <书籍ID> --input <反馈文件> --json` | 按反馈文本反查原文/译文段落 | 命中分类可解释 | `not_found` 需让用户补上下文或截图文字 |
| `review-translations --book <书籍ID> --mode risk|sample|all --json` | 生成审校建议，不直接改译文 | `summary.review_id` 可用于导出报告 | 风险段落先用 risk |
| `apply-review-fixes --book <书籍ID> --input <审校.json> --json` | 应用 approved_translation | `summary.applied` 可解释 | 未知 ID、空译文、占位符缺失会失败 |
| `export-review-report --book <书籍ID> --review-id <ID> --output <报告.md> --json` | 导出 Markdown 审校报告 | 输出文件存在 | 交付前建议保留 |
| `snapshot --book <书籍ID> --name <名称> --json` | 创建译文快照 | 返回 snapshot_id | 重大修复前先跑 |
| `list-snapshots --book <书籍ID> --json` | 列出快照 | count 可解释 | 无快照时先 snapshot |
| `restore-snapshot --book <书籍ID> --snapshot <ID> --json` | 恢复快照 manifest | 段落数可解释 | 恢复前确认用户意图 |

## 导出

| 命令 | 用途 | 成功判断 | 失败处理 |
| --- | --- | --- | --- |
| `export --book <书籍ID> --format txt --output <文件> --json` | 导出 TXT 译本 | 输出文件存在 | 路径不可写时换输出路径 |
| `export --book <书籍ID> --format txt --output <文件> --bilingual --json` | 导出双语 TXT | 输出文件包含原文和译文 | 仅用于校对，不一定适合发布 |
| `export --book <书籍ID> --format epub --output <文件> --json` | 导出 EPUB 译本 | 源书为 EPUB 且输出文件存在 | TXT 书不能导出 EPUB；复杂排版需人工复核 |
| `validate-export --book <书籍ID> --format txt|epub --json` | 导出前检查 pending、质量和 EPUB 标记风险 | 最终交付前应为 `ok` | warning 需要解释；error 必须修复 |
| `delivery-check --book <书籍ID> --format txt|epub --json` | 聚合交付门槛，检查待译、失败批次、占位符、质量和导出风险 | `status` 为 `ok` 且 `summary.ready=true` | 按 `details.blockers` 先修阻断项 |
| `export-epub-risk-report --book <书籍ID> --output <报告.md> --json` | 导出 EPUB 标记风险报告 | EPUB 风险段落数量可解释 | EPUB 有风险时交付包必须包含 |
| `package-delivery --book <书籍ID> --output-dir <目录> --format txt|epub --json` | 生成指定格式的译本、交付门槛报告、质量报告、运行报告、术语和元数据交付包 | `status` 为 `ok` 且目录含 delivery-manifest.json 和 reports/delivery-check.json | error 表示交付门槛未过，只能作为排查材料；warning 必须在交付说明中解释；TXT 书不能打 EPUB 包 |
| `verify-delivery --manifest <目录>/delivery-manifest.json --json` | 校验交付包 manifest 中记录的相对路径、文件大小和 SHA256 | `status` 为 `ok` 且 `summary.errors` 为 0 | error 表示 manifest 损坏、字段缺失、交付文件缺失、被改动或相对路径越界，必须重新生成或更正交付包；交付目录移动后仍应可校验 |

EPUB 导出按导入时保存的 `chapter_path`、`node_index` 和 `text_hash` 回写。遇到节点失效或 hash 不一致时，导出会 warning 并保留原文，不能把这类输出称为最终版。
