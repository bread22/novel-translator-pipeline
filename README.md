# Erotic Novel Translator Automation

本项目是 `novel-translator` 的流程自动化层，用于把一本 EPUB 按书籍目录管理，使用本地模型翻译，再使用 Codex 对译文进行批量审阅，最后导出中文 EPUB。

## 当前架构

### Novel Translator

位于 `~/src/novel-translator`，负责：

- 导入、解包和解析 EPUB；
- 调用 LM Studio 本地模型翻译段落；
- 保存译文、manifest、术语表和翻译进度；
- 执行快照、质量报告和修复写回；
- 导出和验证 EPUB。

### Erotic Novel Translator Automation

本项目负责：

- 为每本书建立独立的 `output/正式中文书名/` 工作目录；
- 按窗口调度翻译、审阅、术语表更新和修复应用；
- 调用 `codex exec` 使用 GPT-5.6-Sol 审阅译文；
- 校验审阅结果是否覆盖全部输入段落；
- 保存快照、审阅 JSON、术语表和质量报告；
- 在翻译完成后导出并验证中文 EPUB。

两者的边界是：Novel Translator 负责翻译和译文状态，Automation 负责流程编排和审阅推进。

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

## 当前翻译流程

书籍不会一次性提交给模型。自动化按 Novel Translator 的 batch 工作，并以 **4 个 batch 为一个审阅窗口**：

```text
翻译 batch 1 ─┐
翻译 batch 2  │
翻译 batch 3  ├─→ 合并为一个窗口
翻译 batch 4 ─┘
                 ↓
        一次 Codex 审阅整个窗口
                 ↓
       合并术语更新和高置信度修复
                 ↓
        翻译下一个 4-batch 窗口
```

因此不是每个分片单独调用 GPT。每个窗口调用一次：

```text
codex exec --model gpt-5.6-sol \
  -c model_reasoning_effort="low"
```

GPT 仍会读取该窗口内的全部段落。审阅输出包含 `checked_ids`，程序要求它覆盖窗口的所有段落；漏项会重试，未知段落 ID 会使该窗口失败，不会默认为已审阅。

只有同时满足以下条件的修复才会自动写回：

- `auto_apply=true`；
- 置信度不低于 `0.9`；
- 包含明确的修订译文。

审阅提出的术语候选会合并到 `glossary.json`，并在下一个翻译窗口前同步给 Novel Translator。

## 翻译命令

从断点继续翻译并审阅：

```bash
source .venv/bin/activate
python scripts/book_pipeline.py \
  --book 'BOOK_ID' \
  --name '正式中文书名' \
  --max-cycles 1000 \
  --review-window-size 4 \
  --translate-retries 3 \
  --apply \
  --autonomous
```

`progress.json` 保存窗口进度。重复执行同一命令会从上次未完成的位置继续，而不是重新翻译已经完成的段落。

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

这个命令通常调用一次 GPT 审阅指定章节，并生成该章节的输入、输出、修复记录和质量报告；如果 `checked_ids` 不完整，程序会自动重试。单章验证默认不重新导出 EPUB；确认结果后，需要时再加入 `--export`。

`--chapter-id` 是范围控制参数。指定它只审阅对应章节；只有省略它才会遍历 manifest 中的全部章节。全书一致性审阅不是默认动作。

## 旧有译文的批量审阅

`auto_review.py` 保留用于已有译文的批量质量审阅：

```bash
python scripts/auto_review.py \
  --book 'BOOK_ID' \
  --mode all \
  --apply
```

新书的完整翻译应使用 `book_pipeline.py`；`auto_review.py` 只处理已经存在的译文，不负责从头推进翻译窗口。

## 测试

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m py_compile scripts/*.py tests/*.py
```

详细的审阅实现说明见 [`docs/review-plan.md`](docs/review-plan.md)。
