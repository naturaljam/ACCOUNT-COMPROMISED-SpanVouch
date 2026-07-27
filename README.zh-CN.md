# SpanVouch

[![English](https://img.shields.io/badge/README-English-111827?style=for-the-badge)](README.md)
[![中文](https://img.shields.io/badge/README-中文-0f766e?style=for-the-badge)](README.zh-CN.md)
[![CI](https://github.com/naturaljam/SpanVouch/actions/workflows/ci.yml/badge.svg)](https://github.com/naturaljam/SpanVouch/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-2F8552.svg)](LICENSE)

![SpanVouch 标志](assets/spanvouch-logo.png)

**面向 Agent 诊断、验证、复核与恢复的开源工程基础设施。**

SpanVouch 将 Agent 执行轨迹转化为可审计的工程流程：结构化证据、受边界约束的诊断、独立
验证、人工决策和可恢复持久化。默认路径确定性、离线运行；模型 provider 调用必须显式授权。

## 为什么是 SpanVouch

- 严格 TraceIR 和版本化 schema，替代临时日志解析；
- 规则优先诊断，provider adapter 作为可选扩展；
- 独立验证、弃权和最多一次证据驱动修订；
- SQLite、租约、幂等键、不可变事件和乐观锁，支持中断恢复；
- 冻结数据集、manifest 和确定性报告，支持持续集成回归。

## 工程闭环

```text
Agent 轨迹 -> TraceIR -> 诊断 -> 独立验证
           -> 有界修订 -> 人工复核 -> 持久化决策
```

IVAD（Independently Verified Agent Diagnosis）在这里是工程协议：诊断结果只有在独立验证和
人工决策完成后，才进入最终决策流程。

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

## 贡献与许可证

提交 Issue 或 Pull Request 前，请阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 和
[SECURITY.md](SECURITY.md)。项目采用 [MIT License](LICENSE)。
