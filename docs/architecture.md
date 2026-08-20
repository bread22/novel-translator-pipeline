# 处理架构

## 数据流

```text
原始 EPUB
   ↓ 保留原文件，建立工作副本
Novel Translator 导入/解包
   ↓
manifest + 当前译文
   ↓
Automation 选择下一章并推进其全部翻译 batch
   ↓
整章审阅
   ├── checked_ids / fixes
   ├── glossary_delta
   ├── memory_delta
   └── chapter_state
   ↓
更新 glossary.json、book_memory.json 和 chapter_states
   ↓
应用客观高置信度修复
   ↓
下一章
   ↓
最终质量报告与中文 EPUB
```

## 章节审阅输入

章节审阅输入带有稳定段落 ID，包含整章源文、当前译文、Glossary、Book Memory 和上一章状态：

```json
{
  "book": "BOOK",
  "chapter_id": "c0001",
  "book_memory": {},
  "previous_chapter_state": {},
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

审阅输出问题、完整段落替换、Glossary 增量、Memory 增量和章节状态。只有客观类别、`major/critical`、`confidence >= 0.9` 且包含替换译文的项目进入自动写回流程。所有增量都记录来源章节和冲突，方便回滚。

## 文件边界

- `novel-translator`：EPUB 解析、翻译、manifest、快照、质量报告、修复写回和导出。
- `erotic_novel_translator`：书籍目录、分片调度、Codex 审阅、术语表、审阅记录、流程日志和最终编排。

Automation 不直接改写原始 EPUB，也不直接连接 LM Studio；它通过 Novel Translator 的命令接口完成翻译相关操作。

## 可靠性要求

每章完成后保存：

1. 当前术语表；
2. 翻译前后快照；
3. 原始章节审阅结果；
4. 已应用修复清单；
5. Book Memory 和 Chapter State；
6. 质量报告。

最终 EPUB 应从解包后的工作副本重新打包，并验证 `mimetype`、OPF、目录、章节顺序、HTML 标签和资源路径。
