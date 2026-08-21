# 两级降级容灾工作流 (Two-Level Fallback Workflow)

流水线采用**两级降级容灾机制（Two-Level Fallback）**处理大模型的内容安全审查拦截（`content_filter` / `sensitive words` / `safety policy`）、上下文超限与格式截断异常。

---

## 1. 核心容灾原理

```text
当前章节待翻译批次
       ↓
【主译 Primary Translator】(默认: Antigravity / Gemini 3.7 Flash)
       ↓
    是否成功？
    ├── 是 → 原子写入 manifest.json，标记 provider provenance
    └── 否 (content_filter / 异常)
           ↓
    当前批次是否为多段落？
    ├── 是 (多段落) → 递归二分拆解 (Binary Split) 并重试主译
    └── 否 (已拆至单段落仍受阻)
           ↓
    【一级备用 Fallback #1】(默认: OpenCode 指定模型 / 线上备用 API)
           ↓
        是否成功？
        ├── 是 → 写入 manifest.json，标记 provenance (e.g. antigravity_content_filter_fb1)
        └── 否 (仍受阻/异常)
               ↓
        【二级备用 Fallback #2】(默认: LM Studio 本地无审查模型 / Murasaki-14B)
               ↓
            是否成功？
            ├── 是 → 写入 manifest.json，标记 provenance (e.g. antigravity_content_filter_fb2)
            └── 否 → 抛出异常暂停流水线，等待人工干预
```

### 关键设计原则
1. **大窗口优先**：主译优先发送大字符窗口（`primary_batch_max_chars`，默认 4000 字符），最大化利用高智商模型的长上下文理解力；
2. **递归二分拆解**：遇到敏感词拦截时，不伪造或改写原文，而是二分拆分窗口定位具体敏感段落，未受污染的段落依然由主译高质量完成；
3. **分级递进救回**：拆至单段落后，顺序尝试一级备用与二级备用；
4. **精确溯源追踪**：每一段落的最终来源和救回原因均原子写入 `data/translation-provenance.json`，供后续审阅和质量报告统计。

---

## 2. 配置方式 (`config.toml`)

所有角色均在根目录 `config.toml` 中集中配置，角色与 Provider 完全解耦：

```toml
[roles]
primary_translator = "antigravity"
fallback_translators = ["opencode", "lmstudio"]
reviewer = "opencode"

[providers.antigravity]
type = "antigravity"
agy = "agy"
model = "gemini-3.7-flash"
effort = "low"
timeout = 600
concurrency = 1

[providers.opencode]
type = "opencode"
binary = "opencode"
model = "opencode/muse-spark-1.2-contributor-free"
timeout = 600

[providers.lmstudio]
type = "openai"
base_url = "http://127.0.0.1:1234/v1"
model = "murasaki-14b-v0.2"
api_key = "lm-studio"
context_tokens = 8192
timeout = 600

[providers.online_api]
type = "openai"
base_url = "https://api.deepseek.com/v1"
model = "deepseek-chat"
api_key = "sk-..."
context_tokens = 65536
timeout = 600
```

---

## 3. 常见后端配置指南

### 3.1 Antigravity (Gemini)
无需启动外部服务，流水线直接调用系统 `PATH` 中的 `agy` CLI，通过进程内信号量控制并发。

### 3.2 OpenCode
确保 `opencode` CLI 可用。可使用模型别名或具体 Provider 模型（如 `opencode/muse-spark-1.2-contributor-free`）。

### 3.3 LM Studio / 本地无审查模型
在 LM Studio 中加载本地模型（如 `murasaki-14b-v0.2`），启动 Local Server（默认 `http://127.0.0.1:1234/v1`）。作为终极备用层，可稳定翻译包含敏感词的特殊段落。

### 3.4 在线通用 API (DeepSeek / OpenRouter / SiliconFlow / OpenAI 等)
配置 `type = "openai"` 并填入对应的 `base_url`、`api_key` 和 `model`，即可无缝加入主译或备用链路。

---

## 4. 运行与预检

启动翻译前，建议先执行预检验证整条容灾链路：

```bash
python scripts/preflight.py
```

预检会对主译、所有备用提供商和审阅者逐一发送真实的端到端探测包，确认全部在线后方可启动流水线。
