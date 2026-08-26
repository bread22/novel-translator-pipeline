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

1. 章节预提取器按段落分块，写入候选和诊断；失败时继续主翻译。
2. `BookWorkspace.glossary_path` 通过 `ProviderTranslator(glossary_path=...)` 显式进入当前 payload。
3. 翻译后章节 reviewer 使用相同的 `apply_glossary_delta` 服务累计证据。
4. target 修订生成 backfill affected/changed/unchanged/failed 记录；失败时章节状态为 `needs_retry`。

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

章节报告的 `glossary` 区段记录 reported、accepted_candidates、activated、blocked、disputed、revised、实际注入数和 backfill 数；证据只保存 paragraph ID、reporter 和短 note。
