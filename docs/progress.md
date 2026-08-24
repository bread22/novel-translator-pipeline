# Novel Translator Pipeline & Studio - 实施进度看板 (Progress)

## 📌 里程碑概览

- [x] **Release v0.1: CLI 核心流水线自包含版 (Component 1)**
- [x] **Release v0.2: 现代化 Web 工作台与独立出版杂志视觉范式 (Components 2 ~ 4)**

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
- [x] **2.2** 实现 SSE 实时事件广播器 (`events.py`) 与当前页面会话内的单书分类记录
- [x] **2.3** 实现非阻塞异步任务调度器 (`task_manager.py`) 与队列管理层 (`queue_manager.py`)
- [x] **2.4** 实现核心 REST API 路由 (`books.py`, `queue.py`, `tasks.py`, `knowledge.py`, `system.py`, `events.py`)
- [x] **2.5** 编写 `tests/test_web_api.py` 与 `tests/test_queue_api.py` 接口自动化测试 (87 项测试 100% 通过)

### Component 3: 现代化 Web 前端与出版杂志视觉范式 (React 19 + Vite + Tailwind v4)
- [x] **3.1** 初始化 `frontend/` 项目结构与依赖配置 (Vite + React 19 + Tailwind v4)
- [x] **3.2** 全面落地 **独立出版杂志 (Editorial Mag)** 视觉范式 (暖白瓷纸色 `#FAF9F6`、Noto Serif 衬线字体、皇家宝蓝 `#1D4ED8` 点缀)
- [x] **3.3** 重构 **任务调度中心 (QueueHubView)**：已注册书籍资产池 + 自由拖拽排序队列 + 动态并发槽位 + 待命启动
- [x] **3.4** 实现 **翻译控制台 (LiveStudioView)**：模型路由多级降级拓扑、动态提示词策略切换与实时事件瀑布流
- [x] **3.5** 实现 **双语阅读器 (ReaderView)**：目录索引、原地人工校对、单段重译与一致性审阅质检报告
- [x] **3.6** 实现 **记忆与术语库 (KnowledgeView)**：词典版式术语表、角色长程档案与世界观设定
- [x] **3.7** 实现 **模型路由与提示词规范管理 (SettingsView)**：AI Provider 管理器、一键并发连通性预检与 Markdown 规范在线编辑

### Component 4: 一键交付与运行保障 (Deployment & Execution)
- [x] **4.1** 编写本地一键启动脚本 `scripts/start_web.py`
- [x] **4.2** 静态资源自动打包部署到 `frontend/dist/`
- [x] **4.3** 修复沙箱外网出站 403 阻断，支持多模型线上 API 正常调用
- [x] **4.4** 全量文档更新与 Release v0.2 打标
