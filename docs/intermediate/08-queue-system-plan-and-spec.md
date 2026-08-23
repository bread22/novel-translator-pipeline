# 翻译任务队列系统 (Queue System) 设计规范与实施计划

## 概述与设计目标

为 `novel-translator-pipeline` 构建生产级、高容错、可观测的**多书籍全自动翻译队列管理系统 (Queue System)**。
解决当前只能单本手动触发、无法并发控制与调度、批量脚本与 Web UI 割裂、缺乏队列排队与优先级调整及状态持久化等问题。

---

## 核心架构设计 (Architecture Spec)

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                             Web Frontend (UI)                               │
│  ┌──────────────────┐  ┌──────────────────┐  ┌───────────────────────────┐  │
│  │   书架中心       │  │   任务队列中心   │  │   翻译控制台 (LiveStudio) │  │
│  │ (批量入队/单本入队)│  │(排队/调序/重试/并发)│  │ (实时单书流水线 & 溯源)   │  │
│  └─────────┬────────┘  └─────────┬────────┘  └─────────────┬─────────────┘  │
└────────────┼─────────────────────┼─────────────────────────┼────────────────┘
             │                     │                         │
             │ HTTP REST / SSE     │ HTTP REST / SSE         │ SSE
             ▼                     ▼                         ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        FastAPI Web Backend (API v1)                         │
│  ┌───────────────────────┐               ┌───────────────────────────────┐  │
│  │  /api/v1/queue routes │               │     Event Broadcaster (SSE)   │  │
│  └───────────┬───────────┘               └───────────────▲───────────────┘  │
│              ▼                                           │ Broadcast Events │
│  ┌───────────────────────────────────────────────────────┴───────────────┐  │
│  │                    QueueManager (核心调度引擎)                        │  │
│  │  - FIFO / 优先级队列 (Thread-Safe Priority/Position Queue)            │  │
│  │  - 动态并发控制槽位 (Concurrency Controller: 1..N Workers)             │  │
│  │  - 任务生命周期状态机: pending → running → completed / failed / stopped│  │
│  │  - 状态持久化与崩溃自愈 (output/queue/queue_state.json)               │  │
│  │  - 异常熔断与自动跳过 (stop_on_error 策略)                            │  │
│  └───────────────────────────────────┬───────────────────────────────────┘  │
│                                      │ Dispatches Worker Threads            │
│                                      ▼                                      │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                  ChapterPipeline & Provider Fallback                  │  │
│  │          (主译 → 递归二分 → 两级降级容灾 → 一致性审阅 → EPUB 导出)      │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 用户确认事项 (User Review Required)

> [!IMPORTANT]
> **并发控制与 LLM API 速率限制 (Concurrency Setting)**
> 默认队列并发数设为 `1`（保证 API 调用不被限流，显存/本地模型不发生冲突）。用户可在 Web 控制台或 `config.toml` 中动态调节并发数（支持 1~4）。

> [!TIP]
> **数据持久化与自动恢复 (State Persistence)**
> 队列状态将实时保存于 `output/queue/queue_state.json`。若服务异常退出或重启，未完成的任务将保留在队列中，避免重新导入丢失进度。

---

## 详细功能规范 (Functional Spec)

### 1. 数据模型规范 (Data Models)

#### `QueueItemStatus`
```python
# 队列任务状态枚举
"pending"     # 排队等待调度
"running"     # 正在执行流水线
"paused"      # 队列已暂停
"completed"   # 翻译与导出完成
"failed"      # 执行失败（可查看错误详情并单键重试）
"cancelled"   # 已由用户取消移出运行
```

#### `QueueItem`
```python
class QueueItem(BaseModel):
    id: str                                  # 唯一任务 ID: "qitem-{timestamp}-{book_id[:8]}"
    book_id: str                             # 书籍唯一标识
    book_name: str                           # 书籍显示标题
    source_type: str = "epub"                # 来源格式 (epub/txt)
    options: PipelineStartRequest            # 启动参数 (apply, autonomous, layout, policy, providers)
    status: str = "pending"                  # QueueItemStatus
    order_index: int = 0                     # 队列排位次序
    priority: int = 0                        # 优先级权重 (默认 0, 越大越先执行)
    overall_progress: float = 0.0            # 进度百分比 (0.0 ~ 1.0)
    current_chapter: str = ""                # 当前执行章节
    current_chapter_index: int = 0           # 当前章节索引
    total_chapters: int = 0                  # 总章节数
    message: str = "等待队列调度..."         # 实时状态文本
    error_detail: str | None = None          # 失败异常堆栈与说明
    enqueued_at: str                         # 入队时间戳 (ISO)
    started_at: str | None = None            # 开始执行时间戳
    completed_at: str | None = None          # 完成时间戳
    retry_count: int = 0                     # 已重试次数
```

#### `QueueStatusResponse`
```python
class QueueStatusResponse(BaseModel):
    is_paused: bool                          # 队列调度是否暂停
    concurrency: int                         # 当前最大并发执行数 (默认 1)
    total_items: int                         # 队列总项数
    running_count: int                       # 运行中项数
    pending_count: int                       # 等待中项数
    completed_count: int                     # 已完成项数
    failed_count: int                        # 失败项数
    items: list[QueueItem]                   # 队列项列表 (按排位排序)
```

---

### 2. REST API 接口规范 (API Spec)

| 方法 | 路径 | 功能描述 | 请求参数 / Body | 返回值 |
|---|---|---|---|---|
| `GET` | `/api/v1/queue` | 获取完整队列状态与所有任务项 | 无 | `QueueStatusResponse` |
| `POST` | `/api/v1/queue/items` | 单本或批量添加书籍至队列 | `EnqueueRequest`: `{ book_ids: list[str], options?: PipelineStartRequest, insert_front?: bool }` | `QueueStatusResponse` |
| `DELETE` | `/api/v1/queue/items/{item_id}` | 取消并移出队列任务 | 无 | `QueueStatusResponse` |
| `POST` | `/api/v1/queue/items/{item_id}/retry`| 重新执行失败/取消的任务 | 无 | `QueueStatusResponse` |
| `POST` | `/api/v1/queue/items/{item_id}/move` | 调整等待中任务排位 (置顶/上移/下移) | `{ direction: "up" \| "down" \| "top" }` | `QueueStatusResponse` |
| `POST` | `/api/v1/queue/reorder` | 批量重排待处理任务顺序 | `{ item_ids: list[str] }` | `QueueStatusResponse` |
| `POST` | `/api/v1/queue/pause` | 暂停队列调度器 (阻止新任务启动) | 无 | `QueueStatusResponse` |
| `POST` | `/api/v1/queue/resume` | 恢复队列调度器 | 无 | `QueueStatusResponse` |
| `POST` | `/api/v1/queue/clear` | 清理已完成/已失败/已取消的项目 | `{ scope: "completed" \| "failed" \| "all_finished" }` | `QueueStatusResponse` |
| `POST` | `/api/v1/queue/config` | 动态修改队列配置 (并发度/熔断) | `{ concurrency?: int, stop_on_error?: bool }` | `QueueStatusResponse` |

---

### 3. SSE 实时事件规范 (SSE Events)

新增广播事件通道：
- `queue_updated`: 队列状态全量变动（入队、出队、调序、状态切换）。
- `queue_item_started`: 某队列项开始执行。
- `queue_item_completed`: 某队列项翻译完结并成功导出 EPUB。
- `queue_item_failed`: 某队列项发生不可恢复异常。
- `queue_paused` / `queue_resumed`: 队列调度器暂停/恢复。

---

### 4. 前端 UI / UX 规范 (Frontend Spec)

1. **新增「任务队列」独立视图 (`frontend/src/views/QueueView.tsx`)**:
   - **顶部全局控制看板**:
     - 队列状态标识（🟢 运行中 / ⏸️ 已暂停 / ⚪ 空闲就绪）。
     - 4 大状态指标卡（运行中、排队中、已完结、失败项）。
     - 并发槽位调节器（1、2、3、4 槽位切换）。
     - 「暂停队列 / 启动队列」主开关、「清空已完成」、「一键加入全部未完结书籍」操作按钮。
   - **执行中任务卡片区 (Active Running Tasks)**:
     - 进度条（段落/章节实时推进、百分比）。
     - 实时执行状态（当前章节、主译/备用救回统计、审阅状态）。
     - 快速操作：暂停单书、终止单书、直达「翻译控制台」深入观察。
   - **等待队列区 (Pending Queue)**:
     - 排队序号徽章（#1, #2, #3...）。
     - 上移、下移、一键置顶、移除排队按钮。
     - 支持拖拽或点选快速重排。
   - **历史归档与失败恢复区 (Finished & Failed)**:
     - 成功项：绿色标签、完成时间、一键下载成品 EPUB 按钮。
     - 失败项：红色标签、错误详细诊断折叠框、一键重试 (Retry) 按钮。
2. **书架中心 (`frontend/src/views/LibraryView.tsx`) 增强**:
   - 顶部工具栏增加「⚡ 一键将全部待译书籍加入队列」按钮。
   - 每个书籍卡片增加「加入队列」快速操作入口，并动态显示队列状态（例如 `⏳ 排队中 #2` / `🚀 正在翻译`）。
3. **顶部导航栏 (`frontend/src/components/Navbar.tsx`) 增强**:
   - 增加「任务队列」选项卡，带有动态计数徽章（如 `队列 3`），并在有任务执行时显示呼吸脉冲点。

---

## 代码修改计划 (Proposed Changes)

### 核心后端模块 (Backend Queue Engine)

#### [NEW] [`translator/core/queue_manager.py`](file:///home/bread22/repos/novel-translator-pipeline/translator/core/queue_manager.py)
- 实现核心 `QueueManager` 单例：
  - 线程安全任务队列调度、信号量并发控制 (`threading.Semaphore`)、工作线程管理。
  - 入队、去重校验、批量入队、重排、优先级更新、单键重试、取消。
  - 自动状态持久化到 `output/queue/queue_state.json`。
  - 崩溃自愈：启动时恢复 pending 任务，重置中断的 running 任务。
  - 触发 SSE 广播事件与 ChapterPipeline 调用。

#### [MODIFY] [`translator/web/models.py`](file:///home/bread22/repos/novel-translator-pipeline/translator/web/models.py)
- 增加 `QueueItem`, `QueueStatusResponse`, `EnqueueRequest`, `QueueReorderRequest`, `QueueConfigUpdateRequest` 等 Pydantic 数据模型。

#### [NEW] [`translator/web/routes/queue.py`](file:///home/bread22/repos/novel-translator-pipeline/translator/web/routes/queue.py)
- 实现完整 `/api/v1/queue` 系列 REST API 端点。

#### [MODIFY] [`translator/web/app.py`](file:///home/bread22/repos/novel-translator-pipeline/translator/web/app.py)
- 注册 `queue_router` 到 `api_v1`。

#### [MODIFY] [`translator/web/events.py`](file:///home/bread22/repos/novel-translator-pipeline/translator/web/events.py)
- 增加队列相关事件名注册与广播支持。

---

### 前端模块 (Frontend Studio & UI)

#### [MODIFY] [`frontend/src/types/api.ts`](file:///home/bread22/repos/novel-translator-pipeline/frontend/src/types/api.ts)
- 添加 TypeScript 接口定义：`QueueItem`, `QueueStatusResponse`, `EnqueueRequest` 等。

#### [MODIFY] [`frontend/src/lib/api.ts`](file:///home/bread22/repos/novel-translator-pipeline/frontend/src/lib/api.ts)
- 封装队列相关的 API 调用方法 (`getQueue`, `enqueueBooks`, `removeQueueItem`, `retryQueueItem`, `moveQueueItem`, `reorderQueue`, `pauseQueue`, `resumeQueue`, `clearQueue`, `updateQueueConfig`)，并在 SSE 事件中注册 `queue_updated` 等监听。

#### [NEW] [`frontend/src/views/QueueView.tsx`](file:///home/bread22/repos/novel-translator-pipeline/frontend/src/views/QueueView.tsx)
- 实现全新的「任务队列」管理面板，包含排队列表、调序、并发设置、重试、实时进度及操作控制。

#### [MODIFY] [`frontend/src/components/Navbar.tsx`](file:///home/bread22/repos/novel-translator-pipeline/frontend/src/components/Navbar.tsx)
- 添加「任务队列」导航 Tab 及动态任务数徽章。

#### [MODIFY] [`frontend/src/views/LibraryView.tsx`](file:///home/bread22/repos/novel-translator-pipeline/frontend/src/views/LibraryView.tsx)
- 增加单书加入队列按钮、批量入队工具栏、卡片队列状态标签。

#### [MODIFY] [`frontend/src/App.tsx`](file:///home/bread22/repos/novel-translator-pipeline/frontend/src/App.tsx)
- 接入 `QueueView` 路由渲染，管理全局队列数据状态与 SSE 联动。

---

### 命令行与脚本联动 (CLI Integration)

#### [MODIFY] [`translator/pipeline/queue.py`](file:///home/bread22/repos/novel-translator-pipeline/translator/pipeline/queue.py) & [`scripts/translation_queue.py`](file:///home/bread22/repos/novel-translator-pipeline/scripts/translation_queue.py)
- 升级批量处理逻辑，可直接复用 `QueueManager` 核心或以统一的配置和状态格式执行，支持并发参数 `--concurrency` 与排队持久化。

---

## 验证与测试计划 (Verification Plan)

### 1. 自动化单元测试 (Automated Tests)
- **后端测试**：
  - 新增 `tests/test_queue_manager.py`：测试并发槽位控制、FIFO 排序、调序、取消、重试、持久化与恢复。
  - 新增 `tests/test_queue_api.py`：测试所有 `/api/v1/queue` API 路由端点返回与异常处理。
  - 运行命令：
    ```bash
    .venv/bin/pytest tests/
    ```
- **前端编译与类型检查**：
  - 运行前端打包与类型校验：
    ```bash
    npm --prefix frontend run build
    ```

### 2. 场景与端到端集成验证 (End-to-End Verification)
1. **多书入队与 FIFO 调度**：向队列加入 3 本测试书籍，验证并发数设为 1 时第 1 本执行完后自动无缝开启第 2 本。
2. **队列控制 (暂停/恢复/调序/取消)**：在排队中上移第 3 本书至首位，验证下一轮优先调度；测试暂停队列与恢复队列。
3. **容错与重试**：模拟某书执行失败，验证错误详情正确记录，点击「重试」后重新入队并能成功继续。
4. **状态持久化**：在有排队任务时重启服务，验证队列任务自动从 `queue_state.json` 恢复。

