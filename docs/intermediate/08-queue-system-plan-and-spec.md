# 翻译任务与队列调度系统 (Queue & Task Hub) 设计规范与实施计划

> [!WARNING]
> 历史队列方案：独立 `QueueManager`/`TaskManager` 已删除。v0.3 由唯一 `JobManager` 管理 `pending → running ↔ paused → completed/failed/cancelled`，重启中的活动状态转为 `recovery_pending`。

## 概述与设计目标

为 `novel-translator-pipeline` 构建生产级、高容错、可观测的**一站式任务与队列调度中心 (Queue Hub)**。
将原本分散的「书架中心」与「执行队列」深度融合成**左右双栏（Dual-Pane）协同工作台**：
- **左栏（书籍资产池 Book Pool）**：统一拖拽上传 EPUB/TXT、已注册书籍全景浏览、重置 (Reset)、删除 (Delete)、阅读详情与一键推入队列；
- **右栏（实时调度队列 Execution Queue）**：正在执行大卡片监控、待译列表**原生拖拽调序 (Drag & Drop)** 与上下移动、并发槽位控制 (1~4)、全生命周期热增删、失败单键重试与成品下载；
- **核心约束与安全隔离**：队列在运行中可随时自由增删、调序排队项，正在占用 Worker 翻译的书籍自动锁定保护。

---

## 核心架构设计 (Architecture Spec)

```text
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                 ⚡ 任务与调度中心 (Queue Hub)                                         │
├──────────────────────────────────────────────────┬─────────────────────────────────────────────────────┤
│ 📚 已注册书籍资产池 (Book Pool)                  │ 🚀 实时调度队列 (Execution Queue)                   │
│                                                  │                                                     │
│ ┌──────────────────────────────────────────────┐ │ ┌─ 队列总控制栏 ──────────────────────────────────┐ │
│ │ 📥 拖拽或点击上传日文 EPUB / TXT            │ │ │ 状态: 🟢 运行中 · 并发: [ 1 | 2 | 3 | 4 ]        │ │
│ └──────────────────────────────────────────────┘ │ │ [ ⏸️ 暂停队列 ] [ ⚡ 全部未完结入队 ] [ 🧹 清理完成]│ │
│                                                  │ └───────────────────────────────────────────────────┘ │
│ 🔍 搜索 / 过滤 (全部 / 待译 / 已完结)             │                                                     │
│                                                  │ 🟢 正在执行 (Running · 槽位锁定保护)                 │
│ ┌─ 书籍卡片 A ─────────────────────────────────┐ │ ┌─ 《测试小说 A》─────────────────────────────────┐ │
│ │ 《测试小说 A》  [68%]                        │ │ │ 正在处理第 3/10 章 · 进度 68% · 已译 1240/1820 段 │ │
│ │ 📖 详情  🔄 重置  🗑️ 删除    [ ⏳ 队列执行中 ] │ │ │ [ ⏸️ 暂停 ] [ 🛑 终止 ] [ 📡 深入作战室 (Studio) ]│ │
│ └──────────────────────────────────────────────┘ │ └───────────────────────────────────────────────────┘ │
│                                                  │                                                     │
│ ┌─ 书籍卡片 B ─────────────────────────────────┐ │ ⏳ 等待排队 (Pending · 🖐️ 支持直接拖动调序/增删)    │
│ │ 《测试小说 B》  [0%]                         │ │ ┌─ ⠿ #1 《测试小说 C》 [ ⬆️ ] [ ⬇️ ] [ 🔝 ] [ ✖ 移出 ]│ │
│ │ 📖 详情  🔄 重置  🗑️ 删除    [ ➡️ 加入队列 ] │ │ ├─ ⠿ #2 《测试小说 B》 [ ⬆️ ] [ ⬇️ ] [ 🔝 ] [ ✖ 移出 ]│ │
│ └──────────────────────────────────────────────┘ │ └───────────────────────────────────────────────────┘ │
│                                                  │                                                     │
│ ┌─ 书籍卡片 C ─────────────────────────────────┐ │ 🏁 已完成与失败归档 (Finished & Failed)             │
│ │ 《测试小说 C》  [0%]                         │ │ ┌─ ❌ 《测试小说 D》: 接口超时  [ 🔄 重新入队重试 ] │ │
│ │ 📖 详情  🔄 重置  🗑️ 删除    [ ⏳ 排队中 #1 ] │ │ ├─ ✅ 《完结小说 E》           [ 📥 下载横排EPUB ] │ │
│ └──────────────────────────────────────────────┘ │ └───────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────┴─────────────────────────────────────────────────────┘
```

---

## 核心功能规范 (Functional Spec)

### 1. 数据模型规范 (Data Models)

#### `QueueItemStatus`
```python
# 队列任务状态枚举
"pending"     # 排队等待调度 (可自由拖拽调序、上移下移、置顶、移出)
"running"     # 正在执行流水线 (占用并发槽位，受锁保护不可直接调序)
"paused"      # 队列调度已暂停
"completed"   # 翻译与导出完成
"failed"      # 执行失败 (保留错误摘要，支持单键重试)
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
    concurrency: int                         # 当前最大并发执行数 (默认 1, 可调 1~4)
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
| `DELETE` | `/api/v1/queue/items/{item_id}` | 取消并移出队列任务 (运行中任务自动终止) | 无 | `QueueStatusResponse` |
| `POST` | `/api/v1/queue/items/{item_id}/retry`| 重新执行失败/取消的任务 | 无 | `QueueStatusResponse` |
| `POST` | `/api/v1/queue/items/{item_id}/move` | 调整排位 (置顶 top / 上移 up / 下移 down) | `{ direction: "up" \| "down" \| "top" }` | `QueueStatusResponse` |
| `POST` | `/api/v1/queue/reorder` | **拖拽完成后批量更新队列顺序** | `{ item_ids: list[str] }` | `QueueStatusResponse` |
| `POST` | `/api/v1/queue/pause` | 暂停队列调度器 (阻止新任务启动，运行中任务继续) | 无 | `QueueStatusResponse` |
| `POST` | `/api/v1/queue/resume` | 恢复队列调度器 | 无 | `QueueStatusResponse` |
| `POST` | `/api/v1/queue/clear` | 清理已完成/已失败/已取消的项目 | `{ scope: "completed" \| "failed" \| "all_finished" }` | `QueueStatusResponse` |
| `POST` | `/api/v1/queue/config` | 动态修改队列配置 (并发度 1~4 / 熔断) | `{ concurrency?: int, stop_on_error?: bool }` | `QueueStatusResponse` |

---

### 3. 前端交互与拖拽调序规范 (Frontend UX & Drag-and-Drop Spec)

1. **原生轻量 HTML5 拖拽重排 (Zero Heavy Dependencies)**:
   - 待处理队列项左侧配有抓手图标 `⠿` (`GripVertical`)，鼠标悬浮呈现 `cursor-grab`，拖拽时变为 `cursor-grabbing`。
   - 拖拽事件监听：
     - `onDragStart`: 记录当前拖拽的 `item_id`，设置半透明拖拽阴影。
     - `onDragOver`: 阻止默认行为，动态计算插入位置并在目标卡片上方/下方渲染亮蓝色指示线 (`border-indigo-500`)。
     - `onDrop`: 重新排列前端 `pending` 数组，触发即时乐观更新 (Optimistic UI)，并防抖调用 `api.reorderQueue(newItemIds)` 同步持久化至后端。
     - `onDragEnd`: 重置拖拽高亮状态。
   - 同时保留 `⬆️` 上移、`⬇️` 下移、`🔝` 一键置顶辅助按钮，满足键盘与无障碍操作。

2. **双栏自适应响应式布局**:
   - 桌面大屏（`lg` 及以上）：左侧书籍资产池 (占宽 45%) 与右侧执行队列 (占宽 55%) 并列对称展示。
   - 平板/手机端：自适应折叠为上下堆叠视图。

3. **实时状态与卡片联动**:
   - 左侧卡片根据右侧队列状态动态高亮：若已在排队中，显示 `⏳ 排队中 #2` 标签并置灰入队按钮；若正在翻译，显示 `🚀 翻译中` 并提供快捷跳转。
   - 点击左侧书籍卡片的 `➡️ 加入队列`，带有微动效平滑飞入右侧待办列表。

4. **导航栏精简 (Consolidated Navbar)**:
   - Tab 1: ⚡ **任务与调度 (Queue)**（原书架中心 + 任务队列合并）
   - Tab 2: 📡 **实时作战室 (Studio)**（单书深入微观诊断）
   - Tab 3: 📖 **双语阅读器 (Reader)**（检验阅读与微调）
   - Tab 4: 🧠 **知识与记忆 (Knowledge)**（术语与长程记忆）
   - Tab 5: ⚙️ **模型与预检 (Settings)**（系统配置与 Provider 体检）

---

## 代码修改计划 (Proposed Changes)

### 核心后端模块 (Backend Queue Engine)

#### [NEW] [`translator/core/queue_manager.py`](file:///home/bread22/repos/novel-translator-pipeline/translator/core/queue_manager.py)
- 实现线程安全 `QueueManager` 单例：
  - 并发槽位管理（信号量控制 `concurrency=1..4`）；
  - 任务入队、去重校验、批量入队、重排（`reorder`）、优先级更新、单键重试、取消；
  - 队列持久化到 `output/queue/queue_state.json`；
  - 异常自愈与启动状态恢复；
  - 触发 SSE 广播事件与底层任务流转。

#### [MODIFY] [`translator/web/models.py`](file:///home/bread22/repos/novel-translator-pipeline/translator/web/models.py)
- 增加 `QueueItem`, `QueueStatusResponse`, `EnqueueRequest`, `QueueReorderRequest`, `QueueConfigUpdateRequest`。

#### [NEW] [`translator/web/routes/queue.py`](file:///home/bread22/repos/novel-translator-pipeline/translator/web/routes/queue.py)
- 实现完整 `/api/v1/queue` REST API 端点。

#### [MODIFY] [`translator/web/app.py`](file:///home/bread22/repos/novel-translator-pipeline/translator/web/app.py)
- 注册 `queue_router` 到 `api_v1`。

#### [MODIFY] [`translator/web/events.py`](file:///home/bread22/repos/novel-translator-pipeline/translator/web/events.py)
- 增加 `queue_updated`, `queue_item_started`, `queue_item_completed`, `queue_item_failed` 等事件广播。

---

### 前端模块 (Frontend Studio & UI)

#### [MODIFY] [`frontend/src/types/api.ts`](file:///home/bread22/repos/novel-translator-pipeline/frontend/src/types/api.ts)
- 添加 TypeScript 接口定义：`QueueItem`, `QueueStatusResponse`, `EnqueueRequest`, `QueueReorderRequest` 等。

#### [MODIFY] [`frontend/src/lib/api.ts`](file:///home/bread22/repos/novel-translator-pipeline/frontend/src/lib/api.ts)
- 封装队列 API 方法（`getQueue`, `enqueueBooks`, `removeQueueItem`, `retryQueueItem`, `moveQueueItem`, `reorderQueue`, `pauseQueue`, `resumeQueue`, `clearQueue`, `updateQueueConfig`），并在 SSE 监听器中注册 `queue_updated`。

#### [NEW] [`frontend/src/views/QueueHubView.tsx`](file:///home/bread22/repos/novel-translator-pipeline/frontend/src/views/QueueHubView.tsx)
- 实现全新左右双栏「任务与调度中心」：
  - 左侧：上传区、已注册书籍卡片、Reset/Delete/阅读/一键入队；
  - 右侧：队列全局控制条（启停/并发/清空）、运行中卡片、**支持拖拽重排 (Drag & Drop) 的 Pending 列表**、已完成与失败重试归档。

#### [MODIFY] [`frontend/src/components/Navbar.tsx`](file:///home/bread22/repos/novel-translator-pipeline/frontend/src/components/Navbar.tsx)
- 整合「书架」与「队列」为「任务调度」，并显示动态排队数徽章。

#### [MODIFY] [`frontend/src/App.tsx`](file:///home/bread22/repos/novel-translator-pipeline/frontend/src/App.tsx)
- 接入 `QueueHubView`，全局同步书籍资产与队列状态。

---

## 验证与测试计划 (Verification Plan)

### 1. 自动化单元测试 (Automated Tests)
- **后端测试**：
  - 新增 `tests/test_queue_manager.py`：测试并发槽位控制、FIFO 排序、拖拽重排 (`reorder`)、取消、重试、持久化与恢复。
  - 新增 `tests/test_queue_api.py`：测试所有 `/api/v1/queue` API 路由端点。
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
1. **拖拽调序测试**：在待办列表中将第 3 项拖拽到第 1 项，验证前端乐观渲染与后端持久化顺序一致。
2. **热增删测试**：在任务 1 运行期间，添加新书至队列，删除任务 3，验证任务 1 不受影响平稳执行，任务 1 完成后自动执行新的排位第 1 项。
3. **并发度切换**：将并发槽位从 1 切换至 2，验证队列自动同时调度 2 本书执行。
4. **单键重试与恢复**：模拟失败任务点击重试，验证重新插入 pending 队列并成功执行。
