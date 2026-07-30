<p align="center">
  <img src="assets/spanvouch-logo.png" width="220" alt="SpanVouch 标志">
</p>

<h1 align="center">SpanVouch</h1>

<p align="center"><strong>面向生产级 AI Agent 的证据化失败诊断基础设施。</strong></p>

<p align="center">
  <a href="https://github.com/naturaljam/SpanVouch/actions/workflows/ci.yml"><img src="https://github.com/naturaljam/SpanVouch/actions/workflows/ci.yml/badge.svg" alt="CI 状态"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.12-3776AB?logo=python&amp;logoColor=white" alt="Python 3.12"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-2F8552.svg" alt="MIT 许可证"></a>
  <a href="paper/IVAD.pdf"><img src="https://img.shields.io/badge/Paper-IVAD-b91c1c" alt="IVAD 论文"></a>
  <a href="https://github.com/naturaljam/SpanVouch/releases/tag/v0.2.0"><img src="https://img.shields.io/badge/Release-v0.2.0-111827" alt="v0.2.0 版本"></a>
</p>

<p align="center">
  <a href="README.md"><img src="https://img.shields.io/badge/README-English-111827?style=for-the-badge" alt="英文 README"></a>
  <a href="README.zh-CN.md"><img src="https://img.shields.io/badge/README-中文-0f766e?style=for-the-badge" alt="中文 README"></a>
</p>

SpanVouch 将 Agent 执行轨迹转化为携带证据的诊断决策。它把因果主张绑定到不可变证据，拒绝结构无效的报告，将验证与诊断分离，限制自动修订次数，保留人工决定权，并把每次决定写入可恢复状态。确定性路径完全离线运行；provider 推理必须获得显式授权。

```text
不可变轨迹 -> 净化证据 -> 结构化诊断
           -> 确定性准入 -> 分离式验证
           -> 有限修订或弃权 -> 人工决定
           -> 持久状态 + 可复制制品
```

[阅读 IVAD 预印本](paper/IVAD.pdf) | [查看 LaTeX 源码](paper/source/) | [下载 v0.2.0 版本](https://github.com/naturaljam/SpanVouch/releases/tag/v0.2.0)

## Agent 诊断为什么需要证据层

Agent 失败不同于普通异常。决定性错误可能发生在最终症状之前的多次有效操作中，也可能跨越模型、工具和多 Agent 边界传播。流畅的解释仍可能引用无关证据、忽略反证，或者重复另一个模型的相关性错误。

| 生产场景 | 研究问题 | 工程要求 |
| --- | --- | --- |
| 长链路轨迹掩盖真正的因果步骤 | 定位失败不等于形成可信诊断 | 保存稳定 span 身份和可重新寻址的证据 |
| 真实引用仍可能无关或不充分 | 证据完整性不等于语义支持 | 分别检查确定性有效性与语义支持 |
| 第二个模型可能共享诊断器的失败模式 | 验证器同意不等于独立 | 控制验证器输入、来源和故障分离 |
| 阈值用接受率交换更少错误 | 风险必须针对已接受诊断定义 | 冻结选择规则；无候选满足目标时不返回运行点 |
| 复核流程跨越进程、重试和部署 | 正确算法仍可能在运行时失效 | 持久化状态、保证幂等，并避免重复决定 |

## SpanVouch 承担的工程任务

SpanVouch 把一次失败执行转化为其他工程师能够检查和复现的运行决策。系统需要完成以下任务：

- 将每个因果主张解析到不可变轨迹字段和规范哈希
- 在语义复核前拒绝身份、完整性、时序、作用域和证据覆盖违规
- 对诊断器与可选语义验证器实施相互分离的控制
- 最多允许一次证据驱动修订，之后必须弃权或交由人工复核
- 保留人工决定权、幂等键、租约、事件历史和比较并交换状态转换
- 将数据集、配置、代码身份、运行来源、输出和报告绑定为可验证制品

这组任务划定了“看起来合理的模型回答”与“可审计工程决策”之间的边界。

## 技术核心

SpanVouch 通过版本化、可独立测试的层次实现完整诊断生命周期。

| 层次 | 技术机制 | 失败处理 |
| --- | --- | --- |
| 轨迹契约 | TraceIR、规范 JSON、稳定 span selector、SHA-256 身份 | 拒绝畸形、歧义或可变证据输入 |
| 诊断上下文 | 净化后的轨迹投影和类型化证据目录 | 排除凭据、隐藏推理、标签和 provider 私有字段 |
| 诊断 | 规则优先引擎、可选 provider adapter、有限因果链 | 输出不受支持、证据缺失或来源无效时关闭流程 |
| 确定性验证 | 身份、哈希、时序、结构、作用域、冲突和覆盖检查 | 无论模型置信度多高，都阻止无资格报告自动通过 |
| 分离式语义验证 | 具有受控上下文、provider、prompt 和可见理由的可选验证器 | 产生类型化发现，请求一次修订，或转交人工 |
| 复核与恢复 | SQLite、不可变事件、租约、幂等键和乐观并发控制 | 恢复中断流程，同时避免制造或重复决定 |
| 评估制品 | 冻结语料、manifest、来源账本、确定性报告和 claim gate | 代码、数据、授权、预算或输出身份不一致时停止 |
| 风险感知接受 | 冻结有限候选、同时精确二项界、最小接受组和一次未触碰测试评估 | 不放宽目标；无可行候选时明确不返回运行点 |

公开契约面包含六个版本化根：trace、diagnostic context、diagnosis、verification、review 和 artifact manifest。框架与 provider adapter 位于核心依赖方向之外，因此任何编排框架或模型供应商都不能拥有决策契约。

## IVAD 研究基础

[IVAD: Evidence-Constrained and Risk-Controlled Failure Diagnosis for AI Agents](paper/IVAD.pdf) 提出 Independently Verified Agent Diagnosis（IVAD，独立验证式 Agent 诊断），SpanVouch 则将协议实现为可运行系统。IVAD 研究的问题比失败归因更窄，也更贴近运行决策：系统何时可以接受一份携带证据的诊断，何时必须修订、弃权或转交人工？

论文提出三个相互连接的核心：

- **携带证据的决策对象**：诊断包含有限因果主张、稳定证据引用、状态、来源和明确的未解决证据
- **相互分离的信任通道**：确定性完整性、可选语义支持、有限修订和人工权限不能静默覆盖彼此
- **组选择风险协议**：冻结有限策略族使用同时单侧精确二项界，并保留“无可行运行点”结果

形式化结论依赖冻结损失与流水线、独立采样的预注册组、有限候选族、同时界、正的最小接受组数量、确定性选择，以及一次未触碰测试集评估。SpanVouch 提供执行该协议所需的契约、状态机、adapter 和制品身份。

- [阅读 8 页预印本](paper/IVAD.pdf)
- [从可复现 LaTeX 源码构建论文](paper/source/)
- [查看论文构建方式和 CC BY 4.0 许可](paper/README.md)
- [下载带版本的 PDF 和源码归档](https://github.com/naturaljam/SpanVouch/releases/tag/v0.2.0)

## 已验证工程证据

公开证据快照 Git 修订 `441871aa19cd4d7c129a721a449c5a098780afd1` 记录了以下结果。

| 验证面 | 已观测结果 |
| --- | --- |
| 证据契约基准 | 36 个候选；20/20 有效报告被接受；16/16 注入缺陷被拦截；0/20 误拦截 |
| 发布测试套件 | 收集 1,638 项测试；1,637 项通过；1 项跳过；语句覆盖率 93.40% |
| 离线评估矩阵 | SupportLab、OpsLab、LangGraph 和 AutoGen 的 24/24 个单元全部完成 |
| Adapter 与一致性检查 | 完成 4 次 adapter 执行和 2 次框架一致性比较 |
| Provider 安全 | 签入的离线矩阵产生 0 次 provider 调用和 0 次 GPU 调用 |

这些测量结果证实确定性契约行为、注入缺陷拦截、进程恢复、交付完整性和离线制品可复制性。离线矩阵使用明确标注的 fake-provider 条件验证接口和编排，但不把这些执行当作语义效果证据。Provider 语义收益和目标风险的经验达成需要相应的结果制品支持。

## 可以构建什么

SpanVouch 为必须解释 Agent 失败、又不能直接信任自由文本模型输出的系统提供决策与证据层。

| 使用场景 | SpanVouch 提供的能力 |
| --- | --- |
| Agent 质量平台 | 统一轨迹、诊断失败、执行证据策略并比较回归结果 |
| 生产事故复核 | 保存因果记录、验证器发现、修订过程和人工决定时间线 |
| 工具调用治理 | 将主张绑定到精确调用，并暴露作用域、来源、凭据和完整性违规 |
| 框架评估 | 在 LangGraph、AutoGen、SupportLab 和 OpsLab 上运行同一诊断契约 |
| 企业审计流程 | 集成策略门、认证复核、不可变事件和持久制品 |

## 集成界面

当前版本包含 Python package、命令行界面（CLI）、FastAPI 应用、六个 JSON Schema 契约根、SQLite 恢复存储、Docker Compose 交付、框架 adapter、provider adapter 和冻结评估资产。核心路径保持确定性且不绑定 provider。

| 界面 | 已包含能力 |
| --- | --- |
| Python 与 CLI | 数据集生成、诊断评估、复核评估和复核操作 |
| HTTP API | 轨迹接收、诊断、复核创建、恢复、查看和人工决定 |
| 契约 | 覆盖全部公开决策对象的版本化 JSON Schema |
| 运行时 | SQLite 状态、租约、幂等、不可变事件和重启恢复 |
| 评估 | 冻结数据集、多框架 adapter、manifest、报告和 claim gate |
| 部署 | 锁定 Python 环境、非特权容器和持久化 Compose volume |

## 在本地运行 SpanVouch

安装 Python 3.12 和 [uv 0.8.x](https://docs.astral.sh/uv/)。Docker Compose v2 为可选依赖。

```bash
git clone https://github.com/naturaljam/SpanVouch.git
cd SpanVouch
uv sync --frozen --group dev
uv run spanvouch dataset generate --output .cache/readme-check --seed 20260715
uv run spanvouch evaluate diagnosis --output .cache/rules.json
uv run spanvouch evaluate review --output .cache/review-rules.json
```

启动 API：

```bash
uv run uvicorn spanvouch.api.app:app --host 127.0.0.1 --port 8000
```

服务运行时，OpenAPI 位于 `http://127.0.0.1:8000/docs`。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/health` | 返回服务健康状态 |
| POST | `/v1/traces` | 接收 TraceIR 文档 |
| POST | `/v1/traces/{trace_id}/diagnoses` | 诊断轨迹 |
| POST | `/v1/traces/{trace_id}/diagnosis-reviews` | 创建复核案例 |
| GET | `/v1/diagnosis-reviews/{case_id}` | 查看案例时间线 |
| POST | `/v1/diagnosis-reviews/{case_id}/resume` | 恢复可恢复任务 |
| POST | `/v1/diagnosis-reviews/{case_id}/decisions` | 记录人工决定 |

诊断接口为 `POST /v1/traces/{trace_id}/diagnoses`。规则诊断不需要 provider key；provider 诊断还需要 `DEEPSEEK_API_KEY` 和 `--allow-live-api`。

## 完成一次离线端到端复核

使用 `evals/datasets/supportlab-v1/traces.jsonl` 中冻结的 SupportLab 轨迹。将选定轨迹提交至 `POST /v1/traces`，再创建、查看并决定复核案例：

```bash
trace_id="$(curl --fail --silent --show-error -H 'content-type: application/json' \
  --data-binary @.cache/spanvouch-demo-trace.json http://127.0.0.1:8000/v1/traces \
  | python -c 'import json,sys; print(json.load(sys.stdin)["trace_id"])')"
created="$(uv run spanvouch review create --trace-id "$trace_id" --idempotency-key demo-create-001)"
case_id="$(python -c 'import json,sys; print(json.loads(sys.argv[1])["case"]["case_id"])' "$created")"
version="$(python -c 'import json,sys; print(json.loads(sys.argv[1])["case"]["version"])' "$created")"
uv run spanvouch review show --case-id "$case_id"
uv run spanvouch review decide --case-id "$case_id" --action confirm --expected-version "$version" \
  --reviewer-label local-reviewer --idempotency-key demo-decision-001
```

## 使用 Docker 运行

Docker Compose 构建锁定镜像，以非特权用户启动 API，并将复核状态保存到持久化 SQLite volume。

```bash
docker compose up --build --detach --wait api
curl --fail http://127.0.0.1:8000/health
docker compose down
```

## 控制 provider 访问

规则诊断与确定性验证不需要 provider key。DeepSeek 诊断和混合语义验证需要 `DEEPSEEK_API_KEY`，同时必须显式传入 `--allow-live-api`。在线调用可能产生费用，并且不会进入持续集成（CI）。服务对 localhost 之外开放前，应将 API 置于认证网关之后。

## 商业化部署

MIT 核心支持私有化部署、Agent 质量与审计平台、策略集成、多团队或多模型 adapter、托管服务和企业支持。版本化契约与 provider 中立的工作流边界，让团队能够替换模型或框架，而不必重写证据和决策层。

## 仓库结构

```text
src/spanvouch/   契约、轨迹、诊断、验证、复核、API、CLI
schemas/v1/      版本化公开 JSON Schema 契约
tests/           单元、契约、架构、集成和端到端测试
evals/           冻结数据集、配置和参考报告
paper/           IVAD 预印本、可复现源码和构建说明
```

## 引用 IVAD 与 SpanVouch

GitHub 读取 [`CITATION.cff`](CITATION.cff) 并展示仓库引用元数据。当你的工作依赖 IVAD 协议、形式化方法或评估设计时，请引用预印本：

```bibtex
@article{liu2026ivad,
  title  = {IVAD: Evidence-Constrained and Risk-Controlled Failure Diagnosis for AI Agents},
  author = {Liu, Hanzhe},
  year   = {2026},
  url    = {https://github.com/naturaljam/SpanVouch/blob/main/paper/IVAD.pdf}
}
```

## 贡献与许可证

提交 Issue 或 Pull Request 前，请阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 和 [SECURITY.md](SECURITY.md)。SpanVouch 软件采用 [MIT License](LICENSE)。IVAD 论文、图表和源码采用 [CC BY 4.0](paper/README.md)。
