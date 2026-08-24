# Novel Translator Studio (Novel Translator Pipeline)

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![React 19](https://img.shields.io/badge/react-19-61dafb.svg)](https://react.dev/)
[![Tailwind CSS v4](https://img.shields.io/badge/tailwind-v4-38bdf8.svg)](https://tailwindcss.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Tests: 87 Passed](https://img.shields.io/badge/tests-87%20passed-brightgreen.svg)](tests/)

**Novel Translator Studio** 是日文轻小说/网络小说全自动 AI 翻译流水线与长程一致性审阅工坊。本项目基于 [`novel-translator`](https://github.com/OYcedar/novel-translator) 提供的基础解包能力，构建了完整的全自动生命周期编排、大模型并发调度、敏感词二分降级容灾、两级备用救回（Two-Level Fallback）、双盲章节一致性审阅与长程事实记忆追踪，并提供世界一流的 **「独立出版杂志 (Editorial Mag)」** 视觉交互工作台。

---

## 🎨 视觉美学：独立出版杂志 (Editorial Mag)

Studio 采用考究高雅的独立出版杂志风格，消除常规 AI 仪表盘的暗黑荧光压迫感：
- **纸张与墨水基底**：温暖精致的出版暖白瓷纸底色（`#FAF9F6`），搭配深邃墨水黑（`#1A1A1A`）与碳素石墨灰（`#4A4A4A`）。
- **典雅排版与字体**：标题全面引入 `Noto Serif SC` / `Zen Old Mincho` 经典衬线书体；正文使用开阔通透的 `Inter`；版次、Token 统计与代码使用精准克制的 `Space Grotesk` / `Fira Code` 等宽字体。
- **出版印章与宝蓝点缀**：沉稳深邃的皇家宝蓝（`#1D4ED8`）按键与焦点，辅以 `EDITION · 2026` 独立出版印章与 `#1` `#2` 印刷序号标。

---

## 🌟 五大核心模块与功能

### 1. 任务调度中心 (Queue & Asset Hub)
- **已注册书籍资产池**：书籍元数据总览（章节数、已译段落数、进度条），支持一键加入队列、全部未完结一键入队、重置翻译记忆、导出 EPUB 与彻底删除。
- **自由拖拽调度队列**：可视化拖拽抓手（`⠿`）调整执行次序，支持置顶、上移、下移与移出队列。
- **并发槽位与待命控制**：支持 1~4 本书籍动态并行槽位控制；书籍加入队列后处于待命暂停状态，支持调序完毕后手动一键「启动队列」。
- **历史记录与失败重试**：已完结书籍快速直达阅读器；异常中断书籍一键「重试」重新入队。

### 2. 翻译控制台 (Live Translation Studio)
- **模型路由拓扑大屏**：实时可视化主译（Primary）、一级备用（Fallback #1）、二级备用（Fallback #2）及双审阅者（Dual Reviewer）路由状态与救回段落统计。
- **动态 Policy 规范切换**：在控制台直接为当前翻译任务选择不同的文学提示词规范（如情色小说规范、通用小说规范、轻小说规范等）。
- **SSE 实时事件瀑布流**：全量推送章节启动、批次完成、全书进度、降级触发与审阅修复事件；单书独立持久化历史记录，切换页面不丢失，支持分类过滤与手动一键清空。

### 3. 双语阅读器 (Bilingual Reader)
- **目录索引 (TOC)**：清晰展示全书章节列表与完成状态指示。
- **段落级精细对照**：上方日文原文衬线排版，下方中文译文纸面排版；清晰标注段落 ID、翻译 Provider 与容灾救回来源。
- **人工原地校对与单段重译**：支持直接在阅读器中点击「编辑」修改译文并即刻写回工作区；支持单段点击「重译」重新调用主译模型。
- **章节质检审阅报告**：折叠面板展示本章一致性审阅报告、长程叙事摘要及所有修正缺陷清单（包含修正原因、被替换内容与新译文）。

### 4. 记忆与术语库 (Knowledge Hub)
- **动态沉淀术语表 (Glossary)**：展示由审阅模型在章节推进时自动提取并合并的专有名词、统一译名、置信度与出现章节，支持手动添加自定义术语。
- **角色长程档案 (Characters)**：提取全书人物名称、别名、角色定位与人物画像。
- **世界观设定 (World Settings)**：沉淀作品独特的技能、道具、势力与世界观解释。
- **全书质检审计报告 (Audit Reports)**：汇总各章节审阅发现的客观问题数与修复写回明细。

### 5. 模型路由与提示词规范管理 (Settings & Prompt Manager)
- **AI Provider 可视化管理器**：直观配置与增删 OpenAI 兼容接口（DeepSeek、SiliconFlow、OpenRouter、LM Studio、Ollama 等）、Antigravity CLI、OpenCode CLI 与 Codex CLI。
- **一键并发连通性测试 (Preflight Probe)**：并发探测所有已配置模型的网络可达性、API 鉴权与往返延迟（ms）。
- **提示词规范管理器 (Prompt Policy Manager)**：在线查看、新建、编辑与保存 Markdown 翻译规范及审阅规范，支持一键「设为系统全局默认规范」。

---

## 🚀 快速启动

### 方式一：启动 Web Studio 工作台 (强烈推荐)

```bash
# 1. 克隆仓库并进入目录
git clone https://github.com/bread22/novel-translator-pipeline.git
cd novel-translator-pipeline

# 2. 创建并激活 Python 虚拟环境 (Python 3.11+)
python3 -m venv .venv
source .venv/bin/activate  # Windows 用户: .venv\Scripts\Activate.ps1

# 3. 安装依赖并配置环境变量
pip install -e .
cp .env.example .env
# 可选：在 .env 中填入 DEEPSEEK_API_KEY 等

# 4. 启动 Web 工作台 (默认端口 8000)
python scripts/start_web.py --port 8000
```

打开浏览器访问 **[http://127.0.0.1:8000](http://127.0.0.1:8000)** 即可开始使用。

---

### 方式二：CLI 命令行一键批量执行

将待翻译的原始 `.epub` 文件放入 `source/` 目录：

```bash
python scripts/batch_translate.py
```

流水线会自动遍历 `source/` 下所有 EPUB，顺序完成书籍注册、章节翻译、两级降级救回、一致性审阅与横排重构，并在项目根目录的 `translated/` 产出最终成品中文 EPUB。

---

### 方式三：CLI 单本书单步执行

```bash
python scripts/book_pipeline.py \
  --book 'BOOK_ID' \
  --name '正式中文书名' \
  --apply \
  --autonomous \
  --finalize \
  --layout horizontal
```

---

## ⚙️ 核心配置说明 (`config.toml`)

项目参数在根目录 `config.toml` 中集中定义，亦可在 Web 工作台的「模型路由与设置」中可视化调整：

```toml
[paths]
output_root = "output"
translation_policy = "docs/prompts/erotic-novel-policy.md"

[roles]
primary_translator = "nemotron"                         # 主译主力模型
fallback_translators = ["gemini_lite", "deepseek"]      # 两级降级备用链
reviewer = "nemotron"                                   # 章节一致性审阅模型
secondary_reviewer = "gemini_lite"                      # 双盲副审模型
dual_review = true                                      # 开启双模型交叉审阅

[providers.nemotron]
type = "openai"
base_url = "https://integrate.api.nvidia.com/v1"
model = "nvidia/llama-3.1-nemotron-70b-instruct"
api_key = "$NVIDIA_API_KEY"
temperature = 0.3
context_tokens = 131072

[providers.deepseek]
type = "openai"
base_url = "https://api.deepseek.com/v1"
model = "deepseek-chat"
api_key = "$DEEPSEEK_API_KEY"
context_tokens = 1048576
```

---

## 🧪 自动化测试

项目具备严苛的自动化测试套件（涵盖 Universal Providers、Two-Level Fallback、Chapter Reviewer、Queue Manager、REST API 及 SSE 事件流）：

```bash
.venv/bin/pytest tests/ -v
```

```text
============================== 87 passed in 2.76s ==============================
```

---

## 📁 目录结构

```text
novel-translator-pipeline/
├── frontend/                      # React 19 + Vite + Tailwind v4 前端工程
│   ├── src/components/            # 报头、导航栏等共享组件
│   ├── src/views/                 # 任务调度、控制台、阅读器、知识库、设置等视图
│   └── dist/                      # 编译就绪的高性能生产前端静态包
├── translator/                    # 核心 Python 后端框架
│   ├── core/                      # Workspace 工作区、配置与队列管理器
│   ├── pipeline/                  # 章节翻译流水线、双审阅器与连通性预检
│   ├── providers/                 # OpenAI、AGY、OpenCode、Codex 统一适配器
│   └── web/                       # FastAPI 路由、SSE 广播器与 Web 容器
├── docs/                          # 架构设计、PRD、降级容灾规范与提示词模板
├── output/                        # 书籍生命周期工作区副本
├── source/                        # CLI 批量翻译待处理目录
├── translated/                    # 最终产出的精排中文 EPUB 交付目录
├── scripts/start_web.py           # Web Studio 一键启动入口
└── config.toml                    # 集中式分层配置文件
```

---

## 📄 开源协议 (License)

本项目基于 [MIT License](LICENSE) 协议开源。
