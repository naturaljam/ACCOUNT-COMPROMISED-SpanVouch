# Prepare the Phase 5 DeepSeek and Qwen/vLLM experiment

**Retrieval date:** 2026-07-20
**Scope:** Prepare paid pilot and formal experiment checkpoints. This research did not call a provider or rent a graphics processing unit (GPU).

Run the live study in two checkpoints: an excluded pilot, then a separately approved formal matrix. DeepSeek remains the sole diagnosis generator and supplies B2/B3 verification. `Qwen/Qwen3-14B`, served through vLLM, supplies B4/B5 verification. B4 is an operational cross-model condition, not a pure model-identity intervention.

Use `deepseek-v4-flash` directly. DeepSeek states that the legacy `deepseek-chat` and `deepseek-reasoner` aliases retire on 2026-07-24 at 15:59 UTC. The API base is `https://api.deepseek.com`, and bearer authentication uses an API key. ([DeepSeek first API call](https://api-docs.deepseek.com/), [DeepSeek models and pricing](https://api-docs.deepseek.com/quick_start/pricing/))

## Prepare the required accounts and resources

Prepare these items before requesting pilot approval:

1. **DeepSeek account and budget**
   - Create or reuse a DeepSeek API key and confirm sufficient balance
   - Set the key locally as `DEEPSEEK_API_KEY`; never paste it into chat, commit it, add it to an image, or place it in an experiment artifact
   - Approve separate pilot and formal CNY caps; the project limits the pilot to 10% of the monthly cap and stops new paid work at 80%
   - Capture the official price page, effective time, model ID, cache-hit and cache-miss input prices, output price, and USD/CNY conversion evidence

On 2026-07-20, DeepSeek listed `deepseek-v4-flash` at USD 0.0028 per million cache-hit input tokens, USD 0.14 per million cache-miss input tokens, and USD 0.28 per million output tokens. DeepSeek states that prices can change. ([DeepSeek models and pricing](https://api-docs.deepseek.com/quick_start/pricing/))

2. **Linux GPU rental quote**
   - Record the cloud provider, region, GPU model and count, video memory, hourly price, billing granularity, disk price, network charges, and release command
   - Start the pilot with one 48 GB NVIDIA GPU, at least 8 vCPU, 32 GB host RAM, and 80 GB free disk
   - Use two 24 GB GPUs only after a tensor-parallel smoke test; do not use one 24 GB GPU for the unquantized checkpoint

These sizes are engineering estimates, not Qwen or vLLM guarantees. The official checkpoint has 14.8 billion parameters, and the BF16 repository is about 29.6 GB. Serving also needs CUDA workspace and key-value cache memory. vLLM documents a default GPU memory utilization of 0.9 in the cited engine release. ([Qwen3-14B model card](https://huggingface.co/Qwen/Qwen3-14B), [Qwen repository at an immutable revision](https://huggingface.co/Qwen/Qwen3-14B/tree/cc692f40d59e239c60676c8947c5f9f75493e02b), [vLLM engine memory arguments](https://docs.vllm.ai/en/v0.11.0/configuration/engine_args.html))

3. **Network and credentials**
   - Allow outbound HTTPS to Docker Hub and Hugging Face while provisioning
   - Connect the SpanVouch controller through localhost, an SSH tunnel, or a private network
   - Require Transport Layer Security (TLS) and a source-IP allowlist if traffic leaves the host
   - Set a short-lived server key as `VLLM_API_KEY`, the client copy as `SPANVOUCH_VLLM_API_KEY`, and the endpoint as `SPANVOUCH_VLLM_BASE_URL`

Do not expose vLLM directly to the Internet. vLLM states that its API key protects OpenAI-compatible path prefixes but does not protect every endpoint on the server. Restrict the firewall to the minimum required surface. ([vLLM security guidance](https://docs.vllm.ai/en/latest/usage/security/))

## Freeze the deployment identity

Record these values in the experiment manifest before the pilot:

- Git commit, experiment ID, corpus, label, candidate, and matrix hashes
- Prompt and schema versions, seeds, repetitions, exclusions, and stop rules
- DeepSeek base URL, literal model ID, thinking mode, generation settings, token cap, and pricing snapshot
- Qwen model ID and full 40-character Hugging Face commit
- vLLM release tag and full `vllm/vllm-openai@sha256:...` repository digest
- GPU provider, region, type, count, driver, CUDA runtime, dtype, quantization, tensor parallelism, model length, and memory setting
- Served-model name, chat template hash, and redacted launch command

The Hugging Face model API returned `40c069824f4251a91eefaf281ebe4c544efd3e18` on 2026-07-20. Treat this value as a candidate pilot pin. Verify that it resolves before download, then retain the downloaded snapshot hash. Bind the tokenizer revision and chat template to the same experiment identity. ([Hugging Face model API](https://huggingface.co/api/models/Qwen/Qwen3-14B), [vLLM Hugging Face integration](https://docs.vllm.ai/en/stable/design/huggingface_integration/))

Use a versioned `vllm/vllm-openai` image. Pull the selected tag, then resolve and record its repository digest. vLLM documents the NVIDIA image, GPU cache mount, port, and shared-memory requirements. Use `--ipc=host` or an explicit `--shm-size`. ([vLLM Docker deployment](https://docs.vllm.ai/en/stable/deployment/docker/))

## Pass the compatibility smoke gate

Complete these checks before pilot authorization:

- Verify DeepSeek authentication, model identity, non-streaming JSON output, usage accounting, timeouts, retries, and one schema-valid response
- Disable DeepSeek thinking with `{"thinking":{"type":"disabled"}}`; DeepSeek states that thinking defaults to enabled and ignores sampling controls such as temperature ([DeepSeek thinking mode](https://api-docs.deepseek.com/guides/thinking_mode/))
- Confirm that vLLM `/v1/models` returns exactly `Qwen/Qwen3-14B`
- Validate one Qwen structured response against the verifier schema
- Set `chat_template_kwargs.enable_thinking=false` and freeze a non-thinking Jinja template hash; Qwen documents this setting as an OpenAI API extension ([Qwen vLLM deployment](https://github.com/QwenLM/Qwen3/blob/main/docs/source/deployment/vllm.md))
- Confirm that provider payloads contain no gold label, split, expected finding, credential, hidden reasoning, or other condition verdict
- Confirm that the budget ledger reserves worst-case cost, does not rebill cache hits, and releases failed reservations

## Execute the paid checkpoints

Follow this sequence:

1. Freeze the excluded pilot manifest and calculate maximum DeepSeek and GPU spend
2. Obtain approval for that exact pilot identity and cap
3. Run the smoke checks and pilot with `--allow-live-provider`, the experiment ID,
   and `--approved-manifest-sha256 your_approved_manifest_sha256_here`
4. Stop the GPU, revoke the key, close ingress, record actual cost, and confirm that the endpoint is unreachable
5. Review completeness, failures, missingness, schema validity, latency, token use, and cost; exclude pilot rows from formal results
6. Freeze the formal manifest and obtain separate formal-spend approval
7. Run formal calls with `--allow-live-provider`, `--formal-run`, and `--approved-manifest-sha256 your_approved_manifest_sha256_here`
8. Complete the paired matrix without selectively deleting or regenerating cells
9. Join sealed labels after provider completion, generate the analysis manifest and H1-H5 gates, then rerun the engineering gates

Research acceptance requires a hash-linked chain from configuration through corpus, diagnoses, B0-B5 results, post-call label join, and analysis. Report complete cell counts, missingness, paired risk and coverage intervals, provider and GPU provenance, actual cost, and every null or negative result. The engineering pipeline can be delivered without this matrix, but it cannot support a DeepSeek/Qwen effectiveness claim.

## Submit the approval packet

Provide this packet immediately before each paid action:

- Mode, experiment ID, and approved manifest SHA-256
- DeepSeek model, maximum requests, input tokens, output tokens, and CNY spend
- GPU provider, region, type, count, hourly price, maximum hours, extra fees, and CNY spend
- Qwen revision, vLLM image digest, dtype, quantization, model length, and redacted launch command
- Expected matrix cells, resume and cache plan, abort criteria, cleanup commands, and combined cap

Approval must name the exact checkpoint and cap. General permission to spend does not approve a specific quote or experiment identity.
