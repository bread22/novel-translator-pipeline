# Novel Translator Pipeline v0.3.1 架构

> 当前实现说明。历史提案保存在 [`docs/intermediate/`](intermediate/README.md)，不得将历史文件中的拟议类名或状态当作运行时事实。

## 1. 系统边界

系统由四个边界组成：

1. **上游书籍运行时**：`NOVEL_TRANSLATOR_ROOT/data/books/<book_id>/manifest.json` 保存章节、段落和当前译文；上游 CLI 负责注册、导出与 EPUB 校验。
2. **本仓库工作区**：`output/<safe-book-name>/` 保存 Glossary v3、Book Memory、Chapter State、review/report/snapshot、事件历史和最终 EPUB。
3. **任务权威源**：进程内唯一 `JobManager` 管理任务；`output/jobs/job_state.v2.json` 是持久化镜像。
4. **Web 投影**：FastAPI 提供 REST snapshot 和 SSE 通知，React reducer/局部 view state 只作为投影缓存。

正文完成度在读取 manifest 时重算；SSE 事件不替代 REST snapshot。

## 2. 端到端数据流

```text
EPUB/TXT
  → novel-translator 注册并生成 manifest
  → BookWorkspace 初始化/迁移
  → JobManager 创建唯一活动任务
  → ChapterPipeline 按章节推进
      → Primary 翻译
      → 自适应 split / fallback_translators
      → 原子写回 manifest + provenance
      → deterministic known-hit pre-scan
      → 字符预算滚动审阅
          → 双 Reviewer / Reviewer fallback
          → checked_ids、fixes、context_findings
          → Window Knowledge Extractor 临时上下文与候选
      → Chapter Knowledge Finalization
          → active/candidate/conflict/discard
          → 单一 apply_knowledge_delta 写入正式知识
      → 客观修复守卫与定向回查
  → finalize：导出、布局/metadata 注入、EPUB 校验、复制、hash
```

## 3. 任务、并发与恢复

`translator/core/job_manager.py` 是 Queue API、Task API 和 CLI batch 的共同执行引擎。

- 同书活动任务唯一；并发入口返回同一任务或显式拒绝非法转换。
- 队列调度暂停只停止新 worker；任务暂停在 pipeline checkpoint 生效，暂停 worker 仍占槽位。
- stop 先进入取消过程，只有 worker 收敛后发布 `cancelled`；终态不可被迟到的完成事件覆盖。
- state 保存失败会使 mutation 失败，不向调用方报告虚假持久化成功。
- 重启加载 v2 state，将旧进程中的活动项转换为 `recovery_pending`，保留顺序、checkpoint 和已写译文。

## 4. 翻译 Provider 路由

角色和 Provider 类型分离：

- 角色：`primary_translator`、`fallback_translators`、`reviewer`、`secondary_reviewer`、`fallback_reviewers`。
- 类型：`openai`、`antigravity`、`opencode`、`codex`。

翻译 payload 只包含当前目标 ID、必要上下文和相关术语。Primary 失败后根据 `split_on_content_filter` 与 `max_provider_split_depth` 决定拆分或立即进入 fallback。后续 Provider 只接收尚未完成的 ID，成功结果逐批原子写入，避免重复覆盖已完成段落。

## 5. 滚动审阅

审阅不是把任意大章节一次性发送给模型：

- `review_chunk_min_chars` / `review_chunk_max_chars` 控制目标字符预算；只在自然段边界切分。
- `review_context_before` / `review_context_after` 加入只读前后文。
- 每个 chunk 在修复投影后调用 Window Knowledge Extractor；仅临时 rolling context 会传给后续 chunk。
- 整章完成后由同一 Extractor 做一次 Finalization，只有 active 候选进入下一章的正式知识上下文。
- 模型失败时对目标 items 自适应二分；双审模式下两个 Reviewer 独立执行并合并共识。
- 后文发现前文问题时，`review_backtrack_enabled` 可触发只针对 context finding 的回查。

自动写回必须同时满足 ID 定位、客观类别、严重度、置信度、replacement 有效、非 no-op、日文残留策略等守卫。所有未应用项保留原因。

## 6. Glossary Automation v3

`translator/glossary/` 提供：

- `taxonomy.py`：DIRECT_ALLOWED、GATED_ALLOWED、BLOCKED 分类边界。
- `validation.py`：形态、证据、置信度与输入约束。
- `name_validation.py`：人名敬称、确定性映射与人工复核队列。
- `lifecycle.py` / `resolution.py`：candidate、active、disputed、revised、retired 状态和冲突处理。
- `projection.py`：按章节文本选择相关 active 术语。
- `backfill.py`：受证据约束的历史译文回填。

`glossary.json` 是单一事实源；上游 terms 文件是兼容投影。ChapterPipeline 的 `apply_knowledge_delta()` 统一调用生命周期服务；deterministic pre-scan 与 Reviewer 都不写正式知识。

## 7. Web、认证与事件

FastAPI 应用工厂注册 books、queue、tasks、knowledge、system 和 events 路由，并在可用时托管 built SPA。

- `WEB_AUTH_TOKEN` 非空时保护 `/api/v1/*`。
- REST 使用 Bearer header；浏览器下载和 EventSource 可使用受控 cookie/query token 链路。
- CORS 默认只接受 localhost/127.0.0.1，可通过 `WEB_CORS_ORIGINS` 添加明确来源。
- 响应含 request ID、CSP、frame、referrer 和 nosniff 安全头。

`EventBroadcaster` 的后台线程通过目标 asyncio loop 交接事件。服务端把按书事件追加到 `events.jsonl`；浏览器重连后重新拉取 canonical snapshots。

## 8. 原子性与事务边界

- JSON 写入：同目录临时文件、flush/fsync、原子 replace。
- 并发书籍数据：书级文件锁，避免不同段落或术语 lost update。
- Prompt/配置：路径封闭、schema 校验、备份、`.env` 私有权限和提交回滚。
- Reset/Delete：先处理活动 worker，再对 staging/目标目录执行事务操作；中途失败恢复原状态。
- Export：序列化同书导出，临时产物通过 ZIP/EPUB 校验、复制和 hash 后才发布。
- EPUB 解包：限制文件数、单文件/总大小、压缩比、重复路径、路径穿越和特殊文件。

## 9. 目录与持久化

```text
translator/
├── core/       # JobManager、workspace、config、paths、layout、metadata、novel tool
├── glossary/   # v3 taxonomy、models、validation、lifecycle、projection、backfill
├── pipeline/   # ChapterPipeline 与 preflight
├── providers/  # Provider adapters
├── review/     # chunk、dual review、merge、guards
└── web/        # FastAPI、routes、SSE、models
```

每章可产生：翻译前后 snapshot、known-hits、review input/output、窗口知识结果、finalization、applied fixes、report 和 provenance。历史 chapter state 只作为上下文读取。最终 EPUB 需验证 `mimetype`、container/OPF、spine、章节、HTML/XML 和资源引用。

## 10. 发布边界

发布归档器复制 Git 跟踪的源码/文档和已验证的 `frontend/dist`，排除缓存与本地草稿。`generate_release_evidence.py` 生成版本、OpenAPI、契约、dist、配置不变性和迁移 dry-run 证据 manifest。
