# Phase 5 Qwen/vLLM Runbook

This runbook is for the isolated Qwen verifier. The checked-in JSON is a localhost
smoke example only. It must not be used for pilot or formal evidence until every
deployment identity field below is frozen.

## 1. Freeze the host and artifacts

Use a Linux GPU host. Record the provider, region, GPU model/count, `nvidia-smi`
driver version, CUDA runtime, lease start/end timestamps and final CNY cost. Pin an
exact `vllm/vllm-openai` image tag and resolve it immediately before the pilot:

```bash
docker pull vllm/vllm-openai:<PINNED_TAG>
docker image inspect vllm/vllm-openai:<PINNED_TAG> \
  --format '{{index .RepoDigests 0}}'
```

Copy the complete `vllm/vllm-openai@sha256:<64-lowercase-hex>` RepoDigest into the
experiment record. A bare `sha256:...`, tag, other repository name, short digest,
or locally calculated image ID is not acceptable. Resolve and record
the exact 40-character Hugging Face commit revision for `Qwen/Qwen3-14B`; do not use
`main` or another moving reference. Pilot and formal validation reject `smoke_only`
and reject missing container or checkpoint pins.

## 2. Restrict and start the endpoint

Bind the service to localhost or a private interface. The configured API root must
not contain URL username/password userinfo; credentials belong only in the
authorization header. Apply an inbound firewall
allowlist, TLS at the private ingress when traffic leaves the host, and a dedicated
short-lived API key. Never commit the key, command history containing it, raw
headers, or provider bodies. Export it through the runtime secret store as
`SPANVOUCH_VLLM_API_KEY`.

Start the pinned image with the pinned Hugging Face revision and model name
`Qwen/Qwen3-14B`. Configure the served model name to exactly that value. Ensure the
chat template supports `chat_template_kwargs.enable_thinking=false`; Phase 5 sends
that setting on every structured generation and does not retain hidden reasoning.
Record the complete command with secret values redacted.

## 3. Smoke-test before authorization

First verify `/v1/models` returns exactly the expected served ID:

```bash
curl --fail --silent \
  -H "Authorization: Bearer $SPANVOUCH_VLLM_API_KEY" \
  "$SPANVOUCH_VLLM_BASE_URL/models"
```

Then send one non-streaming `/v1/chat/completions` request with JSON-object response
format, temperature zero and `chat_template_kwargs.enable_thinking=false`. Validate
the returned content against the diagnosis JSON schema, confirm the completion
envelope model is `Qwen/Qwen3-14B`, and record only allowlisted model, endpoint,
version, image digest and checkpoint revision provenance. Do not retain raw response
bodies or headers. A schema failure blocks the pilot.

Copy `evals/configs/phase5-qwen-vllm.example.json` outside the repository, replace
the null pins, set `smoke_only` to false, and run configuration validation before
requesting paid-run authorization.

## 4. Operate and shut down

Monitor GPU memory, request failures, token use and the Phase 5 budget ledger. Stop
at the registered budget or failure threshold. At the end, stop and remove the
container, revoke the API key, close the firewall rule, terminate the GPU lease, and
record the provider invoice/lease duration and CNY conversion evidence. Verify the
endpoint is no longer reachable before marking shutdown complete.
