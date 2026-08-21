# Novel Translator Pipeline

本项目是 `novel-translator` 的自动化流水线编排与审阅增强层，用于将 EPUB 小说按书籍生命周期管理，提供大模型翻译调度、敏感词/格式异常二分降级容灾、两级备用救回（Two-Level Fallback）、章节一致性审阅与长程记忆事实追踪、横排版式重构，并最终交付高质量中文 EPUB。

导出 EPUB 时默认保留原书版式；对于日文竖排书，可在流水线命令中加入 `--layout horizontal`。该选项不会修改翻译源或 `novel-translator`，而是在其完成 EPUB 导出后追加横排 CSS、更新正文 CSS 引用、将 spine 翻页方向设为 `ltr`，并将语言元数据设为 `zh-CN`，最后再执行 EPUB 校验。校验完成的成品同时会复制到项目根目录的 `translated/`。

---

## 核心特性

- **通用 Provider 架构**：通过统一的 `BaseProvider` 适配器解耦角色与模型，支持 `antigravity` (Gemini)、`opencode`、`codex`、`openai` 兼容协议（本地 LM Studio / Ollama / 在线 DeepSeek、OpenRouter、SiliconFlow 等）；
- **两级降级容灾回路 (Two-Level Fallback)**：主译遇敏感词安全审查拦截（`content_filter`）时，自动触发二分递归拆解；单段落仍受阻时，顺序降级至**一级备用**（如 OpenCode 指定模型），若仍受阻则无缝降级至**二级备用**（如 LM Studio 本地无审查模型），全程自动记录 Provenance 溯源；
- **章节级长上下文一致性审阅**：每章全量翻译完成后，执行一次通用审阅，校验 100% `checked_ids` 覆盖率，自动提取并合并 Glossary 增量、Book Memory 事实记忆和 Chapter State 状态演进；
- **高置信度客观缺陷自动写回**：仅对 `confidence >= 0.9`、`major/critical` 的客观错误（误译、漏译、主客体错位、事实冲突等）自动安全替换；
- **分层配置管理**：所有运行参数集中在 `config.toml`，支持 Schema 校验与环境变量覆盖。

---

## 架构概览

```text
原始 EPUB
   ↓ 保留原文件，建立 output/中文书名/ 工作副本
Novel Translator 导入/解包
   ↓
manifest.json + 待翻译段落
   ↓
【章节翻译流水线 (Chapter Pipeline)】
   ├── 主译 Primary (e.g. Antigravity / Gemini)
   │     ↓ 遇到敏感词审查 / 异常
   │   递归二分拆解 (Binary Split)
   │     ↓
   ├── 一级备用 Fallback #1 (e.g. OpenCode)
   │     ↓ 仍受阻
   └── 二级备用 Fallback #2 (e.g. LM Studio 无审查模型)
   ↓
【章节一致性审阅 (Chapter Reviewer)】(e.g. OpenCode / Codex / AGY)
   ├── 校验 checked_ids 覆盖
   ├── 合并 glossary.json / book_memory.json / chapter_states
   └── 自动写回高置信度客观修复
   ↓
进入下一章直至全书翻译完成
   ↓
最终质量报告 (work-report.yaml) 与中文 EPUB 导出
```

---

## 目录结构

```text
output/正式中文书名/
├── input/                         # 原始 EPUB
├── unpacked/                      # EPUB 解包后的工作副本
├── data/
│   ├── manifest.json              # 核心段落与译文元数据
│   ├── glossary.json              # 动态沉淀的术语表
│   ├── book_memory.json           # 全书长程事实与角色记忆
│   ├── chapter_states/            # 章节摘要与状态记录
│   ├── translation-provenance.json# 段落翻译来源与救回原因溯源
│   ├── provider-diagnostics.json  # Provider 诊断与拦截日志
│   └── progress.json              # 流水线推进断点状态
├── reviews/                       # 审阅输入、输出与修复记录
├── snapshots/                     # 翻译和审阅前快照
├── reports/                       # 质量报告与工作报告 (work-report.yaml)
└── 正式中文书名-中文.epub           # 最终交付的中文电子书
```

---

## 配置设置指南 (`config.toml`)

项目参数在根目录 `config.toml` 中集中定义，采用**分层解耦**设计：

```toml
# ==========================================
# 1. 基础路径配置
# ==========================================
[paths]
output_root = "output"                                   # 书籍处理工作区目录
translation_policy = "docs/prompts/translation-policy.md"# 翻译规范与风格策略文档

# ==========================================
# 2. 角色绑定 (Roles)
# 将流水线工作角色映射到具体的 Provider 名称
# ==========================================
[roles]
primary_translator = "antigravity"                      # 主译提供商 (长上下文/高质量)
fallback_translators = ["opencode", "lmstudio"]         # 多级备用链 (顺序降级救回)
reviewer = "opencode"                                   # 章节一致性审阅者

# ==========================================
# 3. 提供商具体参数 (Providers)
# ==========================================

# --- Antigravity (Gemini CLI) ---
[providers.antigravity]
type = "antigravity"
agy = "agy"                                             # agy 可执行文件路径
model = "gemini-3.7-flash"                              # 模型标识
effort = "low"                                          # 思考深度: low / medium / high
concurrency = 1                                         # 进程内并发控制槽位
timeout = 600

# --- OpenCode (CLI) ---
[providers.opencode]
type = "opencode"
binary = "opencode"                                     # opencode 可执行文件路径
model = "opencode/muse-spark-1.2-contributor-free"      # 默认模型
agent = ""                                              # 指定 agent 别名 (可选)
timeout = 600

# --- 本地 LM Studio (OpenAI 协议) ---
[providers.lmstudio]
type = "openai"
base_url = "http://127.0.0.1:1234/v1"
model = "murasaki-14b-v0.2"                             # 本地无审查翻译模型
api_key = "lm-studio"
context_tokens = 8192                                   # 本地加载时分配的上下文上限
timeout = 600

# --- Codex (CLI) ---
[providers.codex]
type = "codex"
binary = "codex"
model = "gpt-5.6"
reasoning_effort = "low"
timeout = 600

# --- 通用在线 API (DeepSeek / OpenRouter / SiliconFlow / OpenAI 等) ---
[providers.online_api]
type = "openai"
base_url = "https://api.deepseek.com/v1"                # API Base URL
model = "deepseek-chat"                                 # 模型名称
api_key = "sk-..."                                      # API Key (或设置 DEEPSEEK_API_KEY)
context_tokens = 65536                                  # 上下文窗口
timeout = 600

# ==========================================
# 4. 流水线策略参数 (Pipeline)
# ==========================================
[pipeline]
max_cycles = 1000                                       # 单次运行最大处理章节数
max_chapter_batches = 1000                              # 单章推进最大批次数
primary_batch_max_chars = 4000                          # 主译大窗口原文字符上限
max_provider_split_depth = 8                            # 遇到审查时最大二分递归深度
translation_max_tokens = 8192                           # 单次翻译最大输出 token
health_check_timeout = 60                               # 启动预检超时秒数
layout = "preserve"                                     # 默认导出版式: preserve (保持) 或 horizontal (横排)

# ==========================================
# 5. 批量翻译队列 (Queue)
# ==========================================
[queue]
source_root = "source"                                  # 待翻译原始 EPUB 投放目录
max_cycles = 1000
apply = true
autonomous = true
finalize = true
stop_on_error = true
```

### 常见配置场景

1. **接入 DeepSeek 作为主译**：
   - 在 `[providers.online_api]` 填入 `api_key = "sk-..."`；
   - 将 `[roles]` 的 `primary_translator` 设为 `"online_api"` 即可。
2. **将 Codex 作为审阅者**：
   - 将 `[roles]` 的 `reviewer` 设为 `"codex"`。
3. **临时指定配置文件**：
   - 通过环境变量 `TRANSLATOR_CONFIG=/path/to/my-config.toml` 指定独立配置。

---

## 快速上手

### 1. 安装与环境准备

```bash
python3 -m venv .venv
source .venv/bin/activate
cp .env.example .env
```

本项目核心代码仅依赖 Python 标准库。请根据使用的 Provider 确保环境可用：
- **Antigravity**：系统 `PATH` 中包含 `agy` CLI；
- **OpenCode**：系统 `PATH` 中包含 `opencode` CLI；
- **LM Studio**：本地启动 HTTP 服务（默认 `http://127.0.0.1:1234/v1`）；
- **在线 API**：在 `config.toml` 中配置 `api_key` 与 `base_url`。

### 2. 运行连通性预检

在开始翻译前，运行预检工具测试所有配置的 Provider 与 Reviewer：

```bash
python scripts/preflight.py
```

### 3. 启动章节翻译流水线

从断点继续翻译并自动推进整本书：

```bash
python scripts/book_pipeline.py \
  --book 'BOOK_ID' \
  --name '正式中文书名' \
  --apply \
  --autonomous \
  --finalize
```

参数说明：
- `--apply`：自动将高置信度客观审阅修复写回 `manifest.json`；
- `--autonomous`：全自动模式；
- `--finalize`：全部章节完成后，自动校验并导出最终中文 EPUB；
- `--layout horizontal`：日文竖排小说自动重构为横排版式；
- `--primary-translator` / `--fallback-translators` / `--reviewer`：可选临时覆盖 `config.toml` 中的角色分配。

### 4. 独立章节审阅与全书一致性检查

可随时单独对某一章节或整本书执行审阅：

```bash
# 审阅指定单章
python scripts/chapter_review.py \
  --book 'BOOK_ID' \
  --name '正式中文书名' \
  --chapter c0001 \
  --apply \
  --autonomous

# 全书跨章节一致性检查
python scripts/chapter_review.py \
  --book 'BOOK_ID' \
  --name '正式中文书名' \
  --global-consistency
```

---

## 运行测试

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile translator/**/*.py scripts/*.py tests/*.py
```

详细架构与工作流设计请参阅：
- [系统处理架构 (`docs/architecture.md`)](docs/architecture.md)
- [两级降级容灾工作流 (`docs/provider-fallback.md`)](docs/provider-fallback.md)
- [章节一致性审阅与长程记忆 (`docs/review-plan.md`)](docs/review-plan.md)
