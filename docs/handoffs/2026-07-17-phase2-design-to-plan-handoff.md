# Agent Failure Clinic Phase 2 设计转实施计划交接

更新日期：2026-07-17

## 当前状态

- Phase 1 已完成，不要重复 Task 1–11、终审、main 合并或旧 worktree 清理。
- Phase 2 brainstorming 已确定“证据优先的纵向闭环”。
- 完整设计文书：
  `docs/superpowers/specs/2026-07-17-phase2-evidence-diagnosis-mvp-design.md`
- 设计当前状态为“待书面复核”。
- 尚未编写 Phase 2 implementation plan。
- 尚未创建 Phase 2 Git feature branch 或 worktree。
- 尚未开始任何 Phase 2 实现。
- 当前本地尚未配置 remote；用户提供的地址为：
  `git@github.com:naturaljam/Agent_Failure_Clinic.git`。

## 已确认决策

1. Rule Diagnoser 是确定性、可离线运行的主基线。
2. DeepSeek Diagnoser 与规则独立输入、独立输出、独立计分，不读取 invariant 结果。
3. 本期正式支持五类故障和 `no_failure`。
4. `missing_precondition`、`ignored_tool_error`、`context_corruption` 只触发 unsupported abstain。
5. 20 条冻结轨迹全部评测；14 条属于支持集，6 条属于范围外集合。
6. 不修改 Phase 1 三份冻结 fixture；新增 Phase 2 gold sidecar。
7. 必须阻断 `scenario.expected_failure`、`scenario.id`、语义化 `run_id`，以及
   `idempotency_key`、`ignore_error`、deprecated `calculated_amount` 等标签或 fault-injection 泄漏。
8. Diagnosis API 最后实现，默认 rules；DeepSeek 必须显式选择。
9. 自动测试不调用付费 API。live smoke 前再通知用户设置 `DEEPSEEK_API_KEY`，不要索要或记录密钥。
10. 不做 Repair、Verifier、RegressionCase、Release Gate、前端、数据库、队列或公共部署。

## 新对话执行顺序

1. 读取完整设计文书并进行书面复核。
2. 若用户批准设计，使用 `writing-plans` 编写新的 bite-sized TDD implementation plan：
   `docs/superpowers/plans/2026-07-17-phase2-evidence-diagnosis-mvp.md`。
3. 不修改 Phase 1 实施计划。
4. 计划自审后提交，并让用户选择执行方式。
5. 只有进入执行阶段，才使用 `using-git-worktrees` 创建
   `feature/phase2-diagnosis-mvp` 隔离 worktree。

## 推荐的新对话首条指令

```text
读取 docs/handoffs/2026-07-17-phase2-design-to-plan-handoff.md 和
docs/superpowers/specs/2026-07-17-phase2-evidence-diagnosis-mvp-design.md，
先复核 Phase 2 设计文书；设计无问题后使用 writing-plans 编写并提交详细的
bite-sized TDD 实施计划。不要重复 Phase 1，不要提前实现，也不要创建 worktree。
```
