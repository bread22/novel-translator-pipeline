# Novel Translator Pipeline & Studio - 实施进度看板 (Progress)

> [!WARNING]
> 历史里程碑快照，未完成项不代表当前状态；最新验收进度见 `docs/progress.md`，v0.3 已收敛为唯一 `JobManager` 和 Schema v2。

## 📌 里程碑概览

- [x] **Release v0.1: CLI 核心流水线自包含版 (Component 1)**
- [x] **Release v1.0: 现代化 Web 工作台与 Docker 镜像 (Components 2 ~ 4)**
- [ ] **Release v1.1: 任务队列系统 (Queue System - Component 5)**

---

## 🛠️ 任务分解与进度跟踪

### Component 1: 底层依赖与补丁收敛 (Patches & Path Resolution)
- [x] **1.1** 梳理 `novel-translator` 改动，拆解为 2 个独立 PR 补丁 (`patches/0001-...` 与 `patches/0002-...`)
- [x] **1.2** 保持单仓库架构，提供清晰补丁便于向上游提交 PR
- [x] **1.3** 改造 `translator/core/novel_tool.py` 路径自适应与 Python 解释器探测
- [x] **1.4** 更新 `README.md` 与 `.env.example` 降低用户配置门槛
- [x] **1.5** 运行全量测试验证 CLI 核心自包含闭环

### Component 2: 后端 API 与异步任务管理层 (FastAPI + TaskManager + SSE)
- [x] **2.1** 创建 `translator/web/` 基础结构与 FastAPI 应用工厂 (`app.py`)
- [x] **2.2** 实现 SSE 实时事件广播器 (`events.py`)
- [x] **2.3** 实现非阻塞异步任务调度器 (`task_manager.py`)
- [x] **2.4** 实现核心 REST API 路由 (`books.py`, `tasks.py`, `knowledge.py`, `system.py`, `events.py`)
- [x] **2.5** 编写 `tests/test_web_api.py` 接口自动化测试 (57 项测试 100% 通过)

### Component 3: 现代化 Web 前端 (React 18/19 + Vite + Tailwind + Lucide Icons)
- [x] **3.1** 初始化 `frontend/` 项目结构与依赖配置 (Vite + React 19 + Tailwind v4)
- [x] **3.2** 构建全局布局、顶栏状态大屏与实时 SSE 状态同步 (`Navbar.tsx`)
- [x] **3.3** 实现 **书架中心 (LibraryView)**：EPUB/TXT 拖拽上传解析与自适应进度卡片
- [x] **3.4** 实现 **实时作战室 (LiveStudioView)**：两级降级容灾流向拓扑与实时事件瀑布流
- [x] **3.5** 实现 **双语阅读器 (ReaderView)**：章节分栏阅读、原地编辑修改与单段重译
- [x] **3.6** 实现 **记忆与术语透视台 (KnowledgeView)**：Glossary 术语表与角色长程记忆事实
- [x] **3.7** 实现 **模型路由与系统设置 (SettingsView)**：Provider 连通性预检与配置热更新

### Component 4: 容器化与一键交付 (Deployment & Distribution)
- [x] **4.1** 编写多阶段构建 `Dockerfile` (Node.js 构建前端 + Python 3.11+ 运行后端)
- [x] **4.2** 编写 `docker-compose.yml` (预设数据卷持久化)
- [x] **4.3** 编写本地一键启动脚本 `scripts/start_web.py`
- [x] **4.4** 端到端全流程集成验证与全量自测

### Component 5: 任务队列系统 (Queue System)
- [ ] **5.1** 实现 `QueueManager` 核心调度引擎 (并发控制、优先级队列、状态持久化)
- [ ] **5.2** 封装 `/api/v1/queue` 完整 REST API 与 SSE 事件广播
- [ ] **5.3** 开发前端 `QueueView.tsx` 队列管理看板与 Navbar/Library 联动
- [ ] **5.4** CLI 批量脚本 `translation_queue.py` 升级与自动化测试验证
