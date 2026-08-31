# 术语流程

术语流程的目标是让小说中的稳定名词在全书保持一致，尤其是人名、地名、组织名、能力名、种族名、称号和特殊道具名。

## 工作区文件

`export-terminology` 会生成：

```text
<工作区>/
  manifest.json
  terminology/
    glossary.json
    contexts/
      term-contexts.json
```

`glossary.json` 是可编辑文件，`term-contexts.json` 是只读上下文。

## glossary.json 结构

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

字段说明：

- `source`: 原文术语，不能为空。
- `target`: 固定译名。未确认时可暂空，但导入后 `terminology-status` 会 warning。
- `category`: 建议使用 `name`、`place`、`organization`、`ability`、`item`、`title`、`other`。
- `note`: 可写角色身份、性别、称呼禁忌、上下文说明。
- `occurrences` 和 `sample_ids`: 候选统计信息，通常不需要手改。

## 审查规则

- 删除普通句子、一次性描写、误判的英文句首词和无业务意义候选。
- 同一 `source` 只能保留一个主要译名；别名写入 `note`，不要重复建冲突项。
- 不要机械音译所有词。角色名、地名可音译；能力名、组织名和称号通常需要语义化。
- 已有官方译名、系列译名或用户指定译名时，以用户要求为准。
- 如果一个术语暂时无法判断，保留 `source`，把 `target` 留空并在 `note` 说明原因；不要编造。
- 正文术语表只放稳定名词，不放整句、标点包装、对话模板或普通高频词。

## 导入前检查

导入前至少检查：

- JSON 可解析。
- `terms` 是数组。
- 每项有非空 `source`。
- 同一 `source` 没有多个不同 `target`。
- 明显重要的人名、地名已填写 `target`。

导入后运行：

```bash
python3 main.py --agent-mode terminology-status --book <书籍ID> --json
```

有 `error` 必须修；有空译名 warning 时，只有在确认这些术语不影响本轮翻译时才继续。

## 翻译与质量检查关系

翻译命令会把当前批次命中的已填写术语注入 `glossary`。质量报告会检查：原文包含 `source` 且术语有 `target` 时，译文必须包含该 `target`。

如果 `quality-report` 出现 `terminology_mismatch`：

1. 先判断术语表是否错了。
2. 术语表正确时，修译文或重跑相关段落。
3. 术语表错误时，修 `glossary.json` 后重新导入，再重译受影响段落。

