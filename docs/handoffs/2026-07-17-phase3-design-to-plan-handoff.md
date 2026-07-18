# Agent Failure Clinic Phase 3 设计转实施计划交接

更新日期：2026-07-17

## 1. 当前结论

Phase 3 已完成 brainstorming、方案比较、五段设计确认、书面设计和 spec 自审。下一步是使用 `writing-plans` 编写 bite-sized TDD 实施计划；尚未开始任何 Phase 3 实现代码。

Phase 3 主题：

> 独立 Evidence/Semantic Verifier、最多一次补证、可恢复人工 `confirm / correct / reject` 的 API + CLI 工作流。

## 2. Git 与远端状态

- 当前本地分支：`docs/phase3-verification-review-design`
- 当前提交：`894cc7e docs: design Phase 3 verification review workflow`
- 当前 worktree：`D:\self agent\.worktrees\phase2-diagnosis-mvp`
- worktree 路径名称沿用 Phase 2，但当前实际分支是 Phase 3 design branch。
- Phase 3 design branch 目前只存在本地，尚未推送。
- Phase 2 远端分支：`origin/feature/phase2-diagnosis-mvp`
- Phase 2 远端提交：`4df0ccb`
- Phase 2 PR：<https://github.com/naturaljam/Agent_Failure_Clinic/pull/1>
- PR 当前状态：open、未合并，head 为 `feature/phase2-diagnosis-mvp`，base 为 `main`。
- 远端 `main` 当前只有原始 Initial commit；本地 Phase 2 分支已合入该提交以建立共同历史。

不要在未经用户明确要求时合并 PR、覆盖远端 main、删除 worktree 或清理 feature branch。

## 3. 必读文档

按以下顺序读取：

1. `docs/handoffs/2026-07-17-phase3-design-to-plan-handoff.md`
2. `docs/superpowers/specs/2026-07-17-phase3-verification-review-workflow-design.md`
3. `docs/superpowers/specs/2026-07-15-agent-failure-clinic-design.md`
4. `docs/evaluation/phase2-diagnosis-evaluation.md`
5. `docs/handoffs/2026-07-17-phase2-design-to-plan-handoff.md`

Phase 3 的直接设计权威是第 2 项。总体设计只用于确认长期边界，不得用它把 PostgreSQL、队列、Repair 或 Release Gate 提前塞进 Phase 3。

## 4. 已确认的 Phase 3 决策

1. 采用 API + CLI，不做前端。
2. 采用分层状态机纵向闭环，不做单体 graph 或事件驱动队列。
3. `EvidenceVerifier` 是纯确定性组件。
4. `SemanticVerifier` 使用独立 DeepSeek prompt/context，不读取 Diagnosis Agent prompt、原始回复、隐藏推理或 gold label。
5. 两个 Verifier 独立运行后由确定性策略合并 verdict。
6. Verifier 只能返回结构化 finding 和 `EvidenceGap`，不能直接修改 DiagnosisReport。
7. 原 Diagnosis Agent 最多修订一次；RuleDiagnoser 不做生成式 revision。
8. 第二轮验证结束后无条件进入人工复核。
9. 人工动作是 `confirm / correct / reject`。
10. `correct` 只能提交 selector，服务端重新生成 observed value、hash、evidence ID 和 provenance。
11. 使用 Repository 接口 + SQLite；PostgreSQL 和正式 migration 留到后续阶段。
12. 创建 review 时持久化脱敏 `DiagnosticTraceView` 不可变快照，而不是只存 trace fingerprint，也不保存完整原始 TraceIR。
13. 默认 `rules + deterministic` 完全离线；DeepSeek 必须显式选择，CLI 还必须带 `--allow-live-api`。
14. Phase 3 不包含 RegressionCase、Repair Agent、Release Gate、Redis/Celery、PostgreSQL、认证或自动 PR。

## 5. 设计自审修正

spec 自审时修正了两处内部一致性问题：

1. 恢复性：当前 TraceRepository 是内存实现，所以 SQLite 必须保存可恢复的 DiagnosticTraceView canonical JSON、trace/run 绑定和 fingerprint，不能只保存 trace ID。
2. Fixture 数量：最终为 20 条 valid rule reports + 16 条 mutated reports，共 36 条。16 条变异由 6 条 unsupported forced classification 加上其余五种缺陷各 2 条组成。

硬验收因此是 `20/20 valid pass`、`16/16 defect detected` 和 `6/6 unsupported caught`。

## 6. Phase 2 已完成证据

不要重复 Phase 1/2 工作。当前基线：

- 153 tests passed；
- Ruff passed；
- mypy 对 42 个源码文件通过；
- 规则评测双次 byte-exact，SHA-256：
  `2D533196FC6E56AF505B8A62DE89DFCD8767CC90E65C752CEB5F1F79F6207A9C`；
- Docker/Compose/API/Phoenix 冒烟通过；
- API 容器 UID/GID 为 `10001:10001`；
- Phase 2 DeepSeek 完整 20 条实验已完成并记录；
- live artifact 和 `.env` 均在 Git 忽略范围内，不得提交。

Phase 2 DeepSeek 关键结果：

- supported accuracy `0.857`；
- critical Top-1 `0.800`；
- selector validity `1.000`；
- gold evidence hit `1.000`；
- unsupported abstain `0.000`；
- operational error `0.000`。

Phase 3 的首要价值就是检测这些 scope/conflict 问题，而不是再次调 prompt 追求表面准确率。

## 7. 下一步必须执行的工作

### 7.1 使用 writing-plans

读取完整 Phase 3 spec 后，使用 `writing-plans` 创建：

`docs/superpowers/plans/2026-07-17-phase3-verification-review-workflow.md`

计划必须：

- 使用 bite-sized TDD steps；
- 每个 task 明确 create/modify/test 文件；
- 每个行为先写 RED test，再写最小实现；
- 包含精确命令和预期结果；
- 包含小提交边界；
- 保持默认测试不触网；
- 把 Docker persistence、API/CLI smoke、双次确定性 eval 和 live smoke 放在末尾；
- 不包含前端、PostgreSQL、队列、RegressionCase、Repair 或 Release Gate。

### 7.2 建议的计划任务顺序

1. Review domain models 与状态转换；
2. EvidenceVerifier 基础校验；
3. scope/invariant conflict 与 evidence budget；
4. 36 条 review fixture、manifest 和 deterministic evaluator；
5. Repository 协议、SQLite schema 与事务；
6. ReviewService、幂等与人工决定；
7. LangGraph workflow、lease 和 resume；
8. `RevisionCapableDiagnoser` 与一次 revision；
9. SemanticVerifier 严格 schema 与独立 prompt；
10. Diagnosis Review API；
11. `afc-review` HTTP CLI；
12. Docker volume、完整门禁、文档和受控 live experiment。

writing-plans 可以根据依赖重新拆分，但不得改变已确认架构。

## 8. 新对话的执行门禁

- 不要重新 brainstorming；设计已经逐段确认。
- 不要重新写 Phase 3 spec。
- 不要重复 Phase 1/2 Task、评测、终审或 live 20 条实验。
- 不要在 writing-plans 完成前写 Phase 3 实现代码。
- 实施计划写完、自审、提交后，必须让用户复核。
- 只有用户批准实施计划后，才进入 TDD 实现。
- Phase 2 PR 尚未合并；不要从远端 main 创建 Phase 3 implementation branch，否则会丢失 Phase 1/2 代码。
- 若 PR #1 仍未合并，Phase 3 implementation branch 必须基于当前 stacked Phase 3 design branch 或 Phase 2 feature tip。
- 若 PR #1 已合并，先 fetch 并验证远端 main 包含 Phase 2，再决定 implementation branch 基线。

## 9. 推荐的新对话首条指令

```text
读取 docs/handoffs/2026-07-17-phase3-design-to-plan-handoff.md 和
docs/superpowers/specs/2026-07-17-phase3-verification-review-workflow-design.md。
Phase 3 设计已经完成分段确认和自审，不要重新 brainstorming，也不要重复 Phase 1/2。
使用 writing-plans 编写详细的 bite-sized TDD 实施计划，保存到
docs/superpowers/plans/2026-07-17-phase3-verification-review-workflow.md；
自审并提交计划，但不要开始实现。注意 Phase 2 PR #1 当前可能仍未合并，先核对分支基线。
```

## 10. 当前完成定义

本交接点已完成：

- Phase 3 需求澄清；
- 三种架构方案比较；
- 五段设计逐项确认；
- 完整 spec；
- placeholder、矛盾、范围和歧义自审；
- 独立 design branch 与设计提交；
- 本交接文档。

本交接点未完成：

- writing-plans 实施计划；
- Phase 3 任何实现代码；
- Phase 3 design branch 推送；
- Phase 2 PR 合并。
