# Glossary Automation v3 架构与运行说明

> 状态：v0.3.1 已发布。本文只保留公开的运行架构、迁移和回滚说明；详细实施记录归入内部阶段文档。

## 数据边界

- `output/<book>/data/glossary.json`：schema `3.0`，唯一术语事实源。
- `output/<book>/data/novel-translator-terms.json`：只包含上游翻译需要的 active 术语投影。
- `output/<book>/data/name-mapping-review.jsonl`：不确定人名映射人工复核队列。
- `output/<book>/reports/*.json`：每章候选、验证、激活、冲突、修订、回填与注入指标。

顶层保留 `terms`、`conflicts` 和 `revisions`。运行时 reader 允许保留未知 legacy 字段，但模型候选 schema `extra=forbid`。

## 生命周期

```text
Window Knowledge Extractor candidate
  → taxonomy + shape + evidence + confidence validation
  → candidate
      ├─ evidence 达标 → active
      ├─ 冲突 → disputed
      ├─ 新证据支持修订 → revised / 新 canonical active
      └─ blocked/无效 → rejected（仅诊断，不进入 active 投影）
```

`DIRECT_ALLOWED` 可按确定性规则快速激活；`GATED_ALLOWED` 需要足够且独立的 evidence；`BLOCKED` 不进入翻译术语投影。人名还要处理敬称、字符映射和歧义复核。

## 日常流水线

1. 整章翻译完成后，deterministic pre-scan 只查找已有 Glossary 命中，不产生候选或写入。
2. Review Window 修复后，Knowledge Extractor 提取临时 rolling context、候选和冲突。
3. 整章审阅完成后，同一 Extractor 只根据候选和相关 active/locked 知识决定最终动作。
4. pipeline orchestrator 通过 `apply_knowledge_delta()` 校验并合并 active Glossary/Memory；candidate/conflict 写入独立记录。
5. 下一章的 Context Budgeter 只投影相关 active/locked 词条，不把候选传给 Translator。
6. 符合修订规则的 active 词条可触发受控 backfill；只改 source 或旧 target 精确匹配的段落。

## 迁移

默认 dry-run：

```bash
python scripts/migrate_glossary_v3.py --output-root output
python scripts/migrate_glossary_v3.py --output-root output --book BOOK_DIR
```

应用：

```bash
python scripts/migrate_glossary_v3.py --output-root output --apply
```

apply 前创建 v2 备份，写入临时文件并重新打开验证 v3 schema。`BookWorkspace.initialize()` 遇到旧 schema 时也会执行相同迁移逻辑。

## Glossary 迁移与回填

历史 review delta replay 已移除；以下命令仅检查或应用 Glossary v2→v3 迁移：

```bash
python scripts/replay_glossary_v3.py --output-root output
python scripts/replay_glossary_v3.py --output-root output --apply
```

回填由 pipeline/service 调用 `translator/glossary/backfill.py`，遵守：

- source 或旧 translation 精确匹配；
- 只处理受影响 ID；
- 原子 manifest 写回；
- 报告 affected/changed/failed；
- 失败不回退其他已验证词条。

## 诊断指标

Release evidence 和章节报告汇总：`reported`、`known_hits`、`candidates`、`active`、`candidate`、`conflict`、`discard`、`rejected`、`category_blocked`、`backfill_affected/changed/failed`、`injected`。

## 回滚

1. 停止相关 worker；
2. 备份当前 v3 与 manifest；
3. 恢复迁移生成的 v2 backup；
4. 使用兼容旧版本运行；
5. 若只回滚一次错误修订，优先依据 `revisions`/evidence 做定向修复，不整体降级 schema。
