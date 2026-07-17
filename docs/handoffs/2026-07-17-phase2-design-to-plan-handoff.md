# Agent Failure Clinic Phase 2 实施交接

更新日期：2026-07-17

## 当前状态

- 工作分支：`feature/phase2-diagnosis-mvp`
- 隔离 worktree：`D:\self agent\.worktrees\phase2-diagnosis-mvp`
- Phase 1 不需要重做；Phase 2 Task 1–10 已提交，Task 11 的离线实现和交付验证已完成。
- remote：`git@github.com:naturaljam/Agent_Failure_Clinic.git`
- 尚未合并或推送；不要在未经用户选择时操作 main 或 remote。
- DeepSeek 真实付费实验尚未运行。自动测试、默认评测和 Docker 冒烟均未调用外部模型 API。

## 已交付能力

1. 严格的 diagnosis 领域模型、共享故障 taxonomy 和证据引用模型。
2. 标签安全的 `DiagnosticTraceView`，排除 trace/run 身份、scenario 标签和 fault-injection 字段。
3. Phase 2 diagnosis gold sidecar：20 条轨迹中 10 条支持故障、4 条 clean、6 条 unsupported。
4. 确定性 invariant engine 和五类支持故障规则；三类范围外故障由 guard 触发语义弃答。
5. `RuleDiagnoser` 产生包含 critical span、causal claim 和可本地解析 evidence selector 的报告。
6. 统一评测器：分类、critical span、证据、误报、弃答、覆盖率、结构化输出、错误率、token 和 p50/p95 延迟。
7. 有界 DeepSeek provider：JSON mode、关闭 thinking、一次有界重试、稳定错误、SecretStr 密钥保护。
8. 独立 `LlmDiagnoser`：不读取 invariant 结果，模型只返回 selector，本地回填 observed value/hash。
9. `POST /v1/traces/{trace_id}/diagnoses`：默认 rules，显式 deepseek，稳定映射 404/409/422/502/503。
10. 评测 CLI：默认离线 rules；DeepSeek 必须同时指定 `--diagnoser deepseek --allow-live-api`；支持重复 `--run-id` 白名单。

## 2026-07-17 验证证据

- 全量测试：`151 passed`；仅保留一个已知的 Starlette/httpx 弃用警告。
- Ruff：`All checks passed!`
- mypy：`Success: no issues found in 42 source files`
- 两次规则评测 SHA-256 均为：
  `2D533196FC6E56AF505B8A62DE89DFCD8767CC90E65C752CEB5F1F79F6207A9C`
- 规则硬门禁：支持集准确率 `1.0`、critical-span Top-1 `1.0`、selector validity `1.0`、gold evidence hit `1.0`、clean FPR `0.0`、unsupported abstain `1.0`、operational error `0.0`。
- Docker Engine `29.1.3`：digest-pinned、hash-constrained 镜像构建成功。
- Compose：API 与 Phoenix 健康检查通过；容器运行身份 `10001:10001`；运行镜像无 `/app/src`。
- API 冒烟：摄取 `invalid_argument-01` 后，rules 返回 `diagnosed / invalid_argument / span-005 / 2 evidence refs`。
- Compose 服务、网络和测试卷已清理。

## 剩余的受控 live 实验

只有真实 DeepSeek 实验需要操作者动作。不要在聊天、commit、命令参数或日志中传递密钥。

1. 操作者在本机 shell 设置 `DEEPSEEK_API_KEY`。
2. 先运行 `invalid_argument-01` 与 `clean-01` 两条 allowlist smoke。
3. 检查 schema、证据 selector、本地回填、token、延迟、模型和错误字段；不要提交 live 原始报告。
4. 两条 smoke 正常后，再由操作者明确执行完整 20 条实验。
5. 最后按 `finishing-a-development-branch` 选择保留分支、合并或推送；当前不自动集成。

命令与解释见：

- `README.md`
- `docs/evaluation/phase2-diagnosis-evaluation.md`
- `docs/superpowers/specs/2026-07-17-phase2-evidence-diagnosis-mvp-design.md`
- `docs/superpowers/plans/2026-07-17-phase2-evidence-diagnosis-mvp.md`
