# Novel Translator Pipeline & Studio - 实施进度看板 (Progress)

## 📌 里程碑概览

- [x] **Release v0.1: CLI 核心流水线自包含版 (Component 1)**
- [ ] **Release v1.0: 现代化 Web 工作台与 Docker 镜像 (Components 2 ~ 4)**

---

## 🛠️ 任务分解与进度跟踪

### Component 1: 底层依赖收敛 (Submodule & Path Resolution)
- [x] **1.1** 确认 `novel-translator` fork 仓库与修复分支 (含 commit `69d5eb4`)
- [x] **1.2** 在本项目中引入 `vendor/novel-translator` Git Submodule
- [x] **1.3** 改造 `translator/core/novel_tool.py` 路径自适应与 Python 解释器探测
- [x] **1.4** 更新 `README.md` 与 `.env.example` 降低用户配置门槛
- [x] **1.5** 运行全量测试验证 CLI 核心自包含闭环

### Component 2: 后端 API 与异步任务管理层 (FastAPI + TaskManager + SSE)
- [ ] **2.1** 创建 `translator/web/` 基础结构与 FastAPI 应用工厂 (`app.py`)
- [ ] **2.2** 实现 SSE 实时事件广播器 (`events.py`)
- [ ] **2.3** 实现非阻塞异步任务调度器 (`task_manager.py`)
- [ ] **2.4** 实现核心 REST API 路由 (`books.py`, `tasks.py`, `knowledge.py`, `system.py`)
- [ ] **2.5** 编写 `tests/test_web_api.py` 接口自动化测试

### Component 3: 现代化 Web 前端 (React 18/19 + Vite + Tailwind + shadcn/ui)
- [ ] **3.1** 初始化 `frontend/` 项目结构与依赖配置
- [ ] **3.2** 构建全局布局、主题切换与顶栏状态大屏
- [ ] **3.3** 实现 **书架中心 (LibraryPage)**：EPUB 拖拽上传与状态卡片
- [ ] **3.4** 实现 **实时作战室 (LiveStudioPage)**：降级容灾流向拓扑与实时段落瀑布流
- [ ] **3.5** 实现 **双语阅读器 (ReaderEditorPage)**：虚拟滚动长列表与 AI 审阅记录查看
- [ ] **3.6** 实现 **记忆与术语透视台 (KnowledgePage)**：Glossary 与 Book Memory 可视化
- [ ] **3.7** 实现 **模型路由与系统设置 (SettingsPage)**：Provider 连通性预检与配置

### Component 4: 容器化与一键交付 (Deployment & Distribution)
- [ ] **4.1** 编写多阶段构建 `Dockerfile` (Node.js 构建前端 + Python 3.11+ 运行后端)
- [ ] **4.2** 编写 `docker-compose.yml` (预设数据卷持久化)
- [ ] **4.3** 编写本地一键启动脚本 `scripts/start_web.py`
- [ ] **4.4** 端到端全流程集成验证与 Release 打标
