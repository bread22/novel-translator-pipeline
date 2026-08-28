## 变更说明 (Summary)
<!-- 请简要描述本次 PR 解决的问题或新增的特性 -->

## 关联 Issue / 讨论 (Related Issues)
<!-- 例如: Fixes #123 或 Closes #456 -->

## 变更类型 (Type of Change)
- [ ] 🐛 缺陷修复 (Bug fix)
- [ ] ✨ 新特性 (New feature)
- [ ] 📝 文档更新 (Documentation update)
- [ ] ♻️ 代码重构 (Refactoring)
- [ ] 🧪 测试用例 (Tests)

## 验证与测试 (Verification)
- [ ] 后端测试通过：`.venv/bin/python -m pytest -q`
- [ ] Python 质量门禁通过：`.venv/bin/ruff check translator scripts tests` 与 `.venv/bin/mypy translator`
- [ ] API/版本契约通过：`.venv/bin/python scripts/check_frontend_api_contract.py` 与 `.venv/bin/python scripts/check_version_consistency.py`
- [ ] 前端测试与构建通过：`cd frontend && npm run typecheck && npm run lint && npm test && npm run build`
- [ ] 涉及用户流程时已运行：`cd frontend && npm run test:e2e`
- [ ] 文档、迁移、配置或发布行为已同步更新 README/CHANGELOG/对应规范
