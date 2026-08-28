# Novel Translator Studio (Novel Translator Pipeline)

[![CI](https://github.com/bread22/novel-translator-pipeline/actions/workflows/ci.yml/badge.svg)](https://github.com/bread22/novel-translator-pipeline/actions/workflows/ci.yml)
[![Version](https://img.shields.io/badge/version-0.3.1-blue.svg)](CHANGELOG.md)
[![Python 3.10–3.14](https://img.shields.io/badge/python-3.10%E2%80%933.14-blue.svg)](https://www.python.org/downloads/)
[![Node 20](https://img.shields.io/badge/node-20-339933.svg)](https://nodejs.org/)
[![React 19](https://img.shields.io/badge/react-19-61dafb.svg)](https://react.dev/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Novel Translator Studio** 是面向日文轻小说与网络小说的 AI 翻译、双审阅、术语治理和 EPUB 交付流水线。项目以 [`OYcedar/novel-translator`](https://github.com/OYcedar/novel-translator) 作为书籍注册与导出运行时，在本仓库内统一实现 Provider 路由、`JobManager` 队列、章节流水线、Glossary v3、长程记忆、FastAPI/SSE 服务和 React 工作台。

当前稳定版本：**[v0.3.1](https://github.com/bread22/novel-translator-pipeline/releases/tag/v0.3.1)**。

## 核心能力

### 统一任务与队列

- 单书 Studio 和批量队列共用唯一 `translator/core/job_manager.py`，同一本书只保留一个活动任务。
- 支持 1–4 个并发槽、排位调整、队列暂停、任务 checkpoint 暂停/继续、取消、失败重试和历史清理。
- `output/jobs/job_state.v2.json` 保存队列镜像；服务重启后将中断任务转换为 `recovery_pending` 并按队列策略恢复。
- 删除、重置、导出和 manifest/glossary 并发写入使用锁、临时文件、原子替换和回滚保护。

### 翻译与多级 Fallback

- 主译和备用角色与 Provider 类型解耦，支持 OpenAI 兼容 HTTP、Antigravity、OpenCode 和 Codex。
- 翻译窗口按自然段组织；失败时按 `max_provider_split_depth` 自适应拆分，再按 `fallback_translators` 顺序处理剩余 ID。
- `split_on_content_filter=false` 时内容过滤直接进入备用链，避免无意义地重复触发同一 Provider。
- 每段最终来源、救回层级和诊断写入 `translation-provenance.json` 与报告。

### 字符预算滚动审阅

- 章节审阅按源文字符预算切块，只在自然段边界切分。
- 每块携带可配置的前后文段落；新发现的 glossary、memory 与 chapter state 会滚动传给后续块。
- 可启用双 Reviewer 并发审阅、显式 Reviewer fallback、自适应二分重试与前文定向回查。
- `checked_ids` 必须覆盖目标段落；自动写回还需通过类别、严重度、置信度、日文假名残留和 no-op 守卫。

### Glossary Automation v3

- `output/<book>/data/glossary.json` 是术语事实源，schema version 为 `3.0`。
- 词条经过 taxonomy、形态、人名映射、证据和置信度校验后进入 `candidate`、`active`、`disputed`、`revised` 或 `retired` 生命周期。
- 翻译前可用轻量 Provider 预提取实体；翻译时只投影与当前上下文相关的 active 词条。
- 冲突、修订、证据和 provenance 保留在 v3 文档；`novel-translator-terms.json` 仅作为上游兼容投影。
- 支持 v2→v3 dry-run 迁移、历史 review delta replay 和受控回填。

### Web Studio

1. **Queue & Asset Hub**：上传 EPUB/TXT、资产统计、入队、调序、暂停、重试、导出、重置和删除。
2. **Live Studio**：主译/Fallback/双审拓扑、阶段进度、策略切换和按书事件瀑布。
3. **Bilingual Reader**：章节目录、日中对照、人工校对、单段重译和审阅报告。
4. **Knowledge Hub**：Glossary v3、人物记忆、世界观、冲突/报告与人工术语增量。
5. **Settings**：Provider、角色、密钥引用、Prompt Policy、配置备份和连通性预检。

SSE 用于通知和事件显示，REST snapshot 是最终状态校准来源。服务端事件历史保存在每本书的 `data/events.jsonl`，浏览器断线重连后会刷新 books、queue 和 task snapshot。

## 系统要求

- Python **3.10–3.14**
- Node.js **20**（仅从源码构建前端时需要）
- Git
- 可用的 [`novel-translator`](https://github.com/OYcedar/novel-translator) checkout 和 Python 环境
- 至少一个已配置的翻译 Provider；双审阅模式需要两个不同 Reviewer

## 安装

### 使用 GitHub Release 整包

整包包含已验证的 `frontend/dist`，运行时不需要 Node：

```bash
VERSION=0.3.1
curl -LO "https://github.com/bread22/novel-translator-pipeline/releases/download/v${VERSION}/novel-translator-pipeline-${VERSION}.tar.gz"
curl -LO "https://github.com/bread22/novel-translator-pipeline/releases/download/v${VERSION}/SHA256SUMS-${VERSION}.txt"
sha256sum -c SHA256SUMS-${VERSION}.txt --ignore-missing
tar -xzf novel-translator-pipeline-${VERSION}.tar.gz
cd novel-translator-pipeline-${VERSION}
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
```

Release 同时提供 zip、Python wheel/sdist 和 release-evidence 包。wheel 适合导入 Python 包；完整 Studio 应使用整包或源码 checkout。

### 从源码安装

```bash
git clone https://github.com/bread22/novel-translator-pipeline.git ~/src/novel-translator-pipeline
cd ~/src/novel-translator-pipeline
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
cd frontend
npm ci
npm run build
cd ..
```

### 准备上游运行时

```bash
git clone https://github.com/OYcedar/novel-translator.git ~/src/novel-translator
python3 -m venv ~/src/novel-translator/.venv
~/src/novel-translator/.venv/bin/pip install -e "$HOME/src/novel-translator[epub]"
export NOVEL_TRANSLATOR_ROOT="$HOME/src/novel-translator"
export NOVEL_TRANSLATOR_PYTHON="$HOME/src/novel-translator/.venv/bin/python"
```

这些变量也可写入 `.env`。未设置时，程序会尝试发现 `~/src/novel-translator` 及其 `.venv`。

## 启动 Web Studio

```bash
python scripts/start_web.py --port 8000
```

默认只监听 `127.0.0.1`。打开 <http://127.0.0.1:8000>。

### 启用管理认证

```bash
export WEB_AUTH_TOKEN='replace-with-a-random-token'
python scripts/start_web.py --host 127.0.0.1 --port 8000 --auth-token-env WEB_AUTH_TOKEN
```

首次打开可使用：

```text
http://127.0.0.1:8000/?access_token=replace-with-a-random-token#/queue
```

浏览器会把 token 保存在当前 session，并通过 Bearer header、cookie/query token 完成 REST、下载和 EventSource 认证。跨源开发环境使用 `WEB_CORS_ORIGINS` 明确列出允许来源；生产环境不要把服务直接暴露到不受信任网络。

## CLI

### 批量注册并运行

将 `.epub` 放入 `source/`，然后执行：

```bash
python scripts/batch_translate.py --layout horizontal
```

该入口会注册书籍并委托统一 `JobManager`；`--stop-on-error` 控制失败后是否暂停后续派发。

### 单本流水线

```bash
python scripts/book_pipeline.py \
  --book BOOK_ID \
  --name '正式中文书名' \
  --apply \
  --autonomous \
  --finalize \
  --layout horizontal
```

常用覆盖项包括 `--primary-translator`、`--fallback-translators`、`--reviewer`、`--secondary-reviewer`、`--review-chunk-max-chars`、`--review-context-before`、`--review-context-after` 和 `--review-backtrack`。

### 独立章节审阅

```bash
python scripts/chapter_review.py \
  --book BOOK_ID \
  --name '正式中文书名' \
  --chapter c0001 \
  --apply \
  --autonomous
```

## 配置

`config.toml` 的关键部分：

```toml
[paths]
output_root = "output"
translation_policy = "docs/prompts/france-shoin-90s-classic.md"

[roles]
primary_translator = "nemotron"
fallback_translators = ["nemotron-3-super", "gemini_lite"]
reviewer = "nemotron"
secondary_reviewer = "nemotron-3-super"
dual_review = true
fallback_reviewers = []

[pipeline]
primary_batch_max_chars = 1500
max_provider_split_depth = 2
split_on_content_filter = false
translation_max_tokens = 8192
review_chunk_min_chars = 1000
review_chunk_max_chars = 1500
review_context_before = 3
review_context_after = 3
review_backtrack_enabled = true
review_backtrack_min_confidence = 0.8
transient_http_retries = 3
transient_backoff_min_seconds = 10
transient_backoff_max_seconds = 20
transient_backoff_multiplier = 2
transient_backoff_cap_seconds = 80
timeout_retries = 1
connection_retries = 2

[queue]
source_root = "source"
stop_on_error = false
layout = "horizontal"
```

Provider 的 `api_key` 应写成 `$ENV_NAME` 引用，真实值放在 `.env`。Web Settings 保存配置时会校验 schema、生成时间戳备份，并以原子方式提交 `config.toml` 与 `.env`。

## 数据与权威源

- 上游正文：`$NOVEL_TRANSLATOR_ROOT/data/books/<book_id>/manifest.json`
- 工作区：`output/<safe-book-name>/`
  - `data/glossary.json`：Glossary v3 事实源
  - `data/novel-translator-terms.json`：上游兼容投影
  - `data/book_memory.json`：长程人物/世界观记忆
  - `data/chapter_states/`：章节叙事状态
  - `data/events.jsonl`：服务端事件历史
  - `reviews/`、`reports/`、`snapshots/`：审阅证据
- 任务状态：`output/jobs/job_state.v2.json`
- 最终交付：工作区中文 EPUB 与 `translated/` 副本

## 迁移、备份与回滚

所有迁移默认 dry-run；检查报告后再加 `--apply`：

```bash
python scripts/migrate_glossary_v3.py --output-root output
python scripts/migrate_glossary_v3.py --output-root output --apply
python scripts/replay_glossary_v3.py --output-root output
python scripts/replay_glossary_v3.py --output-root output --apply
python scripts/migrate_memory_v2.py --output-root output
python scripts/migrate_review_v2.py --output-root output
python scripts/migrate_queue_state_v2.py --output-root output
```

Glossary v3 初始化会自动迁移旧 schema 并保留备份。回退应用版本前，应备份整个 `output/`、上游 manifests、`config.toml` 和 `.env`。

配置命令：

```bash
python scripts/config.py validate
python scripts/config.py list-backups
python scripts/config.py restore --backup config.toml.bak.<timestamp>
```

## 测试与发布验证

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check translator scripts tests
.venv/bin/mypy translator
.venv/bin/python scripts/check_frontend_api_contract.py
.venv/bin/python scripts/check_version_consistency.py

cd frontend
npm run typecheck
npm run lint
npm test
npm run build
npm run test:e2e
```

真实服务 Playwright：

```bash
E2E_REAL=1 \
E2E_BASE_URL=http://127.0.0.1:8000 \
E2E_HEALTH_URL=http://127.0.0.1:8000/health \
npm run test:e2e
```

认证链路再设置 `E2E_AUTH=1` 和 `E2E_AUTH_TOKEN`，并让后端使用相同的 `WEB_AUTH_TOKEN`。

发布归档与证据：

```bash
python scripts/build_release_archive.py
python scripts/generate_release_evidence.py
```

归档器只复制 Git 跟踪的源码/文档以及经过引用校验的 `frontend/dist`，不会带入本地缓存、`__pycache__` 或未跟踪 QA 草稿。

## 目录结构

```text
novel-translator-pipeline/
├── translator/
│   ├── core/          # 配置、JobManager、工作区、导出布局和上游工具
│   ├── glossary/      # v3 taxonomy、验证、生命周期、投影、预提取和回填
│   ├── pipeline/      # 章节翻译、Fallback、审阅和 finalize
│   ├── providers/     # OpenAI/Antigravity/OpenCode/Codex 适配器
│   ├── review/        # 滚动分块、双审、合并、回查和写回守卫
│   └── web/           # FastAPI、REST、认证、安全头和 SSE
├── frontend/          # React 19、Vite、Tailwind v4 与 Playwright
├── schemas/           # 配置、metadata、review 与 glossary JSON Schema
├── scripts/           # CLI、迁移、预检、发布和证据工具
├── constraints/       # Python 3.10–3.14 依赖约束
├── docs/              # 当前规范、历史实施记录和 Prompt Policy
├── tests/             # 后端、QA、迁移、契约和流水线测试
├── output/            # 本地工作区与队列状态（忽略）
├── source/            # 批量输入（忽略）
├── translated/        # 最终 EPUB 副本（忽略）
└── release/           # 本地发布产物（忽略）
```

## 文档入口

- [架构](docs/architecture.md)
- [Provider Fallback](docs/provider-fallback.md)
- [审阅与长程记忆](docs/review-plan.md)
- [Glossary v3 运行说明](docs/glossary-automation-v3-architecture.md)
- [Changelog](CHANGELOG.md)

## License

[MIT License](LICENSE)
