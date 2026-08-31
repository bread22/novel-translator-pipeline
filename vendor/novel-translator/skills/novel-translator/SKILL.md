---
name: novel-translator
description: 执行 Novel Translator 的 EPUB/TXT 小说翻译流程：注册小说、译前分析、准备 Agent 工作区、导出/导入术语表、生成章节上下文、使用翻译记忆和批次恢复调用 OpenAI 兼容模型翻译、审校质量、人工修复、反馈反查，并打包交付 TXT 或 EPUB 译本。
---

# Novel Translator Skill

本 Skill 是给 Agent 使用 Novel Translator 的执行协议，不是用户说明书。所有业务数据都通过 CLI、工作区 JSON、小说源文件和用户明确提供的信息流转；不要直接改 `data/books/*/manifest.json` 或 `terms.json`，除非用户明确要求开发/排障工具本身。

## 运行边界

- `<项目目录>` 是 Novel Translator 仓库根目录。
- 默认命令前缀：`python3 main.py --agent-mode <命令> ...`
- `<小说文件>` 只支持 `.epub` 和 `.txt`。
- `<书籍ID>` 来自 `add-book --json` 的 `summary.book`。
- `<工作区>` 是临时目录，用于放术语候选、上下文和人工修复文件；不要把临时文件散落到项目根目录。
- `<工作记录目录>` 是单本小说的过程收纳目录，默认位于 `../workspace/books/<书籍ID>/`，包含 `logs/`、`reports/`、`workspace/`、`imports/` 和 `delivery/`。核心翻译状态仍以 `data/books/<书籍ID>/` 为准；工作记录目录用于收纳后台脚本、日志、外部术语表、质检报告、审校材料和交付包。
- 模型地址、API Key 和模型名只从 `setting.toml`、环境或用户本地配置读取；不要写进任务文件、报告、提交或聊天总结。
- 所有 JSON、TXT、EPUB 相关临时文件按 UTF-8 处理。
- `--json` 命令的 stdout 才是机器结果；不要把日志或进度文本当 JSON 解析。

## 交付自治

- 用户要求“处理后续直到成品”或类似表达时，Agent 应默认接手完整收尾：持续执行质量修复、导出验证、风险报告、单语/双语交付包生成和必要的状态汇报，不要让用户操心中途步骤。
- 中途只在需要用户补充资源、确认不可自动判断的译名取舍、充值/权限问题、或存在会影响最终交付的硬阻断时打扰用户。
- 默认交付模式遵循配置和用户最近指令；没有明确要求双语时，按单语译文导出。
- 长任务可以创建后台脚本和心跳监控，但监控完成或任务失效后要及时停止，避免重复提醒。

## 按需参考资料

| 工作 | 必读参考 | 读取时机 |
| --- | --- | --- |
| 命令调用与成功判断 | `references/cli-command-contract.md` | 运行或排查任一 CLI 阶段前 |
| 术语工程 | `references/terminology-workflow.md` | 导出、填写、审查或导入术语表前 |
| 质量检查与恢复 | `references/quality-and-recovery.md` | `quality-report` 有 warning/error、需要人工修复或反馈定位时 |
| 实战经验复盘 | `../../docs/translation-lessons.md` | 长篇 EPUB 交付、称呼校对、EPUB 实机兼容或成品验收前 |

只读取当前阶段需要的参考文件，不要把参考资料全文复制进模型 prompt 或交付报告。

## 主流程

1. 在 `<项目目录>` 运行 `version --json` 和 `check --json`，确认工具版本、Git 提交、CLI、配置状态、当前命令清单、内置 TXT/EPUB 闭环和已跟踪文件密钥扫描都可用。缺 `setting.toml` 时先让用户复制 `setting.example.toml` 并填写模型配置；只做 `--dry-run` 验证时可继续。聚合门禁失败时，再单独运行 `doctor --json`、`commands --json`、`self-test --json` 或 `secret-scan --json` 定位问题；文档命令和 `commands` 输出不一致时，以 `commands` 为准并停止使用旧命令。交付前运行 `check --strict --json`，任何 warning 都必须处理。
2. 如果源文件是 EPUB，先运行 `inspect-epub --path <小说文件> --json`，查看 spine、nav/toc、重复文本和标记风险。
3. 运行 `add-book --path <小说文件> --json` 注册小说，记录 `<书籍ID>`。
4. 运行 `work-records --book <书籍ID> --json` 初始化 `<工作记录目录>`。如果已有外部脚本、日志、术语表或人工文件，使用 `work-records --book <书籍ID> --collect-log-dir <日志目录> --json` 和 `work-records --book <书籍ID> --collect-file <文件> --json` 收纳到该书目录；后续报告优先输出到 `<工作记录目录>/reports/`，交付包优先输出到 `<工作记录目录>/delivery/`。
5. 运行 `text-scope --book <书籍ID> --json`，确认章节数和段落数合理。
6. 运行 `analyze-book --book <书籍ID> --json` 生成译前项目画像，再运行 `translation-plan --book <书籍ID> --json` 获取执行建议。
7. 运行 `export-terminology --book <书籍ID> --output-dir <工作区> --json` 导出术语候选和上下文。
8. 阅读 `terminology-workflow.md`，填写或审查 `<工作区>/terminology/glossary.json`。删除误判候选，补全人名、地名、组织名、能力名等稳定译名。
9. 运行 `import-terminology --book <书籍ID> --input <工作区>/terminology/glossary.json --json` 导入术语表。导入用的外部术语文件也应通过 `work-records --collect-file` 复制到 `<工作记录目录>/imports/`。
10. 运行 `terminology-status --book <书籍ID> --json`。有冲突先修术语表；空译名 warning 必须解释，不能假装已完成。术语表阶段必须做人称/称呼校对：日文 `さん`、`ちゃん`、`くん`、`さま` 等不要机械音译成“桑”“酱”，应按人物关系改为中文称呼、昵称、直呼姓名或省略。
11. 运行 `prepare-agent-workspace --book <书籍ID> --output-dir <工作区> --json` 和 `validate-agent-workspace --book <书籍ID> --workspace <工作区> --json`，确认工作区完整。
12. 运行 `audit-coverage --book <书籍ID> --json`，确认段落覆盖和可导出格式。
13. 大型或重复段落较多的书可使用 compact outline 省 token：运行 `export-translation-outline --book <书籍ID> --output <outline.json> --format compact --json`，只把 `constraints`、`dictionaries`、`units` 发给模型，`location_map` 留在本地；模型必须只返回 `{id, translated_lines}`。随后用 `convert-translation-outline-result --outline <outline.json> --input <模型结果.json> --output <manual.json> --json` 转成手动译文表，并先运行 `import-manual-translations --book <书籍ID> --input <manual.json> --json`。若出现 unknown id、duplicate id、缺失 `translated_lines` 或行数不一致，先修模型结果文件；不得绕过 `import-manual-translations` 直接写 manifest。
14. 长篇小说先运行 `summarize-context --book <书籍ID> --json`，再运行 `context-status --book <书籍ID> --json`；缺章节摘要时不要直接全量翻译。
15. 先确认 `setting.toml` 的 `[translation] style_guide`、`dialogue_style`、`style_sample_file` 和 `quality_passes` 符合本书目标读者；真实翻译请求会把这些要求放入 `quality_profile`，并在样例文件存在时注入 `style_reference`，用于约束文风、对话、忠实度和自查。
16. 先小批量执行 `translate --book <书籍ID> --max-batches 1 --json`。如果只是验证流程，用 `--dry-run`，但不要把 dry-run 当真实译文。
17. 查看 `run-report --book <书籍ID> --json`、`failed-batches --book <书籍ID> --json`、`translation-status --book <书籍ID> --json` 和 `quality-report --book <书籍ID> --json`。有失败批次时先 `retry-failed --book <书籍ID> --json` 或导出人工修复表。
18. 小批量没有规则性事故后，再继续执行 `translate --book <书籍ID> --json`；长篇可设置 `--workers`、`--rpm`、`--stop-on-warning`。
19. 直到 `pending` 为 0 后，再跑 `run-report`、`quality-report` 和 `review-translations --book <书籍ID> --mode risk --json`。未译、失败批次、源文残留、术语不一致、占位符缺失、人称/称呼问题、审校问题或 EPUB 标记风险必须处理或向用户说明。
20. 审校 JSON 只在填写 `approved_translation` 后才可用 `apply-review-fixes` 写入；坏译文用 `reset-translations` 精确清空。
21. 用户反馈漏翻/错翻时，用 `verify-feedback-text --book <书籍ID> --input <反馈文件> --json` 反查段落。
22. 导出前运行 `validate-export --book <书籍ID> --format txt|epub --json` 和 `delivery-check --book <书籍ID> --format txt|epub --json`，确认 `delivery-check.summary.ready=true` 后，再优先用 `package-delivery --book <书籍ID> --output-dir <工作记录目录>/delivery --format txt|epub --json` 生成交付包，并运行 `verify-delivery --manifest <工作记录目录>/delivery/delivery-manifest.json --json` 校验交付文件完整性。
23. EPUB 成品交付后运行 `validate-epub --path <成品.epub> --json`，确认 `valid_for_local_open=true`、`nav_broken_links=0`、`nav_empty_anchors=0`、`nav_linear_spine_count=0`、`spine_missing=0`、`mimetype_first=true`、`mimetype_uncompressed=true`、`toc_prefixed_namespace=false`、`metadata_description_source_residual=false`。通过后运行 `open-local --path <成品.epub> --json` 调起本机默认阅读器，确认阅读器窗口能打开、左侧目录能加载、首页或目录页能正常显示。

## 硬门槛

- 未完成术语导入前，不启动真实模型翻译，除非用户明确要求跳过术语流程。
- 小批量试翻前必须确认 `quality_profile` 的文风目标；如果用户要求特定题材口吻，先改 `setting.toml` 的 `style_guide`、`dialogue_style`，或把认可的译文片段写入 `style_sample_file`。
- `quality-report` 仍有 `terminology_mismatch` 时，不把译文称为最终版。
- `quality-report` 仍有 `placeholder_mismatch` 时，不把译文称为最终版；占位符必须原样保留。
- 交付前 `delivery-check` 不能是 `error` 且 `summary.ready` 必须为 `true`；`run-report.summary.failed` 必须为 0，`validate-export` 不能是 `error`。
- `quality-report` 仍有 `person_address_issue` 时，不把译文称为最终版；日文敬称音译残留、称呼不合中文语境、人物关系称谓不连续必须进入审校队列。
- EPUB 成品交付前 `validate-epub` 不能是 `error`；目录/nav/toc 断链、空目录锚点、spine 缺失、mimetype 非首项或被压缩都必须先修复或明确说明。`toc.ncx` 必须保持默认 NCX 命名空间，不能导出成 `<ns0:ncx>`，否则部分 Android 阅读器会报 `LoadTocError`。`nav.xhtml` 不能保留在线性阅读顺序中，必须是 `linear="no"` 或不在线性 spine 内，否则部分手机阅读器会把整本目录当作第一章正文。通过机器校验后还要用 `open-local` 做一次本机阅读器实开验证；能看到目录和页面内容才算 EPUB 打开测试通过。
- EPUB 的 OPF 元数据也属于交付内容，`dc:title`、`dc:description`、`dc:language` 应随译本更新；手机书籍详情页显示的简介不能残留日文假名。
- EPUB 有 `epub_markup_risk` 时，交付包必须包含 `export-epub-risk-report` 生成的风险报告。
- 审校建议不能自动覆盖译文；只有 `approved_translation` 通过 `apply-review-fixes` 验证后才算应用。
- 长篇小说建议先生成章节上下文；`context-status` 缺摘要时，只能小批量试翻或向用户说明风险。
- 有失败批次时，先 `retry-failed` 或导出人工修复表，不要直接进入最终导出。
- 翻译记忆命中必须匹配当前术语 hash；如果用户刚改过术语，先看 `translation-memory-status`，必要时用 `--no-memory` 强制重译。
- `--dry-run` 只能用于流程验证，不代表翻译完成。
- EPUB 导出是复制原 EPUB 并替换 spine XHTML 段落文本；复杂脚注、内联富文本、图片文字和特殊排版必须提醒用户复核。
- EPUB 回写依赖导入时保存的节点定位；`validate-export` 或 `export` 出现节点定位/hash warning 时，必须说明相关段落已保留原文或需要人工复核。
- 一部小说对应一个 `<工作记录目录>`；不要把后台脚本、运行日志、外部术语表、质量报告和交付包长期散落在项目根目录或全局日志目录。正在运行的日志可以先复制收纳，不要移动以免打断进程。
- 不要直接编辑私钥、API Key、`setting.toml` 或用户小说源文件。

## 禁止做法

- 绕过 CLI 手改 `data/books/<书籍ID>/manifest.json` 来伪造翻译进度。
- 用空译文、原文复制或 dry-run 结果冒充真实译文。
- 删除术语候选只为让状态变绿；必须保留有业务意义的稳定名词。
- 质量报告有 warning 仍直接交付最终结果而不说明风险。
- 把模型密钥写进工作区、报告、提交或 README。

## 长任务中断与失败重试

- 运行中的长篇翻译需要暂停时，优先执行 `request-stop --book <书籍ID> --reason <原因> --json`；新版本翻译进程会在当前批次结束或限流等待点保存进度并退出。
- 恢复前执行 `task-status --book <书籍ID> --json` 查看停止请求，再用 `clear-stop --book <书籍ID> --json` 清除停止标记，然后重新运行 `translate`。
- 旧版本后台进程不认识停止请求；如果代码刚升级过，需要先结束旧进程，再用新代码重启任务。
- 并发翻译必须在每个批次完成后立刻落盘并记录 run，不要等全部并发批次结束后再统一写入。
- 检测到 HTTP 402 或 Insufficient Balance 欠费错误时，立刻停止继续派发翻译请求，取消未开始批次，并向用户报告需要充值。
- `retry-failed --dry-run` 只能生成待重试计划，不能写入译文、不能清空译文、不能创建伪成功进度。
- `retry-failed` 只重试当前仍未翻译的失败段落；历史失败里后来已成功的段落必须自动跳过。
