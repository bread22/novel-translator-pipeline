# Erotic Novel Translator Automation

当前项目负责编排两个工具：

1. `novel-translator`：注册 EPUB、翻译、质量检查、快照和应用修复。
2. `codex exec`：使用 GPT-5.6-Sol 低推理强度审校译文。

本项目不保存小说 EPUB、API Key 或 Codex 登录信息。EPUB 放在 `source/`，由 `.gitignore` 排除；Codex CLI 使用本机登录状态。

## 初始化

```bash
python3 -m venv .venv
source .venv/bin/activate
cp .env.example .env
```

自动化脚本只使用 Python 标准库，不需要额外 pip 依赖。

确保 `~/src/novel-translator/.venv` 已安装 Novel Translator 依赖，并且已配置本地 LM Studio。

## 自动审校

```bash
source .venv/bin/activate
python scripts/auto_review.py \
  --book '女銀行員-美樹-書院文庫' \
  --mode all \
  --apply
```

流程：

1. 调用 Novel Translator 生成快照和质量报告。
2. 从 Novel Translator 的 manifest 生成审校分片。
3. 每个分片调用 `codex exec -m gpt-5.6-sol`，reasoning effort 为 `low`。
4. 只自动应用 `auto_apply=true` 且置信度不低于 0.9 的修复。
5. 通过 Novel Translator 的 `apply-review-fixes` 写回译文。
6. 再次执行质量报告。

语义重写、情节判断和低置信度修改只进入报告，不会自动覆盖译文。
