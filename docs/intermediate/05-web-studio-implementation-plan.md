# Implementation Plan - Novel Translator Studio (Web & Desktop)

> [!WARNING]
> 历史实施计划：双 Manager、跟踪 `frontend/dist` 等方案已被 v0.3 取代；当前交付由唯一 `JobManager` 与构建时生成/校验 SPA 资源完成。

为 `novel-translator-pipeline` 构建面向**全自动 AI 翻译流水线**与**个人内网/桌面**的现代化图形工作台（Novel Translator Studio）。系统彻底去除过时的多人协同校对流程，全面拥抱全自动 AI 调度（主译、二分降级容灾、长程事实记忆沉淀与 AI 审阅自动写回），提供实时透明监控作战室、沉浸式双语检验阅读器、长程记忆透视台与 Docker 一键交付。

---

## User Review Required

> [!IMPORTANT]
> **1. Git Submodule 依赖收敛**
> 本地在 `~/src/novel-translator` 中对嵌套 DOM 解析与 TOC 映射的修改（commit `69d5eb4`）需要推送到你 GitHub 上的 Fork 仓库，并在本项目中作为 `vendor/novel-translator` 子模块引入，确保后续任何人 clone 或 Docker 构建时均自包含全部核心能力。

> [!IMPORTANT]
> **2. 架构模式与交付形态**
> 采用 **FastAPI + React 18/19 (Tailwind CSS + shadcn/ui)** 前后端解耦架构。编译后的前端直接由 FastAPI 内置托管，实现“单一服务容器/单一命令”启动（默认端口 `8000`）。同时提供 `docker-compose.yml` 适配个人家庭服务器/NAS 常驻挂机。

---

## Proposed Changes

整体工程划分为四个核心组件阶段递进实施：

```text
Component 1: 底层依赖收敛 (Submodule & Path Resolution)
Component 2: 后端 API 与异步任务管理层 (FastAPI + TaskManager + SSE)
Component 3: 前端现代化 Web UI (React + Tailwind + shadcn/ui)
Component 4: 容器化与一键交付脚本 (Docker + Startup Scripts)
```

---

### Component 1: 依赖收敛与底层适配 (Dependency Convergence)

#### [MODIFY] [translator/core/novel_tool.py](file:///home/bread22/repos/novel-translator-pipeline/translator/core/novel_tool.py)
* 升级路径发现策略：优先使用项目内置 `vendor/novel-translator`，其次读取 `NOVEL_TRANSLATOR_ROOT` 环境变量，最后 fallback 至 `~/src/novel-translator`。
* 虚拟环境 Python 解释器自动探测机制（适配 `vendor/novel-translator/.venv` 及当前系统环境）。

#### [NEW] [.gitmodules](file:///home/bread22/repos/novel-translator-pipeline/.gitmodules)
* 声明 `vendor/novel-translator` 子模块。

---

### Component 2: 后端 API 与事件总线 (FastAPI Backend & Task Engine)

#### [NEW] [translator/web/__init__.py](file:///home/bread22/repos/novel-translator-pipeline/translator/web/__init__.py)
* 导出 Web 模块启动接口与 CLI 命令入口。

#### [NEW] [translator/web/app.py](file:///home/bread22/repos/novel-translator-pipeline/translator/web/app.py)
* FastAPI 核心应用工厂：挂载 CORS、API 路由、前端静态托管、全局异常处理。

#### [NEW] [translator/web/events.py](file:///home/bread22/repos/novel-translator-pipeline/translator/web/events.py)
* `EventBroadcaster` 异步事件广播器：利用异步队列分发 SSE 实时事件（流水线进度、段落翻译流、降级拦截告警、AI 审阅完成）。

#### [NEW] [translator/web/task_manager.py](file:///home/bread22/repos/novel-translator-pipeline/translator/web/task_manager.py)
* 异步任务调度引擎：
  * 将 `ChapterPipeline`、`TranslationQueue` 包装为非阻塞后台任务；
  * 提供 `start`、`pause`、`resume`、`stop` 状态机控制；
  * 挂载进度回调钩子打通至 `EventBroadcaster`。

#### [NEW] [translator/web/models.py](file:///home/bread22/repos/novel-translator-pipeline/translator/web/models.py)
* Pydantic v2 请求与响应数据协议（`BookSummary`、`ChapterDetail`、`ConfigDTO`、`PreflightResponse` 等）。

#### [NEW] [translator/web/routes/books.py](file:///home/bread22/repos/novel-translator-pipeline/translator/web/routes/books.py)
* 书籍 CRUD、EPUB 拖拽上传、元数据解析、章节目录与双语段落提取、单段微调写回、横排/原版 EPUB 导出与下载。

#### [NEW] [translator/web/routes/tasks.py](file:///home/bread22/repos/novel-translator-pipeline/translator/web/routes/tasks.py)
* 流水线启动、暂停、继续、停止控制；单段即时重译。

#### [NEW] [translator/web/routes/knowledge.py](file:///home/bread22/repos/novel-translator-pipeline/translator/web/routes/knowledge.py)
* 术语库 (`glossary.json`) 浏览与自定义补充；全书记忆 (`book_memory.json`) 与章节状态 (`chapter_states`) 读取。

#### [NEW] [translator/web/routes/system.py](file:///home/bread22/repos/novel-translator-pipeline/translator/web/routes/system.py)
* `config.toml` 读取与保存；Provider 一键连通性心跳与延迟预检。

---

### Component 3: 现代化 Web 前端 (React SPA UI)

位于 `frontend/` 目录：

#### [NEW] [frontend/package.json](file:///home/bread22/repos/novel-translator-pipeline/frontend/package.json)
* React 18/19, Vite, Tailwind CSS, Lucide Icons, Radix UI, TanStack Query, TanStack Virtual.

#### [NEW] [frontend/src/App.tsx](file:///home/bread22/repos/novel-translator-pipeline/frontend/src/App.tsx)
* 整体导航架构：顶栏状态指标、暗黑/明亮主题切换、全功能 Tab 路由。

#### [NEW] [frontend/src/pages/LibraryPage.tsx](file:///home/bread22/repos/novel-translator-pipeline/frontend/src/pages/LibraryPage.tsx)
* 书架中心：EPUB 拖拽上传区、书籍状态卡片瀑布流、进度条、一键开始/导出下载弹窗。

#### [NEW] [frontend/src/pages/LiveStudioPage.tsx](file:///home/bread22/repos/novel-translator-pipeline/frontend/src/pages/LiveStudioPage.tsx)
* 实时监控作战室：两级降级容灾流向拓扑图、段落瀑布流实时推流、控制台日志与 TPS/Token 监控大屏。

#### [NEW] [frontend/src/pages/ReaderEditorPage.tsx](file:///home/bread22/repos/novel-translator-pipeline/frontend/src/pages/ReaderEditorPage.tsx)
* 沉浸式双语阅读与检验器：虚拟滚动长列表、段落来源徽章（主译/救回/AI修复）、可选行内快速微调、AI 自动审阅修复报告查看、单段局部重译。

#### [NEW] [frontend/src/pages/KnowledgePage.tsx](file:///home/bread22/repos/novel-translator-pipeline/frontend/src/pages/KnowledgePage.tsx)
* 记忆与术语透视台：术语表 (Glossary) 分类浏览与手动添加、全书角色事实卡片 (Book Memory)、章节脉络时间线。

#### [NEW] [frontend/src/pages/SettingsPage.tsx](file:///home/bread22/repos/novel-translator-pipeline/frontend/src/pages/SettingsPage.tsx)
* 配置与路由控制台：主译/备用链/审阅者模型绑定、Provider 列表、一键连通性与延迟预检（🟢/🔴 状态徽章）。

---

### Component 4: 容器化与一键交付 (Deployment & Distribution)

#### [NEW] [Dockerfile](file:///home/bread22/repos/novel-translator-pipeline/Dockerfile)
* 多阶段构建：第一阶段 Node.js 编译前端静态文件为 `dist/`；第二阶段 Python 3.11+ 标准镜像挂载后端与 `vendor/novel-translator`，生成自包含镜像。

#### [NEW] [docker-compose.yml](file:///home/bread22/repos/novel-translator-pipeline/docker-compose.yml)
* 预配置持久化数据卷（`source/`, `output/`, `translated/`, `config.toml`, `.env`），一行命令拉起内网服务。

#### [NEW] [scripts/start_web.py](file:///home/bread22/repos/novel-translator-pipeline/scripts/start_web.py)
* 本地一键启动入口（`python scripts/start_web.py` 或 `python -m translator.web`）。

---

## Verification Plan

### 1. 自动化单元与集成测试 (Automated Tests)
* **API 接口测试**：
  ```bash
  python3 -m unittest discover -s tests -v
  ```
  编写 `tests/test_web_api.py` 验证书籍创建、段落读取、配置读写、预检路由的正确性。
* **类型与语法检查**：
  ```bash
  python3 -m py_compile translator/web/**/*.py scripts/start_web.py
  ```
* **前端编译构建测试**：
  ```bash
  cd frontend && npm run build
  ```

### 2. 手动端到端验证 (Manual Verification)
1. **服务启动验证**：运行 `python scripts/start_web.py`，浏览器打开 `http://127.0.0.1:8000` 正常渲染 UI。
2. **预检测试验证**：在设置页面点击【一键预检】，确认正确展示 Antigravity / OpenCode / DeepSeek / LM Studio 的连通与延迟。
3. **EPUB 导入与工作区验证**：拖入一本测试 EPUB，验证元数据解析、章节目录正常加载。
4. **实时流式翻译验证**：点击【开始翻译】，验证作战室实时显示 SSE 推送的段落流、进度百分比及降级拓扑动态高亮。
5. **双语沉浸阅读验证**：在双语对照页面查看已完成章节，确认来源标签与 AI 审阅记录正常展示。
6. **EPUB 导出与版式验证**：测试导出横排与原版 EPUB，确认阅读器排版正常。
