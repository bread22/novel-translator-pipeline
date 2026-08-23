# 全栈数据连通性与交互完整性全量自测与修复计划 (Full Stack Data Connectivity Audit & Fix Plan)

本任务由 Agent 自主完成端到端自查、自测与修复，覆盖所有前端视图、后端接口、数据模型、SSE 事件流与异常边界。

## 1. 审计与测试范围 (Audit Scope)

### 1.1 前端 5 大视图与组件 (Frontend Views)
- **LibraryView (书库总览)**：书籍列表获取、格式与章节解析、进度百分比、字数段落统计、上传、书籍重置 (Reset)、删除书籍。
- **LiveStudioView (实时控制台)**：SSE 事件流连接与重连、断点续译、拓扑图模型与备用 Provider 真实绑定、任务启停/暂停/恢复、进度指标同步、提示词规范选择。
- **ReaderView (双语阅读器)**：章节目录切换、双语/单语排版对照、段落级单点重译 (Retranslate Paragraph)、章节质检抽屉 (Review Drawer)、段落搜索。
- **KnowledgeView (知识库与质检报告)**：术语表增删查改、角色档案与世界观记忆提取映射、全书章节审阅与质检报告列表、详细报告抽屉 (Fixes/Glossary/Memory Delta)。
- **ConfigView (配置中心与联通体检)**：全系统 Provider 预检诊断 (Preflight)、Provider 增改、超时/温度参数、提示词规范列表、配置保存持久化。

### 1.2 后端 5 大模块路由与数据层 (Backend API & Data Flow)
- `translator/web/routes/books.py`
- `translator/web/routes/tasks.py`
- `translator/web/routes/knowledge.py`
- `translator/web/routes/config.py`
- `translator/web/routes/events.py`
- `translator/web/task_manager.py`
- `translator/pipeline/chapter_pipeline.py`

---

## 2. 详细自测与修复计划 (Detailed Action Items)

1. **类型定义与字段对齐 (API Schema Parity)**：
   - 检查 `frontend/src/types/api.ts` 与 Python Pydantic Models (`models.py`, `workspace.py`, `reviewer.py`)，杜绝任何字段命名差异（如下划线/驼峰、缺失字段、默认值不符）。
2. **段落级单点重译功能联通性自查 (Single Paragraph Retranslation)**：
   - 测试 `POST /api/v1/tasks/retranslate_paragraph` 在真实环境下的执行情况，确保重译后不仅更新 manifest.json，还能正确广播更新并反馈给前端阅读器。
3. **知识库与审阅报告数据流自查 (Knowledge & Reviews)**：
   - 检查 `GET /api/v1/knowledge/{id}/reviews` 与 `GET /api/v1/knowledge/{id}/reviews/{chapter_id}` 在空数据、部分数据、多章节数据下的健壮性。
   - 检查角色与世界观提取在各种 `book_memory.json` 变体结构下的兼容性。
4. **配置中心与预检诊断自查 (Preflight & Config)**：
   - 检查 `POST /api/v1/config/preflight` 的错误捕获机制，确保任何 Provider 故障（如 API 密钥无效、网络超时）均返回规范的 JSON 错误诊断而非 500 崩溃。
5. **全量自动化端到端测试用例编写 (Automated Test Suite)**：
   - 为上述所有 API 路由与边界条件编写完整的 Pytest 自动化测试用例，并在测试中验证每一个字段的返回契约。
6. **前端打包与类型检查 (Frontend Build & Validation)**：
   - 运行 TypeScript 类型检查 `tsc --noEmit` 和 Vite 打包 `npm run build`，确保 0 报错。
7. **CI 自动化验证 (CI Pipeline Verification)**：
   - 提交代码至 GitHub 并验证全矩阵 CI 100% Green。

