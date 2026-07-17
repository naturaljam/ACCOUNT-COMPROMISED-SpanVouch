# Agent Failure Clinic Phase 2 交接文档

更新日期：2026-07-17

交接目标：从已合并的 Phase 1 基础设施出发，设计并实现“证据化故障诊断 MVP”，
形成 `TraceIR → DiagnosisReport → 评测指标` 的第一条产品闭环。

## 1. 当前权威状态

- 仓库：`D:\self agent`
- 当前分支：`main`
- 当前 HEAD：`37b6a0f7941287385d5dfce277601af32b5cc69b`
- 当前 HEAD 主题：`fix: enforce LF for frozen dataset fixtures`
- Phase 1 feature 分支与 worktree 已清理。
- main 工作树在交接文档提交前是干净的。
- 仓库当前没有配置 Git remote，因此没有 push、PR 或远端 tag。
- Docker Desktop 可用；最近验证的 Server 为 `29.1.3`、Linux `x86_64`。

恢复时先执行：

```powershell
Set-Location 'D:\self agent'
git status --short --branch
git rev-parse HEAD
git log -1 --oneline
docker version
```

不要重复 Phase 1 的 Task 1–11、全分支终审、main 合并或旧 worktree 清理。

## 2. Phase 1 已完成并验证

Phase 1 已实现：

- Python 3.12 / FastAPI 工程骨架。
- TraceIR v1 强类型边界。
- 单根、无环、全可达的 span tree 校验。
- SupportLab 退款领域、策略、安全金额重算和幂等工具。
- 4 个 clean controls + 8 类故障各 2 个，共 20 个确定性场景。
- ScriptedDecisionModel 与有界 LangGraph Agent。
- OpenTelemetry spans → TraceIR 映射。
- 确定性 dataset、labels、manifest 和 weak baselines。
- `GET /health` 与 `POST /v1/traces`。
- Docker、Compose、Phoenix provision、CI、非 root runtime 和健康检查。

main 合并结果上的最终证据：

- `75 passed`
- Ruff 通过
- mypy 通过，23 个 source files
- 三份 frozen dataset fixtures 强制 LF，byte-exact golden test 通过
- Docker API image build 通过
- API 与 Phoenix 均为 healthy
- API `/health` 返回预期 JSON
- Phoenix `/healthz` 返回 `OK`
- Runtime UID/GID 为 `10001:10001`
- Runtime 不包含 uv 或 `/app/src`
- Docker 服务已清理，无残留运行容器

## 3. 当前系统能做什么

当前可执行链路：

```text
选择确定性 SupportLab 场景
→ LangGraph Agent 执行工具调用
→ OpenTelemetry 记录 spans
→ 转换为 TraceIR
→ 写入 trace ingestion API
→ weak baseline 给出有限故障预测
```

当前 Phoenix 只被 Compose provision 并 health-check；AFC 尚未配置 OTLP exporter，
不能声称 Agent traces 已自动显示在 Phoenix 中。

## 4. 明确尚未实现

- 真实 DeepSeek 驱动的诊断模型。
- 完整 Diagnosis Agent。
- `InvariantResult` 和 `DiagnosisReport` 的生产实现。
- evidence span 引用与诊断置信度校准。
- 自动生成 RegressionCase。
- Repair Agent、Verifier Agent 和完整 Release Gate。
- 前端诊断工作台。
- 公共环境部署。

## 5. Phase 2 的最小目标

Phase 2 第一阶段只交付：

```text
TraceIR
→ 确定性 invariant/rule engine
→ evidence-backed DiagnosisReport
→ 20 条冻结数据集评测
→ DeepSeek provider 对照实验
```

建议首批诊断类型：

1. `wrong_tool`
2. `invalid_argument`
3. `policy_violation`
4. `loop_or_budget_exhaustion`
5. `invalid_final_state`

其余 `missing_precondition`、`ignored_tool_error`、`context_corruption` 在第一批稳定后补充。

## 6. 新对话的执行顺序

1. 读取本文件和总体设计：
   `docs/superpowers/specs/2026-07-15-agent-failure-clinic-design.md`。
2. 使用 brainstorming 明确 Phase 2 MVP 的输入、输出、评测和非目标。
3. 编写新的详细设计与 bite-sized implementation plan；不要直接修改 Phase 1 计划。
4. 创建隔离分支/worktree，建议分支名：`feature/phase2-diagnosis-mvp`。
5. 在新 worktree 建立干净基线：pytest、Ruff、mypy、dataset golden test。
6. 使用 Subagent-Driven Development 与严格 TDD 实施。
7. 每个任务完成后做规格/质量双轴审查，最后做全分支终审。

## 7. 推荐的 Phase 2 任务边界

### Task A：诊断领域模型

- `InvariantResult`
- `EvidenceRef`
- `DiagnosisReport`
- failure type、confidence、无法诊断状态
- JSON schema 和 round-trip tests

### Task B：确定性 invariant engine

- 从 TraceIR spans/attributes/outcome 提取证据。
- 规则不得只凭最终文本猜测。
- 每个结论必须返回 span ID 和具体 evidence。
- 未达到证据阈值时输出 unknown/abstain，而不是强猜。

### Task C：规则诊断器与基线评测

- 在冻结的 20 条数据集上运行。
- 输出分类准确率、证据命中率、abstain rate。
- 保留现有 weak baselines 作为对照，不覆盖其实现。

### Task D：DeepSeek provider

- Provider interface 与规则诊断器解耦。
- 单元测试使用 fake/mock provider。
- 真实 DeepSeek 仅做受控 smoke 和小规模 experiment。
- LLM 输出必须经过 Pydantic schema 校验。
- 诊断必须引用真实 span evidence；无法引用则 abstain。

### Task E：诊断 API

- 在领域模型和评测稳定后再设计 HTTP surface。
- 不要先做前端。
- 必须定义超时、错误映射、幂等和可观测属性。

## 8. DeepSeek 与安全约束

- 密钥只从 `DEEPSEEK_API_KEY` 环境变量读取。
- 禁止把密钥写入 Git、`.env` fixture、日志、trace attributes、报告或镜像。
- 调用必须设置 timeout、有限重试和费用/样本上限。
- 不记录完整敏感 prompt；保留可审计的 prompt/version hash。
- 结构化输出校验失败必须显式报错或 abstain。
- 规则诊断器必须可独立运行，不能让项目完全依赖付费 API。

## 9. 必须保持的 Phase 1 契约

1. `submit_refund` 必须显式接收 `item_skus`，金额由服务端重算。
2. deprecated `calculated_amount` 不能重新参与授权。
3. TraceIR 必须单根、无环、全可达，非法输入返回 422。
4. OTel mapper 必须拒绝 mixed traces 并归一化 tuple/sequence。
5. Agent outcome、OTel status、TraceIR status 必须一致。
6. 冻结数据集必须确定性、父子时间包络正确且保持 LF。
7. Trace conflict 只映射为 409；无效模型为 422；意外错误保持 500。
8. Docker/CI 必须保持 digest pinned、Python 3.12.13、非 root 和受约束 wheel build。

## 10. 已知非阻断技术债

- `uv.lock` 使用清华 PyPI mirror，需决定公共仓库的供应链/可移植性策略。
- delivery workflow 测试仍偏字符串解析。
- `.gitattributes` 回归测试可能受全局或 `.git/info/attributes` 干扰。
- FastAPI TestClient 有一个 StarletteDeprecationWarning。
- 本地 `.venv` 是 Python 3.12.7；CI 与 Docker 固定 3.12.13。

这些问题不应混入 Phase 2 核心诊断任务；如需处理，应单独建 maintenance task。

## 11. 关键文件

- 总体设计：`docs/superpowers/specs/2026-07-15-agent-failure-clinic-design.md`
- Phase 1 历史计划：`docs/superpowers/plans/2026-07-15-afc-phase-1-foundation.md`
- Phase 1 历史交接：`docs/handoffs/2026-07-17-phase1-closeout-phase2-handoff.md`
- TraceIR：`src/afc/trace_ir/`
- SupportLab：`src/afc/supportlab/`
- Observability：`src/afc/observability/`
- Baselines/Dataset：`src/afc/evals/`
- API：`src/afc/api/`
- Frozen dataset：`evals/datasets/supportlab-v1/`
- CI：`.github/workflows/ci.yml`
- Delivery：`Dockerfile`、`compose.yaml`、`build-constraints.txt`

## 12. 分叉对话推荐首条指令

```text
读取 docs/handoffs/2026-07-17-phase2-diagnosis-mvp-handoff.md 和总体设计，
先用 brainstorming 明确 Phase 2 证据化诊断 MVP，再编写详细设计与实施计划；
不要重复 Phase 1 的 Task 1-11、终审、main 合并或 worktree 清理。
```
