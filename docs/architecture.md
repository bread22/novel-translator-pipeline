# 处理架构

## 数据流与两级降级容灾

```text
原始 EPUB
   ↓ 保留原文件，建立工作副本
Novel Translator 导入/解包
   ↓
manifest + 当前译文
   ↓
Automation 推进章节翻译
   ├── 主译 (Primary Translator, e.g. Antigravity / Gemini / Online API)
   │     ↓ 遇到敏感词/格式/错误
   │   递归二分拆解 (Binary Split)
   │     ↓
   ├── 一级备用 (Fallback #1, e.g. OpenCode / 指定模型)
   │     ↓ 仍受阻/失败
   └── 二级备用 (Fallback #2, e.g. LM Studio / 本地无审查模型)
   ↓
整章审阅 (Reviewer, e.g. OpenCode / Codex / Antigravity / Online API)
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

## 通用后端适配层 (Universal Provider Adapters)

本项目采用 **角色与后端解耦（Role-Agnostic）** 架构：

- **角色（Roles）**：仅代表流水线工作岗位，支持在 `config.toml` 中自由指定：
  - `primary_translator`：主力翻译器（可配置为 `antigravity`、`opencode`、`codex`、`online_api` 等）；
  - `fallback_translators`：多级备用翻译器链（如 `["opencode", "lmstudio"]`）；
  - `reviewer`：章节一致性与事实审阅器（可配置为 `opencode`、`codex`、`antigravity`、`online_api` 等）。
- **Provider 类型**：
  1. `openai` / `http`：标准 OpenAI 兼容在线/本地接口（LM Studio、DeepSeek、OpenRouter、SiliconFlow、OpenAI、Ollama、vLLM 等）；
  2. `antigravity`：通过 `agy` CLI 调度 Gemini 系列模型；
  3. `opencode`：通过 `opencode run --format json` 调度本地或远端多模型；
  4. `codex`：通过 `codex exec` 约束 Schema 进行精准翻译与深度审阅。

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
- `novel-translator-pipeline`：书籍目录、分片调度、多级 Fallback 容灾、统一 Provider 适配、Codex/OpenCode/AGY/API 审阅、术语表、审阅记录、流程日志和最终编排。

## 可靠性要求

每章完成后保存：

1. 当前术语表；
2. 翻译前后快照；
3. 原始章节审阅结果；
4. 已应用修复清单；
5. Book Memory 和 Chapter State；
6. 翻译来源诊断 (`translation-provenance.json`)；
7. 质量报告。

最终 EPUB 应从解包后的工作副本重新打包，并验证 `mimetype`、OPF、目录、章节顺序、HTML 标签和资源路径。
