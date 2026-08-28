# 章节一致性审阅与长程记忆机制

> v0.3.1 当前行为；实现位于 `translator/review/` 与 `translator/pipeline/chapter_pipeline.py`。

## 1. 目标

审阅必须覆盖目标段落、利用相邻上下文、持续推进术语/记忆/叙事状态，同时防止模型用主观润色或幻觉破坏有效译文。

## 2. 字符预算滚动分块

章节按 `review_chunk_min_chars` 与 `review_chunk_max_chars` 形成目标块，仅在自然段边界切分。单个超大段保持完整。每块额外携带：

- `review_context_before` 个前文段落；
- `review_context_after` 个后文段落；
- 当前 Glossary 投影；
- Book Memory；
- 上一章/上一块 chapter state；
- translation policy。

只要求 `checked_ids` 覆盖目标块，前后文是只读 context。每块产生的 glossary、memory 和 chapter state 会更新 rolling payload 后再执行下一块。

## 3. Reviewer 路由

- 单审：`reviewer`，失败后只尝试 `fallback_reviewers` 中显式配置的后端。
- 双审：`reviewer` 与不同的 `secondary_reviewer` 并发独立审阅，随后合并共识、冲突和 reporter 证据。
- 失败块可按段落自适应二分到 `max_provider_split_depth`；每次 attempt、candidate、chunk、split path 和 timeout 都通过状态事件记录。
- 取消会停止等待尚未完成的 reviewer，不为已取消任务发布完成事件。

## 4. 输出契约

```json
{
  "checked_ids": ["c0001-p00001"],
  "fixes": [],
  "context_findings": [],
  "glossary_delta": {"add": [], "update": [], "conflicts": []},
  "memory_delta": {"add": [], "update": [], "conflicts": []},
  "chapter_state": {"summary": "", "important_changes": []}
}
```

Schema 与 Pydantic model 必须一致，未知 operation/category、重复或缺失 ID、无效 replacement 都在写回前拒绝。

## 5. 自动写回守卫

一个 fix 只有在全部条件成立时进入写回：

1. ID 属于当前章节目标集合；
2. 属于误译、漏译、幻觉、主客体、指代、术语、事实冲突等客观问题；
3. 严重度和置信度达到策略门槛，或属于必须清理的确定性日文假名残留；
4. replacement 非空、不是当前译文的 no-op，并通过 kana/遮罩字符和客观性检查；
5. 双审合并时满足共识或明确的最小侵入规则；
6. 写回后再次验证已批准 fix 的实际结果。

主观文风、露骨程度和偏好性改写只进入报告。未应用 fix 保留 `not_applied_reason`。

## 6. Context finding 与回查

后文可报告前文 context 的潜在错误。仅当 `review_backtrack_enabled=true` 且 confidence 达到 `review_backtrack_min_confidence` 时，系统对对应前文 ID 发起定向复核。回查仍执行完整写回守卫，不直接采用后文模型的单方 replacement。

## 7. Glossary 与 Memory 合并

Review delta 先经过确定性 schema/taxonomy/证据校验，再进入 Glossary v3 生命周期或 Memory merge。冲突不直接覆盖；记录 reporter、chapter/paragraph evidence、旧值、新值和修订状态。

## 8. 独立命令

```bash
python scripts/chapter_review.py \
  --book BOOK_ID \
  --name '正式中文书名' \
  --chapter c0001 \
  --apply \
  --autonomous
```

可用参数：`--global-consistency`、`--translation-policy`、`--export`、`--reviewer`。完整 chunk 与双审配置也可由 `book_pipeline.py` 的相应参数覆盖。

## 9. 证据

每章保存 review input/output、checked IDs、原始 fixes、approved fixes、未应用原因、glossary/memory delta、chapter state、snapshot 和 report。报告用于解释决策，manifest 与 workspace 文件仍是权威数据源。
