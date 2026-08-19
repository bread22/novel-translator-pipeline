# Erotic Novel Translator Automation

当前项目负责编排两个工具，并按“一本书一个工作目录”管理状态：

1. `novel-translator`：注册 EPUB、翻译、质量检查、快照和应用修复。
2. `codex exec`：使用 GPT-5.6-Sol 低推理强度审校译文。

本项目不保存小说 EPUB、API Key 或 Codex 登录信息。原始 EPUB 放在书籍工作目录的 `input/`，由 `.gitignore` 排除；Codex CLI 使用本机登录状态。

## 两个项目的职责

### Novel Translator

Novel Translator 是翻译引擎和译文状态库，负责：

- 导入、解包和解析 EPUB；
- 按章节和段落调用 LM Studio 中的本地模型；
- 保存段落译文、manifest 和翻译进度；
- 执行 `snapshot`、`quality-report` 和 `apply-review-fixes`；
- 将已翻译内容写回并导出 EPUB。

### Erotic Novel Translator Automation

本项目是流程控制器，负责：

- 创建每本书独立的工作目录；
- 调度“翻译分片 → 审阅分片 → 更新术语表 → 进入下一分片”；
- 通过 `codex exec` 调用 GPT-5.6-Sol 审阅译文；
- 保存审阅结果、术语表、快照和质量报告；
- 按置信度筛选可自动应用的修复；
- 在最后验证并生成中文 EPUB。

两者的边界是：Novel Translator 决定“如何翻译和保存”，Automation 决定“何时翻译、何时审阅以及如何推进整本书”。

## 每本书的目录

目标目录结构如下：

```text
output/正式中文书名/
├── input/
│   └── original.epub
├── unpacked/             # EPUB 解包后的工作副本
├── data/
│   ├── manifest.json
│   ├── glossary.json      # 分片审阅过程中持续更新
│   └── progress.json
├── reviews/               # 每个分片的 Codex 输入和输出
├── snapshots/
├── reports/
└── 正式中文书名-中文.epub
```

原始 EPUB 保留不变，实际翻译使用解包后的工作副本。收尾时将译文写回 XHTML，保留 CSS、图片、OPF 和目录信息，再重新打包为中文 EPUB。

## 初始化

```bash
python3 -m venv .venv
source .venv/bin/activate
cp .env.example .env
```

自动化脚本只使用 Python 标准库，不需要额外 pip 依赖。

确保 `~/src/novel-translator/.venv` 已安装 Novel Translator 依赖，并且已配置本地 LM Studio。

## 当前自动审校流程

```bash
source .venv/bin/activate
python scripts/auto_review.py \
  --book '女銀行員-美樹-書院文庫' \
  --mode all \
  --apply
```

当前脚本执行的是“已有译文的批量审校”流程：

1. 调用 Novel Translator 生成快照和质量报告。
2. 从 Novel Translator 的 manifest 生成审校分片。
3. 每个分片调用 `codex exec -m gpt-5.6-sol`，reasoning effort 为 `low`。
4. 只自动应用 `auto_apply=true` 且置信度不低于 0.9 的修复。
5. 通过 Novel Translator 的 `apply-review-fixes` 写回译文。
6. 再次执行质量报告。

语义重写、情节判断和低置信度修改只进入报告，不会自动覆盖译文。

## 目标迭代流程

完整的书籍处理流程采用小分片，而不是一次性翻译整本书。一个分片通常包含 10～30 个自然段，以保留足够的上下文：

```text
初始化书籍目录并解包 EPUB
        ↓
读取当前 glossary.json
        ↓
翻译分片 A（本地 LM Studio 模型）
        ↓
Codex 审阅分片 A
        ↓
提取问题和术语候选，更新 glossary.json
        ↓
应用高置信度修复
        ↓
翻译分片 B（使用更新后的术语表）
        ↓
重复直到全书完成
        ↓
全书质量报告 → 合成并验证中文 EPUB
```

审阅结果同时包含译文修复和术语更新。术语表应使用结构化数据保存，例如日文词、固定中文译法、用途、置信度和备注；下一分片翻译时作为上下文传给 Novel Translator。

`scripts/auto_review.py` 保留已有译文的批量审校入口；`scripts/book_pipeline.py` 实现分片翻译、即时审阅、术语表合并与断点续跑。

迭代流水线入口：

```bash
source .venv/bin/activate
python scripts/book_pipeline.py \
  --book '女銀行員-美樹-書院文庫' \
  --name '正式中文书名' \
  --max-cycles 1000 \
  --review-window-size 4 \
  --apply
```

每个窗口先让 Novel Translator 翻译 4 个 batch，再由 GPT 一次性审阅窗口内的全部段落；随后合并术语、应用修复并同步回 Novel Translator。再次运行相同命令会读取 `progress.json`，从下一个窗口继续。

全书翻译完成后导出：

```bash
python scripts/book_pipeline.py \
  --book '女銀行員-美樹-書院文庫' \
  --name '正式中文书名' \
  --max-cycles 0 \
  --finalize
```

`--finalize` 仅在待翻译段落为零时导出单语中文 EPUB。

审阅窗口合并、`checked_ids` 和章节级一致性检查均已实施；详细设计记录在 [`docs/review-plan.md`](docs/review-plan.md)。

对已完成的书执行章节级审阅并重新导出：

```bash
python scripts/chapter_review.py \
  --book '女銀行員-美樹-書院文庫' \
  --name '正式中文书名' \
  --apply \
  --autonomous \
  --export
```

对于无需人工确认的连续处理，增加 `--autonomous`：

```bash
python scripts/book_pipeline.py \
  --book '女銀行員-美樹-書院文庫' \
  --name '正式中文书名' \
  --max-cycles 1000 \
  --apply \
  --autonomous \
  --finalize
```

该模式自动写回 Codex 置信度不低于 0.9 且有明确修复文本的项目；每个分片仍保留快照、审阅 JSON 和质量报告。
