# SpanVouch Phase 4：IVAD Research Foundation 详细设计

> 状态：设计已批准，本文是 Phase 4 实施与验收的权威规格
> 日期：2026-07-18
> 系统品牌：SpanVouch
> 研究方法：IVAD（Independently Verified Agent Diagnosis）
> 历史代号：AFC（Agent Failure Clinic）
> 阶段性质：改名、契约冻结、结构迁移与研究可复现性建设
> 实施约束：本文只定义要求，不包含或执行代码
> 上位设计：`docs/superpowers/specs/2026-07-17-ivad-research-engineering-program-design.md`
> 命名规范：`docs/superpowers/specs/2026-07-17-spanvouch-naming-design.md`
> Phase 3 验收：`docs/evaluation/phase3-verification-review.md`

## 1. 结论摘要

Phase 4 将已经通过验收的 AFC Phase 3 纵向闭环迁移为 SpanVouch 的稳定研究底座。阶段采用一次性 hard cutover，不保留运行时 `afc` import、CLI 或环境变量兼容层；同时完整保留 Git 历史、旧数据集、旧 manifest、旧评测报告及其中真实的 AFC provenance。

本阶段只冻结跨模块、跨进程、跨实验所依赖的公共契约，不冻结 SQLite 行结构、LangGraph State、内部 Command、lease 或 provider SDK 对象。结构迁移的目标是形成清晰依赖方向：

```text
contracts <- trace <- diagnosis <- verification <- review
      ^                                      ^
      |                                      |
  adapters implement ports; api/cli call application services
      ^
      |
labs and evaluation consume public interfaces only
```

Phase 4 不增加论文方法能力。它不实现 AutoGen、OpsLab、CodeLab、Conformal 风险控制或新的分层补证策略，而是确保这些能力在 Phase 5–8 可以通过稳定 Seam 接入，并确保未来每一个论文数字都能追溯到代码、数据、模型、prompt、配置和契约版本。

## 2. 当前基线与来源链

### 2.1 Git 基线

实施线程必须先确认以下对象仍存在且 SHA 一致：

| 对象 | SHA | 作用 |
|---|---|---|
| `main` | `dddc7b8b49db81292d72cbac444ad039d17f5dde` | 当前集成起点 |
| `feature/phase2-diagnosis-mvp` | `4df0ccb847cfee610ada9913e7ba31eec7667fc8` | Phase 2 完整历史 |
| `feature/phase3-verification-review` | `31ff910c72c720fa4a61b52b2687edc2053071e3` | Phase 3 文档收口点 |
| Phase 3 code-under-test | `66e8f5d36f7d46db50f7bd962a036fcc94affbe6` | 最终控制器实际验收代码 |
| `docs/ivad-program-design` | `a8bc7896fec931a2ac18d89b0d053bb13bd30ce9` | 本规格形成前的设计分支基线 |

`feature/phase2-diagnosis-mvp` 是 `feature/phase3-verification-review` 的祖先。集成必须按 Phase 2、Phase 3 的顺序进入 `main`，保留全部提交历史，不 squash，不把 Phase 3 直接替换为一个扁平提交。

如任一 SHA 已变化，实施线程必须先停止迁移，比较变化来源，并在交接记录中给出新的可验证基线。不得根据分支名称猜测内容相同。

### 2.2 Phase 3 已验收能力

Phase 4 的输入不是原型空壳，而是一条已经闭环的 production-quality vertical slice：

```text
Trace ingestion
  -> deterministic / optional DeepSeek diagnosis
  -> deterministic / optional semantic verification
  -> at most one evidence-grounded revision
  -> SQLite persistence and crash recovery
  -> mandatory human confirm / correct / reject
  -> API / HTTP-only CLI / Docker delivery
```

Phase 3 最终验收证据包括：

- 710 个测试全部通过，总覆盖率 93%；
- Ruff、strict mypy 通过，检查 63 个源文件；
- 36 个冻结 review candidates 中 20 个合法样本全部通过，16 个确定性 mutation 全部精确检出；
- unsupported scope 检出率 1.0，operational error rate 0.0；
- 两次确定性评测报告 byte-exact，SHA-256 为 `ff6af27b596a65d67fe2bda432f296d40e3f4c14a8537975e85ed9a7820fd39e`；
- 20/20 独立 SQLite 稳定性进程通过；
- Docker build、health、非 root UID/GID `10001:10001`、持久化、重启恢复和清理通过；
- 最终验收未调用付费 API，provider samples 与 tokens 均为 0。

历史 DeepSeek semantic comparison 只证明 provider 路径可运行，不是当前语义准确率结论。其 29/36 strict structured outputs 和 7 个安全降级结果必须作为局限保留，不得改写成 Phase 3 已证明“独立验证有效”。

### 2.3 冻结数据来源

以下旧 artifact 是历史事实，不因品牌迁移重写字节：

| Artifact | SHA-256 |
|---|---|
| `supportlab-v1/manifest.json` | `b14eac192e7b683fb908f2f7f54efccb31ab100bf19563476b824d192060cb38` |
| `supportlab-review-v1/manifest.json` | `677e0075f5b4149db73538411376bf994caa5ba0fdb8ff29b33b487a5fe02076` |
| `review-candidates-v1.jsonl` | `ee04d8d0f1e608fd81c202fca39eeb799f764b3099cfb03d7d94a4ab7eb73bd2` |
| `review-labels-v1.jsonl` | `d41a87247456264863d70f807256a5d1b6f24ab84422dc406a92ef867e36b305` |

## 3. Phase 4 目标、范围与非目标

### 3.1 必须完成

1. 将 Phase 2 和 Phase 3 顺序集成到 `main`，建立带签名说明或等价可审计注释的 Phase 3 frozen baseline marker。
2. 将公开品牌、distribution、import namespace、CLI、环境变量和容器标识一次性迁移为 SpanVouch。
3. 冻结 Contract v1：Trace、Diagnostic Context、Diagnosis、Verification、Review 和 Artifact Manifest。
4. 把验证算法从 `review` 中剥离为独立 Module，把 provider、framework 和 storage 具体实现移入 adapters。
5. 保留 SupportLab 与 evaluation，但阻止生产 core 依赖它们。
6. 定义 canonical serialization、schema versioning、兼容性错误和 fixture 策略。
7. 建立 experiment manifest、artifact bundle、provenance 和 label-leakage 防线。
8. 保持 Phase 3 的功能、确定性评测、持久化、恢复、安全和 Docker 行为不回退。
9. 发布目标版本 `spanvouch==0.2.0`，保持 Python `>=3.12,<3.13`。

### 3.2 明确不做

- 不实现 AutoGen adapter；
- 不新增 OpsLab 或 CodeLab；
- 不实现 Conformal risk controller；
- 不实现新的 Level 1/Level 2 evidence acquisition；
- 不引入 Postgres、Redis、任务队列或分布式 workflow；
- 不新增 Web UI、认证、RBAC 或多租户；
- 不新增 Repair Agent、RegressionCase 或 Release Gate；
- 不扩大 semantic verifier prompt 或运行新的付费实验；
- 不创建空的 `risk/`、`acquisition/`、`opslab/`、`codelab/` 包来假装进度；
- 不篡改旧 AFC artifact、旧 commit 或旧实验报告中的 provenance；
- 不将 Phase 4 的重构包装为论文方法创新或有效性结果。

### 3.3 阶段成功定义

Phase 4 成功不是“目录换了名字”，而是以下三个结果同时成立：

1. **Locality**：验证、存储、框架和领域实验的变化分别局限在对应 Module 或 Adapter。
2. **Reproducibility**：任一新实验输出都能由 Artifact Manifest 追踪到完整输入与执行环境。
3. **Behavior preservation**：Phase 3 的可观察行为、安全边界和冻结评测结果没有回退。

## 4. 论文方向与 Phase 4 的研究作用

### 4.1 论文定位

目标社区为 Software Engineering / AI Reliability。当前工作标题为：

> **SpanVouch: Risk-Controlled, Independently Verified Failure Diagnosis for AI Agents**

系统品牌是 SpanVouch，论文方法是 IVAD。AFC 只作为历史代号出现在旧 artifact provenance、Git 历史或必要的迁移说明中。

首篇论文不开展人类参与者实验，也不声称提高真实开发者效率。论文评估结构化复核包的内容完整性、证据正确性、诊断风险、覆盖率和生成成本。

### 4.2 可证伪中心命题

论文最终希望检验、而不是预先宣布以下命题：

> 在固定选择性风险目标下，通过确定性不变量和具有受控失效隔离的模型通道重新解析不可变轨迹证据，可以减少无证据及错误归因的 Agent 诊断；将一次分层补证纳入完整校准策略后，可以在不超过相应风险上界的前提下恢复覆盖率。

在数学条件、实现核验和完整实验完成前，只能使用“经验风险控制”或准确限定条件的“有限样本风险上界”，不得使用普适 certification、guaranteed truth 或 distribution-free guarantee。

### 4.3 论文贡献边界

计划中的主要贡献是：

1. 可机验、可寻址、可重新解析的 Claim–Evidence Contract；
2. 确定性通道与受控失效隔离语义通道组成的双通道验证协议；
3. 将“独立性”转化为可测量的 failure-separation 实验变量；
4. 对 accepted diagnoses 的 selective risk–coverage 控制；
5. 纳入 post-acquisition calibration 的一次分层补证；
6. 跨 SupportLab、OpsLab、CodeLab 与 LangGraph、AutoGen 的评估。

TraceIR、普通第二 LLM judge、自报置信度阈值、小型自建数据集、API、CLI、SQLite 和 Docker 都不是论文主要创新。

### 4.4 研究问题

| RQ | 问题 | 主要证据 | 对应阶段 |
|---|---|---|---|
| RQ1 | IVAD 是否减少无证据和错误归因诊断？ | false accept、unsupported claim、failure/entity/span accuracy、evidence P/R/F1 | Phase 6、9 |
| RQ2 | 哪些隔离机制降低诊断器和验证器的相关错误？ | 条件错误率、MCC/phi、增量检出率、固定 coverage 风险 | Phase 6、9 |
| RQ3 | `alpha=0.05/0.10` 时能保留多少 coverage？ | selective risk、AURC、coverage@risk、有限样本上界 | Phase 7、9 |
| RQ4 | 分层补证能否在风险不超标时恢复 coverage？ | delta coverage、post-acquisition risk、tokens、延迟、费用 | Phase 8、9 |
| RQ5 | 结论能否跨领域、框架和模型泛化？ | domain/framework/model OOD 分层结果 | Phase 5、9 |

### 4.5 Phase 4 必须产生的论文资产

Phase 4 不回答上述 RQ，但必须交付后续实验可直接引用的研究资产：

- 一张稳定的方法对象图：Trace、Claim、Evidence、Verifier Report、Review 与 Artifact 的关系；
- Contract v1 的字段定义、JSON Schema、canonical fixtures 和版本规则；
- 系统边界和 Adapter 隔离说明，可作为 Method/Implementation 的工程基础；
- Artifact Manifest 与数据来源链，可作为 Reproducibility 和 Appendix 的基础；
- label-leakage threat model 与自动化防线说明；
- Phase 3 frozen baseline，作为后续实验回归对照而不是论文主结果；
- 一份 claim–evidence ledger，记录每个预期论文 claim 需要哪个未来实验支持。

### 4.6 Claim–evidence ledger 初版

| 计划 claim | 当前状态 | 所需证据 |
|---|---|---|
| 确定性 verifier 能识别已定义的结构、引用和 scope 缺陷 | 仅在 36-candidate 工程回归集上支持 | Phase 9 扩展 mutation benchmark 与区间估计 |
| 语义 verifier 提高错误诊断检出率 | 尚无有效证据 | Phase 6 隔离实验、人工/执行真值、强基线 |
| “独立”隔离降低相关失败 | 尚无有效证据 | 同模型、异模型、跨 provider、共享/隔离上下文消融 |
| 风险控制达到目标 alpha | 尚无有效证据 | Phase 7 冻结校准协议与独立 ID test |
| 一次补证恢复 coverage 且不破坏风险 | 尚无有效证据 | Phase 8 post-acquisition calibration 与成本曲线 |
| 结论跨域、跨框架泛化 | 尚无有效证据 | Phase 5 labs 与 Phase 9 OOD evaluation |

任何论文草稿都必须根据此表收缩未被支持的 claim。

## 5. 目标模块结构

### 5.1 单 distribution 决策

Phase 4 保持一个 Python distribution：`spanvouch`。现在不拆成多个 PyPI 包或 monorepo workspace，因为公共 Interface 尚未经过 Phase 5 的 LangGraph/AutoGen 双框架压力测试。是否拆包在 Phase 5 完成后重新评估。

目标结构为：

```text
src/spanvouch/
  contracts/
    trace.py
    diagnosis.py
    verification.py
    review.py
    artifacts.py
    versioning.py
  trace/
    mapper.py
    diagnostic_view.py
    evidence_catalog.py
    repository.py
  diagnosis/
    protocols.py
    rule_diagnoser.py
    llm_diagnoser.py
    engine.py
  verification/
    protocols.py
    deterministic.py
    semantic.py
    invariants.py
    verdicts.py
  review/
    commands.py
    policy.py
    transitions.py
    application.py
    recovery.py
  adapters/
    models/
      deepseek.py
    frameworks/
      langgraph_review.py
    storage/
      sqlite.py
  api/
  cli/
  labs/
    supportlab/
  evaluation/
    datasets.py
    diagnosis.py
    review.py

evals/
  datasets/
  experiments/
  configs/
  reports/

schemas/
  v1/

tests/
  contracts/
  trace/
  diagnosis/
  verification/
  review/
  adapters/
  api/
  cli/
  labs/
  evaluation/
```

文件名可以因现有代码细节小幅调整，但 Module 边界、依赖方向和公共 Interface 不得改变。若实施需要偏离，必须先写 ADR 并由用户批准。

### 5.2 依赖规则

- `contracts` 不依赖 FastAPI、SQLite、LangGraph、DeepSeek SDK、SupportLab 或 evaluation。
- `trace` 只依赖 contracts 和必要的通用库。
- `diagnosis` 依赖 contracts 与 trace，不依赖具体 provider。
- `verification` 依赖 contracts、trace 和 diagnosis，不依赖 review workflow。
- `review` 依赖前述公共 Module，通过 port 调用 workflow 和 persistence。
- `adapters` 依赖 core Interface 并实现它，不反向成为 core 的依赖。
- `api` 和 `cli` 只调用 application service，不直接拼装 SQLite、DeepSeek 或 LangGraph 内部状态。
- `labs` 与 `evaluation` 可以消费公共 Interface；生产 core 不得 import 它们。

### 5.3 必须形成的深 Interface

以下是语义要求，不强制实现为同名 Python class，但对外能力必须等价且集中：

```text
TraceProjector.project(trace) -> DiagnosticContext
Diagnoser.diagnose(context, evidence) -> DiagnosisExecution
Verifier.verify(request) -> VerifierReport
ReviewApplication.create(...)
ReviewApplication.resume(...)
ReviewApplication.decide(...)
ReviewRepository
ModelProvider
ReviewWorkflowRunner
```

每个 Interface 应隐藏足够复杂的实现细节，调用者不应知道：

- SQLite schema 和 CAS/lease SQL；
- LangGraph node 或 reducer；
- DeepSeek HTTP 请求格式；
- deterministic verifier 的具体检查顺序；
- SupportLab 的 failure taxonomy 实现。

### 5.4 热点拆分要求

Phase 3 的大文件只做与边界相关的拆分，不顺便重写算法：

| 当前热点 | Phase 4 目标 |
|---|---|
| `review/sqlite_repository.py` | repository port 与 SQLite Adapter 分离；schema/CAS/lease 可在 Adapter 内按职责拆分 |
| `review/workflow.py` | 纯 transition/policy 与 LangGraph Adapter 分离 |
| `diagnosis/trace_view.py` | Diagnostic Context contract、projection 和 secret sanitization 职责分离 |
| `review/evidence_verifier.py` | deterministic verification 移入独立 verification Module |
| `review/models.py` | 公共 contracts 与内部 runtime state 分离 |

拆分前后相同输入必须产生相同公共输出。Phase 4 不借拆分改变 verdict、revision bound、human decision 语义或恢复策略。

## 6. 一次性改名要求

### 6.1 映射表

| 维度 | 旧值 | 新值 |
|---|---|---|
| 品牌 | Agent Failure Clinic / AFC | SpanVouch |
| distribution | `agent-failure-clinic` | `spanvouch` |
| import root | `afc` | `spanvouch` |
| package path | `src/afc` | `src/spanvouch` |
| 主 CLI | 多个 `afc-*` 入口 | `spanvouch` 主入口及其稳定 subcommands |
| 环境变量 | `AFC_*` | `SPANVOUCH_*` |
| 数据库默认名 | `afc.db` | `spanvouch.db` |
| Compose volume/service 标识 | `afc_*` | `spanvouch_*` |
| API title / user-facing messages | AFC | SpanVouch |
| Python version | `>=3.12,<3.13` | 不变 |
| package version | `0.1.0` | `0.2.0` |

CLI 的具体 subcommand 设计应统一现有入口，例如 dataset、evaluation 和 review；不得同时长期暴露 `afc-review` 与 `spanvouch review` 两套主路径。

### 6.2 Hard cutover 规则

- 迁移完成后不提供 `afc` import alias package。
- 不提供 `AFC_*` 环境变量 fallback。
- 不提供旧 `afc-*` CLI wrapper。
- 不在 README 中把 AFC 与 SpanVouch 写成两个并列产品。
- 未知旧配置必须产生明确错误，不得静默忽略。
- 新用户只看 README 首屏即可区分 SpanVouch 系统与 IVAD 方法。

### 6.3 历史保留规则

以下内容不得批量替换：

- Git commit message 与历史 branch；
- 已冻结 artifact 的原始 bytes；
- 旧 manifest 中真实记录的 AFC generator/provenance；
- Phase 1–3 的历史设计、验收报告和命令记录；
- 用于验证旧结果的 hash。

允许在新文档中写 `SpanVouch (formerly Agent Failure Clinic)`，但不得伪造旧实验当时使用 SpanVouch 名称。

## 7. Contract v1 冻结

### 7.1 冻结目录

```text
src/spanvouch/contracts/
  trace.py
  diagnosis.py
  verification.py
  review.py
  artifacts.py
  versioning.py

schemas/v1/
  spanvouch.trace-1.0.schema.json
  spanvouch.diagnostic-context-1.0.schema.json
  spanvouch.diagnosis-1.0.schema.json
  spanvouch.verification-1.0.schema.json
  spanvouch.review-1.0.schema.json
  spanvouch.artifact-manifest-1.0.schema.json
```

### 7.2 公共契约目录

| Contract | Root / 主要对象 | 冻结目的 |
|---|---|---|
| Trace | `TraceIR`, `TraceSpan` | 框架 adapter 到研究内核的不可变轨迹输入 |
| Diagnostic Context | `DiagnosticTraceView`, `EvidenceCatalog` | 诊断器和 verifier 可见、已清洗、可寻址的证据视图 |
| Diagnosis | `DiagnosisClaim`, `EvidenceRef`, `DiagnosisExecution`, `DiagnosisReport`, provenance, usage | 诊断器输出与验证器输入 |
| Verification | `VerificationInput`, `VerifierReport`, `VerificationFinding`, `EvidenceGap`, provenance, operational error | 双通道验证与后续 risk feature 输入 |
| Review | `DiagnosisReviewCase`, `HumanReviewDecision`, `WorkflowEvent`, `DiagnosisReviewDetail` | 人工裁决和可审计聚合视图 |
| Artifact Manifest | `ArtifactManifest` | 实验、数据、模型、prompt 和结果的来源链 |

### 7.3 不冻结对象

以下对象是 Implementation detail，不构成跨版本承诺：

- LangGraph State、node、reducer 与 Command；
- SQLite schema version、row model、SQL、index、CAS 与 lease 表达；
- `ReviewRuntimeBundle` 和 worker ownership；
- FastAPI dependency injection 与内部 DTO；
- DeepSeek HTTP request/response SDK 对象；
- SupportLab scenario builder 与 mutation generator 内部状态；
- evaluator 的临时 accumulator；
- 模块私有异常和辅助函数。

### 7.4 Schema 标识

每个可独立持久化或跨进程传输的公共 root 必须携带：

```json
{
  "schema_name": "spanvouch.diagnosis",
  "schema_version": "1.0"
}
```

首批标识固定为：

```text
spanvouch.trace/1.0
spanvouch.diagnostic-context/1.0
spanvouch.diagnosis/1.0
spanvouch.verification/1.0
spanvouch.review/1.0
spanvouch.artifact-manifest/1.0
```

Nested value objects 不重复携带版本；其语义由 root contract version 管理。

### 7.5 版本规则

Contract version 与 `spanvouch==0.2.0` package version 独立：

- 增加带默认值、旧 reader 可以忽略的可选字段：minor version；
- 删除字段、重命名字段、改变类型、默认值语义、枚举含义或关键 invariant：major version；
- bug fix、性能优化和内部结构变化：不改变 contract version；
- reader 必须接受自己明确支持的 major/minor 组合；
- 未知 major 必须以 typed compatibility error 拒绝；
- 不允许 silent coercion、best-effort guessing 或自动吞掉未知字段；
- schema migration 必须是显式、可测试、可审计的 pure transformation。

### 7.6 Canonical serialization

公共契约统一要求：

- UTF-8；
- object keys 按字典序排序；
- 无无意义空白；
- string 保留 Unicode，不依赖平台 locale；
- tuple/list 按 JSON array 序列化并保持顺序；
- enum 使用稳定字符串值；
- 时间使用带 `Z` 的 UTC ISO 8601；
- 浮点值禁止 NaN 与 Infinity；
- hash 使用小写 SHA-256 十六进制；
- model 拒绝未知字段；
- 相同语义对象必须生成 byte-identical canonical bytes；
- canonical hash 必须覆盖明确标注的完整 payload，禁止调用者自行选择字段。

Canonicalization 只能在 `contracts/versioning` 或等价单一 Module 中实现。`diagnosis`、`review` 和 evaluator 不得各自保留略有差异的 JSON/hash 实现。

### 7.7 可扩展 taxonomy

当前 `FailureType` 是 SupportLab 专用闭集，不应成为 SpanVouch 永久全局枚举。公共 Diagnosis Contract 必须表达：

```text
taxonomy_id
taxonomy_version
failure_type
```

`failure_type` 在 contract seam 上是有格式约束的稳定字符串。SupportLab 内部仍可使用强类型 enum，并通过 Adapter 或 mapper 转换。迁移后现有 serialized failure values 不变。

`provider`、`model`、`diagnoser_kind`、`verifier_kind` 同样采用可扩展标识符。现有 `rules`、`deepseek` 等值保持不变，但公共 schema 不因未来增加 provider 或 verifier 而被迫 major bump。

### 7.8 Contract 错误语义

至少区分：

- unknown schema name；
- unsupported schema major/minor；
- malformed payload；
- unknown field；
- invariant violation；
- canonical hash mismatch；
- provenance incomplete；
- migration unavailable。

错误对象不得包含 API key、Authorization header、raw provider body、hidden reasoning 或未经清洗的 trace 字段。

### 7.9 Contract 交付物

- versioned Pydantic models 或等价严格模型；
- checked-in JSON Schema；
- 每个 root contract 的 canonical valid fixture；
- unknown-field、unknown-version、hash-mismatch 和 invariant-invalid fixtures；
- canonical round-trip 与 byte-stability tests；
- v1 compatibility matrix；
- `docs/contracts/catalog.md`；
- `docs/architecture/adr-002-contract-versioning.md`。

## 8. Artifact Manifest 与实验可复现性

### 8.1 Manifest 必需字段

每次新评测或数据生成必须生成 `spanvouch.artifact-manifest/1.0`，至少包含：

```text
artifact_id
artifact_kind
created_at_utc
command_name
code.git_commit
code.repository_url_or_local_identity
code.dirty_worktree
package.version
contracts{name -> version}
datasets[{id, version, manifest_sha256, payload_sha256...}]
configuration{canonical_sha256, artifact_ref}
randomness{seed, deterministic_flags}
models[{provider, model, endpoint_class, generation_config, prompt_sha256}]
runtime{python, os, architecture, dependency_lock_sha256}
inputs[{path_or_id, sha256}]
outputs[{path_or_id, sha256, media_type}]
metrics{schema_ref, report_sha256}
usage{requests, input_tokens, output_tokens, total_tokens}
cost{currency, estimated_or_billed, amount, pricing_ref}
parent_artifacts[]
```

不适用的付费模型字段使用明确的 `not_used` 状态，不得以缺字段暗示零调用。dirty worktree 的正式实验默认拒绝；探索性实验可以运行，但必须被标记为 non-release evidence。

### 8.2 Artifact bundle

一个可发布实验 bundle 至少包含：

```text
manifest.json
config.json
metrics.json
stdout.log or structured-events.jsonl
environment.txt
README.md
```

如包含模型调用，还应包含清洗后的 request metadata、raw response 的不可逆 hash、token/latency/cost 记录。不得提交 API key、Authorization header、未清洗 raw provider body、hidden reasoning 或用户隐私数据。

### 8.3 Label-leakage 防线

- gold labels、mutation metadata 和 expected finding codes 只对 evaluator 可见；
- diagnoser/verifier provider payload 不得包含 candidate mutation type、gold failure、expected verdict 或 split identity；
- train/dev、calibration、ID test 和 OOD test 必须按 task template、environment、repository、failure family 分组；
- 数据 loader 的 provider view 和 evaluator view 必须是不同类型或明确不同 Interface；
- artifact manifest 记录 split manifest hash；
- 测试必须注入 sentinel label 并证明其不会进入 provider payload、prompt 或 persisted review snapshot。

### 8.4 历史 artifact 桥接

旧 AFC artifact 保持原 bytes。新的 SpanVouch manifest 通过 SHA-256 和 `parent_artifacts` 引用旧 manifest，不在旧文件中回填 SpanVouch 字段。这样既保持品牌迁移后的统一入口，也不破坏历史真实性。

## 9. 分批迁移方案

迁移采用 staged hard cutover。每批必须在独立、可审查的提交序列中完成，并通过自己的 gate 后再进入下一批。

### Batch 0：集成与 Phase 3 基线冻结

输入：当前 `main`、Phase 2 branch、Phase 3 branch、本文档。

要求：

1. 顺序集成 Phase 2、Phase 3，保留 full history。
2. 解决冲突时优先保留 Phase 3 已验收行为和本设计分支中的最新研究/命名文档。
3. 在集成后的 `main` 重跑 Phase 3 全套离线验收。
4. 建立 Phase 3 frozen baseline marker，记录集成 commit、code-under-test SHA、manifest hashes 和评测报告 hash。
5. 从该 marker 创建 Phase 4 分支，建议名为 `feature/phase4-research-foundation`。

Gate：分支干净、完整历史可追踪、Phase 2/3 都是新 `main` 祖先、Phase 3 全量验收不回退。

Rollback boundary：在新 `main` 未通过完整验收前，不删除旧 feature branch，不改 GitHub 仓库名。

### Batch 1：SpanVouch hard rename

要求：

1. 完成 distribution、namespace、CLI、环境变量、容器、README 和 user-facing message 迁移。
2. 更新 lockfile、build backend package path、mypy package 和 coverage target。
3. 更新全部测试 import 与 delivery assertions。
4. 建立负向扫描，确保 runtime/source/test/config 中不存在旧公共入口。
5. 保留历史 docs 与 frozen artifacts 中允许出现的 AFC。

Gate：wheel 安装后只能 import `spanvouch`；`import afc` 失败；新 CLI/API/Docker smoke 通过；旧 manifest hash 不变。

Rollback boundary：改 GitHub repository slug 和外部 registry 名称延后到所有本地 rename gate 通过之后。

### Batch 2：Contract v1 提取与冻结

要求：

1. 从现有 models 中提取公共 root 和 value objects。
2. 统一 canonical JSON/hash 实现。
3. 引入 schema name/version、strict validation 和 typed compatibility errors。
4. 将 SupportLab closed taxonomy 转换为 contract seam 上的可扩展 taxonomy reference。
5. 生成 JSON Schema、fixtures、catalog 和 ADR。
6. 证明现有公共业务 payload 在语义上保持一致；旧 frozen bytes 不重写。

Gate：所有 contract fixtures byte-stable round trip；unknown major/field/hash mismatch 明确失败；contract package 无基础设施依赖。

Rollback boundary：contract fixture 一旦作为 Phase 4 release evidence 冻结，后续改动必须按版本规则处理，不允许覆盖原 fixture。

### Batch 3：Verification、Review 与 Adapter 边界迁移

要求：

1. 将 deterministic/semantic verification 从 review workflow 移出。
2. 将 LangGraph workflow runner、SQLite repository、DeepSeek provider 变为 Adapter。
3. 将 review workflow 中纯 transition/policy 与副作用分离。
4. 保持一次 revision、human decision、lease/recovery 和安全清洗行为不变。
5. 建立 dependency boundary tests，阻止反向 import。

Gate：相同 Phase 3 fixture 的 diagnosis/verifier/review canonical outputs 相同；SQLite 并发与恢复测试通过；core import graph 无基础设施依赖。

Rollback boundary：不得同时改变模块边界和业务判定规则；如发现行为变化，先恢复到 Phase 3 行为，再单独提出后续设计。

### Batch 4：Research artifact foundation

要求：

1. 实现 Artifact Manifest contract 和 bundle 组织规则。
2. 让现有 dataset generation 与 diagnosis/review evaluation 产生或关联 manifest。
3. 记录代码、数据、contract、prompt、provider、token、成本和环境来源。
4. 建立 dirty-worktree、missing-hash、label-leakage 和 secret-hygiene gate。
5. 生成一个完全离线的 Phase 4 reference bundle。

Gate：第三方仅凭 bundle 与锁文件可以定位并重跑小型离线评测；输出 hash 与 manifest 一致；零密钥泄漏。

Rollback boundary：不得为了 manifest 接入改变评测指标定义或冻结 dataset 内容。

### Batch 5：文档、发布候选与阶段验收

要求：

1. 更新 README、architecture map、contract catalog、migration guide 和 research reproducibility guide。
2. 生成 `spanvouch==0.2.0` wheel 并进行 clean-environment install smoke。
3. 重跑全套 quality、evaluation、Docker、persistence、security 和 artifact gates。
4. 形成 Phase 4 验收报告，逐项引用命令、SHA、hash 和结果。
5. 仅在本地与 CI 全部通过后，才执行 GitHub 仓库改名或 registry 发布；外部改名需用户单独确认。

Gate：满足第 10 节完整 Definition of Done。

## 10. 验收矩阵

### 10.1 Git 与来源链

- Phase 2 与 Phase 3 commit 历史完整存在于 `main`；
- Phase 3 frozen marker 可定位 code-under-test 与最终验收报告；
- Phase 4 分支从已验收 marker 创建；
- 工作区干净；
- release artifact 记录 exact commit 且 `dirty_worktree=false`。

### 10.2 Naming

- distribution 为 `spanvouch`，版本 `0.2.0`；
- package root 为 `src/spanvouch`；
- `import spanvouch` 成功，`import afc` 失败；
- 主 CLI、环境变量、Docker 标识和 API title 使用 SpanVouch；
- runtime/config/source/tests 不存在旧 AFC 公共入口；
- 旧 artifact、历史 docs 和 provenance 的 AFC 保留且 hash 不变。

### 10.3 Contracts

- 六个 root contract 均有 schema name/version；
- Pydantic model、JSON Schema 和 fixture 一致；
- canonical round trip byte-exact；
- 未知字段、未知 major、invalid invariant、hash mismatch 都安全失败；
- public contracts 不依赖 FastAPI、SQLite、LangGraph、DeepSeek 或 SupportLab；
- compatibility matrix 与 migration policy 文档完整。

### 10.4 Architecture

- 依赖方向符合第 5.2 节；
- verification 不再是 review 内部实现细节；
- SQLite、LangGraph、DeepSeek 位于 Adapter 边界；
- labs/evaluation 不被 production core import；
- review 的纯状态转换可不启动数据库、网络或 LangGraph 独立测试。

### 10.5 Behavior regression

- 全部测试通过；
- 总覆盖率不低于 93%；
- Ruff 与 strict mypy 通过；
- Phase 1 dataset regeneration 与旧 manifest 一致；
- Phase 3 review dataset regeneration 与三个旧 hash 一致；
- 两次 deterministic diagnosis/review evaluation byte-exact；
- 36-candidate gate 仍满足 valid pass 1.0、hard defect recall 1.0、unsupported scope detection 1.0、operational error 0.0；
- 默认离线路径 provider calls/tokens 为 0。

### 10.6 Persistence、Docker 与安全

- SQLite schema 初始化、CAS、lease、幂等、崩溃恢复和 20-process 稳定性通过；
- 一次 revision 上限与五类有序审计事件语义不回退；
- API/CLI terminal GET 在容器重启前后 byte-identical；
- 容器 UID/GID 保持 `10001:10001`，数据目录可写且 ownership 正确；
- secret sentinel 不进入异常、数据库、event、API、CLI、manifest 或 artifact bundle；
- `.env`、cache、generated live reports 不进入 Docker context；
- 验收结束清理 isolated container、network 与 named volume。

### 10.7 Research reproducibility

- 每个新 evaluator 输出关联 Artifact Manifest；
- manifest 覆盖 code、dataset、contracts、config、prompt、model、usage、cost、runtime 和 outputs；
- 一个 reference offline bundle 可在 clean environment 重跑；
- provider view 与 evaluator gold view 分离并有 sentinel test；
- Phase 4 不产生任何新的论文有效性 claim；
- claim–evidence ledger 更新并明确后续实验缺口。

## 11. 失败处理与回滚原则

1. **集成失败**：保留原 feature branches，停止后续 rename，先恢复可验证 main。
2. **rename 导致行为漂移**：在 rename batch 内修复，不进入 contract extraction。
3. **contract 无法保持语义兼容**：记录明确 breaking point，提出 major-version 决策，不做 silent coercion。
4. **结构迁移改变 verdict/revision/recovery**：视为 Phase 4 缺陷，不接受“新行为更合理”作为理由。
5. **旧 artifact hash 变化**：立即失败，恢复旧 bytes，通过新 manifest 外部引用。
6. **测试或 build 失败**：停止当前 batch，不继续叠加后续迁移。
7. **付费 API 意外被调用**：立即停止，记录调用来源与费用，修复 offline-default gate 后重跑。
8. **密钥或敏感内容泄漏**：阻断发布，轮换受影响凭据，清理未公开 artifact，并补充回归测试。

禁止使用破坏历史的 `reset --hard`、强推或批量删除来“解决”迁移问题。任何外部 repository rename、PyPI/container registry 发布都需要用户单独批准。

## 12. 交付物清单

Phase 4 完成时至少应存在：

```text
docs/superpowers/specs/2026-07-18-phase4-research-foundation-design.md
docs/superpowers/plans/2026-07-18-phase4-research-foundation.md
docs/handoffs/2026-07-18-phase4-research-foundation-handoff.md
docs/contracts/catalog.md
docs/architecture/adr-002-contract-versioning.md
docs/architecture/adr-003-core-adapter-boundaries.md
docs/migrations/afc-to-spanvouch.md
docs/research/reproducibility.md
docs/evaluation/phase4-research-foundation.md
schemas/v1/*.schema.json
tests/contracts/fixtures/v1/*
evals/reports/reference/phase4-offline-bundle/*
```

实施计划文件由编码线程根据本规格生成；本设计线程不编写实现代码。

## 13. 时间与审查节奏

预计纯实施工作量为 7–10 个工作日：

| 批次 | 预计 |
|---|---:|
| Batch 0 集成与冻结 | 0.5–1 天 |
| Batch 1 hard rename | 1–2 天 |
| Batch 2 contracts | 2–3 天 |
| Batch 3 structure/adapters | 2–3 天 |
| Batch 4 artifacts | 1–2 天 |
| Batch 5 验收与文档 | 1 天 |

时间是排期假设，不是质量门。每个 batch 都应形成独立可验证提交，并在继续前完成一次需求符合性审查和一次实现质量审查。

## 14. Reviewer-facing 自审

### Contribution

- Phase 4 本身是否被错误宣传为论文创新？答案必须是否。
- Contract 是否只是换名 DTO？它必须表达可寻址证据、版本、完整性和 provenance，而不是普通 JSON schema 包装。

### Writing clarity

- SpanVouch、IVAD、AFC 三个名称是否角色稳定？
- “独立”是否始终表示待实验测量的受控失效隔离，而非无证据宣称统计独立？

### Experimental strength

- Phase 3 的 20 traces / 36 candidates 是否只被称为工程回归集？
- 后续每个主要 claim 是否已经映射到强基线、消融和 OOD 实验？

### Evaluation completeness

- Artifact Manifest 能否复现数据、模型、prompt、配置和结果？
- label leakage、near-duplicate split 和 provider drift 是否有记录与自动化防线？

### Method soundness

- core 是否真正独立于具体 framework/provider/storage？
- contract 是否为 Phase 5–8 留出扩展空间，又没有提前创建空模块？
- 风险保证措辞是否严格受校准假设和实验结果约束？

## 15. 最终 Definition of Done

只有以下全部成立，Phase 4 才能标记完成：

1. Phase 2、Phase 3 顺序集成并建立 Phase 3 frozen baseline；
2. SpanVouch hard rename 完成，无运行时 AFC 兼容层；
3. 六类 Contract v1、schema、fixtures、catalog 和版本 ADR 完整；
4. verification、review、adapters 的边界满足依赖规则；
5. Artifact Manifest、reference bundle 和 label-leakage gate 可运行；
6. Phase 3 全量 behavior、evaluation、SQLite、Docker 和 security gates 无回退；
7. 总覆盖率不低于 93%，lint、type check 和 tests 全通过；
8. 旧 AFC frozen artifacts byte-identical 且 provenance 真实；
9. Phase 4 验收报告列出 exact commit、命令、hash、结果和已知限制；
10. 没有实现 Phase 5–8 的业务能力，也没有产生超出证据的论文 claim。

完成 Phase 4 后，项目才进入 Phase 5 Multi-Framework Labs；不得以“目录迁移大致完成”提前开启共享契约上的并行开发。
