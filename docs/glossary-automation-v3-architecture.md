# Glossary automation v3 架构与运行说明

## 数据边界

- `output/<book>/data/glossary.json` 是唯一事实来源，使用 `schema_version: "3.0"`。
- `novel-translator-terms.json` 是可删除、可重建的 active-only 投影；翻译 payload 只含 `source/target/category`。
- 人物经历、关系、剧情和状态继续写入 `book_memory.json`，不进入 glossary `note`。

## 生命周期

`GlossaryCandidate` 只允许 `source/target/category/confidence/evidence_ids/note`。程序生成 `term_id`、证据统计、状态和时间戳。

- DIRECT 类别：置信度至少 0.92 且有真实原文证据后激活。
- GATED 类别：两个段落/章节或两个独立 reporter 的证据后激活。
- BLOCKED 类别在 validator、merge 和 projection 三层拦截；章节审阅仍可通过 `fixes` 修复普通翻译错误。
- 冲突先记录为 `disputed`；多个独立强证据超过旧译评分后记录 `revisions`，并按 source/旧译匹配历史段落。

## 日常流水线

1. 章节预提取器按段落分块，逐块重试并按配置尝试 fallback reviewer；每块完成后写入 schema-clean 输出和 checkpoint，失败时保留失败块诊断并继续主翻译。
2. `BookWorkspace.glossary_path` 通过 `ProviderTranslator(glossary_path=...)` 显式进入当前 payload。
3. 翻译后章节 reviewer 使用相同的 `apply_glossary_delta` 服务累计证据；reviewer 的兼容字段会在生命周期边界投影为 canonical candidate，`reporters` 只用于证据归属。
4. target 修订生成 backfill affected/changed/unchanged/failed 记录；失败时章节状态为 `needs_retry`。

证据验证采用“保留有效子集”策略：同一候选的部分证据 ID 不匹配时，保留真实包含 source 的证据，并在章节统计中记录 discarded 数量；全部证据失效时才拒绝候选。

## v2 迁移和回滚

默认只预览：

```bash
python scripts/migrate_glossary_v3.py --output-root output
```

应用迁移会先创建 `glossary.json.v2.bak`，再原子写入并 reopen 校验：

```bash
python scripts/migrate_glossary_v3.py --output-root output --book BOOK_ID --apply
```

回滚使用迁移报告中的 backup 路径替换 authority 文件，然后删除投影并由 workspace/pipeline 重建。

## 诊断字段

章节报告的 `preextract` 区段记录 extraction_status、failed_chunks 和 extraction_attempts；`glossary` 区段记录 reported、accepted_candidates、activated、blocked、shape_blocked、evidence_total、evidence_valid、evidence_discarded、disputed、revised、实际注入数和 backfill 数；证据只保存 paragraph ID、reporter 和短 note。

历史工作区可先 dry-run 回放已持久化的预提取/审阅输出，再 apply。apply 会备份 authority glossary、重建投影、reopen 校验，翻译正文不写回：

```bash
python scripts/replay_glossary_v3.py --output-root output --book BOOK_ID
python scripts/replay_glossary_v3.py --output-root output --book BOOK_ID --apply
```
