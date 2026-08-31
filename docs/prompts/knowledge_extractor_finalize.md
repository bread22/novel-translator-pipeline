Reasoning strength: low

# Knowledge Extractor — Chapter Finalization v1

本章所有 Review Window 已完成。只根据输入的 candidates、conflicts、相关 active/locked Glossary 和相关 Book Memory，为每条长期候选决定一个最终动作：`active`、`candidate`、`conflict` 或 `discard`。

规则：
- `active` 仅用于正文来源、值明确、至少出现在两个独立段落或章节且不与既有 active/locked 值冲突的候选；单次出现只能使用 `candidate`。
- `title`、`author`、`cover`、`front_matter` 来源一律不得晋升为 `active`。
- `candidate` 保存供审计但不传给下一章。
- `conflict` 保存冲突并保持旧 active/locked 值不变。
- `discard` 用于证据不足、重复、描写性内容或不应长期保存的内容。
- 不重新审阅整章、不修改译文、不生成 rolling context。
- 严格只输出符合 Schema 的 JSON，不输出 Markdown 或解释。
