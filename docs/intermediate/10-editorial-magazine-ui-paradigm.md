# 独立出版杂志 (Editorial Mag) 视觉设计范式规范

## 1. 范式定位与设计哲学

在传统 AI 产品界面普遍陷入「暗黑模式、高饱和度紫色/青色荧光霓虹灯」的审美疲劳背景下，**Novel Translator Studio** 确立了以 **独立出版杂志 (Editorial Mag)** 为核心的视觉系统。

该系统将 **高级出版工坊的严谨排版工艺** 与 **现代工程仪表盘的精确数据流** 相融合，核心哲学包括：
- **纸张与墨水**：采用温润的暖白瓷纸色与深邃墨水黑，营造书籍装帧设计工作室的舒适沉浸感；
- **书卷排版**：标题与文学文本采用东方经典衬线字体，正文采用高清晰度现代西文无衬线字体，数据采用等宽字体；
- **克制点缀**：以皇室宝蓝（Royal Cobalt Blue）作为核心强调，辅以微倒角卡片、极细双线分界与出版印章。

---

## 2. 颜色设计系统 (Color Tokens)

| Token 变量 | 色值 | 适用场景与语义 |
| :--- | :--- | :--- |
| `--bg-main` | `#FAF9F6` | 全局视口背景、温润暖白瓷纸底色 |
| `--bg-panel` | `#F2EFE9` | 导航栏包裹槽、进度条轨道、标签栏底板 |
| `--bg-card` | `#FFFFFF` | 卡片容器、对话框、输入控件表面 |
| `--bg-card-hover` | `#FAF9F6` | 列表项、表格行悬浮交互底色 |
| `--border-color` | `#E5E0D8` | 标准 1px 极细微暖灰边框 |
| `--border-strong` | `#D4CEBF` | 强调边框、激活状态卡片外框 |
| `--text-primary` | `#1A1A1A` | 深邃出版墨水黑，用于标题、重点文本与按钮 |
| `--text-secondary` | `#4A4A4A` | 碳素石墨灰，用于正文描述、表单标签 |
| `--text-muted` | `#888888` | 暖灰辅助色，用于时间戳、空状态提示、副标题 |
| `--accent` | `#1D4ED8` | 皇家宝蓝，主操作按钮、激活胶囊、高亮关键数据 |
| `--accent-hover` | `#1E40AF` | 宝蓝悬浮深色态 |
| `--accent-subtle` | `#EFF6FF` | 浅蓝背景色，用于 Primary 状态高亮与通知块 |
| `--accent-border` | `#BFDBFE` | 浅蓝边框色 |

---

## 3. 字体与排版系统 (Typography)

```css
:root {
  --font-serif: "Noto Serif SC", "Zen Old Mincho", "Songti SC", serif;
  --font-sans: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  --font-mono: "Space Grotesk", "Fira Code", monospace;
}
```

1. **衬线字体 (`font-serif`)**：
   - **导入源**：Google Fonts `Noto Serif SC`, `Zen Old Mincho`。
   - **适用区域**：书名、章节标题、双语阅读器中文译文与日文原文、模块大标题、印章徽标。
2. **无衬线字体 (`font-sans`)**：
   - **导入源**：`Inter`。
   - **适用区域**：导航标签、按钮文字、设置项说明、系统状态提示。
3. **等宽字体 (`font-mono`)**：
   - **导入源**：`Space Grotesk`, `Fira Code`。
   - **适用区域**：章节序号（`#1`, `Chapter 1`）、Token 统计、进度百分比、网络延迟（`ms`）、实时日志瀑布流。

---

## 4. 几何与投影 (Geometry & Elevation)

- **微倒角半径**：统一为 `0px`（纯方矩形）至 `2px`（微倒角 `rounded-sm`），强调印刷物切割工艺质感，避免夸张的大圆角。
- **印刷阴影**：
  ```css
  box-shadow: 0 2px 10px -2px rgba(0, 0, 0, 0.04), 0 1px 3px -1px rgba(0, 0, 0, 0.02);
  ```
- **出版印章样式 (Editorial Stamp)**：
  ```css
  .editorial-stamp {
    border: 1px solid #1A1A1A;
    background-color: #FFFFFF;
    color: #1A1A1A;
    font-family: var(--font-serif);
    font-style: italic;
    padding: 2px 6px;
    font-size: 10px;
  }
  ```

---

## 5. 核心组件应用规范

### 5.1 顶部报头 (Navbar)
- 纯黑方块印章徽标 `NT`，搭配衬线品牌字样与 `EDITION · 2026` 独立出版印章。
- 导航栏采用内嵌在 `#F2EFE9` 槽位中的宝蓝高亮卡片。
- 右侧带有绿/红方块心跳呼吸灯与 `LIVE` / `OFFLINE` 状态标。

### 5.2 任务调度双栏 (QueueHubView)
- **左侧已注册书籍资产池**：采用藏书单卡片，展示书籍格式标签、章节数、段落翻译百分比与暖白底色。
- **右侧执行队列**：配备 `⠿` 拖拽排序手柄、`#1` `#2` 序号徽标、待命/暂停黄色提醒条与宝蓝启动按钮。

### 5.3 翻译控制台 (LiveStudioView)
- **模型拓扑卡片**：主译（Primary）使用宝蓝浅底框，一级备用（Fallback #1）使用翡翠绿浅底框，二级备用（Fallback #2）使用暖红浅底框。
- **事件流瀑布**：采用暖白纸质背景，分类使用宝蓝/翡翠绿/琥珀黄彩色卡片清晰分割。

### 5.4 双语阅读器 (ReaderView)
- 左侧目次索引（TOC）采用目录排版。
- 右侧双语段落采用「日文原文字符在上方，中文译文在浅纸色底块中排版」的经典对齐结构。
- 审阅质检报告面板采用总编审计章风格。
