# Provider 与多级 Fallback 工作流

> v0.3.1 当前行为。角色顺序来自 `config.toml`；本文示例不代表必须使用某个商业模型。

## 1. 角色与类型

角色：

- `primary_translator`
- `fallback_translators`
- `reviewer` / `secondary_reviewer`
- `fallback_reviewers`

Provider 类型：

- `openai`：OpenAI 兼容 HTTP API，包括本地 LM Studio/Ollama/vLLM。
- `antigravity`：`agy` CLI。
- `opencode`：`opencode run --format json`。
- `codex`：Codex CLI schema-constrained execution。

## 2. 翻译失败路由

```text
目标 batch
  → Primary
      ├─ 成功：只写回返回且验证通过的目标 ID
      └─ 失败/过滤/格式错误
          ├─ split_on_content_filter=true 且未到深度上限：按段落二分
          └─ 否：进入 fallback_translators
                → Fallback #1
                → Fallback #2 ...
                → 全部失败：任务 failed；保留已完成 ID 与诊断
```

每次 fallback 只接收仍未完成的 ID。`max_provider_split_depth` 限制递归；单个超大自然段不会被字符切断。来源和失败类型写入 provenance。

## 3. Reviewer Fallback

Reviewer 不会隐式借用翻译 fallback。只有 `fallback_reviewers` 显式配置的后端会参与审阅恢复。双审的 primary/secondary 各自执行并记录角色、candidate index、attempt、chunk、split path 和 timeout。

## 4. 配置示例

```toml
[roles]
primary_translator = "primary"
fallback_translators = ["fallback_1", "fallback_2"]
reviewer = "reviewer_1"
secondary_reviewer = "reviewer_2"
dual_review = true
fallback_reviewers = []

[providers.primary]
type = "openai"
base_url = "https://provider.example/v1"
model = "MODEL"
api_key = "$PRIMARY_API_KEY"
context_tokens = 131072
timeout = 600

[providers.fallback_1]
type = "opencode"
binary = "opencode"
model = "PROVIDER/MODEL"
timeout = 600

[providers.fallback_2]
type = "openai"
base_url = "http://127.0.0.1:1234/v1"
model = "LOCAL_MODEL"
api_key = "local"
context_tokens = 8192
timeout = 600

[pipeline]
primary_batch_max_chars = 1500
max_provider_split_depth = 2
split_on_content_filter = false
translation_max_tokens = 8192
health_check_timeout = 120
```

API key 使用 `$ENV_NAME` 引用，不把真实 secret 写进 `config.toml`。

## 5. 预检

Web Settings 提供 provider preflight；CLI 可直接执行：

```bash
python scripts/preflight.py
```

该命令运行真实健康探测，缺少网络、凭据、binary 或 model 时返回结构化错误。预检失败不修改 manifest、workspace 或队列状态。

## 6. 观测与恢复

- task status 显示当前 Provider、阶段、attempt 和 message；
- SSE 发布 phase、batch、fallback、reviewer 与 queue 更新；
- `translation-provenance.json` 记录段落来源；
- `provider-diagnostics.json` 和 reports 记录失败/救回统计；
- 重试从持久化 manifest/checkpoint 继续，已完成 ID 不重新发送。
