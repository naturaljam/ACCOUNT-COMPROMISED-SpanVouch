# Phase 5 live-experiment official-source refresh

**Checked:** 2026-07-20 (Asia/Shanghai)  
**Scope:** Current first-party documentation only. No credentials were read, no
provider request was sent, and no GPU was rented. This is a pre-flight
compatibility record, not evidence from a paid experiment.

## Findings that remain compatible

### DeepSeek

- The documented OpenAI-compatible base URL is `https://api.deepseek.com`; the
  documented current model IDs include `deepseek-v4-flash` and
  `deepseek-v4-pro`. [DeepSeek quick start](https://api-docs.deepseek.com/quick_start/)
- `deepseek-v4-flash` supports both thinking and non-thinking modes; thinking
  defaults to enabled. For this OpenAI SDK integration, the configured
  `extra_body={"thinking":{"type":"disabled"}}` is the documented way to
  request non-thinking mode. [DeepSeek thinking-mode guide](https://api-docs.deepseek.com/guides/thinking_mode/)
- Current listed Flash prices per 1M tokens are USD `0.0028` cache-hit input,
  USD `0.14` cache-miss input, and USD `0.28` output. The vendor explicitly
  reserves the right to change prices. [DeepSeek models and pricing](https://api-docs.deepseek.com/quick_start/pricing/)
- `deepseek-chat` and `deepseek-reasoner` are scheduled to retire on
  2026-07-24 15:59 UTC. They must not be introduced into the long-running
  experiment; keep the explicit V4 model ID and thinking switch.
  [DeepSeek models and pricing](https://api-docs.deepseek.com/quick_start/pricing/)

### Qwen3/vLLM

- The selected public checkpoint remains `Qwen/Qwen3-14B` (Apache-2.0). The
  Hub's currently resolved main revision is
  `40c069824f4251a91eefaf281ebe4c544efd3e18`; freeze that exact revision and
  record the downloaded snapshot digest. A branch name is not a reproducible
  model identity. [Pinned Qwen3-14B revision](https://huggingface.co/Qwen/Qwen3-14B/tree/40c069824f4251a91eefaf281ebe4c544efd3e18), [model card](https://huggingface.co/Qwen/Qwen3-14B)
- Qwen's own vLLM guide documents `chat_template_kwargs` with
  `enable_thinking: false` as the per-request hard switch. It also says this
  extension is not OpenAI API compatible. A custom non-thinking chat template
  is the stronger server-side control. [Qwen vLLM deployment guide](https://github.com/QwenLM/Qwen3/blob/main/docs/source/deployment/vllm.md)
- Qwen documents Qwen3's pretraining context as 32,768 tokens, so the planned
  `max_model_len: 32768` is compatible. The same guide warns that the default
  server model length can be 40,960 and that GPU memory utilization defaults to
  `0.9`; record both values actually used. [Qwen vLLM deployment guide](https://github.com/QwenLM/Qwen3/blob/main/docs/source/deployment/vllm.md)
- Do not enable reasoning parsing in the non-thinking arm. Qwen documents that
  vLLM 0.8.5 cannot combine it with `enable_thinking=false`; vLLM 0.9.0 added
  compatible `qwen3` parsing. The clean experiment choice is to omit reasoning
  parsing entirely from the non-thinking server. [Qwen vLLM deployment guide](https://github.com/QwenLM/Qwen3/blob/main/docs/source/deployment/vllm.md)
- vLLM's stable Docker documentation provides pre-built images and a non-root
  deployment path. Pin a tagged image to its resolved repository digest rather
  than relying on a mutable tag. [vLLM Docker deployment](https://docs.vllm.ai/en/stable/deployment/docker/)
- Do not expose vLLM directly on the public Internet. The official security
  documentation distinguishes protected and unprotected endpoints and
  recommends network isolation, minimizing exposed endpoints, and a reverse
  proxy/firewall boundary. [vLLM security guidance](https://docs.vllm.ai/en/stable/usage/security/)

## Drift and blockers in `evals/configs/phase5-pilot.json`

| Field | Status | Required action before a live run |
| --- | --- | --- |
| `generator` / DeepSeek verifiers | Compatible | Keep `deepseek-v4-flash`, `https://api.deepseek.com`, and explicit disabled thinking. Capture a dated price snapshot and exact URL hash immediately before approval. |
| `live_provenance.*.pricing.source_url` | **Blocked** | Both values are `https://pricing.example.invalid/source`; replace with a real, immutable/local canonical pricing snapshot and its actual SHA-256. |
| `qwen.container_repo_digest` | **Blocked** | It is an all-`a` placeholder. Pull a selected vLLM image tag, resolve its real `vllm/vllm-openai@sha256:...` digest, and freeze it. |
| `qwen.hf_revision` | **Blocked** | It is an all-`b` placeholder. Replace it with `40c069824f4251a91eefaf281ebe4c544efd3e18` (then verify that exact revision downloads) or re-resolve just before download and record any newer approved SHA. |
| `qwen.chat_template_sha256` | **Blocked** | It is an all-`c` placeholder. Hash the exact template actually served; if per-request switching is used, also archive the request setting. |
| `qwen.gpu_lease_approval` | **Blocked** | `example-cloud`, `test-region-1`, `gpu-48gb`, and CNY 10 are examples, not an approved quote. Replace with a real provider, region, GPU, hourly price, maximum hours, and cap. |
| `base_url_sha256` fields | Needs re-freeze | The hashes cannot establish a readable deployment record on their own. Recompute against the literal endpoints selected for the approved run and include the secret-free canonical source in the manifest. |

## Drift in the existing preparation note

`docs/research/phase5-live-experiment-preparation-2026-07-20.md` is directionally
consistent with the documentation above. Its stated Qwen Hub revision is only a
candidate and must be re-resolved just before download; its legacy DeepSeek
deprecation wording should be treated as time-sensitive. Its example cloud
lease and placeholder provenance are intentionally not launch-authorizing.

## Launch gate

Do **not** issue `--allow-live-provider` yet. The minimal remaining gate is a
user-approved, hash-bound packet containing (1) a current DeepSeek price
snapshot, (2) an actual GPU quote and lease cap, (3) an exact Qwen Hub revision
and chat-template hash, and (4) a real immutable vLLM image digest. After those
identities are frozen, run a single excluded pilot smoke request per provider;
only then approve the full B0--B5 pilot matrix.
