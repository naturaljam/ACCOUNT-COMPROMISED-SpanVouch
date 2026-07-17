# IVAD：独立验证的 Agent 故障诊断研究与工程总体设计

> 状态：对话设计已批准，待书面复核
> 日期：2026-07-17
> 项目：Agent Failure Clinic（AFC）
> 方法名称：IVAD（Independently Verified Agent Diagnosis）
> 目标：3–4 个月形成完整 arXiv 会议论文初稿，并持续演进为成熟开源工程
> 目标社区：Software Engineering / AI Reliability
> 主要资源：DeepSeek 付费 API、可租用云 GPU、每月 500–1000 元预算
> 上位工程设计：`docs/superpowers/specs/2026-07-15-agent-failure-clinic-design.md`
> 前置研究：`docs/research/2026-07-17-afc-independent-verification-novelty.md`

## 1. 设计结论

AFC 从“能够输出证据引用的 Agent 故障诊断工程”升级为一项方法优先的研究计划：

> **IVAD 将 Agent 故障诊断建模为受机器可验证证据契约约束的选择性决策问题。诊断器生成结构化 claim，确定性验证器与受控隔离的语义验证器重新解析不可变轨迹证据，风险控制器只接受满足目标风险的诊断；证据不足时，系统最多执行一次分层、只读、可审计的补证，否则弃答。**

总体投入按以下比例分配：

- 70%：IVAD 方法、Conformal 风险控制、独立性实验和核心实验；
- 20%：跨领域、跨框架数据与 benchmark 适配；
- 10%：API、CLI、存储、部署和开源工程成熟度。

研究内核与产品工作流共享同一领域模型，但保持独立模块。论文不把多 Agent 编排、TraceIR、第二个 LLM judge、普通置信度阈值、数据集规模或部署设施单独包装成主要创新。

## 2. 背景与经验依据

Phase 2 的 DeepSeek 实验表明，结构化输出和 selector 合法不等于诊断可信。现有 20 条回归集上的结果包括：

- 支持范围内准确率：`0.857`；
- critical span Top-1：`0.800`；
- selector validity：`1.000`；
- gold evidence hit：`1.000`；
- evidence precision：`0.247`；
- clean false-positive rate：`0.000`；
- unsupported abstain rate：`0.000`。

这组结果只作为工程动机与回归证据，不作为论文结论。它揭示了三个核心问题：

1. 引用了真实 span，不代表引用与 claim 相关，更不代表证据充分；
2. 诊断模型可能以结构上完全合法的方式给出错误归因；
3. 对范围外输入强制给出结论会产生高风险的过度诊断。

相关工作已经覆盖轨迹归因、Trace IR、LLM 验证、abstention、conformal error attribution 和预算化补证。IVAD 的贡献必须来自完整协议及其可证伪实验，而不是组件命名。

## 3. 论文定位与贡献边界

### 3.1 核心论文命题

论文检验以下可证伪命题：

> 在固定选择性风险目标下，通过确定性不变量和具有受控失效隔离的模型通道重新解析不可变轨迹证据，能够减少无证据及错误归因的 Agent 诊断；将一次分层补证纳入完整校准策略后，可以在不超过相应风险上界的前提下恢复覆盖率。

在完成数学条件、实现验证和实验验证之前，论文只使用“经验风险控制”或“有限样本风险上界”的准确表述，不预先宣称普适 certification 或 distribution-free guarantee。

### 3.2 主要贡献

1. **Claim–Evidence Contract**：把故障类型、责任实体、关键步骤、因果关系和违反的不变量绑定到可重新解析的不可变轨迹字段。
2. **双通道验证协议**：确定性通道检查结构、身份、时序、工具 I/O 和不变量；异构语义通道只评价不能程序化判定的相关性、充分性和因果 claim。
3. **可测量的失效隔离**：把模型、provider、prompt、可见上下文、特征路径和规则通道的隔离作为实验变量，测量相关失败，而不是仅凭两个 Agent 的名称宣称独立。
4. **选择性诊断风险控制**：控制已接受诊断中的错误归因或证据不足风险，并同时报告覆盖率。
5. **校准内的一次分层补证**：只针对明确缺失的证据槽位执行一次有预算的 Level 1/2 补证，并对完整策略进行后置校准。
6. **跨领域和跨框架实证**：在 SupportLab、OpsLab、CodeLab 以及 LangGraph、AutoGen 上统一评估。

### 3.3 明确不作为主要创新

- 将轨迹转换为 TraceIR；
- 构建新的故障 taxonomy；
- 再增加一个 LLM judge；
- 让模型输出置信度后设阈值；
- 单纯使用 abstention 或 self-refine；
- 构造一个小型自建数据集；
- API、CLI、SQLite、Docker 或可视化界面；
- 后续 Repair Agent、RegressionCase 或 Release Gate。

### 3.4 首篇论文范围

首篇论文不开展人类参与者实验，也不声称 IVAD 已提高真实开发者效率。弃答时仍输出结构化复核包作为工程功能，但论文只评价其内容完整性、证据正确性和生成成本。未来若研究开发者效率，将单独完成伦理流程、功效分析和正式人类实验。

## 4. 研究问题

| RQ | 问题 | 主要测量 |
|---|---|---|
| RQ1 | IVAD 是否减少无证据和错误归因诊断？ | false accept、unsupported claim、failure/entity/span 准确率、evidence P/R/F1 |
| RQ2 | 哪些失效隔离机制能降低诊断器与验证器的相关错误？ | 条件错误率、错误相关、增量检出率、固定 coverage 下风险 |
| RQ3 | 在 `α=0.05/0.10` 的目标风险下能保留多少覆盖率？ | selective risk、coverage、AURC、coverage@risk、有限样本上界 |
| RQ4 | 分层补证能否在风险不超标时恢复覆盖率？ | coverage 增量、补证后风险、命中率、token、延迟、费用 |
| RQ5 | 结论能否跨领域、框架和模型泛化？ | domain/framework/model OOD 的分层结果与风险失效情况 |

## 5. 统一概念与领域契约

### 5.1 关键术语

- **诊断**：关于故障类型、责任实体、决定性步骤和因果关系的结构化报告。
- **证据**：可从不可变 TraceIR 通过稳定 selector 重新解析的事实，不包括模型隐藏推理。
- **支持证据**：提高某个 claim 成立可能性的轨迹事实。
- **反证**：削弱或否定某个 claim 的轨迹事实。
- **证据充分**：支持证据覆盖所有关键字段，且不存在未处置的决定性反证或时序矛盾。
- **受控失效隔离**：通过输入、模型、provider、prompt、特征或确定性逻辑隔离错误来源；它不是统计独立的先验假设。
- **接受**：系统在指定校准版本和风险目标下允许报告进入下游使用。
- **弃答**：系统拒绝输出确定诊断，并生成原因和最小复核包。

### 5.2 Claim–Evidence Contract

每份诊断至少包含：

```text
DiagnosisClaim
├── failure_type
├── responsible_entity
├── decisive_spans[]
├── causal_relation
├── violated_invariants[]
├── support_refs[]
├── counter_evidence_refs[]
├── alternatives[]
└── provenance
```

每个 `EvidenceRef` 必须绑定：

- `trace_id`、`span_id` 和稳定 `field_path`；
- 规范化值的 hash，而不是仅保存自然语言摘录；
- 事件时间、父子关系和证据可见级别；
- 支持、反证或上下文证据类型；
- 生成时使用的 schema 版本。

验证器必须从原始快照重新解析引用。不存在、越权、hash 不匹配、时序不可能或与工具结果冲突的引用属于 hard violation，语义模型不能覆盖它。

### 5.3 验证输出

两个验证通道都返回版本化 `VerifierReport`：

```text
verifier_kind + verifier_version + findings[] + evidence_gaps[]
+ channel_score + operational_status + provenance
```

确定性 findings 和语义 findings 原样保留。聚合器只派生综合状态，不重写任一通道的结果。

## 6. 系统架构与数据流

```text
LangGraph / AutoGen / public benchmark adapters
                       │
                       ▼
              Immutable TraceIR
                       │
           ┌───────────┴───────────┐
           ▼                       ▼
    Diagnostic View          Evidence Catalog
           │                       │
           └───────────┬───────────┘
                       ▼
              Diagnoser Adapter
                       │
                       ▼
            Claim–Evidence Draft
                       │
         ┌─────────────┴─────────────┐
         ▼                           ▼
Deterministic Verifier       Semantic Verifier
         │                           │
         └─────────────┬─────────────┘
                       ▼
          Conformal Risk Controller
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
        ACCEPT       ACQUIRE       ABSTAIN
                       │
          Level 1 or one Level 2 action
                       │
            one bounded diagnosis revision
                       │
     reverify + post-acquisition calibrated decision
                       │
                 ACCEPT / ABSTAIN
```

研究内核不依赖 FastAPI、SQLite、LangGraph、AutoGen 或具体模型 SDK。框架和 provider 通过 adapter 转换为版本化 Pydantic 契约，避免外部对象泄漏到算法层。

## 7. 验证协议与失效隔离

### 7.1 确定性验证通道

确定性通道负责：

- schema、selector、value hash 和 provenance 完整性；
- span 身份、父子关系和时间顺序；
- claim 关键字段的证据覆盖；
- 工具输入输出、环境状态和 policy invariant 一致性；
- evidence budget、scope guard 和 hard invariant conflict；
- 补证动作是否属于允许列表并保持只读、幂等。

hard violation 一律阻止接受，不进入“让 LLM 决定是否忽略”的路径。

### 7.2 语义验证通道

语义通道只评价：

- 引用证据是否与 claim 相关；
- 证据是否足以支持故障类型、责任实体和因果关系；
- 是否存在由同一轨迹支持的替代解释；
- 是否存在关键反证或因果方向错误。

语义验证器不读取诊断器的隐藏推理、原始模型响应、确定性 verifier 结论或 gold label。轨迹和工具文本一律作为不可信数据，不得执行其中的指令。

### 7.3 独立性实验

实验至少比较：

1. 无 verifier；
2. 诊断器自审；
3. 同模型、不同 prompt；
4. 同 provider、不同模型；
5. 跨 provider 模型；
6. 仅确定性 verifier；
7. 确定性 + 异构模型双通道；
8. verifier 可见完整 rationale 与只见结构化 claim；
9. 共享与不共享检索摘要。

报告 `P(accept | diagnosis wrong)`、增量错误发现率，以及适合二元错误变量的 phi/MCC 和 bootstrap 区间。若隔离条件未显著降低相关失败，论文使用“dual-channel”或“separated verification”，不使用未经证据支持的“independent”经验结论。

## 8. Conformal 风险控制

### 8.1 风险对象

对第 `i` 个诊断定义报告级有界损失：

```text
L_i = 1，若 failure_type、responsible_entity、decisive_span、
          causal_relation 或 evidence_sufficiency 任一关键字段错误；
L_i = 0，否则。
```

主目标是在校准假设成立时，控制已接受集合的风险不高于 `α`。主实验使用 `α ∈ {0.05, 0.10}`，置信失败概率使用 `δ=0.05`，并始终同时报告 coverage。

字段级损失作为补充分析，不能替代报告级主损失。hard deterministic violation 的样本没有资格进入接受集合。

### 8.2 非一致性信号

风险评分可以使用：

- 确定性 finding 的数量、类型和严重度；
- 支持证据与反证覆盖；
- selector、关键字段和 critical span grounding；
- 两个 verifier 的分歧；
- 诊断器与 verifier 的条件错误相关特征；
- 语义支持分数；
- trace 完整性、schema 漂移和 OOD 信号；
- 是否及如何执行过补证。

评分模型只能使用 train/dev 数据训练。测试标签、补证后的 gold 信息和人工最终裁决不能进入评分输入。

### 8.3 校准算法

主算法采用 **Selective Conformal Risk Control 的 calibration-only 路径（SCRC-I）**，把 IVAD 的报告级有界损失和冻结风险评分作为其任务实例。选择该路径是因为它不需要在部署时联合访问 calibration 与 test 样本，更符合在线诊断系统；其结论按原方法的 PAC-style 条件表述，而不改写为 exact test-conditional guarantee。

执行协议如下：

1. 在查看 calibration label 之前冻结评分函数、候选 coverage/threshold 网格、目标 `α`、`δ` 和完整补证策略。
2. 使用 SCRC-I 在 calibration 集上同时约束被选择诊断的覆盖和条件风险。
3. 对预先冻结的多个 coverage 候选和无补证/有补证两个决策层分配 `δ`，避免事后挑选最优 coverage 破坏结论。
4. 选择满足风险条件的最大 coverage；没有合法配置、接受样本量不足或风险上界过宽时全部弃答。
5. 在独立 ID test 上报告风险、coverage 和风险上界；domain/framework/model OOD 只报告经验结果。

SCRC-T 作为 exact finite-sample 敏感性对照，但其联合 calibration/test 的操作方式不作为默认部署路径；SCoRE 等面向 trusted subset 的选择性 conformal 方法作为强基线。方法附录必须给出 IVAD 损失、选择函数、同时性修正、样本量条件与所引用定理之间的逐项映射。只有这些条件与实现一致时才使用“保证”措辞；否则明确降级为经验风险控制。

### 8.4 数据切分与适用边界

数据按任务模板、环境、仓库和故障家族分组，禁止近重复变体跨越切分：

```text
train/dev → calibration → ID test → domain/framework/model OOD test
```

ID 保证依赖 calibration 与 test 的交换性。领域、框架、模型或采集策略发生漂移时，原保证不自动成立；系统标记 OOD，并使用保守弃答或专门校准器。不得把 ID 校准结论直接宣传为 OOD guarantee。

## 9. 分层补证协议

补证只能由明确的 `EvidenceGap` 触发：

- **Level 0**：使用当前可见证据，不执行新动作；
- **Level 1**：展开已经存在但被截断或隐藏的 span、snapshot 或字段；
- **Level 2**：最多重放一个白名单内、只读、幂等的工具调用；
- 无合法动作、动作失败或证据仍不足：弃答并进入人工复核。

整个运行最多执行一次 Level 1/2 补证和一次诊断修订。补证请求只描述缺失证据槽位，不接触 gold label。补证策略、预算、动作结果和后置评分是统一算法的一部分，必须在 calibration 前冻结。

无补证和补证后接受分别使用预先校准的决策层。补证路径的 calibration 样本必须完整执行冻结的 acquisition policy，并以最终诊断计算损失；两个层各自满足目标风险时，其混合接受集合的加权风险才可沿用同一目标。禁止把新证据直接插入旧阈值后仍宣称旧风险上界有效。

## 10. 数据与实验体系

### 10.1 实验矩阵

主矩阵为 `3 个领域 × 2 个框架 × 多种诊断/验证模型`：

- **SupportLab**：客服、知识检索、订单、权限和工具调用；
- **OpsLab**：告警、部署、配置、服务依赖、超时和级联故障；
- **CodeLab**：代码生成、测试、修复、仓库操作和依赖故障；
- **LangGraph + AutoGen**：每个领域至少覆盖两种框架路径；
- **DeepSeek 为主诊断模型**：同时保留规则基线、开放权重模型或不同 provider 验证条件。

优先适配至少一个公开大规模归因 benchmark 和一个完整轨迹/可重放环境。若官方实现不可运行，结果必须标记为概念复现，不得冒充官方 SOTA baseline。

### 10.2 样本单元与真值

每个样本包含：

```text
任务定义 + 环境版本 + 完整轨迹 + Agent 可见诊断视图
+ Evidence Catalog + 故障注入清单或可执行断言
+ gold failure/entity/span + 允许的分层补证动作
```

主实验真值来自受控故障注入、执行断言、状态差异和可重放结果，不使用 LLM judge 生成 gold。自然发生但无法客观复现的案例只进入补充定性分析。注入点必须通过重放或反事实恢复验证其确实导致失败，避免把“被修改的步骤”直接等同于因果根因。

### 10.3 规模与切分

- 正式目标：600–900 条基础轨迹、3,000–5,000 个受控故障实例；
- 最低论文门槛：600 条基础轨迹、3,000 个故障实例，并完成预先规定的分组切分；
- 三个领域尽量均衡，每个框架和主要故障族保留校准与测试样本；
- 现有 20 条 SupportLab 数据只作为 deterministic regression suite；
- pilot 后使用主指标方差和目标风险所需接受样本量验证规模是否充分，规模不足时优先增加校准和 ID test，而不是增加模型数量。

### 10.4 Evidence mutation suite

在不改变底层 gold failure 的前提下构造 verifier 压力测试：

- 删除真正支持 span；
- 引用不存在、越权或 hash 不匹配的 span；
- 交换相似工具调用的证据；
- 把结果伪装为原因，制造时序倒置；
- 注入诱导 verifier 的工具文本；
- 使用正确类别但错误责任实体或关键步骤；
- 使用真实但无关证据支持错误类别；
- 同时提供支持与反证；
- 截断长轨迹；
- 构造多根因、已恢复错误和无唯一归因案例。

结果必须分别评价诊断正确性、证据真实性、相关性、充分性和因果归因，不能把 citation presence 当作 evidence grounding。

### 10.5 Baselines 与消融

Baselines：

- direct LLM diagnosis，无 abstention；
- self-reported confidence threshold；
- self-consistency / majority vote；
- 同模型自审；
- 确定性 invariant checker；
- 标准 split conformal 或已有 conformal attribution 方法；
- IVAD 无补证；
- oracle evidence 上界。

主要消融：

- 去除确定性或语义通道；
- 同源与异构 verifier；
- 去除独立性特征；
- 普通阈值与 Conformal 风险控制；
- 无补证、Level 1、Level 1+2；
- 补证后沿用旧阈值与使用补证路径校准器；
- verifier 查看完整 rationale 与只查看结构化 claim。

### 10.6 指标

- selective diagnostic risk、coverage、risk–coverage curve、AURC；
- failure type、responsible entity、decisive span 和因果关系准确率；
- unsupported claim rate、false accept/reject；
- evidence precision、recall、F1 和 critical span Top-k；
- 补证命中率、恢复 coverage、弃答原因分布；
- p50/p95 延迟、token、API/GPU 成本和 operational error；
- bootstrap 置信区间和适合配对样本的显著性检验。

## 11. 工程架构

建议的逻辑模块边界如下；具体包名在后续里程碑规格中结合现有代码布局确定：

```text
ivad-core
├── trace             # 不可变 TraceIR 与 evidence catalog
├── contracts         # Claim–Evidence Contract
├── diagnosis         # 诊断器接口
├── verification      # 确定性与语义验证
├── risk              # 校准、选择性决策和风险报告
└── acquisition       # Level 0/1/2 补证策略

ivad-adapters
├── langgraph
├── autogen
├── deepseek
└── openai-compatible

ivad-labs
├── supportlab
├── opslab
└── codelab

ivad-eval
├── datasets
├── baselines
├── experiments
├── metrics
└── reports

ivad-product
├── api
├── cli
├── review
└── storage
```

模块只通过版本化契约交互。DeepSeek 响应、LangGraph state、AutoGen message 和 SQLite row 均不得成为研究内核接口。

### 11.1 现有成果映射

- Phase 2 诊断器和 Evidence Catalog 演进为 `diagnosis` 与 SupportLab 初始资产；
- Phase 3 `EvidenceVerifier` 演进为确定性验证通道；
- Phase 3 `SemanticVerifier` 保留为可选原型，后续才升级为经过失效隔离实验和校准的论文语义通道；
- Phase 3 review policy、SQLite、API 和 CLI 保留在产品层；
- 现有 20 条冻结数据保留为 CI 回归集，不计入论文主实验规模。

### 11.2 实验产物

每次运行生成不可修改的 artifact bundle：

```text
manifest + trace + diagnostic_view + diagnosis
+ verifier_outputs + calibration_version
+ acquisition_record + final_decision + metrics + cost
```

SQLite 只做产品索引；论文原始产物使用 JSONL/Parquet 等开放格式。每份结果绑定 Git commit、数据版本、schema、模型标识、prompt hash、seed、token、延迟和费用。商业 API 原始结构化响应在脱敏后冻结，不能保存密钥、Authorization header 或隐藏推理。

## 12. 错误处理与安全边界

- 非法 selector、hash 漂移和 hard invariant conflict：确定性拒绝；
- schema 漂移或 trace 不完整：标记 evidence gap，不猜测字段；
- provider 配置、超时、限流或协议错误：记录结构化 operational status，安全弃答；
- 语义 verifier 输出非法：不自动 repair，不将其当作通过；
- OOD 或校准版本失效：取消风险保证并使用保守策略；
- Level 2 动作不在白名单、不可证明只读或不幂等：禁止执行；
- 补证失败、超预算或一次后仍不足：弃答；
- 冲突 verifier 结论：保留两者原始报告，由风险控制器决定补证或弃答；
- 所有在线付费批次必须显式开启，默认测试、API 和 CLI 离线运行。

## 13. 测试与质量门槛

### 13.1 单元测试

- 契约 schema、canonical serialization 和版本兼容；
- selector 重解析、hash、身份、时序和 invariant 规则；
- verifier 聚合真值表；
- 风险评分、校准阈值、极端样本量和全部弃答；
- Level 0/1/2 状态机、预算和一次上限；
- OOD、provider failure 和 secret redaction。

### 13.2 集成测试

- LangGraph、AutoGen 到统一 TraceIR；
- 诊断—双通道验证—风险决策—补证—重验证；
- SQLite/API/CLI 的幂等、恢复和审计；
- DeepSeek fake provider 的成功、非法输出、超时和限流；
- 固定小数据集的 byte-stable 评测结果。

### 13.3 发布门槛

- CI 使用离线固定集，不依赖付费 API；
- 在线实验必须输出完整 manifest、成本和失败分布；
- 全量实验从干净环境可以复现；
- 数据卡、模型卡、许可证、限制和复现命令与论文同步；
- 简历、README 和论文只写已经运行并留有 artifact 的结果。

## 14. 开源与复现策略

项目完整开源，核心代码优先采用宽松许可证。外部 benchmark、模型权重和数据按各自许可证单独记录，不把不可再分发内容复制进仓库。

开源发布至少包含：

- 研究内核、框架 adapter 和三类实验 Lab；
- 数据生成与故障注入脚本；
- 可公开的轨迹、标签、evidence mutation 和切分 manifest；
- calibration、baseline、消融和图表生成脚本；
- 小型 CPU/offline reproduction 与完整实验说明；
- Docker 环境、锁定依赖和 CI；
- 失败案例、威胁与负结果，不只发布最佳指标。

DeepSeek 版本漂移通过模型标识、请求参数、响应 hash、运行日期和开放模型对照缓解。无法完全复现的商业 API 结果必须明确标注限制。

## 15. 预算控制

- 月度 API 与云 GPU 总预算上限：1000 元；目标区间：500–1000 元；
- 本地规则、缓存和小模型先过滤，DeepSeek 只处理必要诊断和语义验证；
- 大批次运行前先用固定小样本估算单样本费用；
- 每批次设置 token、调用次数和 wall-clock 硬上限；
- 昂贵模型只做抽样复核或关键 baseline，不进行无边界网格搜索；
- 超出月度上限时停止新的付费实验，不通过缩减日志或删除失败结果掩盖成本。

## 16. 交付路线

### 16.1 当日里程碑：完整完成 Phase 3

Phase 3 仍按已批准的独立设计与实施计划完成，范围是“诊断—确定性/可选语义验证—最多一次修订—人工复核”的可恢复产品闭环。完成标准以 Phase 3 计划的 Final Acceptance Matrix 为准，至少包括：

- 确定性 evidence、scope、budget 和 invariant 检查；
- 36-candidate 冻结评测集与离线 evaluator；
- SQLite schema、repository、CAS、lease、幂等和审计；
- LangGraph 有界工作流、崩溃恢复和一次 revision；
- 可选 DeepSeek SemanticVerifier，默认离线；
- FastAPI、HTTP-only CLI 和 Docker 持久化；
- 完整离线质量门、secret hygiene、文档与受控 live evidence。

Phase 3 的 SemanticVerifier 是工程原型，不等于论文已经证明“独立验证”，也没有 Conformal 风险保证。

### 16.2 第 1 个月：研究内核

- 冻结 TraceIR、Claim–Evidence Contract 和 experiment manifest；
- 提取 Phase 2/3 能力到明确模块边界；
- 完成 LangGraph、AutoGen adapter；
- 建立 SupportLab、OpsLab、CodeLab 最小闭环；
- 跑通无验证、自审和确定性验证等基线；
- 达到约 150 条基础轨迹、800 个故障实例。

### 16.3 第 2 个月：核心方法

- 完成异构语义验证与失效隔离测量；
- 完成 Conformal 风险控制和风险报告；
- 完成 Level 0/1/2 补证及补证后校准；
- 完成 RQ1–RQ3 初步实验；
- 累计约 300–450 条轨迹、1,500–2,500 个故障实例。

### 16.4 第 3 个月：规模化实验与论文初稿

- 扩展至 600–900 条轨迹、3,000–5,000 个故障实例；
- 完成跨领域、框架和模型 OOD；
- 完成 RQ4–RQ5、消融、成本和延迟分析；
- 从第 1 个月同步写作，第 3 个月形成完整论文初稿。

### 16.5 第 4 个月：投稿级收口

- 冻结代码、数据、环境和实验结果；
- 完成全量复现、统计分析、图表和威胁审查；
- 整理匿名 artifact、开源发布和 arXiv 版本；
- 用缓冲时间处理失败实验与必要补充消融。

## 17. 规格分解与执行顺序

本文件是研究工程总体规格，不直接生成覆盖四个月的巨型实现计划。后续依次形成独立规格和实施计划：

1. 完成现有 Phase 3 实施计划；
2. IVAD Contract、TraceIR 与 artifact foundation；
3. LangGraph/AutoGen adapters 与三类 Labs；
4. dual-channel verification 与 independence evaluation；
5. Conformal risk controller；
6. layered evidence acquisition 与 post-acquisition calibration；
7. benchmark scale-up、实验冻结和论文 artifact。

每个子项目遵循“设计—书面复核—实施计划—测试驱动实现—验收”的独立闭环。除当前已批准且已有计划的 Phase 3 外，不跨越规格门禁提前实现后续模块。

## 18. 主要投稿风险与应对

| 风险 | 应对 |
|---|---|
| 与 AgentRx / VerifyMAS 重叠 | 聚焦可机验 contract、选择性风险、失效隔离和校准补证，不把普通验证当创新 |
| “独立”定义不成立 | 作为实验变量测量相关失败；结果不支持时收缩措辞 |
| 与 conformal attribution / BCEA 重叠 | 明确风险对象是 accepted diagnosis correctness，并验证时序 Agent 证据动作空间 |
| LLM 注入、标注、评价形成真值循环 | 主真值来自执行断言、受控注入和反事实重放 |
| 注入点不是真正根因 | 要求故障重现和移除注入后的恢复验证 |
| evidence sufficiency 主观 | 拆分真实性、相关性、覆盖、反证和因果必要性 |
| cross-domain 只是换皮 | 使用不同工具环境、故障机制、框架和分组 OOD 切分 |
| verifier 全拒绝换取低风险 | 强制报告 coverage、AURC、成本并做等 coverage 比较 |
| 自适应补证破坏校准 | 冻结完整策略并使用补证后专门校准路径 |
| 商业 API 不可复现 | 冻结请求/响应 provenance，并提供开放模型对照 |
| 方法和系统两头不强 | 70% 资源优先保障方法、风险控制和实验 |

## 19. 里程碑退出条件

arXiv 初稿只有同时满足以下条件才可称为“完整论文初稿”：

- Claim–Evidence Contract 和双通道协议已实现并开源；
- Conformal 风险算法的假设、实现和验证一致；
- 分层补证被纳入校准而非事后拼接；
- 最低数据规模和分组切分完成；
- RQ1–RQ5、主要 baselines 和消融有可复现结果；
- 负结果、成本、OOD 失效和威胁均被报告；
- 从干净环境复现实验表格和主要图；
- 论文所有定量 claim 均能追溯到冻结 artifact。

未达到这些条件时，项目仍可作为成熟工程发布，但不能用工程功能替代论文证据。
