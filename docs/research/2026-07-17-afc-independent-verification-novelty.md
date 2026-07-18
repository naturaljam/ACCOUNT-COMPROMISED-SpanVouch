# AFC 独立证据验证论文方向：创新性与投稿风险评估

> 调研截止日期：2026-07-17
> 目标社区：软件工程 / AI Reliability（完整会议论文；先发 arXiv）
> 来源策略：只使用论文原文、OpenReview、作者官方代码库等一手来源。本文对“新颖”的判断是基于截至截止日可检索到的公开文献，而不是法律意义上的穷尽性检索。

## 1. 执行结论

当前中心命题是：

> 在跨领域、跨框架的 Agent 轨迹中，独立 Evidence Verifier 能降低无证据诊断和错误归因；基于风险的 abstention 提升已接受诊断的精度，最多一次补证恢复部分覆盖率，并向人工复核输出结构化证据。

**结论：问题重要，但按当前一句话表述，方法创新性偏弱。** 2026 年已经出现多个高度相邻的工作：

- **AgentRx** 已实现“约束合成 → 逐步验证 → 带证据的可审计验证日志 → LLM 归因”；
- **VerifyMAS** 已把故障归因改写为“全轨迹上的假设验证”，并以 `neutral` 表达证据不足；
- **Conformal Agent Error Attribution** 已为 Agent 错误定位提供有限样本、分布无关的覆盖保证和人工调试用连续区间；
- **HarnessFix** 已有面向 Agent 故障诊断的 Trace IR、span 级数据/控制流证据、责任定位和修复验证；
- **BCEA** 虽在视觉语言领域，但已经完整提出“证据验证 → abstain 或有界补证 → 补证后重新校准以恢复统计保证”；
- **GroundEval** 已说明最终答案或 LLM judge 可能接受“合理但没有实际取证路径”的结论，并用确定性轨迹证据替代 judge。

因此，不能把“Verifier”“evidence-grounded”“abstention”或“一次补证”中的任一单点当作首要创新。AFC 有机会形成的贡献应收紧为：

1. **可机验的诊断证据契约**：诊断结论必须引用 TraceIR 中可寻址的 span、字段和不变量；验证器重新解析原始轨迹，而不是给诊断文本再打一次主观分。
2. **可度量的独立性**：诊断器与验证器在模型、提示、特征路径或确定性检查器上形成受控隔离，并实证测量相关失效，而不是仅用两个 LLM 调用宣称“独立”。
3. **面向错误归因的选择性验证协议**：以“已接受诊断的错误风险”为控制目标，报告 risk–coverage；不满足风险预算时 abstain/转人工。
4. **校准后的一次主动补证**：只允许一次、固定预算、针对缺失证据槽位的补证；必须把补证策略纳入校准，否则选择性取证会破坏风险保证。
5. **跨单/多 Agent、跨领域、跨框架的统一实证**：不仅预测 error label，还同时评价根因 step、证据充分性、错误归因、弃权质量和人工复核效用。

若把论文改造成上述“**证据契约 + 独立失效隔离 + 风险控制 + 校准补证**”的完整协议，创新性有望达到软件工程 / AI Reliability 完整论文标准；若只是“诊断 LLM 后接一个 verifier LLM”，很可能被认为是已有组件拼装。

## 2. 最接近工作及逐项边界

### 2.1 AgentRx：Diagnosing AI Agent Failures from Execution Trajectories（2026）

**具体能力。** AgentRx 发布 115 条人工标注失败轨迹，覆盖结构化 API 工作流、事故管理和开放式 Web/文件任务；每条标注关键失败步骤和跨领域故障类别。方法会合成约束、逐步检查轨迹、生成带关联证据的可审计约束违规日志，再由 LLM judge 定位关键步骤与类别。[论文原文](https://arxiv.org/abs/2602.02475)

**与 AFC 重合。** 跨领域轨迹、证据化诊断、独立于被诊断 Agent 的检查流程、结构化可审计输出，均直接重合。这是 AFC 最危险的近邻之一。

**差异机会。** AgentRx 摘要没有提出对“诊断报告本身”的独立验收协议、选择性风险/abstention 保证或有界主动补证。AFC 必须把 verifier 的输入、证据契约、拒绝条件和风险保证形式化，并证明它不是 AgentRx 验证日志的重命名。

### 2.2 VerifyMAS：Hypothesis Verification for Failure Attribution（2026）

**具体能力。** VerifyMAS 不直接预测 agent–error 对，而是对预定义错误假设在完整多 Agent 轨迹上做 `entail / neutral / contradict` 三分类；只有被支持的错误假设才进入责任 Agent 定位。`neutral` 专门覆盖证据薄弱、不完整、含混或对最终结果影响不清的情况；其 SFT 数据也显式构造支持、反证和中性样本。[论文原文](https://arxiv.org/html/2605.17467)

**与 AFC 重合。** “先提出诊断，再对全轨迹证据进行验证”“证据不足时不强制给结论”与 AFC 的 verifier 和 abstention 高度重合。

**差异机会。** VerifyMAS 的 verifier 本身同时承担错误类型验证和 Agent 定位；AFC 可研究**诊断器—验证器的失效独立性**、可重解析 span 证据、校准的选择性风险，以及补证/人工复核协议。若 AFC 只让另一个模型输出 supported/unsupported，则新意不足。

### 2.3 HarnessFix：From Failed Trajectories to Reliable LLM Agents（2026）

**具体能力。** HarnessFix 将原始轨迹和 harness 代码编译为 HTIR：TraceStep、数据流/控制流 TraceLink、源/目标 span、实现锚点和 harness 层责任映射。诊断过程从失败症状回溯候选步骤并裁决证据，之后形成结构化诊断、范围受限的补丁和验证；在 GAIA、SWE-Bench Verified、AppWorld、Terminal-Bench 2.0 Verified 上评测。[论文原文](https://arxiv.org/html/2606.06324)

**与 AFC 重合。** TraceIR、span 级证据、根因定位、跨 benchmark 和后置验证都高度相邻。AFC 的 TraceIR 本身不能再作为论文主创新。

**差异机会。** HarnessFix 目标是定位并修复 harness 缺陷，没有报告诊断报告的选择性风险、abstention 或一次补证。AFC 应把“验证诊断是否可由原始证据重建”作为独立对象，而不是直接进入修复。

### 2.4 Conformal Agent Error Attribution（2026）

**具体能力。** 该工作用 conformal prediction 为多 Agent 决定性错误步骤输出连续预测区间，提供有限样本、分布无关的覆盖保证；可包装不同黑盒打分器，并将区间用于人工调试或回滚恢复。实验使用 Who&When 和多个受控错误注入数据集。[论文原文](https://arxiv.org/html/2605.06788)

**与 AFC 重合。** 不确定性量化、风险控制、拒绝过度确定的点归因，以及人工只检查局部轨迹，均与 AFC 的可信诊断和人工复核目标重合。

**差异机会。** 它保证“真错误步骤落入预测集合”的 coverage，不验证结构化诊断 claim 是否有充分证据，也不以 selective error among accepted diagnoses 为直接目标。AFC 可同时控制错误归因与无证据 claim，但必须与 conformal set coverage 做严格基线比较。

### 2.5 Who&When / Who&When Pro（2025/2026）

**具体能力。** 原 Who&When 从 127 个 LLM 多 Agent 系统收集失败日志，标注责任 Agent 和决定性错误步骤；报告最优方法在责任 Agent 上 53.5%，步骤定位仅 14.2%。Who&When Pro 通过“精确重放成功前缀后才注入故障”的受控流程扩展到 12,326 条失败轨迹、26 个 benchmark、15 个框架、9 类任务、3 种模态和 18 个错误模式，并人工抽检标签。[Who&When 原文](https://arxiv.org/abs/2505.00212)；[Who&When Pro 原文](https://arxiv.org/html/2607.09996)

**与 AFC 重合。** 它们已经定义核心归因任务并提供规模远超 AFC 自建 20 条轨迹的评测资产。

**差异机会。** AFC 不应再主张“首个跨域/跨框架故障归因 benchmark”。合理路线是复用其测试集，同时新增 evidence annotations、unsupported diagnosis mutations、abstention 和补证评测。

### 2.6 TraceElephant：Seeing the Whole Elephant（2026）

**具体能力。** TraceElephant 提供完整执行轨迹和可复现环境，研究完整可观测性对多 Agent 故障归因的影响；完整轨迹相对输出-only 使 Agent 级准确率提升 22%、步骤级提升 76%，运行环境还能进一步提升步骤定位。[论文原文](https://arxiv.org/html/2604.22708)

**与 AFC 重合。** 它直接支持 AFC 的前提：可信诊断依赖完整、开发者可见的轨迹证据。

**差异机会。** 完整可观测性本身不是 AFC 的新贡献。AFC 要研究的是“即使给定完整轨迹，如何独立判定诊断 claim 是否受证据支持，以及何时拒答”。

### 2.7 AgentDebug / AgentErrorBench（2025）

**具体能力。** 该工作给出覆盖 memory、reflection、planning、action 和 system operation 的错误分类，构建来自 ALFWorld、GAIA、WebShop 的失败轨迹数据，并用 AgentDebug 隔离根因、给纠错反馈，使 Agent 迭代恢复；论文报告相比强基线提升 all-correct 与 step accuracy，并提高后续任务成功率。[论文原文](https://arxiv.org/abs/2509.25370)；[作者代码库](https://github.com/ulab-uiuc/AgentDebug)

**与 AFC 重合。** 跨域根因诊断、步骤定位、结构化类别和诊断后的反馈闭环均已有。

**差异机会。** AFC 的主问题不能只是“诊断能否提高恢复率”，而应验证诊断证据的可靠性、选择性决策和独立错误发现。

### 2.8 AgenTracer（2025）

**具体能力。** AgenTracer 用反事实重放和程序化故障注入构建 TracerTraj，再以多粒度强化学习训练 8B 归因模型；目标是从长轨迹定位责任 Agent/步骤，并向现有多 Agent 系统输出可执行反馈。[论文原文](https://arxiv.org/abs/2509.03312)

**与 AFC 重合。** 自动注入、根因步骤真值、跨系统归因和反馈能力都构成直接基线。

**差异机会。** AFC 可将其输出当作“待验证诊断”，测试 verifier 是否能识别无证据或错误归因，而不是再训练一个同类 attribution model。

### 2.9 MAST / MAST-Data（2025）

**具体能力。** MAST 通过 grounded theory 分析多 Agent 系统轨迹，形成 3 大类、14 个细粒度失败模式，并发布 1,642 条标注执行轨迹；论文报告所测 7 个开源 MAS 存在较高失败率。[论文原文](https://arxiv.org/abs/2503.13657)

**与 AFC 重合。** 故障 taxonomy 和结构化诊断标签并不新。

**差异机会。** MAST 更偏经验分类，AFC 可用其 taxonomy 做外部验证，但证据契约不能只依赖封闭 taxonomy，否则难处理未知/多重根因。

### 2.10 AEGIS（2025/2026）

**具体能力。** AEGIS 通过受控错误生成构建 9,533 条、跨 6 个任务领域和 6 个 MAS 框架的轨迹，标注责任 Agent 和错误模式，并训练归因模型。[论文原文](https://arxiv.org/html/2509.14295v1)；[OpenReview](https://openreview.net/forum?id=zqcYoxXiN3)

**与 AFC 重合。** 大规模跨域/框架故障注入、归因标签和专门模型都已存在。

**差异机会。** AFC 可在 AEGIS 之上增加 claim–evidence 对、反证、证据删除/交换变异和 verifier 选择性评测，而不是重复生成相似数据。

### 2.11 GroundEval（2026）

**具体能力。** GroundEval 用确定性、时间约束和访问控制的证据评测 stateful Agent；它同时检查最终回答和产生回答的轨迹，聚焦“是否先检查再声称不存在”“是否只用当时可见证据”“是否使用正确因果机制”。其案例显示 frontier LLM judge 会高分接受没有实际检索过所依赖 artifact 的合理回答。[论文原文](https://arxiv.org/abs/2606.22737)

**与 AFC 重合。** “合理解释不等于有效证据路径”“应从工具轨迹确定性复核 claim”正是 AFC 的核心动机。

**差异机会。** GroundEval 评估 Agent 最终回答，而 AFC 验证的是事后故障诊断与责任归因；但 AFC 必须将其列为强概念基线，并证明诊断场景有新增难点。

### 2.12 BCEA：Budgeted Conformal Evidence Acquisition（2026）

**具体能力。** BCEA 对视觉语言 claim 采用回答、abstain 或在固定预算下主动获取额外视觉证据的三路决策。论文特别证明：把补证直接插入已校准过滤器会破坏统计保证；把完整补证策略纳入评分并对补证后分数重新校准，才能恢复有限样本保证并提高 coverage。[论文原文](https://arxiv.org/abs/2606.16667)

**与 AFC 重合。** “验证 → 弃权 → 有界补证恢复覆盖率”这一抽象组合已经存在，不能作为 AFC 的一般性首创。

**差异机会。** AFC 面向时序 Agent 轨迹、根因责任和工具证据，补证动作可能是读取隐藏 span、重放只读工具或请求环境状态。可新之处是 Agent debugging 的 action space、因果/时序约束和跨框架 TraceIR；统计上必须吸收 BCEA 的 post-acquisition recalibration 要求。

### 2.13 Self-Refine（2023）

**具体能力。** Self-Refine 使用同一个 LLM 生成、反馈和迭代修改，不要求训练数据或额外训练；在多个任务上展示迭代自反馈的收益。[论文原文](https://arxiv.org/abs/2303.17651)

**与 AFC 重合。** “验证失败后请求一次修改/补充”属于成熟的 test-time refinement 范式。

**差异机会。** AFC 的补证必须不是泛化的“再想一次”，而是由缺失证据槽位驱动、预算固定、可审计且重新校准；限制为一次应由成本—风险实验支持，而不是任意超参数。

### 2.14 JudgeBench 与 LLM Judge 偏差研究（2024）

**具体能力。** JudgeBench 用知识、推理、数学和代码中的客观正确性构建困难 response pair，发现包括 GPT-4o 在内的多类强 judge 接近随机；位置偏差研究在 12 个 judge、超过 10 万次评判上确认 position bias 并非随机波动。[JudgeBench 原文](https://arxiv.org/abs/2410.12784)；[位置偏差原文](https://arxiv.org/abs/2406.07791)

**与 AFC 重合。** 它们为“不应把诊断器或单一 LLM judge 的自信当真”提供动机。

**差异机会。** “再加一个 judge”并不自动带来可靠性。AFC 必须通过客观证据变异、校准和独立失效实验验证 verifier，而不能只报告和人类标签的一致率。

## 3. 哪些点不新，哪些组合仍可能新

### 3.1 已经不新或很难单独成立的点

- 从 Agent execution trajectory 做根因/责任步骤定位；
- 建立跨域、跨框架的故障 taxonomy 或注入数据集；
- 将轨迹转换为结构化 Trace IR；
- 让 LLM 依据证据输出解释或验证日志；
- 用第二个 LLM / judge 复核第一个模型；
- 使用 `neutral`、低置信度或 abstention 表达不确定；
- 用 conformal prediction 给错误步骤提供覆盖保证；
- 诊断后 self-refine、rollback 或修复；
- 在弃权前进行一次有界补证这一抽象策略；
- 为人工调试输出局部轨迹或结构化摘要。

### 3.2 可形成论文主贡献的强化组合

建议将方法命名层面从“独立 verifier”升级为一个明确协议，例如 **IVAD：Independently Verified Agent Diagnosis**，其不可缺失组成如下：

1. **Claim–Evidence Contract**
   每条诊断 claim 被拆成 `(failure_type, responsible_entity, decisive_span, causal_relation, violated_invariant)`；每个字段必须引用不可变 TraceIR span ID，并携带支持/反证类型。解析器能从原始 trace 独立重建所引内容，拒绝不存在、越权、时序不可能或与工具结果不一致的引用。

2. **Dual-channel Verification**
   一条通道做确定性检查（schema、时间、父子 span、tool I/O、policy invariant、environment state）；另一条异构模型只判断不能程序化的语义/因果 claim。最终 verdict 不直接继承诊断器的 rationale。

3. **Independence as an experimental variable**
   比较同模型同提示、同模型不同提示、不同模型、规则+模型、不同 provider、证据盲化等条件，测量 conditional error correlation 与 verifier 的增量错误发现率。只有实证隔离相关错误，才能使用“independent”。

4. **Selective diagnostic risk**
   目标不是总体 accuracy，而是控制已接受报告中的错误归因率/无证据 claim 率。输出 calibrated risk–coverage curve、AURC、coverage@risk，并在领域外/框架外单独报告。

5. **One-shot active evidence completion**
   verifier 只为明确缺失槽位请求一次补证，例如读取被截断 span、查询只读环境状态、重放幂等工具或返回“证据不可获取”。补证策略、成本和后置分数共同校准，且不能看到 gold label。

6. **Human escalation packet**
   弃权不是空结果，而是输出最小证据包：候选根因、冲突证据、缺失证据、需要人决定的问题。评测其是否减少开发者检查时间与误判，而不是只问“用户是否喜欢”。

这一组合的潜在新意不在组件，而在：**首次把 Agent 故障诊断建模为受证据契约约束、具有可测独立性的选择性决策系统，并把补证纳入风险校准。** 这一“首次”仍需投稿前再次系统检索和谨慎措辞。

## 4. 最可能的审稿拒稿理由

1. **与 AgentRx / VerifyMAS 贡献重叠。** 审稿人会问：验证日志或 hypothesis verification 已经存在，AFC 只是换一个名字吗？
2. **“独立”没有定义。** 两个同源 frontier LLM 使用相同 trace、相近 prompt，错误高度相关；模块分开不等于统计或认识论独立。
3. **与 conformal error attribution/BCEA 的不确定性贡献重叠。** 若没有正式风险目标、校准协议和 shift 分析，abstention 只是阈值技巧；若有，则必须解释和 conformal work 的区别。
4. **真值循环。** 用 LLM 注入故障、LLM 标注根因、再用 LLM verifier 评测，可能只是同类模型之间的风格一致性。
5. **注入点不等于因果根因。** 错误可能被系统恢复，或多个早期缺陷共同导致失败；单一决定性 step 的标签假设过强。
6. **证据充分性定义主观。** “引用了 span”不代表 claim 被支持；相关性、因果性、反事实必要性必须区分。
7. **跨域/跨框架只是表面多样性。** 若所有任务共享同一故障模板、同一 prompt 或同一生成模型，OOD 结论站不住。
8. **数据规模不足。** 现有 20 条 SupportLab 轨迹只能做工程 smoke test，无法支持完整会议论文和统计显著性。
9. **补证泄漏或破坏校准。** 自适应选择更多证据会改变数据分布；若未做 post-acquisition calibration，风险保证无效。
10. **Verifier 通过拒绝一切获得高精度。** 必须报告 coverage、成本和风险曲线，并与等覆盖率基线比较。
11. **人工研究过小或任务不真实。** 8–15 人可能只够 pilot；若声称提升开发者效率，需要功效分析、真实调试任务、随机化和伦理流程。
12. **商业 API 不可复现。** DeepSeek 版本漂移、隐藏系统更新和采样不确定性会威胁复现；需冻结原始响应、版本、prompt hash、成本和开源模型对照。
13. **系统论文与方法论文两头不靠。** 若既没有严格保证/新算法，也没有大规模真实部署或人因结果，SE 审稿人会视为工程集成。

## 5. 达到完整 SE / AI Reliability 论文标准必须补的实验

### 5.1 研究问题与核心指标

| RQ | 必答问题 | 至少报告的指标 |
|---|---|---|
| RQ1 | AFC 能否提高故障类型、责任实体和决定性步骤诊断？ | macro/micro F1、exact match、step distance、multi-label F1 |
| RQ2 | Verifier 是否真的减少无证据 claim 和错误归因？ | false-accept rate、false-reject rate、unsupported-claim rate、evidence precision/recall/F1 |
| RQ3 | 在给定风险预算下能保留多少诊断？ | risk–coverage、AURC、coverage@5%/10% risk、Brier/ECE、置信区间 |
| RQ4 | 一次补证能否在不破坏风险控制下恢复 coverage？ | delta coverage、post-acquisition risk、额外 tokens/延迟、请求命中率 |
| RQ5 | 结果是否跨领域、框架、模型泛化？ | in-domain、leave-one-domain-out、leave-one-framework-out、leave-one-model-out 分层结果 |
| RQ6 | “独立性”中哪个隔离机制有效？ | 条件错误相关、增量发现率、同源/异源模型和规则通道消融 |
| RQ7 | 结构化证据包是否帮助开发者？ | 人工诊断准确率、完成时间、误修率、NASA-TLX/信心校准（若做人类研究） |
| RQ8 | 工程开销和故障边界是什么？ | p50/p95 latency、tokens、API/GPU 成本、abstain 原因分布、失败案例 taxonomy |

### 5.2 数据与规模

- **不要只扩写 SupportLab。** 至少使用一个公开大规模归因 benchmark（优先 Who&When Pro / AEGIS）、一个完整轨迹可观测 benchmark（TraceElephant 或可复现实验环境）以及 AFC 自建的 SupportLab、OpsLab、ResearchLab/DataLab。
- 同时覆盖 single-agent 与 MAS、至少两种编排框架、至少三类工具环境；训练/开发/测试按任务模板和框架隔离，防止近重复泄漏。
- 增加人工校验的 challenge set，包含多根因、可恢复错误、无明确根因、证据缺失和“合理但错误”的诊断。
- 20 条现有轨迹仅保留为 deterministic regression suite。论文主实验建议至少达到**数百条人工或执行真值样本 + 数千条受控变异/注入样本**，并报告样本量/功效依据。

### 5.3 强基线

- direct LLM attribution / LLM-as-a-judge；
- AgentRx 风格的 constraint-validation pipeline；
- VerifyMAS 风格的 entail/neutral/contradict；
- AgenTracer 或其他专门 attribution model；
- Conformal Agent Error Attribution；
- 规则/确定性 invariant checker；
- self-consistency、双模型互审、ensemble/majority vote；
- oracle evidence 与 no-verifier/no-abstention/no-acquisition 上下界。

若官方实现或数据可用，应运行作者代码；无法运行时必须明确标为“概念复现”，不能把自写弱版本当作 SOTA。

### 5.4 诊断证据压力测试

构造不改变 gold failure 的诊断/证据变异，专门测 verifier，而不是只测诊断器：

- 删除真正支持 span；
- 引用不存在或越权的 span ID；
- 交换相似工具调用的证据；
- 将后发生结果伪装成先验原因；
- 加入诱导 verifier 的 prompt injection/tool text；
- 给出正确类别但错误责任 Agent/步骤；
- 给出错误类别但引用真实且无关证据；
- 同时提供支持与反证；
- 截断或压缩长轨迹；
- 制造多根因和无唯一归因案例。

主结果需分解为：诊断是否正确、证据是否真实、证据是否相关、证据是否充分、因果归因是否成立。否则“evidence-grounded”会退化为 citation presence。

### 5.5 独立性消融

至少比较：

1. 无 verifier；
2. 诊断器自检；
3. 同模型、不同 prompt verifier；
4. 同 provider、不同模型 verifier；
5. 跨 provider verifier；
6. 确定性规则 verifier；
7. 规则 + 异构 LLM 双通道；
8. verifier 看完整 rationale vs. 只看规范化 claim 和原始 trace；
9. verifier 与诊断器共享/不共享检索摘要。

报告 `P(verifier accepts | diagnosis wrong)`、双方错误的 phi/互信息或其他相关指标，以及在固定 coverage 下的错误下降。这样“独立”才是可证伪的贡献。

### 5.6 Abstention 与校准

- 在开发集校准，在 ID、domain-OOD、framework-OOD、model-OOD 测试；禁止在测试集调阈值。
- 同时报告 selective risk 与 coverage，使用 bootstrap 置信区间和配对显著性检验。
- 与 softmax/self-reported confidence、self-consistency、entropy、conformal wrapper 比较。
- 对 risk target 是否达标做正式检验；若无法提供分布无关保证，应明确写“empirical risk control”，不要暗示 certification。
- 失败时输出可操作 abstention reason，而非统一“低置信度”。

### 5.7 一次补证

- 比较 no acquisition、random acquisition、一次 targeted acquisition、多次/unbounded acquisition 和 oracle acquisition；
- 固定 token、工具调用和 wall-clock 预算；
- 只允许读取/重放安全、只读、幂等资源；
- 记录补证选择是否泄漏 gold label；
- 依照 BCEA 揭示的问题，把完整 acquisition policy 纳入 calibration，并评估 shift 下风险；
- 通过成本—覆盖率曲线说明“最多一次”是 Pareto 选择，而不是拍脑袋。

### 5.8 人工研究（建议从 pilot 升级为正式研究）

若论文中心 claim 包含“帮助人工复核”，8–15 人适合作为 pilot，不足以支撑强结论。正式研究建议：

- 先做功效分析，再决定人数（通常应高于最初设想）；
- 采用 within-subject 或混合设计，随机化轨迹和条件；
- 对照：原始轨迹、普通 LLM 诊断、AFC 已验证报告、AFC abstention packet；
- 任务：判断根因、证据充分性和下一步处置，而不是偏好打分；
- 指标：正确率、时间、误修决策、信心校准、认知负荷；
- 预注册排除规则、统计分析和主要终点，并遵守所在机构的人类参与者/伦理要求。

## 6. 建议的论文 claim 与标题

### 6.1 可防守的中心 claim

不要写：

> An independent verifier improves trustworthy agent diagnosis.

建议写成可检验版本：

> Under a fixed selective-risk target, a verifier that re-parses immutable trace evidence through deterministic invariants and a model channel with controlled failure separation reduces unsupported and misattributed diagnoses across unseen agent domains and frameworks; a post-acquisition-calibrated, one-shot evidence request recovers coverage without exceeding the target empirical risk.

中文：

> 在固定选择性风险目标下，通过确定性不变量与受控失效隔离的模型通道重新解析不可变轨迹证据，可以在未见领域和框架上减少无证据及错误归因；经补证后重新校准的一次证据请求，可在不超过目标经验风险的情况下恢复覆盖率。

若最终采用 conformal 方法且满足其假设，可将“经验风险”升级为相应的有限样本保证；在完成理论与实验前不要预先承诺 guarantee。

### 6.2 候选标题

- **Trust, but Re-Trace: Independently Verified Failure Diagnosis for LLM Agents**
- **AFC: Selective, Evidence-Contracted Failure Diagnosis for LLM Agents**
- **When Not to Blame an Agent: Risk-Controlled Verification of Trajectory-Based Diagnoses**

## 7. 创新度评分

### 当前 idea：**4.0 / 10**

理由：问题重要、工程切入合理，但独立 verifier、trajectory evidence、neutral/abstention、conformal uncertainty、bounded evidence acquisition 各自以及部分组合都已有非常接近的 2026 工作。若实现为“diagnoser + second LLM judge + confidence threshold + one retry”，很难通过完整会议论文的新颖性审查。

### 强化后 idea：**7.5 / 10**

前提是同时完成：

- 可机验、可寻址、可重解析的 claim–evidence contract；
- 确定性 + 异构语义双通道 verifier；
- 将“独立性”作为实验变量并测量相关失效；
- 对 accepted diagnoses 的风险—覆盖率进行正式校准；
- 对一次补证做 post-acquisition calibration；
- 在公开大规模 benchmark 与真实可复现环境上进行跨域/框架/模型 OOD 评测；
- 对 unsupported evidence、错误归因和因果混淆进行专门压力测试。

如再进一步给出新的选择性风险保证、对自适应补证的理论分析，或在真实开发者调试中显示显著减少误修和时间，创新性可接近 **8 / 10**。但当前文献更新极快，尤其 2026 年 5–7 月已有多篇直接近邻，因此投稿前必须再做一次滚动检索。

## 8. 对 AFC 工程路线的直接影响

1. 现有 TraceIR 和 20 条 SupportLab 轨迹应保留为工程基座与回归集，不包装成论文主贡献。
2. Phase 2 不应直接实现“DeepSeek 输出 DiagnosisReport”后就宣称研究闭环；应先冻结 `DiagnosisClaim`、`EvidenceRef`、`CounterEvidenceRef`、`VerifierVerdict`、`AbstentionReason` 和 `EvidenceRequest` 契约。
3. DeepSeek 可作为 diagnoser 主实验，但 verifier 至少要有规则通道和另一模型/provider 条件；所有响应、模型版本、prompt hash、seed、token、延迟和成本落盘。
4. 从第一天构建 evidence mutation suite 和 risk–coverage evaluator，避免后期只有分类 accuracy。
5. 优先复用公开 benchmark，再建设 AFC 独有的 evidence annotation 层；论文价值来自新的问题定义和验证协议，而不是再造一个较小的故障数据集。

## 9. 2026-07-17 补充检索：选择性 Conformal 风险控制

总体设计自检时进一步检索了“对已接受预测的条件风险做 conformal 控制”这一更精确的问题。结果表明，不能用普通 split conformal 或未经修正的阈值扫描直接支撑 IVAD 的 accepted-diagnosis risk claim。

- **Selective Conformal Risk Control（SCRC）**把选择性分类和 conformal risk control 组合成两阶段框架。SCRC-T 通过联合 calibration/test 阈值保持交换性并提供 exact finite-sample 结论；SCRC-I 只使用 calibration data，提供更适合部署的 PAC-style 结论。IVAD 应把 SCRC-I 作为默认统计骨架，把 SCRC-T 作为敏感性对照，而不是自行发明一个只在 calibration 上挑阈值的“conformal”算法。[论文原文](https://arxiv.org/abs/2512.12844)
- **Conformal Selective Prediction with General Risk Control（SCoRE）**直接面向系统决定“信任或拒绝”预测的场景，使用 conformal inference 与 e-value/hypothesis-testing 机制控制 trusted subset 的有限样本风险。它与 IVAD 的 accept/abstain 决策非常接近，必须作为强相关工作和潜在基线。[论文原文](https://arxiv.org/abs/2603.24704)
- **Conformal Risk Control for Non-Monotonic Losses**指出原始 CRC 依赖一维参数上的单调损失，并给出非单调、多参数算法的稳定性相关风险界。IVAD 的双 verifier、abstention 和 acquisition 组合可能形成非单调策略，因此必须验证单调性；若不成立，应使用支持非单调损失的方法或收缩算法族。[论文原文](https://arxiv.org/abs/2602.20151)

直接设计影响：

1. IVAD 主方法采用 SCRC-I 风格的 calibration-only 选择性风险控制，并逐项映射其假设；
2. 预先冻结 coverage/threshold 候选并处理多重选择，不能看完 calibration 结果后任意挑点；
3. 无补证与补证后决策分层校准，完整 acquisition policy 必须在 calibration 前冻结；
4. SCRC-T、SCoRE、普通 confidence threshold 和标准 conformal wrapper 进入 baseline；
5. 在理论与实现核验完成前，只写 PAC-style risk bound 或 empirical risk control，不笼统声称 distribution-free certification。
