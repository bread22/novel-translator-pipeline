# 后续审阅流程改进计划

## 状态

窗口审阅、`checked_ids` 和章节级一致性检查均已实施。章节级检查当前采用“先单章验证，再决定是否扩大范围”的运行方式。

## 目标

在不让低成本模型决定 GPT 审阅范围的前提下，减少 GPT 调用次数，并增加跨段落上下文。

## 目标流程

```text
Murasaki 翻译 batch 1
Murasaki 翻译 batch 2
Murasaki 翻译 batch 3
Murasaki 翻译 batch 4
        ↓
一次 GPT 审阅 3～4 个 batch
        ↓
合并术语更新和译文修复
        ↓
继续下一组 batch
```

GPT 仍然读取窗口内的全部段落，不使用本地模型的风险结果决定哪些段落可以跳过。

## 已实施与计划改动

### 1. 延迟合并审阅（已实施）

- 每次先翻译 3～4 个 Novel Translator batch；
- 将这些 batch 合并为一个 GPT 审阅窗口；
- 使用字符数上限控制输入，建议初始上限为 30,000～50,000 字符；
- 保留每个段落的稳定 ID；
- 只有整个窗口审阅完成后才更新术语表。

### 2. 精简 GPT 输出（已实施）

审阅结果增加 `checked_ids`，证明窗口中的所有段落都已检查；`issues` 只输出有问题的段落：

```json
{
  "checked_ids": ["c0001-p00001", "c0001-p00002"],
  "issues": [
    {
      "id": "c0001-p00002",
      "type": "mistranslation",
      "severity": "medium",
      "approved_translation": "修正后的译文",
      "auto_apply": true,
      "confidence": 0.97
    }
  ],
  "term_updates": []
}
```

程序必须验证：

- `checked_ids` 覆盖输入窗口的所有段落；
- `issues` 中的 ID 必须来自当前窗口；
- 未列入 `issues` 的段落不修改；
- 漏回 `checked_ids` 时自动重试，不直接视为已检查。

### 3. 规则检查保持逐 batch 执行（已实施）

这些检查不需要 GPT：

- 日文残留；
- HTML 标签和占位符；
- 数字、单位和标点；
- 重复句；
- 译文为空或异常过短；
- 术语表冲突。

规则检查结果作为 GPT 的审阅提示，但不用于跳过段落。

### 4. 章节级一致性检查（已实施）

每章结束后额外进行一次 GPT 检查，重点关注：

- 人物称谓和人称；
- 术语统一；
- 时间顺序；
- 叙事视角；
- 前后文指代；
- 章节内重复或矛盾。

章节检查只返回问题段落 ID，不重写整章。入口为 `scripts/chapter_review.py`。

默认操作应先指定一个章节：

```bash
python scripts/chapter_review.py \
  --book 'BOOK_ID' \
  --name '正式中文书名' \
  --chapter-id c0001 \
  --apply \
  --autonomous
```

`--chapter-id` 是范围控制参数。指定后只生成并审阅该章节的输入、输出和修复文件；省略后会遍历 manifest 中的全部章节，因此全书审阅必须显式选择。`--export` 不属于单章验证的默认步骤，确认结果后再用于重新导出 EPUB。

## 不采用的方案

不采用“本地模型先筛选，GPT 只审阅本地模型标记段落”的方案。该方案虽然减少调用，但本地模型漏掉的问题也会被 GPT 跳过，不满足当前的自动质量目标。

## 当前结论与可选优化

规划中的流程改造已经完成，不存在必须继续执行的实施步骤。当前运行顺序为：

1. 先用 `--chapter-id` 验证单章一致性；
2. 确认结果后，再按需扩大到全书；
3. 翻译完成后执行最终质量报告并导出 EPUB。

以下属于可选的后续优化，不影响当前流程使用：

- 统计窗口字符数、GPT 调用耗时和审阅输出大小；
- 根据实际误报率调整章节审阅提示词；
- 增加审阅覆盖率和跨章节术语一致性的汇总统计。

## 已记录的下一阶段方案：分层上下文审阅

这部分暂不改变当前流程，后续有时间再实现。目标是减少 GPT 重复审阅的正文数量，同时保留判断翻译所需的长期上下文。

### 目标架构

```text
本地模型翻译
        ↓
4-batch 窗口审阅
输入：Book Memory、Chapter State、当前窗口、滚动上下文
        ↓
高置信度修复 + memory_delta
        ↓
章节完成
        ↓
整章一致性审阅
        ↓
更新 Book Memory
        ↓
全书完成后的轻量全局一致性检查
```

### 新增状态文件

```text
output/正式中文书名/data/
├── book_memory.json
└── chapter_states/
    ├── c0001.json
    └── c0002.json
```

`book_memory.json` 只保存跨章节仍然有价值的信息：人物、别名、固定称呼、人物关系、固定术语、重要事实、叙事视角和文风规则。`chapter_states/` 保存当前章节的地点、在场人物、情绪关系、时间线和滚动摘要。

### 审阅上下文

GPT 的输入分为：

```text
Book Memory
+ Chapter State
+ 当前 4-batch 窗口
+ 前后滚动段落
+ 按需检索的相关历史片段
```

当前窗口仍然完整传给 GPT，不把 GPT 降级成只查看本地模型标记的孤立段落。

### 审阅输出扩展

在现有 `checked_ids`、`issues` 和 `term_updates` 外增加：

```json
{
  "memory_delta": {
    "add": [],
    "update": [],
    "conflicts": []
  },
  "chapter_state_delta": {}
}
```

GPT 只提交增量，不直接重写整个 `book_memory.json`。程序负责 Schema 校验、合并、冲突记录和快照。译文修复继续使用 `auto_apply=true` 且置信度不低于 `0.9` 的门槛。

### 实施顺序

1. 增加 Book Memory、Chapter State 的 JSON Schema、存储和增量合并器；
2. 增加上下文构建器，将长期记忆、章节状态和窗口上下文组合给 GPT；
3. 让窗口审阅输出 `memory_delta` 和 `chapter_state_delta`；
4. 在整章完成后执行章节主审阅，同时更新长期记忆；
5. 全书完成后先审阅章节摘要、人物表、术语表和已知冲突；
6. 发现潜在冲突时，再按需读取相关章节原文和译文；
7. 用三种模式做对照测试：4-batch 窗口、整章、整章加 Book Memory。

### 评估指标

- 真实错误发现数；
- 主客体错误发现率；
- 人物称呼和术语一致性；
- 误报数量；
- GPT 调用次数、输入输出 token 和总耗时。

当前 4-batch 窗口流程继续作为基线，完成 benchmark 后再决定是否将整章审阅提升为主审阅流程。
