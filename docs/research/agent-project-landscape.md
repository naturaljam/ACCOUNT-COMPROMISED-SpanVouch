# 面向 Agent/LLM 应用工程求职的项目机会调研

> 调研时间：2026-07-15。范围限定为官方文档、官方仓库、论文原文和官方工程博客。文中的“判断/建议”是基于这些来源的工程推断，不是来源方的原话。

## 结论先行

如果目标是用 4–6 周做出能写入简历的 Agent 项目，竞争力不来自“有几个 Agent、接了多少 MCP、用了哪一个框架”，而来自一个可验证的闭环：**真实任务 → 有边界的自主决策 → 可恢复执行 → 高风险动作审批 → 结构化轨迹 → 故障定位 → 生产失败回流评测集 → 版本对比与回归门禁**。

主流框架已经把编排、持久状态、HITL 和 tracing 做成基础能力；Langfuse 甚至把“生产轨迹 → 失败分析 → 数据集 → 实验 → 改进”列为 2026 产品主线。因此，单做“多 Agent + RAG + MCP + Dashboard”已经难以形成差异。更可取的是：选一个窄业务闭环，把**证据链、权限边界、故障诊断和持续评测**做到可演示、可度量、可部署。

## 1. 生产级 Agent 的共同架构

### 1.1 编排不是自由聊天，而是可控状态机

- LangGraph 把自身定位为长运行、有状态 Agent 的低层编排运行时，提供 durable execution、streaming、HITL、persistence 和 memory；官方建议在需要“确定性流程 + Agent 决策”、重定制与延迟控制时使用它。[LangGraph reference](https://langchain-ai.github.io/langgraph/reference/)
- LangGraph 的 interrupt 会把图状态写入 checkpointer，依赖 `thread_id` 恢复，并明确要求生产环境使用持久化 checkpointer。[LangGraph interrupts](https://langchain-ai.github.io/langgraph/how-tos/human_in_the_loop/breakpoints/)
- AutoGen 将 AgentChat 定位为对话式单/多 Agent 层，将 Core 定位为可扩展的事件驱动多 Agent 运行时；Core 支持确定性与动态业务流程、分布式 Agent、Docker code executor 和 MCP workbench。[AutoGen overview](https://microsoft.github.io/autogen/stable/index.html)
- AutoGen 的 GraphFlow 能表达并行 fan-out、条件分支、join 和有退出条件的循环，但官方仍标为 experimental，callable edge condition 还不能序列化。[AutoGen GraphFlow API](https://microsoft.github.io/autogen/stable/reference/python/autogen_agentchat.teams.html)

**工程推断：**生产项目应让代码/图控制生命周期、重试、超时、预算和状态；LLM 只负责需要语义判断的节点。多 Agent 是角色隔离或并行 I/O 的手段，不应成为项目目标。

### 1.2 状态、审批和恢复必须是一等能力

- OpenAI Agents SDK 的 HITL 会在敏感工具调用前产生 interruption；`RunState` 可序列化后长期保存，审批/拒绝后从原运行恢复，而且审批能穿透 handoff 和 nested agent-as-tool。[OpenAI Agents SDK HITL](https://openai.github.io/openai-agents-python/human_in_the_loop/)
- 同一文档提醒：长期挂起的审批应额外存 Agent 定义/SDK 版本，避免恢复时模型、prompt 或工具定义已经变化；序列化状态也可能包含应用上下文和运行元数据，不能随意放秘密。
- AutoGen 提供可组合 termination conditions，避免多 Agent 无限对话，并支持 team/agent state 的保存与加载。[AutoGen termination](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/termination.html)

**工程推断：**简历项目至少需要展示一次“服务重启后仍能恢复待审批任务”，以及幂等键、重试预算、最大步数、超时与补偿路径。

### 1.3 Trace 不是日志美化，而是评测和故障诊断的数据模型

- OpenAI Agents SDK 默认追踪 agent run、LLM generation、tool call、handoff、guardrail 和自定义 span，并允许自定义 trace processor；但 Zero Data Retention 场景不可用其托管 tracing。[OpenAI Agents SDK tracing](https://openai.github.io/openai-agents-python/tracing/)
- OpenTelemetry GenAI 语义约定正在标准化 `invoke_agent`、LLM 调用和 `execute_tool` 等 span；官方 Python contrib 已提供 OpenAI Agents SDK 的 instrumentation。[OTel GenAI releases](https://github.com/open-telemetry/semantic-conventions/releases)、[OTel OpenAI Agents instrumentation](https://github.com/open-telemetry/opentelemetry-python-contrib/blob/main/instrumentation-genai/opentelemetry-instrumentation-openai-agents-v2/README.rst)
- OpenInference 是构建在 OpenTelemetry 上的 AI 观测语义规范，覆盖 LLM、Agent、tool、retrieval 等工作负载。[OpenInference specification](https://arize-ai.github.io/openinference/spec/)

**能力边界：**OTel/OpenInference 解决“如何记录和交换轨迹”，不自动告诉你哪一步导致失败，也不替代领域级正确性评测。它们适合作为项目的可移植 trace 层，而不是项目本身。

### 1.4 评测必须覆盖结果、单步和完整轨迹

- LangSmith 官方把 Agent 评测分为 final response、single step 和 trajectory；完整轨迹可以比较精确序列、允许多条正确路径的集合/子序列，或用 judge 检查工具参数与行为。[Agent evaluation approaches](https://docs.langchain.com/langsmith/evaluation-approaches)
- LangSmith 推荐离线评测用于版本比较和回归，在线评测用于生产质量/安全监控，并把失败生产轨迹加入数据集形成反馈循环。[LangSmith evaluation](https://docs.langchain.com/langsmith/evaluation)
- Langfuse 的 2026 roadmap 明确将产品定位为框架中立的持续改进层：track、understand、evaluate、improve；重点工作包括 low-score 分析、failure clustering、生产数据刷新测试集、合成数据和自动触发实验。[Langfuse roadmap](https://langfuse.com/docs/roadmap)

**工程推断：**只报告 RAG Recall 或 LLM-as-Judge 总分不够。项目应同时报告任务成功率、正确工具及参数、轨迹约束违规、高风险动作误放行率、恢复成功率、p50/p95 延迟、token/任务成本，并给出单 Agent/无诊断闭环等消融基线。

### 1.5 MCP 只统一工具接入，不自动解决工具安全

- MCP 远程授权建立在 OAuth 2.1、Protected Resource Metadata、audience/resource binding 和 PKCE 上；官方建议使用短期 token、逐工具/能力最小权限 scope、HTTPS 和安全 token 存储。[MCP authorization](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)、[MCP authorization tutorial](https://modelcontextprotocol.io/docs/tutorials/security/authorization)
- MCP 工具规范建议始终保留能拒绝工具调用的人类环节，并要求客户端把来自非可信服务器的 tool annotations 当作不可信信息。[MCP tools](https://modelcontextprotocol.io/specification/draft/server/tools)
- MCP 安全指南指出一键安装本地 server 等同潜在代码执行，必须展示完整命令并显式审批，还应警示 `sudo`、删除、网络和敏感路径访问。[MCP security best practices](https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices)

**工程推断：**“接入 N 个 MCP server”不是生产能力；按用户/租户/工具/参数做授权、风险分级、审批、审计和凭据隔离，才是可讲的工程闭环。

## 2. 代表性框架与平台的能力边界

| 组件 | 最值得借鉴 | 不应误判为已经解决 |
|---|---|---|
| LangGraph | 显式状态图、checkpoint、interrupt、可恢复长任务、确定性与 Agentic 节点混合 | 领域策略、权限模型、正确性数据集、自动根因诊断 |
| OpenAI Agents SDK | 轻量 agent loop、tool/handoff/guardrail、可序列化 HITL、内置 trace | 任意后端的统一 durable workflow；ZDR 下托管 tracing 不可用 |
| AutoGen | 事件驱动 Core、AgentChat teams、termination、分布式和代码执行扩展 | GraphFlow 尚属 experimental；复杂 team 不等于更高任务成功率 |
| Langfuse/LangSmith | 生产 trace、离线/在线 eval、数据集、实验与人类标注闭环 | 业务真值、领域因果诊断、你的 Agent runtime；Langfuse 明确保持 execution-neutral |
| OTel/OpenInference | 跨框架的 trace schema 与后端可移植性 | 自动评测、失败根因和修复策略 |
| MCP | 工具发现/调用及标准化远程授权 | 自动可信、参数级权限、prompt-injection 防护、默认安全审批 |

## 3. Agent 故障诊断与 AI-SRE：已有方案说明赛道边界

### AgentRx：从“看 trace”推进到“定位关键失败步”

Microsoft AgentRx 将原始日志归一化为 trajectory IR，生成静态/动态 invariants，逐步检查约束，再由 judge 定位 critical step 并归入 10 类失败 taxonomy；官方仓库报告其在 Tau-bench、Flash incident management 和 Magentic-One 轨迹上优于基线。[AgentRx repository](https://github.com/microsoft/AgentRx)、[Microsoft Research introduction](https://www.microsoft.com/en-us/research/blog/systematic-debugging-for-ai-agents-introducing-the-agentrx-framework/)

**可借鉴模式：**结构化 IR、规则/约束先检查、LLM 后归因、每条诊断附证据。**不宜直接复制：**做一个通用 trace viewer 或把同一 pipeline 换皮，差异不足。

### OpenRCA：有公开真值，但重、难且不能只看最终答案

OpenRCA 是 ICLR 2025 的软件运行故障 RCA benchmark，要求从 KPI、依赖 trace graph 和日志中识别发生时间、组件和原因；其 RCA-agent 用 Python 按需取数，避免把所有遥测塞进上下文。官方仓库建议至少 80GB 存储和 32GB 内存，个人项目直接全量复现成本较高。[OpenRCA repository](https://github.com/microsoft/OpenRCA)

OpenRCA 2.0 进一步指出 outcome-only 标签掩盖了“猜中服务但没有正确因果路径”的问题，并引入逐步 causal propagation 标注。[OpenRCA 2.0 paper](https://arxiv.org/abs/2606.27154)

**可借鉴模式：**在小型可控微服务上做 fault injection，保留真实 cause → propagation → symptom 真值，再评价证据链，而非只看最终 root cause 字符串。

### IncidentFox：通用“AI SRE 平台”已经较拥挤

IncidentFox 官方仓库已经覆盖 Slack/Teams/Chat 入口、告警自动调查、日志抽样、拓扑/爆炸半径、专用 Kubernetes/AWS/metrics/code agents、45+ integrations、自托管和 Helm；生产安全层还包含 gVisor sandbox 与 credential proxy。[IncidentFox repository](https://github.com/incidentfox/incidentfox)

**同质化判断：**再做一个“多 Agent 调 Prometheus/Grafana/K8s 找 root cause”的通用平台，会直接与成熟 OSS 正面重叠。个人项目必须收窄到新的可测问题，例如“变更导致的故障证据链”“Agent 自身失败诊断”或“从失败轨迹自动生成回归测试”，而不是拼接更多 integrations。

## 4. 常见作品集的同质化问题

以下是基于官方 quickstart/平台边界的判断：

1. **角色扮演式多 Agent**：planner/researcher/writer/critic 互聊，没有权限差异、并行收益、独立状态或消融实验。框架本身已提供 group chat、handoff 和 graph，搭起来不等于工程创新。
2. **RAG + MCP 数量竞赛**：只展示检索和工具调用，没有逐工具 scope、审批、幂等、审计、错误恢复和注入测试。MCP 官方明确说工具可形成任意代码执行路径，默认不能信任 annotations。
3. **只做 trace dashboard**：Langfuse、LangSmith、OpenAI SDK 以及 OTel 生态已经覆盖 trace、cost、latency、eval；没有故障定位与回归闭环的 dashboard 很难证明价值。
4. **通用 AI-SRE**：IncidentFox 已经覆盖大部分演示卖点，OpenRCA 也提供 benchmark；没有更窄真值和证据路径的复刻会显得同质。
5. **只报自造高分**：缺少冻结数据集、基线、失败案例、重复运行、置信区间和版本门禁；尤其不应把 LLM-as-Judge 当唯一真值。

## 5. 4–6 周个人可交付的候选机会

### 机会 A：Agent Failure Clinic——轨迹诊断到回归测试的闭环

**用户与痛点：**Agent 开发者看到一次失败 trace，却不知道关键失败步，也无法系统地把失败转成测试。

**窄闭环：**导入一种规范轨迹（先支持 OpenAI Agents SDK 或 LangGraph）→ 转换为 OTel/OpenInference 风格 IR → 规则检查 tool schema/权限/计划约束/错误传播 → LLM 归因并附证据 → 人工确认 → 自动生成最小回归 case → 对新 prompt/model 运行实验并给出 pass/fail gate。

**差异化证据：**AgentRx 证明“invariant + judge”能定位失败；Langfuse roadmap 证明 production-to-dataset 与 failure clustering 是明确需求。项目应避开做通用观测平台，聚焦“单次失败 → 可复现测试 → 版本门禁”。

**4–6 周可行性：高。**只支持一个 trace adapter、6–8 类失败、一个可视化报告和 50–100 条带故障注入的轨迹即可。风险是与 Langfuse roadmap 重叠；应强调本地优先、开放 IR、可插拔规则和可运行 regression artifact。

### 机会 B：Change-Causal Investigator——面向发布变更的证据型故障调查 Agent

**用户与痛点：**发布后报警时，工程师需要把 deploy/commit、服务拓扑、metrics/logs/traces 串成可验证的因果链。

**窄闭环：**在 OpenTelemetry Demo 或小型自建微服务上注入 8–12 类变更故障；Agent 只读查询 deployment diff、trace、metric、log；输出 `change → propagation → symptom` 证据图、替代假设和置信度；高风险回滚只生成计划并等待审批，不直接执行。

**差异化证据：**OpenRCA 2.0 强调逐步因果路径；IncidentFox 说明通用 AI-SRE 已拥挤。把问题限制为“change-induced incidents + causality ground truth”能保留业务价值又避免做完整 SRE 平台。

**4–6 周可行性：中高。**必须限制为 3–5 个服务、一个 telemetry stack、8–12 个故障脚本；不复现完整 OpenRCA。可量化 root-cause accuracy、causal-path precision/recall、证据引用正确率、查询成本与 time-to-diagnosis。

### 机会 C：MCP Action Firewall——带审批、最小权限和回放评测的执行型 Agent

**用户与痛点：**Agent 一旦能发邮件、改工单、部署或写数据库，工具级权限和 prompt-injection 风险会超过普通聊天应用。

**窄闭环：**统一接入 2–3 个 MCP server；建立 per-user/per-tool/per-argument policy、OAuth scope 映射、risk score、审批和短期授权；在隔离 sandbox 中执行；将每次决策和工具结果写成标准 trace；提供恶意 tool description、间接 prompt injection、越权参数、token audience 错配和重放攻击测试集。

**差异化证据：**MCP 官方明确要求最小权限、token audience binding、显式工具审批并把 annotations 视为不可信。多数作品只展示“能调用工具”，这个项目展示“如何安全地调用工具”。

**4–6 周可行性：高。**不要实现完整 OAuth server；使用现成 IdP/库，聚焦 policy enforcement point、审批 UI、审计、20–40 条 adversarial eval。为了更像应用工程，可用“发布变更审批”作为具体前台场景，而不是只做网关 SDK。

### 机会 D：Production-to-Eval Curator——生产失败聚类与黄金集维护 Agent

**用户与痛点：**线上 trace 数量大，人工无法持续找到新失败模式、去重并维护测试集。

**窄闭环：**从 Langfuse/OTel 导入低分或异常轨迹 → 隐私清洗 → 基于轨迹结构和语义聚类 → 选代表 case → Agent 提议 failure taxonomy、expected constraints 和 evaluator → 人工确认 → 写入版本化数据集 → 自动跑基线/候选版本并生成变更报告。

**差异化证据：**完全对应 Langfuse 2026 的 low-score analysis、failure clustering、production-to-dataset refresh 和 experiment triggering；因此市场需求证据强，但平台也正在建设同类能力。

**4–6 周可行性：中高。**适合做 Langfuse/OTel 之上的轻量开源工具，不做新 observability backend。核心难点是隐私清洗、聚类质量评测和人工确认 UX。与机会 A 相比更偏数据运营，技术面试的“Agent 决策”故事略弱。

### 机会 E：Evidence-Gated Operations Agent——有证据门槛的运维/发布执行闭环

**用户与痛点：**普通 ops copilot 会生成建议，但缺少“执行前必须满足哪些证据”的确定性门槛。

**窄闭环：**Agent 收集 CI、变更、依赖和健康指标 → 生成计划 → deterministic policy 检查必需证据 → critic 只审核证据与规则，不泛泛点评文本 → 人工批准 → 执行一个可逆动作 → 观察结果 → 成功关闭或补偿回滚。

**差异化证据：**结合 LangGraph/OpenAI SDK 的 durable HITL、MCP 最小权限与 AgentRx invariant 思路；重点不是多 Agent，而是“evidence contract + reversible action + resume/replay”。

**4–6 周可行性：高。**只支持一种动作（如 staging deployment 或 feature flag）和一种补偿路径。指标可包括 evidence completeness、unsafe-action block rate、false block rate、resume success、rollback success 和端到端成本。

## 6. 候选机会对比（供后续选型，不代替最终决策）

| 方向 | Agent/LLM 应用工程信号 | 差异化 | 评测可得性 | 4–6 周风险 | 主要重叠 |
|---|---:|---:|---:|---:|---|
| A 轨迹诊断→回归 | 很强：tool/trace/eval/HITL/CI | 高 | 高，可人工注入轨迹故障 | 低–中 | AgentRx、Langfuse roadmap |
| B 变更因果调查 | 很强：RAG/tool/规划/证据/可观测 | 高（限定 change causality 后） | 中高，可 fault injection | 中 | IncidentFox、OpenRCA |
| C MCP Action Firewall | 强：工具、授权、安全、HITL、审计 | 高 | 高，可构造攻击集 | 低–中 | MCP gateway/企业安全产品 |
| D 生产失败策展 | 强：trace、聚类、数据集、实验 | 中 | 中，需要定义聚类真值 | 中 | Langfuse 正在重点建设 |
| E 证据门控执行 Agent | 很强：durable workflow、policy、动作闭环 | 高 | 高，规则与 sandbox 可控 | 低–中 | 通用 workflow/ops copilot |

## 7. 无论选哪一个，最终交付应包含

- 一个可复现的真实任务环境，而非纯 prompt demo；
- 明确的状态图、失败状态、最大步数、超时、重试、幂等与补偿；
- 至少一个会产生真实副作用但被审批/沙箱约束的工具；
- OTel/OpenInference 风格 trace，能关联 agent、LLM、retrieval、tool、approval 和业务结果；
- 冻结数据集、单 Agent/无安全层等基线、组件级与端到端 eval；
- 故障注入与失败案例报告，而不是只展示成功视频；
- Docker Compose 一键启动、API/worker/database/observability 分层、README 架构图；
- 一条可验证的简历表述：问题规模、基线、改进、可靠性、成本/延迟和安全边界都有证据。

## 参考来源索引

- [LangGraph reference](https://langchain-ai.github.io/langgraph/reference/)
- [OpenAI Agents SDK HITL](https://openai.github.io/openai-agents-python/human_in_the_loop/)
- [OpenAI Agents SDK tracing](https://openai.github.io/openai-agents-python/tracing/)
- [AutoGen official docs](https://microsoft.github.io/autogen/stable/index.html)
- [Langfuse 2026 roadmap](https://langfuse.com/docs/roadmap)
- [LangSmith agent evaluation](https://docs.langchain.com/langsmith/evaluation-approaches)
- [OpenTelemetry semantic conventions](https://opentelemetry.io/docs/specs/semconv/)
- [OpenInference specification](https://arize-ai.github.io/openinference/spec/)
- [MCP security best practices](https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices)
- [Microsoft AgentRx](https://github.com/microsoft/AgentRx)
- [Microsoft OpenRCA](https://github.com/microsoft/OpenRCA)
- [IncidentFox](https://github.com/incidentfox/incidentfox)
