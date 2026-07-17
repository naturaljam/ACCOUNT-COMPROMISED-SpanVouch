# Agent Failure Clinic 交接文档

> 历史状态说明：本文件记录 Phase 1 收口过程。Phase 1 后续已于
> `37b6a0f7941287385d5dfce277601af32b5cc69b` 合并到 `main`。启动新对话请改读
> `docs/handoffs/2026-07-17-phase2-diagnosis-mvp-handoff.md`，不要执行本文旧的合并步骤。

更新日期：2026-07-17
交接目标：保留 Phase 1 全分支终审与 final-fix 证据，等待分支 owner 决定 main 集成，
然后再启动 Phase 2 的证据化诊断 MVP。

## 1. 项目目标

Agent Failure Clinic（AFC）不是普通的多智能体演示，而是一个面向 Agent/LLM 应用工程岗位的生产化作品集项目。目标闭环是：

```text
失败轨迹输入
→ 证据化根因诊断
→ 回归样例生成
→ 版本实验
→ Eval/Release Gate
```

当前 Phase 1 已搭建可复现的 SupportLab 被测 Agent、TraceIR、OpenTelemetry 映射、20 条标注轨迹、弱基线、轨迹接入 API，以及 Docker/CI 交付链路。

## 2. 仓库与收口状态

### 2.1 历史 pre-handoff snapshot

以下 HEAD、工作树和 Docker 状态是本交接文档首次提交前的历史快照，仅用于追溯，
不是恢复工作时应期待的当前状态：

- 主仓库：`D:\self agent`
- 当前开发 worktree：`D:\self agent\.worktrees\afc-phase1-foundation`
- 当前分支：`feature/afc-phase1-foundation`
- Task 11 收口实现 commit：`44421483f8636cef6d7cc7daf472b52abb76aa8a`
- Task 11 收口主题：`fix: make CI build artifact reproducible`
- `main` 当前 HEAD：`833a9bca1884ce14e7da618ecfdb74d8c40efebb`
- 当前代码工作树：无代码改动；仅本交接文档为新建、尚未提交文件
- Phase 1 尚未合并到 `main`
- 当前没有运行中的 Docker 容器
- Docker daemon 已启动：Docker Server `29.1.3`，Linux `x86_64`

### 2.2 当前 final-fix 收口点

- 全分支终审已在 review HEAD `3a39481c2a09f0b8be12b026b7155c7ec74aff98` 上完成。
- 终审发现的 Important TraceIR span-tree invariant 已在本交接文档所在的 final-fix
  提交中修复，并增加模型/API 回归测试。
- 当前准确 HEAD、工作树和门禁证据以 `git rev-parse HEAD`、`git status` 以及
  `D:\self agent\.git\worktrees\afc-phase1-foundation\sdd\phase1-final-fix-report.md`
  为准。
- Phase 1 仍未合并到 `main`；不要把“final-fix 已收口”误读为“已合并”。

恢复后先执行：

```powershell
Set-Location 'D:\self agent\.worktrees\afc-phase1-foundation'
git status --short --branch
git rev-parse HEAD
docker version
```

历史 snapshot 中，在提交本交接文档前预期 HEAD 为
`44421483f8636cef6d7cc7daf472b52abb76aa8a`，且 `git status` 只显示尚未提交的
交接文档。该预期已随交接文档和后续终审修复提交而失效，不得再作为恢复断言。

## 3. 当前已经实现的功能

### 3.1 SupportLab 被测 Agent

- LangGraph 有界执行图。
- 客户、订单、退款政策和退款提交工具。
- 服务端按显式 `item_skus` 重新计算退款金额。
- 审批、金额、客户、订单状态和策略上限校验。
- 退款幂等写入和稳定 ID。
- `succeeded`、`failed`、`step_limit` 三种结构化终态。
- 对预期工具/参数错误返回结构化失败，不吞掉 `BaseException`。

### 3.2 确定性失败场景

现有 20 个场景：4 个 clean control，以及下列 8 类失败各 2 个：

1. `wrong_tool`
2. `invalid_argument`
3. `missing_precondition`
4. `ignored_tool_error`
5. `context_corruption`
6. `policy_violation`
7. `loop_or_budget_exhaustion`
8. `invalid_final_state`

决策层当前使用 `ScriptedDecisionModel`，用于稳定复现和评测；尚未使用真实 DeepSeek 决策。

### 3.3 TraceIR 与可观测性

- TraceIR v1 的强类型 Pydantic 数据模型。
- span ID、父子关系、trace ID、时间和 JSON 属性校验。
- OpenTelemetry spans → TraceIR 映射。
- OTel tuple/sequence 属性递归归一化为 JSON list。
- 混合 trace 输入显式拒绝，避免静默合并。
- Agent 根 span 的 outcome 与 OTel/TraceIR 状态一致。
- Phase 1 仅 provision 并 health-check Phoenix；AFC 尚未通过 OTLP 向 Phoenix 导出轨迹。
- AFC → Phoenix 的 OTLP wiring 和可见 trace 路径属于后续阶段。

### 3.4 数据集与弱基线

- `evals/datasets/supportlab-v1` 包含 20 条确定性 traces 和 labels。
- 固定 seed：`20260715`。
- committed fixture、两次隔离生成和 manifest 哈希可逐字节对齐。
- 96/96 parent-child 边满足拓扑与时间包络。
- 已有 final-state baseline 和 rule-only weak baseline。
- 当前 baseline 只用于对照，不能视为完整自动诊断器。

### 3.5 API

- `GET /health`
- `POST /v1/traces`
- OpenAPI：`/docs`
- 相同 TraceIR 重试保持 `201` 幂等成功。
- 同 `trace_id` 不同内容返回 `409 Conflict`。
- 无效 TraceIR 返回 `422`。
- 非预期内部错误保持 `500`，没有被宽泛异常捕获掩盖。

### 3.6 交付工程

- 多阶段 Dockerfile。
- 基础镜像固定不可变 digest。
- Runtime 使用非 root UID/GID `10001`。
- Runtime 不携带 uv、构建缓存和源码目录。
- Docker Compose 启动 AFC API 与 Phoenix；Phoenix 在 Phase 1 仅被 provision 和
  health-check，不代表 AFC trace export 已接通。
- API `/health` 与 Phoenix `/healthz` 健康检查。
- GitHub Actions 固定 action commit SHA。
- CI 包含 lint、mypy、pytest、dataset drift、Docker build 和 health smoke。

## 4. 最近一次验证证据

截至 commit `44421483f8636cef6d7cc7daf472b52abb76aa8a`，Task 11 的实现、修复和独立复审已验证：

- `68 passed`
- 覆盖率 `97%`
- Ruff 通过
- mypy 通过
- 20-trace dataset manifest 通过
- `docker compose config` 通过
- AFC API 镜像真实 build 通过
- API 与 Phoenix 均进入 healthy
- API `/health` 返回预期 JSON
- Phoenix `/healthz` 返回 `OK`
- 容器进程 UID/GID 为 `10001`
- `docker compose down` 后无残留容器
- Hatchling 及其完整传递构建依赖使用 exact version + SHA256 constraints
- Docker 与 CI 都采用“受约束隔离构建 wheel → 安装 wheel”的 artifact 路径
- `.python-version`、setup-python 与 Docker Python patch 均固定为 `3.12.13`

Task 11 最终独立复审结论：Spec COMPLIANT、Quality APPROVED；仅剩 1 个非阻断 Minor——delivery workflow 测试使用扁平字符串解析。

## 5. 审查状态

- Task 1–10：均完成实现、独立规格审查和质量审查。
- Task 11：已完成实现、多轮修复和独立双轴复审。
- Task 11 最终结论：Spec COMPLIANT、Quality APPROVED、Critical 0、Important 0、Minor 1。
- Task 11 收口 commits：`51760be6`、`137fba6`、`4442148`。
- 全分支终审：已执行；review HEAD 为 `3a39481c2a09f0b8be12b026b7155c7ec74aff98`。
- 终审 Important：TraceIR 必须是单根、无环、全可达 span tree；已在当前 final-fix
  收口中修复并补充模型/API 回归测试。
- Phase 1 合并与 tag：未执行。

Git 元数据中的执行账本：

```text
D:\self agent\.git\worktrees\afc-phase1-foundation\sdd\progress.md
```

Task 11 和全分支终审均已完成；当前只剩根据 final-fix 报告核对收口证据并由分支
owner 决定是否合并到 `main`。

## 6. 恢复后的首要任务：核对 final-fix 后完成集成决策

全分支终审及其 Important 修复已经完成，不要重新从 Task 1-11 或旧 review HEAD
开始。恢复后按以下顺序执行：

1. 读取 `phase1-final-fix-report.md` 并核对其中的 fix commit SHA。
2. 确认 `git status` 与 `git rev-parse HEAD`，不要套用第 2.1 节的历史 snapshot。
3. 如需在另一环境重新验收，运行下列新鲜最终门禁：

```powershell
uv sync --frozen --group dev
uv run ruff check src tests
uv run mypy
uv run pytest -v
uv run afc-generate-dataset --output .cache/final-dataset-check --seed 20260715
docker compose config --quiet
docker compose build api
docker compose up -d api phoenix
docker compose ps
Invoke-RestMethod http://localhost:8000/health
Invoke-WebRequest http://localhost:6006/healthz -UseBasicParsing
docker compose down --remove-orphans
```

4. 将 `uv.lock` 清华镜像策略作为已记录 Minor 后续处理，不在本次 final-fix 扩围。
5. 获得分支 owner 的集成决定后，将 feature 分支合并到 `main`；建议后续创建
   `v0.1.0` tag。

在实际合并前，不要声称 Phase 1 已合并到 `main`。

## 7. Phase 2 建议范围：证据化诊断 MVP

Phase 1 收口后，今天优先做最短产品闭环：

```text
TraceIR
→ invariant/rule checks
→ DiagnosisReport
→ evidence spans
→ dataset evaluation
```

建议依次实现：

1. `InvariantResult`、`DiagnosisReport`、evidence reference 和 confidence 数据模型。
2. 确定性 invariant/rule engine。
3. 首批覆盖：wrong tool、invalid argument、policy violation、loop/budget exhaustion、invalid final state。
4. 诊断接口与评测器。
5. 在 20 条固定数据集上输出：分类准确率、证据命中率、无法诊断率和 baseline 对比。
6. 在稳定规则诊断器之后增加 DeepSeek provider。

DeepSeek 约束：

- 密钥只能从 `DEEPSEEK_API_KEY` 环境变量读取。
- 禁止把密钥写入仓库、日志、fixture、报告或 Docker image。
- 单元测试使用 fake/mock provider。
- 真实 API 只做小规模 smoke，必须设置超时、重试上限和费用边界。
- LLM 输出必须经过结构化 schema 校验，并引用实际 span 证据。

## 8. 必须保持的架构与安全约束

1. `SupportTools.submit_refund` 必须显式接收 `item_skus`。
2. `calculated_amount` 已 deprecated/ignored，不能重新成为调用方可信金额。
3. 退款金额必须由服务端根据订单与 SKU 重算。
4. 幂等键作用域必须包含订单，不能跨订单返回假成功。
5. TraceIR mapper 输入必须属于同一个 trace。
6. TraceIR 属性必须是真正的 JSON value，不能用 `cast` 掩盖 tuple。
7. dataset normalization 必须保持父子拓扑和时间包络。
8. Agent 运行终态必须同时反映在业务 outcome、OTel status 和 TraceIR status。
9. API 只将明确的 `TraceConflictError` 映射为 409，禁止宽捕获所有 ValueError。
10. Docker runtime 必须保持非 root、digest pinned 和健康检查。

## 9. 关键文件导航

- 项目说明：`README.md`
- 总体设计：`docs/superpowers/specs/2026-07-15-agent-failure-clinic-design.md`
- Phase 1 实施计划：`docs/superpowers/plans/2026-07-15-afc-phase-1-foundation.md`
- 项目调研：`docs/research/agent-project-landscape.md`
- TraceIR：`src/afc/trace_ir/`
- SupportLab：`src/afc/supportlab/`
- OTel 映射：`src/afc/observability/`
- Baselines/数据集：`src/afc/evals/`
- API：`src/afc/api/`
- 固定数据集：`evals/datasets/supportlab-v1/`
- CI：`.github/workflows/ci.yml`
- 容器：`Dockerfile`、`compose.yaml`
- TraceIR ADR：`docs/architecture/adr-001-traceir-boundary.md`

## 10. 当前明确未实现的功能

以下内容不要误报为已完成：

- 真实 DeepSeek 驱动的 Agent 决策。
- 完整 Diagnosis Agent。
- 自动生成 RegressionCase。
- Repair Agent 与自动修复。
- Verifier Agent 和完整 release gate。
- 前端诊断工作台。
- 公共环境部署。

## 11. 对新对话的推荐首条指令

```text
读取 docs/handoffs/2026-07-17-phase1-closeout-phase2-handoff.md 和
phase1-final-fix-report.md，核对 final-fix commit 与门禁证据后完成集成决策；
不要重复已经完成的 Task 1-11、全分支终审或 Important 修复。
```
