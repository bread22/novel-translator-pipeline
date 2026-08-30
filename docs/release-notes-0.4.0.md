# Novel Translator Pipeline 0.4.0 Release Notes

发布日期：2026-08-29

## 重点更新

### 更可靠的语义审阅与自动写回

- Reviewer 使用字符预算选择相邻段落、术语、长期记忆、章节状态和定向回查证据。
- HTTP、限流、连接和读取超时具有明确的重试、退让、fallback 与拆分路径。
- hard-fix 写回只接受满足类别、严重度、置信度和内容守卫的客观修复，并拒绝源文复制与无变化替换。
- 标题感知、亲属称谓、专名保留、假名/韩文残留和标点段落获得专项校验。

### 独立 Knowledge Extractor

- 每个 Review Window 生成仅供本章后续窗口使用的 rolling context 和候选。
- 章节结束时统一执行 Finalization，将候选判定为 active、candidate、conflict 或 discard。
- 人物档案、关系和世界设定持久化到 Book Memory，并投影到 Knowledge Hub。

### 本地 Agent Provider 路径

- Antigravity 的 `agy`、Codex/OpenCode 的 `binary` 在 Settings 中显示并要求绝对路径。
- 新增 Provider 时路径是必填项，避免 Web 服务环境与交互式 shell 的 `PATH` 不一致。
- `config.toml` 作为每台主机的本地文件被 Git 忽略；`config.toml.example` 提供可提交模板。

## 安装与升级

新安装：

```bash
cp config.toml.example config.toml
cp .env.example .env
cd frontend && npm ci && npm run build && cd ..
python scripts/start_web.py --port 8000
```

从 0.3.1 升级时保留现有 `config.toml` 和 `.env`，并为本地 CLI Provider 将 `agy`/`binary`
改为部署主机上的绝对可执行路径。升级前备份 `output/`、上游 manifest、配置和环境文件。

## 验证与回滚

- `/health`、OpenAPI、前后端类型契约、配置 schema、前端 dist 引用和迁移 dry-run 均纳入 release evidence。
- 回滚应用版本前先停止任务并备份工作区；恢复旧版本代码后继续使用原本的本地 `config.toml` 和 `.env`。

完整变更列表见 [`CHANGELOG.md`](../CHANGELOG.md)。
