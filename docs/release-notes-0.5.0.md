# Novel Translator Pipeline 0.5.0 Release Notes

发布日期：2026-09-02

## 重点更新

### Knowledge 与 Glossary 生命周期

- Knowledge Extractor 现在按窗口产生候选、证据和滚动上下文，并在章节结束时统一判定 active、candidate、conflict 或 discard。
- Glossary v3 增加候选晋级、冲突/修订、跨章节证据范围、分类别名归一化和 OpenCC 人名校验。
- 迁移与回填工具保留 dry-run、报告和可回滚路径，发布证据会检查现有工作区状态。

### EPUB 与运行时集成

- 集成 vendored Novel Translator Python API，移除不再使用的 CLI 模块和旧源输入。
- EPUB 处理覆盖完整 spine 文档，支持装饰型跨文件章节、通用 front matter、章节拆分和导航修复。
- Reader 与导出流程现在能够保留文档角色和跨文件结构。

### Provider、Review 与翻译稳定性

- 增加 Knowledge extractor fallback、审阅备用端配置和 fallback SSE 事件。
- Provider 重试、超时退让、OpenCode 自定义 provider 名称和本地可执行路径校验保持可观测。
- 翻译流水线允许带上下文的源字符保留，并在残留假名出现时执行确定性的修复与 fallback 路由。

## 安装与升级

新安装：

```bash
cp config.toml.example config.toml
cp .env.example .env
cd frontend && npm ci && npm run build && cd ..
python scripts/start_web.py --port 8000
```

从 0.4.0 升级时保留现有 `config.toml`、`.env` 和 `output/`，升级前备份工作区；如使用本地 Provider，确认配置中的可执行路径仍为部署主机上的绝对路径。

## 验证与回滚

- `/health`、OpenAPI、前后端类型契约、配置 schema、前端 dist 引用和迁移 dry-run 均纳入 release evidence。
- 回滚应用版本前停止任务并备份工作区；恢复旧版本代码后继续使用原本的本地 `config.toml` 和 `.env`。

完整变更列表见 [`CHANGELOG.md`](../CHANGELOG.md)。
