# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.1.0] - 2026-08-23

### 🚀 Initial Release (CLI 核心流水线自包含版)

#### 🌟 核心特性
- **通用 Provider 架构**：通过统一的 `BaseProvider` 适配器解耦角色与模型，支持 `antigravity` (Gemini)、`opencode`、`codex` 及 `openai` 兼容协议（本地 LM Studio / Ollama / 在线 DeepSeek、SiliconFlow 等）；
- **两级降级容灾回路 (Two-Level Fallback)**：主译遇敏感词安全审查拦截（`content_filter`）时，自动触发二分递归拆解；单段落仍受阻时，顺序降级至**一级备用**（如 OpenCode），若仍受阻则无缝降级至**二级备用**（如 LM Studio 本地无审查模型），全程自动记录 Provenance 溯源；
- **章节级长上下文一致性审阅**：每章全量翻译完成后，执行一次通用审阅，校验 100% `checked_ids` 覆盖率，自动提取并合并 Glossary 增量、Book Memory 事实记忆和 Chapter State 状态演进；
- **高置信度客观缺陷自动写回**：仅对 `confidence >= 0.9`、`major/critical` 的客观错误（误译、漏译、主客体错位、事实冲突等）自动安全替换；
- **横排版式重构 (Horizontal Layout)**：支持日文竖排 EPUB 自动重构为中文横排版式，调整翻页方向为 `ltr` 并重置语言元数据；
- **一键批量翻译队列**：支持将待翻译 EPUB 投入 `source/` 目录，一键全自动顺序处理并导出至 `translated/`；
- **Provider 连通性预检**：内置 `preflight.py` 连通性测试与延迟探测工具；
- **上游 PR 补丁就绪**：内置 `patches/` 目录，提供 EPUB 嵌套 DOM 修复与 Strict JSON Schema 参数增强补丁（对应上游 PR #1 与 PR #2）。

---
