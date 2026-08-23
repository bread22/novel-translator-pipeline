# 项目阶段设计方案与技术规范归档 (Intermediate Plans & Specs)

本目录（`docs/intermediate/`）集中归档与维护 `novel-translator-pipeline` 历次演进的设计规范、实施计划、审计自测与交付报告，避免方案散落。

---

## 📑 归档文档索引清单

| 编号 | 文档名称 | 核心主题与说明 | 状态 |
| :--- | :--- | :--- | :---: |
| **01** | [系统整体处理架构与数据流](01-architecture-overview.md) | 统一 Provider 架构、角色解耦、可靠性与模块组织 | 已归档 |
| **02** | [两级降级容灾工作流规范](02-provider-fallback-spec.md) | 主译敏感词拦截、二分拆解、一级/二级备用救回与溯源 | 已归档 |
| **03** | [章节一致性审阅与长程事实记忆方案](03-chapter-review-plan.md) | 章节长上下文审阅、checked_ids 全覆盖、客观修复安全写回 | 已归档 |
| **04** | [Web Studio 产品需求与技术规范 (PRD & Spec)](04-web-studio-prd-specs.md) | 面向全自动流水线的 Web 工作台 PRD、技术选型与 REST/SSE 契约 | 已归档 |
| **05** | [Web Studio 四阶段工程实施计划](05-web-studio-implementation-plan.md) | 依赖收敛、后端 API、React 前端与 Docker 交付实施计划 | 已归档 |
| **06** | [全栈数据连通性自测与修复计划](06-web-studio-audit-fix-plan.md) | 前后端契约对齐、单段重译、知识库/配置自测与测试用例规划 | 已归档 |
| **07** | [全栈端到端审计与交付报告 (Walkthrough)](07-web-studio-walkthrough-report.md) | 修复缺陷复盘、5 大视图验证矩阵、CI 与 100% 测试通过报告 | 已归档 |
| **08** | [翻译任务队列系统设计规范与实施计划 (Queue Spec & Plan)](08-queue-system-plan-and-spec.md) | **【最新】** 多书队列调度、动态并发槽位、优先级排队、状态持久化与 Web/CLI 联动 | **待实施** |
| **09** | [实施进度看板与里程碑记录](09-progress-milestones.md) | 各 Component 阶段进度与里程碑看板追踪 | 持续更新 |

---

## 🔍 核心模块速查

- **队列系统最新规范**：详见 [08-queue-system-plan-and-spec.md](08-queue-system-plan-and-spec.md)
- **Web 端架构与接口契约**：详见 [04-web-studio-prd-specs.md](04-web-studio-prd-specs.md) 与 [01-architecture-overview.md](01-architecture-overview.md)
- **容灾与审阅机制**：详见 [02-provider-fallback-spec.md](02-provider-fallback-spec.md) 与 [03-chapter-review-plan.md](03-chapter-review-plan.md)

