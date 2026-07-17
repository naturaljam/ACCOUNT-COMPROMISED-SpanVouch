# Agent Failure Clinic Phase 3：独立验证与人工复核工作流设计

更新日期：2026-07-17

状态：已完成分段设计确认，待用户书面复核

## 1. 摘要

Phase 3 在 Phase 2 的证据化诊断能力之上，完成“诊断 → 独立验证 → 最多一次补证 → 人工确认”的可恢复纵向闭环。

系统新增两个职责隔离的 Verifier：

- `EvidenceVerifier` 使用确定性策略检查 evidence、critical span、scope 和 invariant 冲突；
- `SemanticVerifier` 使用独立 DeepSeek 上下文评价因果链是否被证据支持，以及是否存在明显替代假设。

Verifier 不能直接修改诊断，也不能决定发布。它只能生成结构化 verdict、finding 和 `EvidenceGap`。协调器最多允许原 Diagnosis Agent 修订一次，之后必须进入人工 `confirm / correct / reject`。所有版本、验证结果、状态转换和人工决定写入 SQLite，API 进程重启后可以恢复。

Phase 3 采用 API + CLI，不实现前端，不引入 Redis、Celery 或 PostgreSQL。

## 2. 背景与 Phase 2 证据

Phase 2 已完成规则诊断器、独立 DeepSeek 诊断器、证据 selector、本地 evidence 回填、统一评测和 Diagnosis API。

20 条真实 DeepSeek 对照实验的主要结果是：

- 支持集准确率：`0.857`；
- critical-span Top-1：`0.800`；
- evidence-selector validity：`1.000`；
- gold-evidence hit：`1.000`；
- clean false-positive rate：`0.000`；
- unsupported abstain rate：`0.000`；
- operational-error rate：`0.000`。

这说明 DeepSeek 能稳定生成结构化、可解析的证据引用，但缺少可靠的 scope control：6 条范围外故障全部被强制归入支持类型或 `no_failure`。另外，两条 policy violation 被判为 invalid argument，两条 loop case 的 critical span 选中了 root，而不是最后一个重复工具 span。

Phase 3 不以改 prompt 的方式掩盖这些失败，而是建立独立审核边界，并把分歧交给受限补证和人工裁决。

## 3. 目标与非目标

### 3.1 目标

1. 对任意已生成的 DiagnosisReport 执行可解释、可审计的确定性验证。
2. 可选运行独立 Semantic Verifier，不共享 Diagnosis Agent 的 prompt、原始回复或隐藏推理。
3. Verifier 发现可修复证据缺口时，只允许原 Diagnosis Agent 修订一次。
4. 提供 `confirm / correct / reject` 人工动作，并持久化最终决定。
5. API 进程重启后可以从 SQLite 中恢复非终态 review case。
6. 保持离线默认：测试、默认 API 和默认 CLI 不调用外部模型。
7. 形成可量化的 verifier fixture、指标和 live 对照报告。

### 3.2 明确不做

- 不做 Review UI；
- 不做 PostgreSQL、Redis、Celery 或分布式 worker；
- 不做 RegressionCase、Repair Agent、候选 patch 或 Release Gate；
- 不做用户认证和 RBAC；
- 不自动合并、推送或创建发布 PR；
- 不保存原始 DeepSeek response、隐藏推理、Authorization header 或 API key；
- 不允许 Verifier 直接改写 DiagnosisReport；
- 不允许任何 Agent 绕过人工最终决定。

## 4. 总体架构

```text
TraceIR + DiagnosisReport
          │
          ▼
   ReviewWorkflow
          │
          ├── EvidenceVerifier（确定性）
          │     ├─ selector/value/hash 重解析
          │     ├─ claim/critical span 证据覆盖
          │     ├─ evidence budget
          │     └─ scope guard / hard invariant conflict
          │
          ├── SemanticVerifier（独立 DeepSeek，可选）
          │     ├─ 因果链支持度
          │     ├─ 替代假设
          │     └─ verified / needs_evidence / review_required
          │
          ├── 最多一次 Diagnosis revision
          │
          └── 人工 confirm / correct / reject
                    │
                    ▼
             SQLite ReviewRepository
```

### 4.1 模块边界

建议模块结构：

```text
src/afc/
  review/
    models.py                 # Review aggregate、finding、gap、decision
    protocols.py              # Verifier、reviser、repository 协议
    evidence_verifier.py      # 纯确定性策略
    semantic_verifier.py      # 严格 schema、prompt、selector 校验
    verdicts.py               # 两层 verdict 合并策略
    workflow.py               # LangGraph 有界状态机
    service.py                # 应用服务与事务边界
    sqlite_repository.py      # SQLite 实现
    schema.py                 # schema v2 初始化和兼容策略
  api/routes/
    diagnosis_reviews.py
  cli/
    review.py
  evals/
    review_labels.py
    review_metrics.py
    run_review_eval.py
```

`EvidenceVerifier` 和 verdict 合并逻辑不依赖 FastAPI、SQLite、DeepSeek 或文件系统。`SemanticVerifier` 依赖现有 `ModelProvider` 协议，不依赖具体 HTTP adapter。`ReviewWorkflow` 依赖协议，不依赖 SQLite 实现。

### 4.2 输入快照

当前 TraceRepository 是进程内存实现。若 SQLite 只保存 `trace_id` 或 fingerprint，API 重启后就无法恢复验证。因此创建 review case 时必须持久化：

- canonical `DiagnosticTraceView` JSON；
- trace view SHA-256；
- trace ID 与 run ID 绑定元数据；该元数据用于存储关联和 API 查询，不进入模型输入；
- EvidenceCatalog 可重建版本；
- 初始 DiagnosisReport canonical JSON 与 SHA-256；
- taxonomy、diagnoser、prompt/ruleset 版本。

不保存完整原始 TraceIR。恢复、correction 和 revision 只使用脱敏快照，从而同时满足可恢复性与最小数据原则。

## 5. 领域模型

### 5.1 ReviewStatus

```text
pending_verification
verifying
revision_requested
revising
awaiting_human_review
confirmed
corrected
rejected
```

`confirmed / corrected / rejected` 是终态。

### 5.2 VerificationFinding

字段：

- `code`：稳定机器码；
- `severity`：`info / warning / hard`；
- `source`：`evidence_verifier / semantic_verifier / provider`；
- `claim_indexes`；
- `span_ids`；
- `evidence_ids`；
- `message`：简短、可展示文本。

首版稳定 code：

```text
invalid_selector
evidence_value_mismatch
evidence_hash_mismatch
claim_not_grounded
critical_span_not_grounded
duplicate_reference
evidence_budget_exceeded
clean_trace_conflict
unsupported_scope
diagnosis_conflict
alternative_hypothesis
semantic_support_missing
invalid_verifier_output
provider_operational_error
```

### 5.3 EvidenceGap

`EvidenceGap` 是 Verifier 与 Diagnosis revision 之间唯一允许的反馈协议：

- `finding_code`；
- `claim_index` 或 `stage`；
- `required_evidence_kind`；
- `allowed_selectors`；
- `related_span_ids`；
- `instruction`：固定模板生成的短说明。

它不包含 Verifier 隐藏推理、自由文本长分析或 gold label。

### 5.4 VerifierReport

- `verdict`：`verified / needs_evidence / review_required`；
- `findings`；
- `evidence_gaps`；
- 可选 `alternative_failure_type`；
- `confidence`；
- provenance：verifier version、policy version、prompt hash、model；
- usage：token、latency、request ID；
- operational error metadata。

### 5.5 DiagnosisRevision

- `revision_number`，初始报告为 0；
- `previous_report_sha256`；
- `report`；
- `report_sha256`；
- `triggering_gap_ids`；
- diagnoser provenance；
- 创建时间。

Revision 只能追加，不能覆盖旧版本。

### 5.6 HumanReviewDecision

- `action`：`confirm / correct / reject`；
- `reviewer_label`；
- `reason`；
- `expected_version`；
- `correction`；
- `created_at`。

`reviewer_label` 是调用方提供的审计标签，不宣称已认证身份。认证与 RBAC 属于后续部署安全阶段。

`correct` 使用 `DiagnosisCorrectionDraft`，只允许提交 status、failure type、critical span、claims、selectors、confidence 和 abstain reason。客户端不能提交 observed value、value hash、evidence ID 或 provenance；服务端必须从保存的 DiagnosticTraceView 重新生成这些字段。

### 5.7 DiagnosisReviewCase

- case ID；
- immutable input fingerprint；
- 当前 status 与 version；
- verification mode；
- 当前 revision number；
- evidence revision count；
- 当前 deterministic/semantic verifier run ID；
- terminal human decision ID；
- created/updated time；
- 完整 workflow event 引用。

## 6. 状态机与工作流

```text
PENDING_VERIFICATION
        │
        ▼
VERIFYING
   ├─ verified ────────────────────────┐
   ├─ review_required ────────────────┤
   ├─ needs_evidence + revision=0      │
   │          ▼                        │
   │    REVISION_REQUESTED             │
   │          ▼                        │
   │       REVISING                    │
   │          ▼                        │
   │      VERIFYING（最后一次）         │
   │                                   ▼
   └─ provider operational error → AWAITING_HUMAN_REVIEW
                                       │
                         ┌─────────────┼─────────────┐
                         ▼             ▼             ▼
                    CONFIRMED      CORRECTED      REJECTED
```

### 6.1 执行顺序

1. 从 TraceRepository 读取 TraceIR。
2. 运行所选 diagnoser，生成初始 DiagnosisReport。
3. 创建 review case，并事务性保存脱敏输入快照、revision 0 和事件。
4. 将 case CAS 转换为 `verifying`。
5. 先运行 EvidenceVerifier。
6. 若存在 hard finding，跳过 Semantic Verifier；可修复且仍有 revision 额度时生成 EvidenceGap，否则进入人工复核。
7. 若 deterministic 通过且 mode 为 `hybrid`，运行 Semantic Verifier。
8. 合并 verdict。
9. 若 `needs_evidence` 且 revision count 为 0，并且 diagnoser 支持 revision，保存 gaps 后运行一次 revision。
10. 保存 revision 1，重新运行完整验证。
11. 第二轮结束后无条件进入 `awaiting_human_review`。
12. 人工决定产生终态。

### 6.2 有界性

- evidence revision 上限：1；
- 单次 DeepSeek HTTP retry：继续使用 Phase 2 provider 的最多一次有界重试；
- Semantic Verifier invalid output 不做自由修复循环，记录 `invalid_verifier_output` 并进入人工复核；
- RuleDiagnoser 不支持生成式 revision；
- 任一 Agent 无权修改重试上限、状态或人工决定。

### 6.3 崩溃恢复

每次外部模型调用前，先提交 `verifying` 或 `revising` 状态及 workflow event；调用后再提交结果。

若进程在外部调用期间崩溃，case 会保留非终态与 lease 时间。`resume` 仅能在 lease 超时后通过 CAS 重新占用。模型调用可能出现 at-least-once 计费，但不会重复写入 revision、verifier run 或人工决定。该限制在引入持久任务队列前必须明确记录。

## 7. EvidenceVerifier 策略

EvidenceVerifier 是纯确定性组件，输入为保存的 DiagnosticTraceView、EvidenceCatalog 和待审 DiagnosisReport。

### 7.1 身份与完整性

- report 绑定的 trace/run 与 review input 一致；
- report fingerprint 与保存版本一致；
- provenance 必填版本字段存在；
- selector、critical span、evidence ID 不重复。

### 7.2 Evidence 重解析

- 每个 selector 必须存在于 EvidenceCatalog；
- observed value 必须等于本地重解析值；
- value SHA-256 必须重新计算并匹配；
- claim 引用的 evidence ID 必须存在；
- diagnosed 状态下，每个 claim 至少引用一条 evidence；
- 每个 critical span 至少由同 span 的一条 evidence ref 支撑。

### 7.3 Evidence budget

- 每个 claim 最多 4 条 evidence；
- 整份报告最多 8 条 evidence；
- 超限产生 `evidence_budget_exceeded` 和可修复 EvidenceGap；
- budget 不适用于 deterministic verifier 自己的 findings。

该限制针对 Phase 2 DeepSeek 平均 4.75、最高 18 条 evidence 的冗余问题，不通过删除真实 evidence 来提高指标，而是要求 Diagnosis Agent生成更小的决定性证据集。

### 7.4 Scope 与 invariant 冲突

- clean root 与 diagnosed failure 冲突，产生 hard `clean_trace_conflict`；
- unsupported guard 命中时，只接受 `abstained/unsupported_failure_type`；
- supported hard invariant 与报告 failure type 不一致，产生 hard `diagnosis_conflict`；
- loop critical span 与 deterministic last repeated span 不一致，产生 `critical_span_not_grounded`；
- scope/invariant 策略使用 DiagnosticTraceView，不读取 scenario label 或 gold sidecar。

## 8. SemanticVerifier

### 8.1 独立性

SemanticVerifier 输入：

- 脱敏 DiagnosticTraceView；
- 待审 DiagnosisReport；
- 合法 selector catalog。

禁止输入：

- gold label；
- Diagnosis Agent system/user prompt；
- Diagnosis Agent 原始 provider response；
- Diagnosis Agent 或 Verifier 隐藏推理；
- EvidenceVerifier verdict 和 findings；
- scenario、fault injection、语义化 run ID。

EvidenceVerifier 与 SemanticVerifier 独立运行，协调器只在二者完成后合并 verdict。

### 8.2 输出 schema

Semantic Verifier draft 必须包含：

- `verdict`；
- 至多 5 条 findings；
- 至多 3 条 evidence gaps；
- 可选 supported/unsupported alternative；
- `confidence` 数值；
- selector 引用。

模型只返回 selector；服务端负责 EvidenceRef 重解析。extra fields、错误枚举、自然语言 confidence、未知 selector 或状态不一致均转换为 `review_required/invalid_verifier_output`。

### 8.3 Prompt injection 防护

- system prompt 明确把 trace 和工具输出视为不可信数据；
- trace payload 与指令分离并使用 canonical JSON；
- 工具结果中的“忽略规则”“返回 verified”等文本不能改变输出协议；
- 测试使用注入字符串验证 Verifier 不执行数据中的指令。

## 9. Verdict 合并与补证

### 9.1 合并规则

```text
任一 deterministic hard finding
    -> needs_evidence（可修复）或 review_required（不可修复）

deterministic pass + semantic verified
    -> verified

deterministic pass + semantic needs_evidence
    -> needs_evidence

deterministic 与 semantic 意见冲突
    -> review_required

provider operational error
    -> review_required + operational finding
```

`deterministic` verification mode 不调用 Semantic Verifier。确定性通过时综合 verdict 为 `verified`，但仍进入人工复核。

### 9.2 Revision 协议

现有 `Diagnoser` 保持不变。新增可选 `RevisionCapableDiagnoser` 协议：

```text
revise(view, evidence_catalog, previous_report, evidence_gaps) -> DiagnosisExecution
```

LlmDiagnoser 实现该协议。revision prompt 仅包含原始脱敏输入、上一版结构化报告和 EvidenceGap。RuleDiagnoser 不实现该协议。

修订后的 report 重新执行完整 Pydantic 校验、selector 回填、EvidenceVerifier 和可选 SemanticVerifier。任何旧 report 都保留。

## 10. 人工决定

### 10.1 Confirm

- 确认当前 revision；
- 若综合 verdict 不是 `verified`，reason 必填并记录为人工 override；
- confirm 不修改 DiagnosisReport。

### 10.2 Correct

- correction 必须是完整结构化 diagnosis draft；
- 服务端重新解析 selector 并生成 EvidenceRef；
- correction 必须通过领域 schema 和 EvidenceVerifier；
- 不重新调用 Semantic Verifier；人类修正是最终语义裁决，但仍不能绕过确定性证据约束；
- 校验失败返回 422，case 和 version 不变；
- 成功后追加 human-correction revision，并进入 `corrected`。

### 10.3 Reject

- reason 必填；
- 不生成替代诊断；
- case 进入 `rejected`。

### 10.4 并发

所有人工写操作要求 `expected_version`。Repository 使用：

```sql
UPDATE review_cases
SET status = ?, version = version + 1
WHERE case_id = ? AND version = ? AND status = 'awaiting_human_review'
```

影响行数为 0 时返回 409，不重试或覆盖另一位 reviewer 的决定。

## 11. SQLite 持久化

### 11.1 表

- `schema_metadata`：schema version；
- `review_cases`：当前状态、version、mode、revision count、lease；
- `review_inputs`：trace/run 绑定、DiagnosticTraceView canonical JSON 与 fingerprint；
- `diagnosis_revisions`：不可变报告版本；
- `verifier_runs`：deterministic/semantic 报告、usage 和 operational error；
- `human_decisions`：人工动作、reason、correction reference；
- `workflow_events`：追加式状态转换；
- `idempotency_keys`：scope、key、request fingerprint、result reference、
  `reservation_id`、`lease_expires_at`、`created_at`、`updated_at`。

### 11.2 配置

- `PRAGMA foreign_keys = ON`；
- WAL journal mode；
- busy timeout；
- 显式事务；
- canonical JSON UTF-8；
- application-generated UUID；
- UTC timestamp；
- 数据库默认路径 `.data/afc.db`；
- Docker 使用 `/data/afc.db` 和命名卷。

Phase 3 使用 schema v2 初始化器，其中 create-idempotency reservation 属于 v2 合同。初始化器必须验证已有 schema version；未知或较新版本拒绝启动，不能自动破坏性升级。Phase 3 尚未发布，因此开发期 schema v1 数据库不属于受支持的生产合同，必须删除并由初始化器重建为 v2；本阶段不提供静默 v1-to-v2 migration。Alembic 和 PostgreSQL migration 留到后续基础设施阶段。

## 12. API 设计

### 12.1 创建 review

```text
POST /v1/traces/{trace_id}/diagnosis-reviews
```

请求：

```json
{
  "diagnoser": "rules",
  "verifier": "deterministic",
  "idempotency_key": "review-request-001"
}
```

默认 `rules + deterministic`，完全离线。`deepseek` diagnoser 或 `hybrid` verifier 必须显式选择。

创建端点由服务端取得 TraceIR、运行 diagnosis、生成安全快照并启动同步 workflow。客户端不能上传任意 DiagnosisReport。

### 12.2 查询

```text
GET /v1/diagnosis-reviews/{case_id}
```

返回 case、全部 revision、VerifierReport、event timeline 和 terminal decision。不返回原始 provider body、prompt 文本或密钥。

### 12.3 恢复

```text
POST /v1/diagnosis-reviews/{case_id}/resume
```

仅允许恢复 lease 已过期的 `verifying / revising` case，或尚未开始的 pending case。正常 `awaiting_human_review` 不能通过 resume 重新消耗模型。

### 12.4 人工决定

```text
POST /v1/diagnosis-reviews/{case_id}/decisions
```

请求包含 action、expected version、reviewer label、reason 和可选 correction。

### 12.5 稳定错误

- 404：trace 或 case 不存在；
- 409：幂等冲突、stale version、非法状态转换、未过期 lease；
- 422：correction schema、selector 或证据校验失败；
- 502：永久 provider 错误；
- 503：provider 未配置或临时错误重试耗尽；
- 500：SQLite/未知内部错误，响应不暴露 SQL、路径或敏感上下文。

若 provider 错误发生在 case 创建后，必须先持久化 verifier run、workflow event 和 `awaiting_human_review` 状态，再返回包含 `case_id`、稳定 error code 和 retryable 标记的 502/503。调用方仍可 GET case 并进行人工决定。

## 13. CLI 设计

新增入口 `afc-review`，作为 HTTP API 的薄客户端：

```text
afc-review create --trace-id ... --diagnoser rules --verifier deterministic
afc-review show --case-id ...
afc-review resume --case-id ...
afc-review decide --case-id ... --action confirm --expected-version ...
```

CLI 不直接访问 SQLite。

任何会调用 DeepSeek 的组合必须带 `--allow-live-api`。没有该标志时，在读取 API key 或发起 HTTP 前退出。CLI 输出稳定 JSON，方便脚本和简历演示录制。

## 14. 幂等、安全与数据治理

### 14.1 幂等

- create key scope：`trace_id + endpoint`；
- decision key scope：`case_id + endpoint`；
- request fingerprint 使用 canonical payload SHA-256；
- 相同 key + 相同 fingerprint 返回原结果；
- 相同 key + 不同 fingerprint 返回 409；
- idempotency record 与业务写入同事务提交。

### 14.2 Secrets

- API key 仅从环境读取并使用 `SecretStr`；
- 不进入 SQLite、event、exception、CLI JSON 或测试 fixture；
- `.env` 继续被 Git 忽略；
- delivery test 扫描 tracked files 和 SQLite test artifact 的 key pattern。

### 14.3 数据最小化

- SQLite 仅存 DiagnosticTraceView，不存原始 TraceIR；
- 不存原始 model response；
- prompt 只存 version 和 SHA-256；
- observed values 仅限 Phase 2 allowlist；
- future PII scrubber 可在 view 构建边界替换，不影响 review 模型。

## 15. 评测设计

### 15.1 冻结 fixture

新增 deterministic review candidate cohort：

- 20 条 RuleDiagnoser 合法报告；
- 16 条确定性变异报告；
- 六条 unsupported trace 各生成一条 forced-classification 变异，其余五种缺陷各 2 条：
  - invalid selector；
  - evidence hash/value mismatch；
  - ungrounded claim；
  - ungrounded critical span；
  - unsupported scope forced classification；
  - supported invariant/type conflict。

fixture 使用 manifest、精确 SHA-256 和 LF。Phase 2 live DeepSeek artifact 不提交，只作为本地非 CI 对照。

### 15.2 指标

- valid report pass rate；
- hard defect recall；
- false block rate；
- unsupported scope detection rate；
- claim/critical grounding detection；
- evidence-gap precision；
- revision recovery rate；
- verifier disagreement rate；
- human override/correction/rejection distribution；
- structured-output success；
- operational-error rate；
- input/output/total token；
- p50/p95 latency；
- 可获得时的成本估算。

### 15.3 Live 对照

实现完成后，对 Phase 2 本地 `deepseek-full-20.json` 运行 EvidenceVerifier：

- 检查 6 条 unsupported scope 错误；
- 检查 2 条 policy/type conflict；
- 检查 2 条 loop critical span conflict；
- 记录 evidence budget findings；
- 不把本地 live artifact 或 provider metadata 提交到 Git。

Semantic Verifier 先运行 2 条 allowlist smoke，再运行完整对照。它不设置未经验证的分类准确率门槛，只报告结构、分歧、token、延迟和错误。

## 16. 测试策略

### 16.1 单元测试

- 每条 EvidenceVerifier policy；
- verdict 合并真值表；
- EvidenceGap 构造；
- correction 本地 evidence 回填；
- 所有状态转换和终态保护；
- revision 上限。

### 16.2 SQLite 集成测试

- schema v2 初始化和重复初始化；
- 未知 schema 拒绝；
- foreign key 与事务回滚；
- WAL/busy timeout；
- immutable revision；
- CAS version；
- idempotency；
- lease 与进程重启恢复。

### 16.3 工作流测试

使用 fake diagnoser 和 fake Semantic Verifier：

- 直接 verified；
- 一次 revision 后 verified；
- revision 后仍有 finding；
- rule report 不做生成式 revision；
- invalid verifier output；
- provider operational error；
- crash/stale lease resume；
- confirm/correct/reject；
- failed correction 不改变 case。

### 16.4 API/CLI 测试

- 404/409/422/502/503；
- 默认离线；
- live flag guard；
- stable JSON；
- API restart 后 GET/resume；
- 密钥与 provider body 不泄漏。

### 16.5 安全测试

- trace/tool output prompt injection；
- correction 伪造 observed value/hash；
- idempotency key 冲突；
- stale reviewer race；
- SQLite/日志/artifact secret scan。

### 16.6 容器测试

- API UID/GID 保持 `10001:10001`；
- `/data` 可写；
- 命名卷重启后 case 存在；
- health、create、show、decide 冒烟；
- cleanup 不删除显式持久化验收卷之前先验证数据恢复。

## 17. 硬验收

1. 20/20 合法规则报告通过确定性验证。
2. 16/16 注入缺陷被发现。
3. 6/6 unsupported forced diagnosis 被 scope guard 拦截。
4. selector 重解析与 hash 校验准确率 100%。
5. revision 次数永远不超过 1。
6. 所有 terminal case 都有完整 event chain 和 human decision。
7. stale version 与 idempotency conflict 检测率 100%。
8. SQLite 重启恢复测试全部通过。
9. 默认测试、API 和 CLI 不发起外部模型请求。
10. 现有 Phase 1/2 测试全部继续通过。
11. deterministic review evaluation 连续两次 byte-exact。
12. Docker 持久卷、非 root 身份和 API 端到端冒烟通过。
13. live Semantic Verifier 报告 schema success、verdict 分布、分歧、token、延迟和错误。

## 18. Phase 3 完成定义

Phase 3 只有在以下用户故事可以从 API 和 CLI 重复演示时才完成：

> 一条已存储 trace 经服务端诊断后，经过确定性与可选语义独立验证；若存在可修复证据缺口，原 Diagnosis Agent 最多修订一次；系统在进程重启后仍能恢复 review case；人工最终确认、结构化修正或拒绝，并得到不可变、可审计的终态记录。

## 19. 实施顺序建议

1. review 领域模型和状态转换；
2. EvidenceVerifier 与 deterministic fixture；
3. Repository 协议和 SQLite schema；
4. ReviewService 与 LangGraph workflow；
5. correction 和人工决定；
6. SemanticVerifier 与 revision 协议；
7. API；
8. CLI；
9. review evaluator 与 hard gates；
10. Docker persistence、文档和 live experiment。

每一步遵循 TDD，并保持默认测试无外部网络调用。

## 20. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| Diagnosis 与 Semantic Verifier 使用同一模型 | 同源偏差 | 独立 prompt/context；确定性证据为主；报告该限制；人工最终裁决 |
| SQLite 与最终 PostgreSQL 不同 | 后续迁移成本 | 领域 Repository 协议；SQL 隔离；不让 workflow 依赖 SQLite 细节 |
| scope rule 对 SupportLab 过拟合 | 泛化不足 | 明确项目当前只支持 SupportLab；版本化 policy；后续扩展 fixture |
| 外部调用中崩溃 | 可能重复计费 | 调用前持久状态；lease/CAS；记录 at-least-once 限制 |
| 人工 correction 绕过安全 | 产生伪证据 | 只接收 selector；服务端重建 EvidenceRef；强制 deterministic verify |
| review 模型继续膨胀 | Phase 3 延期 | 不做 UI、队列、PostgreSQL、RegressionCase 或 Release Gate |

## 21. 已确认决策

1. Phase 3 使用 API + CLI，不做前端。
2. 使用 EvidenceVerifier + 独立 DeepSeek SemanticVerifier。
3. 人工动作是 `confirm / correct / reject`。
4. correction 必须引用已有合法 selector。
5. 使用 Repository 接口 + SQLite 持久化。
6. Verifier 只返回 EvidenceGap，原 Diagnosis Agent 最多修订一次。
7. 默认 `rules + deterministic` 完全离线；DeepSeek 必须显式选择。
8. 第二轮验证后无条件进入人工复核。
9. Verifier 和人工复核不承担发布权限。
