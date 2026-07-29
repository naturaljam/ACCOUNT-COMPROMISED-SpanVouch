# SpanVouch

[![English](https://img.shields.io/badge/README-English-111827?style=for-the-badge)](README.md)
[![中文](https://img.shields.io/badge/README-中文-0f766e?style=for-the-badge)](README.zh-CN.md)
[![CI](https://github.com/naturaljam/SpanVouch/actions/workflows/ci.yml/badge.svg)](https://github.com/naturaljam/SpanVouch/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-2F8552.svg)](LICENSE)
[![IVAD 论文](https://img.shields.io/badge/Paper-IVAD-b91c1c?style=for-the-badge)](paper/IVAD.pdf)

![SpanVouch 标志](assets/spanvouch-logo.png)

**面向 Agent 诊断、验证、复核与恢复的开源工程基础设施。**

SpanVouch 将 Agent 执行轨迹转化为可审计的工程流程：结构化证据、受边界约束的诊断、独立
验证、人工决策和可恢复持久化。默认路径确定性、离线运行；模型 provider 调用必须显式授权。

[阅读 IVAD 预印本](paper/IVAD.pdf)，或[查看 LaTeX 源码](paper/source/)。

## 为什么是 SpanVouch

- 严格 TraceIR 和版本化 schema，替代临时日志解析；
- 规则优先诊断，provider adapter 作为可选扩展；
- 独立验证、弃权和最多一次证据驱动修订；
- SQLite、租约、幂等键、不可变事件和乐观锁，支持中断恢复；
- 冻结数据集、manifest 和确定性报告，支持持续集成回归。

## IVAD 协议

IVAD（Independently Verified Agent Diagnosis，独立验证式 Agent 诊断）的目标，是阻止缺少可核验证据的“合理解释”直接成为工程决策。SpanVouch 是 IVAD 的开源参考实现。

```text
不可变轨迹 -> 主张与证据契约 -> 确定性资格检查
           -> 分离式语义验证 -> 有界修订
           -> 人工决策 -> 持久化制品
```

IVAD 将诊断可信度拆成五个职责：

- **证据绑定**：每个因果主张都解析到不可变轨迹字段和规范哈希
- **硬资格检查**：确定性规则检查身份、完整性、时序、作用域和证据覆盖
- **语义验证**：可选的分离式验证器检查相关性、充分性、反证和替代原因
- **有界恢复**：最多允许一次可审计修订，随后必须弃权或进入人工复核
- **风险感知接受**：冻结的有限策略族使用同时精确二项界；没有候选满足目标时返回“无可行运行点”

形式化风险结论依赖以下设置：冻结损失与流水线、独立采样的预注册组、有限候选族、同时界、正的最小接受量、确定性选择，以及仅执行一次的未触碰测试集评估。

## 已验证结果

公开快照 Git 修订 `441871aa19cd4d7c129a721a449c5a098780afd1` 记录了以下工程证据：

| 验证面 | 结果 |
| --- | --- |
| 证据契约基准 | 36 个候选；20/20 有效报告被接受；16/16 注入缺陷被拦截；0/20 误拦截 |
| 发布测试套件 | 收集 1,638 项测试；1,637 项通过；1 项跳过；语句覆盖率 93.40% |
| 离线评估矩阵 | SupportLab、OpsLab、LangGraph 和 AutoGen 的 24/24 个单元全部完成 |
| 适配器与一致性检查 | 完成 4 次适配器执行和 2 次框架一致性比较 |
| Provider 安全 | 签入的离线矩阵产生 0 次 provider 调用和 0 次 GPU 调用 |

这些结果验证确定性契约行为、恢复、交付和制品可复现性。它们不代表可选语义验证器已经带来诊断效果提升，也不代表部署运行点已经达到目标风险。

## 阅读论文

**IVAD: Evidence-Constrained and Risk-Controlled Failure Diagnosis for AI Agents** 给出数学协议、SpanVouch 架构、实验设计、证据边界和结果。

- [阅读 8 页预印本](paper/IVAD.pdf)
- [查看可复现的 LaTeX 源码](paper/source/)
- [查看论文构建与许可说明](paper/README.md)

## 可以构建什么

SpanVouch 可作为 Agent 质量平台、客服运营复核、工具调用事故分析和审计记录的基础层。仓库
包含 FastAPI、CLI、SQLite 恢复、Docker/Compose 交付、LangGraph/AutoGen 适配器，以及 SupportLab
和 OpsLab 评估实验场。

## 快速开始

环境要求：Python 3.12、[uv](https://docs.astral.sh/uv/) 0.8.x；容器运行需要 Docker Compose v2。

```bash
git clone https://github.com/naturaljam/SpanVouch.git
cd SpanVouch
uv sync --frozen --group dev
uv run spanvouch dataset generate --output .cache/readme-check --seed 20260715
uv run spanvouch evaluate diagnosis --output .cache/rules.json
uv run spanvouch evaluate review --output .cache/review-rules.json
```

启动 API：`uv run uvicorn spanvouch.api.app:app --host 127.0.0.1 --port 8000`

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/health` | 服务健康检查 |
| POST | `/v1/traces` | 接收 TraceIR |
| POST | `/v1/traces/{trace_id}/diagnoses` | 诊断轨迹 |
| POST | `/v1/traces/{trace_id}/diagnosis-reviews` | 创建复核案例 |
| GET | `/v1/diagnosis-reviews/{case_id}` | 查看案例时间线 |
| POST | `/v1/diagnosis-reviews/{case_id}/resume` | 恢复可恢复任务 |
| POST | `/v1/diagnosis-reviews/{case_id}/decisions` | 记录人工决策 |

服务启动后，可在 `http://127.0.0.1:8000/docs` 查看 OpenAPI。

## Docker

```bash
docker compose up --build --detach --wait api
curl --fail http://127.0.0.1:8000/health
docker compose down
```

镜像以非 root 用户运行，复核状态保存在持久化 SQLite volume 中。

## Provider 与安全

规则诊断和确定性验证不需要 provider key。DeepSeek 诊断和混合语义验证需要配置
`DEEPSEEK_API_KEY`，并显式传入 `--allow-live-api`。在线调用可能产生费用，且不会进入 CI。
服务对外暴露时，应置于经过认证的网关之后。

## 企业部署

MIT 核心适合私有化部署、企业 Agent 质量与审计平台、政策与合规集成、多团队多模型适配，
以及托管服务和企业支持。架构保持核心流程与单一模型 provider 解耦。

## 仓库结构

```text
src/spanvouch/   核心契约、轨迹、诊断、验证、复核、API 和 CLI
schemas/v1/      版本化公开 schema
tests/           单元、契约、架构、集成和端到端测试
evals/           冻结数据集、配置和参考报告
paper/           IVAD 预印本、LaTeX 源码和构建说明
```

## 贡献与许可证

提交 Issue 或 Pull Request 前，请阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 和
[SECURITY.md](SECURITY.md)。SpanVouch 软件采用 [MIT License](LICENSE)，论文使用
[paper/README.md](paper/README.md) 中单独列出的版权声明。
