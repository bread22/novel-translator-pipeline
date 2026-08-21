# Novel Translator Pipeline

本项目是 `novel-translator` 的自动化流水线编排与审阅增强层，用于将 EPUB 小说按书籍生命周期管理，提供大模型翻译调度、敏感词/格式异常二分降级容灾、章节一致性审阅与事实记忆追踪、横排版式重构，并最终交付高质量中文 EPUB。

导出 EPUB 时默认保留原书版式；对于日文竖排书，可在流水线命令中加入 `--layout horizontal`。该选项不会修改翻译源或 `novel-translator`，而是在其完成 EPUB 导出后追加横排 CSS、更新正文 CSS 引用、将 spine 翻页方向设为 `ltr`，并将语言元数据设为 `zh-CN`，最后再执行 EPUB 校验。校验完成的成品同时会复制到项目根目录的 `translated/`。

运行参数集中在根目录 `config.toml`，包括 provider 地址、模型、LM Studio 上下文上限、AGY、OpenCode、审阅器和流水线设置。Python 代码读取该文件；环境变量仅作为显式临时覆盖。可用 `TRANSLATOR_CONFIG=/path/to/config.toml` 指定另一份参数文件。

配置分为两层：`[roles]` 只把程序角色映射到 provider；`[providers.*]` 只定义各后端本身。例如当前为 `primary_translator = "antigravity"`、`fallback_translator = "lmstudio"`、`reviewer = "opencode"`，后端参数分别位于 `[providers.antigravity]`、`[providers.lmstudio]`、`[providers.opencode]` 和 `[providers.codex]`。

## 当前架构

### Novel Translator (底座工具)

位于 `~/src/novel-translator`，负责：

- 导入、解包和解析 EPUB；
- 保存翻译 manifest，并提供 EPUB/状态/快照/导出能力；
- 保存译文、manifest、术语表和翻译进度；
- 执行快照、质量报告和修复写回；
- 导出和验证 EPUB。

### Novel Translator Pipeline (本项目)

本项目负责：

- 为每本书建立独立的 `output/正式中文书名/` 工作目录；
- 调度主译（Gemini / OpenCode）并在遇到敏感词或报错时自动二分拆解并降级（LM Studio / Murasaki）；
- 推进章节流水线：整章翻译 -> 章节一致性审阅 -> 记忆与术语合并 -> 自动写回修复；
- 校验审阅结果是否覆盖全部输入段落；
- 导出时自动注入横排阅读样式并校正翻页方向；
- 提供批量全自动翻译队列（Queue）。

两者的边界是：Novel Translator 负责 manifest、快照、质量、修复和 EPUB 交付；Automation 负责 provider 调用、流程编排和审阅推进。

## 目录结构

```text
output/正式中文书名/
├── input/                         # 原始 EPUB
├── unpacked/                      # EPUB 解包后的工作副本
├── data/
│   ├── manifest.json
│   ├── glossary.json               # 持续更新的术语表
│   ├── novel-translator-terms.json
│   └── progress.json
├── reviews/                       # Codex 输入、输出和修复记录
├── snapshots/                     # 翻译和审阅前快照
├── reports/                       # 质量报告和一致性报告
└── 正式中文书名-中文.epub
```

书籍输出、EPUB 和运行时报告均被 `.gitignore` 排除；项目 Git 只保存代码、配置、测试和文档。

## 环境

```bash
python3 -m venv .venv
source .venv/bin/activate
cp .env.example .env
```

本项目只使用 Python 标准库。另需完成以下配置：

1. `~/src/novel-translator/.venv` 已安装 Novel Translator 依赖；
2. LM Studio 已加载本地翻译模型并监听配置的 API 地址；
3. Codex CLI 已登录，并能调用 `gpt-5.6-sol`。

如果使用 OpenCode 作为 reviewer 或 translator，确保 `opencode` 在 `PATH` 中可用，并已在本机 OpenCode 配置中选定 provider/model。也可以用环境变量指定模型：

```bash
export REVIEWER_BACKEND=opencode
export OPENCODE_REVIEWER_MODEL='provider/model'
export TRANSLATION_PRIMARY_PROVIDER=opencode
export OPENCODE_TRANSLATOR_MODEL='provider/model'
```

不设置 `OPENCODE_*_MODEL` 时，OpenCode 使用自己的默认模型。命令行参数 `--reviewer-backend`、`--primary-provider` 和 `--fallback-provider` 会覆盖对应环境变量。

## Antigravity 翻译后端

项目提供了一个 OpenAI-compatible 中间层，把 Novel Translator 的翻译请求转发给 Antigravity CLI：

```text
Novel Translator
        ↓ OpenAI-compatible HTTP
scripts/antigravity_backend.py
        ↓ agy CLI
Gemini 3.7 Flash
```

启动桥接服务：

```bash
source .venv/bin/activate
python scripts/antigravity_backend.py \
  --model gemini-3.7-flash \
  --port 1235
```

然后将 `~/src/novel-translator/setting.toml` 的 `[llm]` 临时切换为：

```toml
[llm]
base_url = "http://127.0.0.1:1235/v1"
api_key = "antigravity"
model = "gemini-3.7-flash"
timeout = 600
```

切回 LM Studio 时恢复：

```toml
[llm]
base_url = "http://127.0.0.1:1234/v1"
api_key = "lm-studio"
model = "murasaki-14b-v0.2"
```

这里的 Gemini 是通过 `agy` 调用的远程/CLI 后端，不是 LM Studio 中的本地模型。桥接层默认并发为 1，以避免同时启动大量 CLI 进程；可通过 `--concurrency` 调整。

### Gemini blocked fallback

章节流程默认使用 Gemini 大窗口翻译；明确的 provider content-filter 会触发窗口二分，最小失败片段交给配置的 fallback。默认 fallback 是 LM Studio/Murasaki，也可通过 `--fallback-provider opencode` 切换。设置 `MURASAKI_BASE_URL` 和 `MURASAKI_MODEL` 可覆盖 LM Studio fallback 地址与模型。每段来源保存到 `translation-provenance.json`，provider 诊断保存到 `provider-diagnostics.json`，详见 `docs/provider-fallback.md`。

### OpenCode reviewer/translator 后端

OpenCode 集成使用本地 CLI，不改动 `novel-translator`：

```text
book_pipeline
   ├── reviewer:    opencode run --format json
   └── translator:  opencode run --format json
```

启动前健康检查会实际执行一次 OpenCode JSON 请求；翻译请求也会严格校验 `items`、段落 ID 和输出完整性。推荐先单独验证：

```bash
opencode run --format json 'Return exactly {"ok":true}.'
```

选择 OpenCode 同时承担主译和审阅：

```bash
python scripts/book_pipeline.py \
  --book 'BOOK_ID' \
  --name '正式中文书名' \
  --reviewer-backend opencode \
  --primary-provider opencode \
  --fallback-provider opencode
```

## 当前翻译流程

默认流程以章节为单位。翻译后端仍可内部按 batch 工作，但编排层会持续推进到当前章节完成，再调用一次章节审阅：

```text
翻译当前章节的全部 batch
          ↓
     一次整章审阅
          ↓
合并 glossary / book_memory / chapter_state
          ↓
应用客观高置信度修复
          ↓
       进入下一章
```

因此不是每个分片单独调用审阅模型。每章默认调用一次：

```text
codex exec --model gpt-5.6-sol \
  -c model_reasoning_effort="low"
```

审阅模型读取整章段落、Glossary、Book Memory 和上一章状态。输出包含 `checked_ids`，程序要求它精确覆盖整章所有段落；漏项、未知 ID 或重复 ID 会重试，仍失败则暂停。

只有同时满足以下条件的客观修复才会自动写回：

- `auto_apply=true`；
- 置信度不低于 `0.9`；
- `severity` 为 `major` 或 `critical`；
- `category` 属于主客体、指代、漏译、增译、误译、术语或事实冲突；
- 包含完整段落修订译文。

审阅提出的稳定术语会合并到 `glossary.json`；会影响后续章节的信息写入 `data/book_memory.json`，章节摘要写入 `data/chapter_states/`。

## 翻译命令

从断点继续翻译并审阅：

```bash
source .venv/bin/activate
python scripts/book_pipeline.py \
  --book 'BOOK_ID' \
  --name '正式中文书名' \
  --max-cycles 1000 \
  --review-mode chapter \
  --max-chapter-batches 1000 \
  --translate-retries 3 \
  --apply \
  --autonomous
```

`progress.json` 保存章节进度。重复执行同一命令会从上次未完成的章节继续，而不是重新翻译已经完成的段落。

如需回滚到旧的 4-batch 窗口流程：

```bash
python scripts/book_pipeline.py \
  --book 'BOOK_ID' \
  --name '正式中文书名' \
  --review-mode window \
  --review-window-size 4
```

本地翻译每个 batch 后都会检查 `translate` 返回值和 `failed-batches`。发现失败批次时自动调用 `retry-failed`，最多尝试 3 次；仍然失败则写入 `reports/translation-failure-*.json`，将进度标记为 `paused` 并停止。翻译没有产生新段落但仍有 pending 段落时也会暂停，不会误判为完成。

翻译完成后导出中文 EPUB：

```bash
python scripts/book_pipeline.py \
  --book 'BOOK_ID' \
  --name '正式中文书名' \
  --max-cycles 0 \
  --finalize
```

`--finalize` 会再次确认 pending 段落为零且没有失败批次，之后才导出单语中文 EPUB，并执行导出验证。

## 单章一致性审阅

章节级一致性审阅用于检查人物称谓、术语、时间顺序、叙事视角、指代和章节内矛盾。它是独立的审阅命令，**当前使用时必须先指定单章**：

```bash
python scripts/chapter_review.py \
  --book 'BOOK_ID' \
  --name '正式中文书名' \
  --chapter-id c0001 \
  --apply \
  --autonomous
```

这个命令调用一次章节审阅，并生成该章节的输入、输出、修复记录、状态、快照和质量报告；如果 `checked_ids` 不完整，程序会自动重试。单章验证默认不重新导出 EPUB；确认结果后，需要时再加入 `--export`。

`--chapter-id` 是范围控制参数。指定它只审阅对应章节；只有省略它才会遍历 manifest 中的全部章节。全书一致性审阅不是默认动作。

翻译完成后可额外检查所有章节状态、Glossary 和 Book Memory：

```bash
python scripts/chapter_review.py \
  --book 'BOOK_ID' \
  --name '正式中文书名' \
  --all \
  --global-consistency
```

## 测试

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile scripts/*.py tests/*.py
```

详细的审阅实现说明见 [`docs/review-plan.md`](docs/review-plan.md)。
