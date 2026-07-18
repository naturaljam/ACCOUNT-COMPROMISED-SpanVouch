# SpanVouch Phase 4 设计到编码线程交接

> 日期：2026-07-18
> 交接范围：Phase 4 改名、契约冻结、结构迁移、研究 artifact 基础
> 权威设计：`docs/superpowers/specs/2026-07-18-phase4-research-foundation-design.md`
> 当前线程职责：idea、研究与设计；未执行 Phase 4 代码

## 1. 接手者先读

按以下顺序阅读，后者不得覆盖前者已经批准的边界：

1. `docs/superpowers/specs/2026-07-18-phase4-research-foundation-design.md`
2. `docs/superpowers/specs/2026-07-17-ivad-research-engineering-program-design.md`
3. `docs/superpowers/specs/2026-07-17-spanvouch-naming-design.md`
4. `docs/evaluation/phase3-verification-review.md`
5. `docs/research/2026-07-17-afc-independent-verification-novelty.md`

如果实现计划、旧 README 或历史 handoff 与 Phase 4 详细设计冲突，以 Phase 4 详细设计为准；冻结 artifact 的原始 hash 和 Phase 3 实际验收记录除外。

## 2. 当前可验证仓库状态

设计文档形成时的仓库与 worktree 状态：

| 分支/对象 | SHA |
|---|---|
| `main` | `dddc7b8b49db81292d72cbac444ad039d17f5dde` |
| `feature/phase2-diagnosis-mvp` | `4df0ccb847cfee610ada9913e7ba31eec7667fc8` |
| `feature/phase3-verification-review` | `31ff910c72c720fa4a61b52b2687edc2053071e3` |
| Phase 3 code-under-test | `66e8f5d36f7d46db50f7bd962a036fcc94affbe6` |
| `docs/ivad-program-design` 文档基线 | `a8bc7896fec931a2ac18d89b0d053bb13bd30ce9` |

Phase 3 worktree：

```text
D:\self agent\.worktrees\phase2-diagnosis-mvp
```

Phase 3 branch 已与对应 remote branch 同步。`feature/phase2-diagnosis-mvp` 是 Phase 3 branch 的祖先，但 Phase 2/3 尚未进入当时的 `main`。

接手时必须重新核对 refs，不得假设本文 SHA 永远不变。

## 3. Phase 3 验收底线

编码线程必须把以下结果视为不可回退 baseline：

- 710 tests passing；
- 93% total coverage；
- Ruff clean；
- strict mypy clean over 63 source files；
- focused evaluation/delivery suite 37 passing；
- SQLite stability 20/20 processes；
- Phase 1 与 Phase 3 regenerated manifests 与冻结版本一致；
- deterministic review report byte-exact，SHA-256 `ff6af27b596a65d67fe2bda432f296d40e3f4c14a8537975e85ed9a7820fd39e`；
- 36 candidates：valid pass 1.0、hard-defect recall 1.0、unsupported-scope detection 1.0、operational errors 0；
- provider samples/tokens 0；
- Docker build、health、non-root、persistence、restart、cleanup 全通过。

Windows 全套 pytest 的权威调用是冻结 `.venv` 的 Python module invocation。Phase 3 报告已经记录 `uv run pytest` 在本机 bundled runtime 下无法 import repository `tests` namespace 的环境差异。不要把这个已知 runner 差异误判成业务测试失败，也不要跳过 Linux CI 的 locked-wheel `uv run --no-sync` 路径。

## 4. 已批准且不得重新选择的决策

1. **集成**：Phase 2 后 Phase 3 顺序进入 `main`，保留 full history。
2. **品牌**：公开系统名 SpanVouch，论文方法名 IVAD，AFC 只作为历史代号。
3. **改名**：一次性 hard cutover，无运行时 `afc` alias、旧 CLI wrapper 或 `AFC_*` fallback。
4. **冻结范围**：只冻结跨模块/进程/实验的公共 contracts，不冻结 SQLite row、LangGraph state、Command 或 lease。
5. **迁移次序**：baseline freeze → rename → Contract v1 → structure/adapters → artifact foundation → full verification。
6. **package**：Phase 4 仍为单一 `spanvouch` distribution，不拆多包 workspace。
7. **扩展性**：公共 failure taxonomy 使用 `taxonomy_id + taxonomy_version + failure_type`，不冻结 SupportLab closed enum 为全局 taxonomy。
8. **研究范围**：Phase 4 不实现 AutoGen、OpsLab、CodeLab、Conformal 或新补证能力。
9. **论文范围**：首篇论文不做人类参与者实验，不宣称开发者效率提升。
10. **API/GPU**：Phase 4 默认完全离线，不需要 DeepSeek 付费调用或云 GPU。

## 5. 编码线程必须先做的事

编码线程不得直接开始移动 `src/afc`。正确顺序是：

1. 检查 worktree clean state、所有分支 SHA 和 ancestry。
2. 阅读 Phase 3 验收报告，确认权威测试命令。
3. 将本设计文档分支内容纳入最终集成方案。
4. 编写正式 implementation plan：`docs/superpowers/plans/2026-07-18-phase4-research-foundation.md`。
5. 计划必须按 Batch 0–5 拆分，每个 batch 有独立测试、审查和提交边界。
6. 用户审查 implementation plan 后，才开始 Phase 4 代码迁移。

实现计划不得把整个 rename、contract extraction 和结构重构合并成一个巨型提交。

## 6. 推荐执行批次

### Batch 0：集成和 baseline marker

- 顺序集成 Phase 2、Phase 3 和设计文档；
- 保留完整历史；
- 在集成后的 `main` 重跑 Phase 3 离线验收；
- 记录 frozen baseline marker；
- 从 marker 创建 `feature/phase4-research-foundation`。

停止条件：任一核心 gate 失败、SHA 不明或冲突解决改变 Phase 3 行为。

### Batch 1：SpanVouch rename

- `agent-failure-clinic` → `spanvouch`；
- `src/afc` → `src/spanvouch`；
- `afc-*` → 统一 `spanvouch` CLI/subcommands；
- `AFC_*` → `SPANVOUCH_*`；
- database、Compose、API title 和 user-facing messages 同步迁移；
- 旧 frozen bytes 和历史 docs 不批量替换。

停止条件：旧 artifact hash 变化、wheel import 不唯一、CLI/API/Docker smoke 失败。

### Batch 2：Contract v1

- 冻结 Trace、Diagnostic Context、Diagnosis、Verification、Review、Artifact Manifest；
- 统一 canonical JSON/hash；
- 生成 JSON Schema 和 canonical fixtures；
- 实现 typed compatibility failures；
- 建立 contract catalog 与 versioning ADR。

停止条件：无法做到 byte-stable round trip，或 contract core 依赖基础设施。

### Batch 3：结构与 adapters

- verification 从 review 分离；
- SQLite、LangGraph、DeepSeek 移到 adapters；
- review transition/policy 与副作用分离；
- 建立 import boundary tests；
- 保持 revision、decision、lease、recovery 和 sanitization 语义。

停止条件：相同 fixture 产生不同公共输出，或 persistence/recovery 回退。

### Batch 4：research artifacts

- 新 evaluator 输出 Artifact Manifest；
- 记录 code/data/contract/config/prompt/model/usage/cost/runtime；
- provider view 与 evaluator gold view 分离；
- 生成 Phase 4 offline reference bundle；
- 建立 dirty-worktree、secret 和 label-leakage gates。

停止条件：manifest 无法闭合来源链，或为接入 manifest 修改冻结 dataset/metric。

### Batch 5：release candidate 与验收

- 构建并 clean-install `spanvouch==0.2.0`；
- 运行全量 lint/type/test/eval/Docker/security/recovery gates；
- 写 `docs/evaluation/phase4-research-foundation.md`；
- 更新 README、migration guide、contract catalog 和 reproducibility guide。

外部 GitHub repository rename、PyPI 或 container registry 发布不属于默认授权，必须另行请求用户确认。

## 7. 目标结构速查

```text
contracts <- trace <- diagnosis <- verification <- review

adapters/models/deepseek       implements model provider port
adapters/frameworks/langgraph  implements workflow runner port
adapters/storage/sqlite        implements repository port
api and cli                    call review application services
labs/supportlab and evaluation consume public contracts
production core               never imports labs/evaluation
```

不要创建空的 Phase 5–8 目录。当前只创建有真实 Phase 4 责任的 Module。

## 8. 必须冻结与不得冻结

必须冻结：

- TraceIR / TraceSpan；
- DiagnosticTraceView / EvidenceCatalog；
- DiagnosisClaim / EvidenceRef / DiagnosisExecution；
- VerifierReport / VerificationFinding / EvidenceGap；
- DiagnosisReviewCase / HumanReviewDecision / WorkflowEvent / public detail；
- ArtifactManifest；
- canonicalization 与 compatibility semantics。

不得冻结：

- SQLite rows/schema；
- LangGraph State/Command/reducer；
- runtime bundle 与 lease internals；
- provider SDK request/response；
- FastAPI internal DTO；
- SupportLab generator internals；
- evaluator accumulator。

## 9. 论文方向速查

论文中心不是“我们做了一个多智能体诊断平台”，而是：

> IVAD 将 Agent 故障诊断建模为受可机验 Claim–Evidence Contract 约束的选择性决策问题，并研究确定性检查、受控失效隔离语义验证、Conformal 风险控制和一次分层补证能否减少被接受诊断中的错误归因。

Phase 4 对论文的贡献只包括研究基础：

- 稳定、版本化、可引用的实验对象；
- framework/provider/storage 隔离边界；
- artifact provenance；
- label-leakage 防线；
- Phase 3 frozen engineering baseline。

Phase 4 不应在论文中声称：

- semantic verifier 已提高准确率；
- “独立性”已被证明；
- 已实现 conformal guarantee；
- 分层补证已恢复 coverage；
- 20 traces / 36 candidates 足以支撑完整会议论文；
- 系统已提升开发者效率。

## 10. 绝对禁止事项

- 不 squash Phase 2/3 历史；
- 不覆盖旧 manifest、dataset 或 evaluation bytes；
- 不保留双 namespace 作为“保险”；
- 不静默接受未知 contract version 或 field；
- 不把 SupportLab taxonomy 冻结成全局唯一 taxonomy；
- 不在 core 中 import FastAPI、SQLite、LangGraph、DeepSeek、labs 或 evaluation；
- 不在 Phase 4 顺便修改 verifier 判定规则；
- 不默认调用 DeepSeek 或租用 GPU；
- 不提交 `.env`、API key、raw provider body 或 hidden reasoning；
- 不在测试/build 失败后继续叠加下一 batch；
- 不擅自重命名远程仓库或发布 public package。

## 11. 每个批次的汇报格式

编码线程完成一个 batch 后，向用户报告：

```text
Batch:
Input commit:
Output commit:
Files/modules changed:
Contract or behavior impact:
Commands executed:
Passing evidence:
Frozen hashes checked:
Known warnings/limitations:
Ready for next batch: yes/no
```

不接受只有“测试通过”而没有 exact command、commit 和 artifact hash 的汇报。

## 12. Phase 4 最终交付要求

完成声明必须同时提供：

1. 集成后的 `main` SHA 和 Phase 4 branch SHA；
2. Phase 3 frozen marker；
3. rename inventory 与允许保留 AFC 的例外清单；
4. Contract v1 catalog、schemas、fixtures 和 compatibility matrix；
5. dependency boundary 证明；
6. offline reference artifact bundle；
7. 全量测试、覆盖率、lint、mypy、evaluation、SQLite、Docker 和 security 证据；
8. 旧 frozen hashes 复核结果；
9. Phase 4 验收报告；
10. 已知限制与 Phase 5 起点。

## 13. 给编码线程的推荐首条指令

```text
请接手 SpanVouch Phase 4，但先不要修改代码。先完整阅读：
1) docs/superpowers/specs/2026-07-18-phase4-research-foundation-design.md
2) docs/handoffs/2026-07-18-phase4-research-foundation-handoff.md
3) docs/evaluation/phase3-verification-review.md

先核对 main、Phase 2、Phase 3 和设计分支 SHA，并根据权威规格写出
docs/superpowers/plans/2026-07-18-phase4-research-foundation.md。
计划必须按 Batch 0–5 拆分，使用测试驱动和小提交，明确每个 gate、停止条件和回滚边界。
在我批准实施计划前，不执行 merge、rename 或源码迁移。
```

## 14. 交接完成状态

本交接已经给出 Phase 4 的输入基线、批准决策、论文边界、迁移次序、停止条件和最终验收要求。设计线程没有执行 merge、rename、contract extraction 或任何源码修改；编码线程应从“核对 SHA 并编写 implementation plan”开始。
