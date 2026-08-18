# 处理架构

## 数据流

```text
原始 EPUB
   ↓ 保留原文件，建立工作副本
Novel Translator 导入/解包
   ↓
manifest + 当前译文
   ↓
Automation 选择下一个分片
   ↓
Novel Translator + LM Studio 翻译
   ↓
Codex CLI 审阅
   ├── issues
   ├── approved_translation
   └── term_updates
   ↓
更新 glossary.json，应用高置信度修复
   ↓
下一个分片
   ↓
最终质量报告与中文 EPUB
```

## 分片定义

分片是带有稳定段落 ID 的小段译文，默认建议 10～30 个自然段。它不是新的 EPUB，也不是单纯的文本预览，而是包含源文、当前译文和元数据的审阅输入：

```json
{
  "book": "BOOK",
  "chunk_id": "chunk-0001",
  "items": [
    {
      "id": "paragraph-id",
      "source": "源文",
      "translated": "当前译文"
    }
  ],
  "glossary": {}
}
```

Codex 输出问题、建议译文和术语候选。普通模式只有 `auto_apply=true` 且 `confidence >= 0.9` 的修复进入自动写回流程；`--autonomous` 模式则自动写回所有有明确 `approved_translation` 且 `confidence >= 0.9` 的项目。术语更新也记录来源分片和置信度，方便回滚。

## 文件边界

- `novel-translator`：EPUB 解析、翻译、manifest、快照、质量报告、修复写回和导出。
- `erotic_novel_translator`：书籍目录、分片调度、Codex 审阅、术语表、审阅记录、流程日志和最终编排。

Automation 不直接改写原始 EPUB，也不直接连接 LM Studio；它通过 Novel Translator 的命令接口完成翻译相关操作。

## 可靠性要求

每个分片完成后保存：

1. 当前术语表；
2. 翻译前后快照；
3. Codex 原始审阅结果；
4. 已应用修复清单；
5. 质量报告。

最终 EPUB 应从解包后的工作副本重新打包，并验证 `mimetype`、OPF、目录、章节顺序、HTML 标签和资源路径。
