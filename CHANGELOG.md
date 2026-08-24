# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.2.0] - 2026-08-24

### 🌟 Major Release: Novel Translator Studio Web 全功能工作台与独立出版杂志视觉范式

#### 🎨 视觉范式革新 (Editorial Mag Paradigm)
- **独立出版杂志 (Editorial Mag) 美学落地**：全面重构前端视觉为温润优雅的出版瓷白暖纸基底（`#FAF9F6`），深邃墨水黑标题与石墨碳素灰正文；
- **典雅排版与字体系统**：引入 `Noto Serif SC` / `Zen Old Mincho` 经典衬线书体、现代开阔的 `Inter` 字体与精准克制的 `Space Grotesk` / `Fira Code` 等宽字体；
- **出版印章与皇室宝蓝点缀**：采用沉稳考究的皇家宝蓝（`#1D4ED8`）按键与焦点，辅以 `EDITION · 2026` 独立出版印章与 `#1` `#2` 印刷序号徽标。

#### 🚀 任务调度与队列中心 (Queue & Asset Hub)
- **已注册书籍资产池与队列解耦**：消除独立书架页面冗余，左侧展示已注册书籍资产列表（章节统计、翻译进度、重置记忆、导出 EPUB 与彻底删除），右侧支持批量排队；
- **自由拖拽排序与队列待命**：引入原生拖拽抓手（`⠿`）与置顶/上移/下移控制；书籍加入队列后处于待命暂停状态，支持调序完毕后手动一键启动；
- **动态并发槽位与失败重试**：支持 1~4 本并发槽位控制；异常书籍支持一键单书重试入队。

#### ⚡ 翻译控制台与实时事件流 (Live Studio & SSE Waterfall)
- **实时模型拓扑大屏**：实时可视化 Primary、Fallback #1、Fallback #2 及双审阅者路由流向与救回段落统计；
- **动态 Policy 规范切换**：在控制台直接为当前翻译任务选择不同的文学提示词规范（如情色小说规范、通用小说规范、轻小说规范等）；
- **单书独立 SSE 日志持久化**：重构状态为 `eventsByBook: Record<string, StreamEvent[]>`，切换标签页或书籍时实时日志不丢失，支持分类过滤与手动一键清空。

#### 📖 双语阅读器与审阅质检 (Bilingual Reader)
- **目次索引与段落精细对照**：目录索引展示全书章节状态，正文日中双语段落精细对照并标注翻译来源与容灾救回标记；
- **原地人工校对与单段重译**：支持直接在阅读器中编辑保存译文并即刻同步工作区；支持单段重新调用主译模型；
- **章节质检审阅报告**：折叠面板展示本章一致性审阅报告、长程叙事摘要及所有修正缺陷清单（包含修正原因、被替换内容与新译文）。

#### 🧠 知识库与提示词规范管理器 (Knowledge & Prompt Manager)
- **动态沉淀术语表与角色长程记忆**：展示由审阅模型在章节推进时自动提取并合并的专有名词、统一译名、置信度与出现章节，支持手动添加自定义术语；
- **AI Provider 可视化管理器与连通性探测**：直观配置与增删 OpenAI 兼容接口、Antigravity CLI、OpenCode CLI 与 Codex CLI，支持一键并发连通性测试（Preflight Probe）；
- **提示词规范管理器 (Prompt Policy Manager)**：在线查看、新建、编辑与保存 Markdown 翻译规范及审阅规范，支持一键设为系统全局默认规范。

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
