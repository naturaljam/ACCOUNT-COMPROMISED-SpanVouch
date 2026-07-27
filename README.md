# SpanVouch

[![CI](https://github.com/naturaljam/SpanVouch/actions/workflows/ci.yml/badge.svg)](https://github.com/naturaljam/SpanVouch/actions/workflows/ci.yml)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-2F8552.svg)](LICENSE)

![SpanVouch mark](assets/spanvouch-mark.svg)

**Open-source infrastructure for evidence-backed agent diagnosis, verification, review, and recovery.**

SpanVouch turns an agent execution trace into an auditable engineering workflow: structured evidence,
bounded diagnosis, independent verification, human decisions, and durable recovery. The default path
is deterministic and offline. Provider-backed calls are explicit opt-in.

## Why it matters

- strict TraceIR and versioned schemas instead of ad-hoc log parsing;
- rules-first diagnosis with optional provider adapters;
- independent verification, abstention, and one bounded revision;
- SQLite persistence, leases, idempotency, immutable events, and compare-and-swap updates;
- frozen datasets, manifests, and deterministic reports for regression control.

## Engineering loop

```text
agent trace -> TraceIR -> diagnosis -> independent verification
             -> bounded revision -> human review -> durable decision
```

Core dependency direction: `contracts <- trace <- diagnosis <- verification <- review`.

The IVAD (Independently Verified Agent Diagnosis) boundary is implemented as an engineering
protocol: diagnosis is never treated as verified until an independent verifier and a human
decision have completed their steps.

## What you can build

SpanVouch is a foundation for internal agent quality platforms, support-operations review,
tool-call incident analysis, and compliance-oriented audit trails. It includes FastAPI and CLI
delivery, SQLite recovery, Docker/Compose packaging, LangGraph and AutoGen adapters, and offline
evaluation labs such as SupportLab and OpsLab.

## Quick start

Requirements: Python 3.12, [uv](https://docs.astral.sh/uv/) 0.8.x, and optionally Docker Compose v2.

```bash
git clone https://github.com/naturaljam/SpanVouch.git
cd SpanVouch
uv sync --frozen --group dev
uv run spanvouch dataset generate --output .cache/readme-check --seed 20260715
uv run spanvouch evaluate diagnosis --output .cache/rules.json
uv run spanvouch evaluate review --output .cache/review-rules.json
```

Start the local API with `uv run uvicorn spanvouch.api.app:app --host 127.0.0.1 --port 8000`.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/health` | service health |
| POST | `/v1/traces` | ingest a TraceIR document |
| POST | `/v1/traces/{trace_id}/diagnoses` | diagnose a trace |
| POST | `/v1/traces/{trace_id}/diagnosis-reviews` | create a review case |
| GET | `/v1/diagnosis-reviews/{case_id}` | read the case timeline |
| POST | `/v1/diagnosis-reviews/{case_id}/resume` | resume recoverable work |
| POST | `/v1/diagnosis-reviews/{case_id}/decisions` | record a human decision |

OpenAPI is available at `http://127.0.0.1:8000/docs` while the service is running.
The diagnosis endpoint is `POST /v1/traces/{trace_id}/diagnoses`.

For a complete offline review, use the frozen trace at
`evals/datasets/supportlab-v1/traces.jsonl`, post it to `POST /v1/traces`, then use the CLI:

```bash
trace_id="$(curl --fail --silent --show-error -H 'content-type: application/json' \
  --data-binary @.cache/spanvouch-demo-trace.json http://127.0.0.1:8000/v1/traces \
  | python -c 'import json,sys; print(json.load(sys.stdin)["trace_id"])')"
created="$(uv run spanvouch review create --trace-id "$trace_id" --idempotency-key demo-create-001)"
case_id="$(python -c 'import json,sys; print(json.loads(sys.argv[1])["case"]["case_id"])' "$created")"
version="$(python -c 'import json,sys; print(json.loads(sys.argv[1])["case"]["version"])' "$created")"
uv run spanvouch review show --case-id "$case_id"
uv run spanvouch review decide --case-id "$case_id" --action confirm \
  --expected-version "$version" --reviewer-label local-reviewer \
  --idempotency-key demo-decision-001
```

## Docker

```bash
docker compose up --build --detach --wait api
curl --fail http://127.0.0.1:8000/health
docker compose down
```

The image runs unprivileged and stores review state in a persistent SQLite volume.

## Optional providers and safety

Rules and deterministic verification never need a provider key. DeepSeek diagnosis and hybrid
semantic verification require `DEEPSEEK_API_KEY` plus an explicit `--allow-live-api` flag. Live
calls may incur cost and are excluded from CI. The included service has no authentication or RBAC;
keep it on localhost or put it behind an authenticated gateway.

## Project status

This is an open-source engineering release with an end-to-end offline workflow, tested contracts,
delivery artifacts, and reproducible CI gates. Phase 6 is not presented as completed functionality.
Claims about model effectiveness require separately reproduced, provider-authorized experiments.

## Commercialization paths

The MIT-licensed core can support private deployments, enterprise policy and audit integrations,
multi-team or multi-model adapters, managed hosting, and optional enterprise support. These are
extension directions, not claims of existing customers, SLAs, or production certification.

## Repository map

```text
src/spanvouch/   core contracts, trace, diagnosis, verification, review, API, CLI
schemas/v1/      versioned public schemas
tests/           unit, contract, architecture, integration, and E2E tests
evals/           frozen datasets, configs, and reference reports
```

## Contributing

Issues and pull requests are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) and
[SECURITY.md](SECURITY.md). Keep provider execution opt-in, preserve contract compatibility,
and include focused tests for behavior changes.

## License

SpanVouch is available under the [MIT License](LICENSE).

---

# SpanVouch 中文说明

SpanVouch 是一个面向工具调用型 Agent 的开源工程基础设施，用于把执行轨迹转化为可审计的
“证据 -> 诊断 -> 独立验证 -> 人工决策 -> 持久恢复”闭环。

## 工程定位

项目默认离线运行，规则诊断和确定性验证无需模型费用。所有 provider 调用都必须显式授权，
并带有预算、allowlist 和 provenance 边界。核心代码、契约、API、CLI、Docker 交付和 CI 回归
验证已形成统一工程闭环。

## 核心能力

- 严格 TraceIR、版本化 schema、规范化 JSON 和内容哈希；
- 规则优先的诊断，以及可选的 provider adapter；
- 独立验证、弃权和最多一次证据驱动修订；
- SQLite、租约、幂等键、不可变事件和乐观锁恢复；
- FastAPI、CLI、Docker/Compose，以及 LangGraph、AutoGen 适配器；
- 冻结数据集和字节级确定性报告，便于持续集成回归。

## 快速开始

```bash
uv sync --frozen --group dev
uv run spanvouch dataset generate --output .cache/readme-check --seed 20260715
uv run spanvouch evaluate diagnosis --output .cache/rules.json
uv run uvicorn spanvouch.api.app:app --host 127.0.0.1 --port 8000
```

默认数据库路径是 `.data/spanvouch.db`，可通过 `SPANVOUCH_DB_PATH` 覆盖。

## 商业化方向

MIT 核心可用于企业内部 Agent 质量平台、私有化部署、审计与合规集成、多团队多模型适配，
以及未来的托管服务和企业支持。这些是可扩展方向，不代表已有客户、SLA 或生产认证。

## 当前状态与边界

这是一个开源工程发布，不把项目包装成论文研究成果；Phase 6 也未宣称完成。当前服务未内置
认证和 RBAC，建议仅绑定 localhost 或置于受控网关之后。模型效果结论必须通过独立、获授权的
实验得到，不能由离线工程测试替代。

欢迎提交 Issue 和 Pull Request。项目采用 MIT License。
