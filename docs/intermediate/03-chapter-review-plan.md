# 章节一致性审阅与长程记忆机制

## 1. 架构目标

在保证审阅深度与跨段落长上下文连贯性的同时，最小化高阶模型调用成本，并实现长程记忆追踪与客观事实的自动沉淀。

---

## 2. 审阅数据流

```text
当前章节翻译完成全部段落
         ↓
组装审阅输入 Payload:
  - 当前章节全部段落 (带稳定 ID)
  - 翻译策略文档 (Translation Policy)
  - 全书长程记忆 (Book Memory)
  - 上一章节状态 (Previous Chapter State)
  - 动态术语表 (Glossary)
         ↓
执行通用 Reviewer 审阅 (OpenCode / Codex / Antigravity / Online API)
         ↓
产出结构化审阅结果:
  ├── checked_ids (全量覆盖校验)
  ├── fixes (译文缺陷与修复建议)
  ├── glossary_delta (术语新增/更新/冲突)
  ├── memory_delta (角色/世界观/关键事实变更)
  └── chapter_state (本章关键事实总结与状态演进)
         ↓
验证 checked_ids 覆盖全部输入（缺失则自动重试，最多 2 次）
         ↓
原子更新并持久化：
  - data/glossary.json
  - data/book_memory.json
  - data/chapter_states/<chapter_id>.json
  - data/novel-translator-terms.json
         ↓
筛选高置信度客观修复 (auto_apply=true, confidence>=0.9, major/critical)
         ↓
原子写回 manifest.json 并生成本章审阅报告
```

---

## 3. 校验与安全写回规则

为了防止审阅模型破坏有效译文，自动写回必须严格遵守以下守卫条件：

1. **`checked_ids` 全覆盖守卫**：
   - 审阅输出必须包含 `checked_ids` 列表，且必须 100% 覆盖输入章节的所有段落 ID；
   - 若出现漏报，流水线自动发起针对性重试。

2. **客观缺陷修复守卫**：
   - 仅对属于以下**客观类别**的问题执行自动写回：
     - `mistranslation`（严重误译）
     - `omission`（漏译）
     - `hallucination`（增译幻觉）
     - `subject_object`（主客体混淆）
     - `reference`（代词指代错误）
     - `terminology`（术语不一致）
     - `fact_conflict`（事实矛盾）
   - 纯主观润色（如 `style_enhancement`、`explicitness_intensity`）**不进行自动写回**，仅作为审阅建议记录在报告中。

3. **置信度与严重度门槛**：
   - `confidence >= 0.9` 且 `severity` 为 `major` 或 `critical`。

4. **术语与记忆合并冲突守卫**：
   - 术语和记忆条目新增/更新时，若发现与既有条目冲突，系统不会暴力覆盖，而是记录在 `conflicts` 列表中并保留历史记录。

---

## 4. 单章审阅独立命令

除了随流水线自动执行，章节一致性审阅亦可单独运行：

```bash
python scripts/chapter_review.py \
  --book 'BOOK_ID' \
  --name '正式中文书名' \
  --chapter c0001 \
  --apply \
  --autonomous
```

参数说明：
- `--chapter <ID>`：指定审阅单个章节；省略时将按顺序审阅整本书的所有章节；
- `--global-consistency`：整书全部章节审阅完毕后，执行全书跨章节一致性终审；
- `--apply`：自动将符合高置信度客观规则的修复写回 `manifest.json`；
- `--autonomous`：全自动模式；
- `--reviewer`：临时指定审阅后端（覆盖 `config.toml` 配置）。

