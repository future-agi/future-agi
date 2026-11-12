<div align="center">

<a href="https://futureagi.com">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://futureagi.com/assets/logo-dark.svg">
    <img alt="Future AGI" src="https://futureagi.com/assets/logo-light.svg" height="64">
  </picture>
</a>

# Agent Command Center

### The OpenAI-compatible gateway for production AI.

**One gateway, every modality.**
Text · chat · embeddings · images · audio (speech + TTS) · OCR · rerank · structured decisions · Assistants + threads · vector stores · batch jobs — all with dedicated routes. (The realtime, video, search and scheduled-job routes answer 501 on this release.)
Route · cache · govern · guard · observe. Single Go binary. Drop-in OpenAI API.

<p>
  <a href="https://github.com/future-agi/future-agi/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-blue?style=flat-square" alt="Apache 2.0"></a>
  <a href="./go.mod"><img src="https://img.shields.io/badge/built%20with-Go%201.26.10-00ADD8?style=flat-square&logo=go" alt="Go 1.26.10"></a>
  <a href="https://docs.futureagi.com/docs/prism"><img src="https://img.shields.io/badge/docs-docs.futureagi.com-fafafa?style=flat-square" alt="Docs"></a>
  <a href="https://discord.com/invite/n2tCUKBkAw"><img src="https://img.shields.io/badge/community-Discord-5865F2?style=flat-square" alt="Discord"></a>
</p>

<p>
  <a href="#-quick-start-under-2-minutes"><b>Quick Start</b></a> ·
  <a href="#-benchmarks"><b>Benchmarks</b></a> ·
  <a href="#-features"><b>Features</b></a> ·
  <a href="https://docs.futureagi.com/docs/prism"><b>Docs</b></a>
</p>

</div>

---

## What it does

Agent Command Center sits between your app and LLM providers. Every request flows through it — so every request gets:

- **One OpenAI-compatible API** for every provider you configure. An OpenAI client needs only the gateway's base URL and a gateway key.
- **Routing and resilience** — retries, circuit breakers, provider failover, model fallback, and load balancing by round-robin, weight, latency, cost, adaptive scoring or a race between providers (`fastest`). Traffic mirroring is a self-hosted setting for the operator's own traffic.
- **Cost control** — an exact-match response cache that the gateway's operator turns on, opt-in per organization (a self-hosted gateway can add a semantic tier), budgets, an organization request quota, gateway keys and rate limits.
- **Safety** — built-in checks (PII, prompt injection and jailbreak, secrets, hallucination patterns, content moderation, keyword blocklist, topic, language, system-prompt protection, data leakage, input validation), rules that call your webhook, evaluate an expression, enforce tool permissions or MCP security, or run Future AGI evals, and adapters for 15 third-party guardrail vendors.
- **Observability** — Prometheus + OpenTelemetry, per-request metrics (cost, tokens, cache status, provider).
- **Modern protocols** — MCP, A2A, Assistants + threads, vector stores, batches, files, responses API. The realtime WebSocket and video routes answer 501 on this release.

One binary and one config file; a control plane is optional.

### On Future AGI's hosted gateway

The hosted deployment can leave some features switched off even where the code supports them. What it runs is listed in the [release notes, H1 to H4](../docs/gateway/release-notes.md#on-futureagis-hosted-gateway).

Where your gateway offers Future AGI credits (Gateway > Models shows the Future AGI credits card), you can turn credits on there. Connectors work where MCP is enabled for your gateway.

---

## 📈 Benchmarks

The only performance figures measured for this release are Go benchmarks of the gateway's chat-completion handler, run in process. This release publishes no throughput (req/s), no P50/P95/P99 under load, no end-to-end latency, no gateway-overhead figure and no comparison with other gateways, because none of those was measured on it.

### What one benchmark operation is

Each operation sends one `POST /v1/chat/completions` (`{"model":"gpt-4o","messages":[{"role":"user","content":"Hello"}]}`) straight into the server's HTTP handler: `ServeHTTP` with `httptest.NewRecorder()`, so the client side opens no socket. The benchmark server has request auth on with one key (an internal key; OwnKey uses an org key), the auth plugin as its only pipeline plugin (no cost, logging, rate-limit, cache or guardrail plugin), no routing strategy, and one provider. That provider is a mock OpenAI upstream, an `httptest.NewServer` listening on 127.0.0.1 that answers a canned response at once, so each operation includes one HTTP request and response over loopback TCP from the gateway to it. Source: [`internal/server/benchmark_test.go`](./internal/server/benchmark_test.go) and [`internal/server/own_key_benchmark_test.go`](./internal/server/own_key_benchmark_test.go).

These figures are not the latency a client sees and not the gateway's overhead in a deployment. A real request also crosses the client's network and the provider's network, waits for the provider's answer, and runs whatever plugins, guardrails and routing your config turns on.

### Results

This release's code, measured 2026-10-23 by the release gate on a Google Cloud n2d-standard-8 VM (8 vCPU, AMD EPYC 7B13, linux/amd64) that ran only the gate's benchmark job (the gate's Redis, Postgres, ClickHouse and MinIO test containers were up), go1.26.10 for both commits, `GOMAXPROCS=4`. Each benchmark has 40 samples: 20 rounds of `-count 2`, alternating with the dev commit this release merges (44285494be).

| Benchmark                             | One operation                                                                                                                                              |               Median ns/op | Range ns/op (40 samples) | allocs/op |
| ------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------: | -----------------------: | --------: |
| `BenchmarkChatCompletionNonStreaming` | one request at a time, internal key                                                                                                                        | **248 921** (about 249 µs) |        228 828 – 284 328 |       311 |
| `BenchmarkChatCompletionParallel`     | the same request from 4 goroutines at once (`b.RunParallel`); ns/op is the wall time divided by all requests completed, not the time any one request takes |                 **57 468** |          54 731 – 63 537 |       310 |
| `BenchmarkChatCompletionOwnKey`       | NonStreaming with an org key (`byok`) whose org's own `openai` provider serves the `openai/gpt-4o` it asks for                                             | **248 874** (about 249 µs) |        238 455 – 276 124 |       318 |

In the same run, dev 44285494be measured 235 190 ns/op and 304 allocs/op for NonStreaming, and 59 138 ns/op and 303 allocs/op for Parallel. So this release's NonStreaming median is 5.8 % (13 731 ns, about 14 µs) higher than dev's, and it allocates 7 more objects per request: 311 and 310 allocs/op are the ceilings the release's benchmark pin allows ([`release_bench_pin.txt`](./scripts/scenarios/gates/lib/release_bench_pin.txt): dev + 7, both built with the same Go). Dev has no OwnKey benchmark.

Reproduce: `GOMAXPROCS=4 go test ./internal/server -run '^$' -bench '^BenchmarkChatCompletion' -benchmem -count 40` (from `agentcc-gateway/`; see [Reproduce the benchmarks](#-reproduce-the-benchmarks)).

---

## 🚀 Quick start (under 2 minutes)

```bash
# 1. Docker. Pin the release you run: set VERSION to its tag, vX.Y.Z (every
#    published tag: https://hub.docker.com/r/futureagi/agentcc-gateway/tags).
#    The clone checks out the same tag, so the example config is that
#    release's. The image reads its config from /app/config.yaml, so mount
#    one: the example config serves OpenAI with your OPENAI_API_KEY.
VERSION=vX.Y.Z
git clone --depth 1 --branch "$VERSION" https://github.com/future-agi/future-agi.git
cd future-agi/agentcc-gateway
docker run -p 8080:8080 \
  -e OPENAI_API_KEY=sk-... \
  -v "$PWD/config.example.yaml:/app/config.yaml:ro" \
  "futureagi/agentcc-gateway:$VERSION"
# The alternative is futureagi/agentcc-gateway:latest, which moves to each
# new release.

# 2. Call any model through the OpenAI-compatible endpoint
curl http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "Hello"}]
  }'
```

<details><summary><b>Run from source (Go 1.25.0 or newer)</b></summary>

```bash
git clone https://github.com/future-agi/future-agi.git
cd future-agi/agentcc-gateway
cp config.example.yaml config.yaml
go run ./cmd/agentcc --config config.yaml
```

`go.mod` names go1.26.10 as its toolchain, the Go this release is built and tested with. With the default `GOTOOLCHAIN=auto`, an older Go downloads go1.26.10 from the module proxy and builds with it; a newer Go builds with itself.

</details>

<details><summary><b>Kubernetes / Helm</b></summary>

The Future AGI Helm chart (`deploy/helm/futureagi`) runs the gateway with the rest of the platform (its `agentccGateway` values). You can also run AgentCC Gateway with Docker or as part of the Future AGI Docker Compose stack.

</details>

---

## 🧩 Features

<table>
<tr>
<th width="25%">Core routing</th>
<th width="25%">Resilience</th>
<th width="25%">Caching</th>
<th width="25%">Governance</th>
</tr>
<tr valign="top">
<td>

- OpenAI-compatible REST
- Any OpenAI-compatible provider
- Anthropic-native format
- Google (Gemini / Vertex) format
- Bedrock format
- Model aliases + weights
- Traffic mirroring (self-hosted, operator traffic only)

</td>
<td>

- **Load balancing**: round-robin, weighted, least-latency, cost-optimized, adaptive, fastest (race)
- Retries, provider failover, circuit breakers, model fallback chains, complexity tiers, provider lock, access groups
- Cross-provider format translation (OpenAI ↔ Anthropic ↔ Gemini ↔ Bedrock)
- Prompt caching (Anthropic `cache_control`)
- W3C Trace Context propagation
- Multi-replica: RPM and organization TPM counters, budget spend, credits, key revocations and provider key rotations shared through Redis; a config-file key's own TPM counter stays replica-local
- Edge worker script (`edge`, served at `/-/admin/edge/config`)

</td>
<td>

- **6 exact-cache backends**<br>memory · redis · disk · s3 · gcs · azure-blob
- **4 semantic backends** (self-hosted, gateway-wide)<br>memory · pinecone · qdrant · weaviate
- TTL, LRU eviction
- Entries keyed per organization
- Configurable similarity threshold
- Non-streaming requests the gateway runs as a chat request: streams, embeddings and Responses are never cached ([details](#-response-cache))

</td>
<td>

- Virtual keys per tenant/user
- Budget caps in USD
- Rate limits per key, organization, model and user (RPM and TPM)
- RBAC + team hierarchy (self-hosted config.yaml)
- IP allowlists
- Audit events (JSON lines on stdout)

</td>
</tr>
<tr>
<th>Safety</th>
<th>Modern protocols</th>
<th>Observability</th>
<th>Developer experience</th>
</tr>
<tr valign="top">
<td>

- PII · prompt injection · jailbreak
- Secrets · hallucination patterns
- Content moderation · keyword blocklist · topic · language
- System-prompt protection · data leakage · input validation
- MCP security · tool permissions
- Expression rules
- Webhook + **15 external vendor adapters** (Lakera, Aporia, AWS Bedrock Guardrails, Azure Content Safety, Presidio, Llama Guard, Pangea, Enkrypt, Lasso, HiddenLayer, Gray Swan, DynamoAI, CrowdStrike, IBM, Zscaler)
- Future AGI evals

</td>
<td>

- MCP (tools + resources + prompts)
- A2A (agent-to-agent)
- Batches (`/-/batches`, admin token)
- Files API (held in memory)
- OpenAI Responses API
- Streaming SSE + chunk-level plugins
- Webhooks

</td>
<td>

- Self-hosted Prometheus metrics at `/-/metrics` or `prometheus.path`, admin token (request, cost, tokens, cache, errors); no hosted scrape endpoint
- OpenTelemetry spans (W3C context propagation up + down)
- Response-header scrubber — strips upstream fingerprints
- Cost from the bundled LiteLLM pricing database
- Per-tenant spend sync
- Response headers: `x-agentcc-provider`, `x-agentcc-cost`, `x-agentcc-cache`, `x-agentcc-latency-ms` ([all headers](#-request-and-response-headers))
- Audit events on stdout (request, budget, guardrail)
- Threshold alerts on requests, errors, tokens, cost and latency, with a severity and a cooldown

</td>
<td>

- Single Go binary
- YAML-first config; `POST /-/reload` reloads routing
- Env-var substitution
- Client SDKs: Python (`agentcc`), TypeScript (`@agentcc/client`)
- Docker + Helm

</td>
</tr>
</table>

---

## 📋 Capabilities in detail

**One gateway, every modality your providers expose.** Text · chat · vision · embeddings · reranking · speech-to-text · text-to-speech (+ streaming) · image generation · OCR · structured decisions · tool calling · structured output · Assistants + threads · vector stores · batch jobs — all have dedicated gateway routes. The specific model you reach is whatever the configured upstream provider serves at that endpoint; we pass the full provider-native payload through. The realtime, video, search and scheduled-job routes (`/v1/realtime`, `/v1/videos`, `/v1/search`, `/v1/scheduled`) exist but are not available on this release: they answer 501 (`/v1/realtime` answers 403 to any key but an internal one before that). No provider adapter that ships with this release serves realtime, video or search, and the config refuses `routing.scheduled.enabled`.

### 🌈 Modalities

Every modality below has dedicated routes in the gateway. The **specific models** reachable depend on what your configured provider(s) support — we pass through whatever the provider exposes at the endpoint.

| Modality                                     | Gateway endpoints                                                                                                    |
| -------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| Text / chat                                  | `/v1/chat/completions` · `/v1/completions` · `/v1/messages` (Anthropic) · `/v1/responses`                            |
| Vision (image → text)                        | `/v1/chat/completions` with image content parts                                                                      |
| Image generation                             | `/v1/images/generations`                                                                                             |
| Speech-to-text                               | `/v1/audio/transcriptions` · `/v1/audio/translations` · streaming: `/deepgram/v1/listen` · `/cartesia/stt/websocket` |
| Text-to-speech (+ streaming)                 | `/v1/audio/speech` · `/v1/audio/speech/stream` · streaming: `/deepgram/v1/speak` · `/cartesia/tts/websocket`         |
| Embeddings                                   | `/v1/embeddings`                                                                                                     |
| Reranking                                    | `/v1/rerank`                                                                                                         |
| Structured decisions (your own provider key) | `/v1/systemone` · `/v1/decisions` · `/v1/decisions/models`                                                           |
| OCR                                          | `/v1/ocr`                                                                                                            |
| Tool calling                                 | on all chat endpoints (provider-native schemas preserved)                                                            |
| Structured output                            | `/v1/chat/completions` · `/v1/responses` (JSON schema)                                                               |
| Assistants + threads                         | `/v1/assistants` · `/v1/threads` · `/v1/threads/{id}/{messages,runs,runs/{id}/steps}`                                |
| Vector stores (RAG)                          | `/v1/vector_stores` · `/v1/vector_stores/{id}/{files,file_batches,search}`                                           |
| Batch (async)                                | `/-/batches` (admin token + `x-agentcc-batch-key`; each item a chat completion run as the key's org)                 |

### 🔌 Endpoints

<table>
<tr><th>Category</th><th>Endpoints</th><th>Highlights</th></tr>
<tr>
<td><b>Chat &amp; Completions</b></td>
<td><code>POST /v1/chat/completions</code><br><code>POST /v1/completions</code><br><code>POST /v1/messages</code> <sub>(Anthropic native)</sub></td>
<td>Streaming + non-streaming · OpenAI + Anthropic + Gemini + Bedrock formats preserved round-trip · logprobs · tool calls · structured output · vision</td>
</tr>
<tr>
<td><b>Embeddings &amp; Rerank</b></td>
<td><code>POST /v1/embeddings</code><br><code>POST /v1/rerank</code></td>
<td>An embedding request makes one upstream attempt, with no failover or model fallback, and is never served from the response cache</td>
</tr>
<tr>
<td><b>Decisions</b></td>
<td><code>POST /v1/systemone</code><br><code>POST /v1/decisions</code><br><code>GET /v1/decisions/models</code></td>
<td>System One (TypeSafe Jev, Perplexity Decider, any System One endpoint) and OpenAI Decisions · body forwarded byte for byte · always the customer's own provider key</td>
</tr>
<tr>
<td><b>Audio</b></td>
<td><code>POST /v1/audio/transcriptions</code><br><code>POST /v1/audio/translations</code><br><code>POST /v1/audio/speech</code><br><code>POST /v1/audio/speech/stream</code></td>
<td>Transcription, translation and speech through the provider's audio API · speech streamed at <code>/v1/audio/speech/stream</code></td>
</tr>
<tr>
<td><b>Images</b></td>
<td><code>POST /v1/images/generations</code></td>
<td>Image generation through the provider's images API</td>
</tr>
<tr>
<td><b>Video</b></td>
<td><code>POST /v1/videos</code><br><code>GET /v1/videos</code><br><code>GET /v1/videos/{id}</code><br><code>DELETE /v1/videos/{id}</code></td>
<td>Answer 501 <code>endpoint_not_configured</code> on this release: no provider adapter that ships with it serves video</td>
</tr>
<tr>
<td><b>OCR &amp; Search</b></td>
<td><code>POST /v1/ocr</code><br><code>POST /v1/search</code></td>
<td>Document OCR · <code>/v1/search</code> answers 501 <code>not_supported</code> on this release: no provider adapter that ships with it serves search</td>
</tr>
<tr>
<td><b>Token counting</b></td>
<td><code>POST /v1/count_tokens</code><br><code>POST /v1/messages/count_tokens</code></td>
<td><code>/v1/count_tokens</code> estimates the count (about four characters a token, plus a few per message) and reports the model's context window · <code>/v1/messages/count_tokens</code> passes through to Anthropic's count</td>
</tr>
<tr>
<td><b>Batches</b></td>
<td><code>POST /-/batches</code> · <code>GET</code> · cancel (admin token)</td>
<td>Async chat-completion batches, each item run as the org of the key in <code>x-agentcc-batch-key</code>; held in memory, not the OpenAI Batch API</td>
</tr>
<tr>
<td><b>Files</b></td>
<td><code>POST /v1/files</code> · list · get · download · delete</td>
<td>Held in memory by the gateway that received the upload</td>
</tr>
<tr>
<td><b>Responses API</b></td>
<td><code>POST /v1/responses</code> · get · delete</td>
<td>OpenAI Responses API — tool calling, structured output, background responses</td>
</tr>
<tr>
<td><b>Realtime (WebSocket)</b></td>
<td><code>GET /v1/realtime</code></td>
<td>Not available on this release: answers 501 to an internal key (no provider adapter that ships with it serves realtime) and 403 to every other key</td>
</tr>
<tr>
<td><b>Streaming speech (WebSocket)</b></td>
<td><code>GET /deepgram/v1/listen</code> · <code>/deepgram/v1/speak</code> · <code>/cartesia/tts/websocket</code> · <code>/cartesia/stt/websocket</code> · <code>/cartesia/stt/turns/websocket</code></td>
<td>Deepgram and Cartesia's own streaming APIs, relayed on the platform's vendor key · vendor SDKs need only a base URL and the gateway key · metered in audio seconds / characters</td>
</tr>
<tr>
<td><b>Assistants API</b></td>
<td><code>/v1/assistants</code> · create · get · update · delete · list</td>
<td>Proxied to the provider's Assistants (Beta) API. <code>assistants.enabled: false</code> turns the assistants, threads and vector-store routes off (404 <code>assistants_disabled</code>)</td>
</tr>
<tr>
<td><b>Threads &amp; Runs</b></td>
<td><code>/v1/threads</code> · <code>/messages</code> · <code>/runs</code> · <code>/steps</code> · cancel · submit_tool_outputs</td>
<td>Proxied to the provider's threads and runs</td>
</tr>
<tr>
<td><b>Vector Stores</b></td>
<td><code>/v1/vector_stores</code> · files · file_batches · search · delete</td>
<td>Proxied to the provider's vector store API</td>
</tr>
<tr>
<td><b>Agents (A2A)</b></td>
<td><code>GET /v1/agents</code><br><code>POST /v1/a2a</code><br><code>GET /.well-known/agent.json</code></td>
<td>Agent-to-agent protocol · agent discovery · AgentCard (with <code>a2a.enabled</code>)</td>
</tr>
<tr>
<td><b>MCP</b></td>
<td><code>POST</code> · <code>GET</code> · <code>DELETE /mcp</code> · <code>/mcp/v/{vs_id}</code><br><code>GET /-/mcp/status</code> · tools · resources · prompts · <code>POST /-/mcp/test</code> (each names its <code>org_id</code>)</td>
<td>One MCP endpoint for your agents: each organization's own upstream servers, virtual servers, one record per tool call · per-key tool filters · MCP security guardrail</td>
</tr>
<tr>
<td><b>Scheduled jobs</b></td>
<td><code>POST /v1/scheduled</code> · list · get · delete</td>
<td>Answer 501 <code>endpoint_not_configured</code> on this release: the config refuses <code>routing.scheduled.enabled</code></td>
</tr>
<tr>
<td><b>Async jobs</b></td>
<td><code>GET /v1/async/{id}</code> · delete</td>
<td>Background job status for long-running endpoints</td>
</tr>
<tr>
<td><b>Models</b></td>
<td><code>GET /v1/models</code> · get</td>
<td>OpenAI-compatible list · only the models the key can call · model pricing</td>
</tr>
<tr>
<td><b>Provider format native (Gemini)</b></td>
<td><code>POST /v1beta/models/{action}</code></td>
<td>Google-native endpoints for apps wired to the Gemini SDK</td>
</tr>
<tr>
<td><b>Health</b></td>
<td><code>/healthz</code> · <code>/livez</code> · <code>/readyz</code> · <code>/-/health/providers</code></td>
<td>K8s-compatible probes · per-provider upstream health</td>
</tr>
<tr>
<td><b>Admin — keys, orgs, config</b></td>
<td><code>/-/keys</code> · <code>/-/orgs/{id}/config</code> · <code>/-/keys/{id}/credits</code> · <code>/-/reload</code> · <code>/-/config</code></td>
<td>Virtual-key CRUD · per-org config · live reload without restart · this gateway's settings (no secret) and the org config sections it enforces (<code>org_sections_enforced</code>)</td>
</tr>
<tr>
<td><b>Admin — operations</b></td>
<td><code>/-/metrics</code> (or <code>prometheus.path</code>) · <code>/-/cluster/nodes</code> · <code>/-/shadow/stats</code> · <code>/-/admin/edge/config</code></td>
<td>Prometheus scrape · this node's registry entry (membership is per node) · stats of the results <code>routing.mirror</code> keeps with <code>capture_results</code> · the edge worker script (with <code>edge.enabled</code>)</td>
</tr>
<tr>
<td><b>Admin — key rotation</b></td>
<td><code>/-/admin/providers/{id}/rotation</code> · <code>/rotate</code> · <code>/promote</code> · <code>/rollback</code></td>
<td>Provider-key rotation without a restart: the promote checks the new key with one upstream call, then every new call sends it, on every replica with Redis; rollback cancels a pending rotation. Update the deployment config with the new key before the next restart or rollout</td>
</tr>
</table>

<sub>Every route: grep `router.Handle` under <code>internal/server/\*.go</code>.</sub>

### 🛡️ Guardrails

Each rule in `guardrails.rules` runs at stage `pre` (on the request) or `post` (on the answer), in `sync` or `async` mode, with action `block`, `warn` or `log`; the engine skips a rule whose name it does not register or whose stage is neither. Packages live under [`internal/guardrails/`](./internal/guardrails/).

Built-in checks, chosen by the rule's `name`:

| Rule `name`                | Package         | What it catches                                                                                                             |
| -------------------------- | --------------- | --------------------------------------------------------------------------------------------------------------------------- |
| `pii-detection`            | `pii`           | Emails, phone numbers, SSNs, credit cards, IBANs, MAC addresses and other identifiers — masked, redacted, hashed or blocked |
| `prompt-injection`         | `injection`     | Prompt-injection and jailbreak attempts                                                                                     |
| `secret-detection`         | `secrets`       | Credentials and secrets in requests or answers                                                                              |
| `hallucination-detection`  | `hallucination` | Heuristic patterns of a possible hallucination in the answer (no fact-checking model)                                       |
| `content-moderation`       | `contentmod`    | Toxicity, hate speech, violence, sexual content, self-harm                                                                  |
| `keyword-blocklist`        | `blocklist`     | Words and phrases you list                                                                                                  |
| `topic-restriction`        | `topic`         | Topics outside an allowed list, or forbidden topics                                                                         |
| `language-detection`       | `language`      | Scripts and languages that are not allowed                                                                                  |
| `system-prompt-protection` | `sysprompt`     | Attempts to extract or override the system prompt                                                                           |
| `data-leakage-prevention`  | `leakage`       | Data-leakage and code-injection patterns in the answer                                                                      |
| `input-validation`         | `validation`    | Regex patterns and length limits                                                                                            |

Rules chosen by their `config`, under any name:

| Rule `config`               | Package      | What it does                                                                                        |
| --------------------------- | ------------ | --------------------------------------------------------------------------------------------------- |
| `url: ...`                  | `webhook`    | Calls your own HTTP endpoint                                                                        |
| `expression: ...`           | `expression` | Evaluates an expression against request and response data                                           |
| `provider: tool_permission` | `toolperm`   | Allows or denies tool calls by name                                                                 |
| `provider: mcp_security`    | `mcpsec`     | MCP server allowlists, blocked tools, injection checks on tool inputs, tool-call limits per request |
| `provider: futureagi`       | `futureagi`  | Runs Future AGI evals; the rule needs its own `api_key`, `secret_key` and `eval_id` or `eval_ids`   |
| `provider: <vendor>`        | `external`   | One of the 15 vendor adapters below                                                                 |

A model's refusal isn't sent to evaluation, webhook or external checks until the check's kind is set; built-in content checks read it.

<sup>Per-guardrail latency varies by payload, provider, and scanner. This release publishes no per-guardrail latency figure.</sup>

#### 🤝 External guardrail vendors — 15 first-class adapters

Already have a preferred safety stack? Point a rule at it with `provider: <vendor>` in its `config`. Each vendor has a dedicated Go adapter with auth + request/response mapping; its results flow through the same decision pipeline as the built-ins. Packages live under [`internal/guardrails/external/`](./internal/guardrails/external/).

| Vendor                   | `provider`             | Package filename |
| ------------------------ | ---------------------- | ---------------- |
| AWS Bedrock Guardrails   | `bedrock_guardrails`   | `bedrock.go`     |
| Azure AI Content Safety  | `azure_content_safety` | `azure.go`       |
| IBM                      | `ibm_ai`               | `ibm.go`         |
| Lakera                   | `lakera`               | `lakera.go`      |
| Aporia                   | `aporia`               | `aporia.go`      |
| Presidio (Microsoft OSS) | `presidio`             | `presidio.go`    |
| Llama Guard (Meta OSS)   | `llama_guard`          | `llamaguard.go`  |
| Pangea                   | `pangea`               | `pangea.go`      |
| Enkrypt AI               | `enkrypt`              | `enkrypt.go`     |
| Lasso Security           | `lasso`                | `lasso.go`       |
| HiddenLayer              | `hiddenlayer`          | `hiddenlayer.go` |
| Gray Swan                | `grayswan`             | `grayswan.go`    |
| DynamoAI                 | `dynamoai`             | `dynamoai.go`    |
| CrowdStrike              | `crowdstrike`          | `crowdstrike.go` |
| Zscaler                  | `zscaler`              | `zscaler.go`     |

They run in the same rule list as the built-in checks. Example config:

```yaml
guardrails:
  enabled: true
  rules:
    - name: "lakera"
      stage: "pre"
      action: "block"
      config:
        provider: "lakera"
        api_key: "${LAKERA_API_KEY}"
    - name: "bedrock"
      stage: "pre"
      action: "block"
      config:
        provider: "bedrock_guardrails"
        guardrail_id: "gr-abc123"
        guardrail_version: "DRAFT"
    - name: "presidio"
      stage: "pre"
      action: "block"
      config:
        provider: "presidio"
        endpoint: "http://presidio:8080" # the gateway calls <endpoint>/analyze
    - name: "pii-detection" # built-in check alongside the external ones
      stage: "pre"
      action: "block"
```

`${VAR}` is expanded from the environment only in this `config.yaml`, which the operator controls. A guardrail an organization configures through the console or the control plane gives the value itself: a `${...}` in a FutureAGI check's `api_key`, `secret_key` or `base_url`, or in any top-level setting of an external-provider check, is refused, so the gateway never resolves it against its own environment. Anywhere else in a guardrail (a blocklist word such as `${jndi:`, a pattern, an expression, a webhook header, a check name) `${` is plain text.

### 🔧 Pipeline plugins

Every request flows through an ordered plugin pipeline. Pre-stage guardrails run before the response cache (priorities 34 and 35), so a cache hit never bypasses the checks on the request. The table describes the main plugins' roles, not their execution order:

| Plugin       | Phase         | What it does                                                                                                                                                                                              |
| ------------ | ------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ipacl`      | Pre-auth      | CIDR-based IP allow/deny                                                                                                                                                                                  |
| `auth`       | Pre-auth      | API-key lookup, tenant/org resolution                                                                                                                                                                     |
| `rbac`       | Post-auth     | Role permissions on the model, the provider and guardrail overrides (self-hosted config.yaml `rbac`)                                                                                                      |
| `ratelimit`  | Pre-request   | Requests per minute per key; an organization's requests and tokens per minute for the whole org, per key, per model and per user (chat completions, completions, Responses, Messages and Gemini requests) |
| `quota`      | Pre-request   | An organization's monthly gateway-request limit and pause, read from Redis (with `redis` on)                                                                                                              |
| `budget`     | Pre-request   | Dollar caps per organization, team, user, key and tag; past a cap a budget blocks, throttles or warns                                                                                                     |
| `credits`    | Pre-request   | Credit holds for models served on Future AGI credits (`managed: true` providers), which ship switched off                                                                                                 |
| `cache`      | Pre-upstream  | Exact lookup (and the semantic tier, where config.yaml turns it on) and write-back                                                                                                                        |
| `toolpolicy` | Pre-upstream  | Strips or refuses tools by an allow and deny list (the gateway's `tool_policy` and an organization's)                                                                                                     |
| `validation` | Pre-request   | Checks a request against the model database: token limits and capability switches, the gateway's and the organization's                                                                                   |
| `cost`       | Post-upstream | Per-request cost calc using LiteLLM pricing DB                                                                                                                                                            |
| `prometheus` | Always        | Histogram + counter emission                                                                                                                                                                              |
| `otel`       | Always        | OpenTelemetry spans (W3C Trace Context)                                                                                                                                                                   |
| `audit`      | Always        | Audit events as JSON lines on stdout with `audit.enabled` (request, budget, guardrail)                                                                                                                    |
| `alerting`   | Always        | Threshold alerts on request, error, token, cost and latency metrics (webhook, Slack, log); an org rule carries a severity and waits its cooldown (default 15m)                                            |
| `logging`    | Always        | Structured JSON access log with redaction                                                                                                                                                                 |

An organization's `ip_acl`, `budgets`, `tool_policy` and `alerting` settings (Gateway > Org Config) run in the `ipacl`, `budget`, `toolpolicy` and `alerting` plugins on every gateway that receives org configs (`control_plane.url` or `admin.token` set), whatever the gateway's own switches for them say (an org's `ip_acl` only where `server.trusted_proxies` or `server.trusted_hops` is set; without either, `GET /-/config` reports it in `org_sections_not_enforced`); the switches add the gateway's own rules. An org's `cache` settings need the gateway's `cache.enabled`, and its `rate_limiting` settings `rate_limiting.enabled`. `GET /-/config` lists what a gateway enforces (`org_sections_enforced`). An organization's guardrail checks (Gateway > Guardrails) need the gateway's `guardrails.enabled`: `config.example.yaml`, `config.hosted-local.yaml` and the Helm chart set it to `true` with no rule of their own, so only the checks an organization saves run; `GET /-/config` reports it as `guardrail_pipeline.enabled`.

### 🌐 Provider formats

Each provider block names its `api_format`; the gateway speaks that format to the provider and answers the caller in the API it called.

| `api_format`                | Providers                                                                                                                                                                                                                                                   | Notes                                                                                                                                                                                         |
| --------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `openai`                    | OpenAI · Groq · Together · Fireworks · OpenRouter · Perplexity (`api_path_prefix: ""`, as in config.example.yaml) · Mistral · DeepInfra · Cerebras · xAI · Hugging Face · self-hosted vLLM · LM Studio · Ollama · TGI · any other OpenAI-compatible service | streaming, logprobs, tool calling, structured output, vision, JSON mode                                                                                                                       |
| `azure`                     | Azure OpenAI                                                                                                                                                                                                                                                | deployment names, the `api-version` header, api-key or Azure AD bearer auth (`auth-type: bearer`)                                                                                             |
| `anthropic`                 | Anthropic                                                                                                                                                                                                                                                   | native `/messages` round-trip, `cache_control` blocks                                                                                                                                         |
| `gemini` (or `google`)      | Google AI Studio; Vertex AI, with a service-account `credentials_file` or a bearer token                                                                                                                                                                    | `/v1beta/models/{action}` native routing                                                                                                                                                      |
| `bedrock`                   | AWS Bedrock                                                                                                                                                                                                                                                 | SigV4 signing with AWS credentials                                                                                                                                                            |
| `cohere`                    | Cohere                                                                                                                                                                                                                                                      | chat, embeddings, rerank                                                                                                                                                                      |
| `systemone` (or `typesafe`) | TypeSafe (Jev) · Perplexity Decider · any System One endpoint (Microsoft Foundry, Strands, vLLM)                                                                                                                                                            | structured decisions only (`/v1/systemone`, `/v1/decisions`): `noul` / `choice` / `score` questions and any new type passed through untouched, vendor-reported usage; never probed for models |
| `deepgram`                  | Deepgram                                                                                                                                                                                                                                                    | streaming speech-to-text and Aura text-to-speech over Deepgram's own WebSocket API                                                                                                            |
| `cartesia`                  | Cartesia                                                                                                                                                                                                                                                    | streaming Sonic text-to-speech and Ink speech-to-text over Cartesia's own WebSocket API                                                                                                       |

An OpenAI-compatible provider needs only a block in the config file (`base_url`, `api_format`, `models`); for the provider types below, `type` fills in the defaults the block leaves empty. See [`config.example.yaml`](./config.example.yaml).

#### Provider type defaults

In `config.yaml`, `type` selects the following defaults **before validation**, at
startup and reload. Only an empty `base_url` or `api_format`, or an unset
`api_path_prefix`, is filled. Explicit values always win, including
`api_path_prefix: ""`. The provider's map ID does not select a preset, and type
names are exact, lowercase matches.

| `type`        | Default `base_url`                   | Default `api_format` | Default `api_path_prefix` |
| ------------- | ------------------------------------ | -------------------- | ------------------------- |
| `groq`        | `https://api.groq.com/openai`        | `openai`             | `/v1`                     |
| `mistral`     | `https://api.mistral.ai`             | `openai`             | `/v1`                     |
| `together`    | `https://api.together.xyz`           | `openai`             | `/v1`                     |
| `fireworks`   | `https://api.fireworks.ai/inference` | `openai`             | `/v1`                     |
| `deepinfra`   | `https://api.deepinfra.com`          | `openai`             | `/v1`                     |
| `perplexity`  | `https://api.perplexity.ai`          | `openai`             | `/v1`                     |
| `cerebras`    | `https://api.cerebras.ai`            | `openai`             | `/v1`                     |
| `xai`         | `https://api.x.ai`                   | `openai`             | `/v1`                     |
| `huggingface` | `https://router.huggingface.co`      | `openai`             | `/v1`                     |
| `anyscale`    | `https://api.endpoints.anyscale.com` | `openai`             | `/v1`                     |
| `replicate`   | `https://api.replicate.com`          | `openai`             | `/v1`                     |
| `openrouter`  | `https://openrouter.ai/api`          | `openai`             | `/v1`                     |
| `azure`       | No default; supply the resource URL  | `azure`              | Not filled by the preset  |

For example, a Groq provider needs only its type and key to pass provider-field
validation (configure models or discovery as appropriate):

```yaml
providers:
  my-groq:
    type: groq
    api_key: ${GROQ_API_KEY}
```

Azure still requires its resource-specific `base_url`; type plus key alone is
not sufficient. An unknown or absent type still requires both `base_url` and
`api_format`. A missing-field error names the provider ID, the nonempty type,
and the missing field.

These are the existing preset values, not new endpoint mappings or a guarantee
that every upstream exposes every gateway API. Keep endpoint-specific overrides:
Perplexity Sonar uses `api_path_prefix: ""`; DeepInfra's OpenAI-compatible endpoint
uses `api_path_prefix: /v1/openai`. Type does not fill credentials, models,
`managed`, or any tenant policy. Organization overrides keep their existing
precedence: an org's own upstream is independent of the config.yaml provider,
and an org inheriting that provider's endpoint uses its own key and may override
the path prefix.

### 💾 Response cache

The exact tier keys an entry by the request, as the checks on the request left it, and by the caller's organization. A self-hosted gateway can add a semantic tier in its config file (`cache.semantic`): it matches a new prompt to a stored one by the similarity of character n-gram vectors (no embedding model is called). Its backend, threshold and vector dimensions are gateway-wide settings, not settings an organization can change through its own `cache.semantic` subsection; that subsection is not applied.

An organization's `cache.enabled` and `cache.default_ttl` govern both tiers when the gateway's `cache.enabled` is on. Disabling the org cache bypasses exact and semantic reads and writes. The resolved TTL applies to entries in both tiers: a per-request TTL overrides the org's default TTL, which overrides the gateway's default. Semantic entries are partitioned by organization too.

The response cache runs only on a gateway whose operator turns it on (`cache.enabled` in the config file); FutureAGI's hosted gateway runs with it off (release notes, H2). On every deployment it is also opt-in per organization: a request is cached only when its key's organization has an org config (from the control plane, or `PUT /-/orgs/{org_id}/config`) with `cache.enabled` on. A request with no org config, such as one sent with a config-file key that has no `org_id`, or one sent without a key while request auth is off, is never cached. With the org cache off or no org config, `x-agentcc-cache` is `skip_org_disabled`.

The cache boundary is the execution path, not the provider's API format. It covers non-streaming requests executed through the canonical/chat pipeline: `/v1/chat/completions`, `/v1/completions`, and `/v1/messages`, including Messages requests routed or retried through an Anthropic-format provider. For Messages, only the native pass-through path bypasses the cache; a provider speaking Messages natively does not by itself imply pass-through. Gemini `generateContent` is cached when translated for a provider that does not speak that API; its native pass-through path bypasses the cache. It never covers streams (they bypass both tiers on every deployment), embeddings or the Responses API. Native pass-through `/v1/messages` and `generateContent` requests report `x-agentcc-cache: skip`. An entry is keyed by the chat form of the request, not by its route: a legacy completion's prompt becomes one user message, so it shares an entry with a chat completion whose only message is that prompt, for the same model and parameters. Known limit: a `/v1/completions` request served by an OpenAI-compatible provider, which gets the legacy request as written, is stored, but a later request that hits the entry (or an entry such a chat completion stored) is answered 500 `internal_error` ("no response from provider") instead of the cached answer; `/v1/completions` through any other provider is served from the cache.

A cache hit is not charged and has no `x-agentcc-cost` header (it is absent, not `0`). Pre-stage guardrails still run before the cache, and output checks read the cached answer.

| Tier     | `backend`    | Persistence          | Typical use                                                                                                                     |
| -------- | ------------ | -------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| Exact    | `memory`     | in-process           | single-replica dev / test                                                                                                       |
| Exact    | `redis`      | shared               | multi-replica; one node, Redis Cluster (`mode: cluster`) or Sentinel (`mode: sentinel`), TLS with `tls: true` (never plaintext) |
| Exact    | `disk`       | local disk           | single-node, restart-safe                                                                                                       |
| Exact    | `s3`         | AWS S3               | archival, cold cache                                                                                                            |
| Exact    | `gcs`        | Google Cloud Storage | archival                                                                                                                        |
| Exact    | `azure-blob` | Azure Blob           | archival                                                                                                                        |
| Semantic | `memory`     | in-process           | prototyping                                                                                                                     |
| Semantic | `pinecone`   | managed vector       |                                                                                                                                 |
| Semantic | `qdrant`     | self-hosted vector   |                                                                                                                                 |
| Semantic | `weaviate`   | self-hosted vector   |                                                                                                                                 |

Set per gateway: the default TTL and the maximum entries of the exact tier, and the semantic tier's similarity threshold and vector dimensions.

### 🧩 Internal packages

Each is its own Go package under [`internal/`](./internal/).

<table>
<tr>
<th>Group</th>
<th>Packages</th>
</tr>
<tr>
<td><b>Request path</b></td>
<td><code>server</code> (with its router) · <code>middleware</code> · <code>pipeline</code> · <code>routing</code> · <code>streaming</code> · <code>translation</code></td>
</tr>
<tr>
<td><b>Providers</b></td>
<td><code>providers/{openai,anthropic,azure,bedrock,cohere,gemini,gauth}</code> · <code>anthropicfmt</code> · <code>genaifmt</code></td>
</tr>
<tr>
<td><b>Safety</b></td>
<td><code>guardrails/*</code> · <code>privacy</code> · <code>secrets</code></td>
</tr>
<tr>
<td><b>Governance</b></td>
<td><code>auth</code> · <code>rbac</code> · <code>tenant</code> · <code>budget</code> · <code>rotation</code> (key rotation)</td>
</tr>
<tr>
<td><b>Caching &amp; state</b></td>
<td><code>cache</code> · <code>redisstate</code> · <code>modeldb</code> (LiteLLM pricing) · <code>tokenizer</code></td>
</tr>
<tr>
<td><b>Async &amp; jobs</b></td>
<td><code>async</code> · <code>batch</code> · <code>scheduled</code> (its routes answer 501 on this release)</td>
</tr>
<tr>
<td><b>Modern protocols</b></td>
<td><code>mcp</code> · <code>a2a</code> · <code>realtime</code> · <code>responses</code> · <code>files</code> · <code>video</code> (the realtime and video routes answer 501 on this release)</td>
</tr>
<tr>
<td><b>Deployment</b></td>
<td><code>cluster</code> (this node's registry entry) · <code>edge</code> (edge mode) · <code>config</code></td>
</tr>
<tr>
<td><b>Observability</b></td>
<td><code>metrics</code> · <code>otel</code> · <code>audit</code> · <code>alerting</code></td>
</tr>
<tr>
<td><b>Plugins</b></td>
<td><code>plugins/*</code> (pipeline plugins — see table above)</td>
</tr>
</table>

### 🔐 Governance & access control

| Capability                        | How it works                                                                                                                                                                                                                                                                    | Granularity                           |
| --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------- |
| **Virtual keys**                  | Gateway-issued keys abstract real provider keys; rotate upstream without app changes                                                                                                                                                                                            | tenant · user · team                  |
| **Key rotation**                  | Without a restart: rotate → promote (validated with one upstream call) via <code>/-/admin/providers/{id}/rotate</code>; rollback cancels a pending rotation; update the deployment config with the new key before the next restart or rollout                                   | per provider                          |
| **Key-scoped model ACL**          | <code>models: [...]</code> / <code>providers: [...]</code> on each key — requests outside the list rejected at the handler                                                                                                                                                      | key                                   |
| **Budgets**                       | Dollar caps per period (daily, weekly, monthly or total) with per-model caps, enforced in the `budget` plugin; past a cap a budget blocks (429), throttles to `throttle_rpm` requests a minute (429 `budget_throttled` with `Retry-After`) or warns                             | org · team · user · key · tag · model |
| **Credits ledger**                | Managed keys charge against a prepaid ledger; switched off in this release (no shipped config marks a provider managed)                                                                                                                                                         | key                                   |
| **Rate limits**                   | The `ratelimit` plugin shares RPM and organization TPM counters through Redis (`redis.enabled`); a config-file key's own `rate_limit_tpm` stays replica-local even with Redis (details below)                                                                                   | key · organization · model · user     |
| **Quotas**                        | An organization's monthly gateway-request limit, and a pause, in the `quota` plugin (needs Redis)                                                                                                                                                                               | organization                          |
| **IP allowlist**                  | CIDR ranges, checked at the key gate on every route and in the `ipacl` plugin; the client IP is the TCP peer unless `server.trusted_proxies` names the proxies in front, and an organization's list applies only where `server.trusted_proxies` or `server.trusted_hops` is set | key · tenant                          |
| **RBAC**                          | Roles with permissions on models, providers and guardrail overrides; teams with model lists; set in the gateway's config file only (an org config has no `rbac` section)                                                                                                        | gateway                               |
| **Tool-call governance** | `allowed_tools` / `denied_tools` per key, enforced for MCP + function calling; on MCP, each organization's own servers, and a virtual server's access rules by key, agent and user | key · org · agent · user |
| **Audit log**                     | JSON-lines events on stdout (`audit.enabled`): request start and end, budget checks, guardrail triggers; ship them with your log collector                                                                                                                                      | node                                  |
| **Multi-tenant isolation**        | Separate rate-limit counters and response-cache keys per organization, whose keys use its own providers; the latency and error ranking of model `auto` is gateway-wide (release notes, Known limits), and traffic mirroring never copies an organization key's request          | tenant                                |
| **SSO** (via Future AGI platform) | SAML 2.0 + OIDC                                                                                                                                                                                                                                                                 | org                                   |

With Redis available, replicas share the key RPM counter (`auth.keys[].rate_limit_rpm`, falling back to the org's `per_key_rpm` or the gateway's `rate_limiting.global_rpm`) and the org's `global_rpm`, `per_key_rpm`, `per_model_rpm`, `per_user_rpm`, `global_tpm` and `per_key_tpm` counters. Without Redis, each replica counts on its own. A config-file key's own `auth.keys[].rate_limit_tpm` is always replica-local: with N replicas it can use up to N times that limit, and one request can take its counter past the limit before the next request is refused. This is distinct from the org's shared `per_key_tpm` (release notes, Known limits).

An organization's settings saved in the console are pushed to one gateway replica, which applies them at once. Additive or relaxing changes reach every other replica within the control-plane sync interval (`control_plane.sync_interval`: 60 s by default in `docker-compose.yml` and Helm; at the gateway's own default of 0 there is no periodic sync, and the other replicas keep the old settings until they restart). That restrictive changes reach every replica at once is pending and is not true of this release: until then a restrictive change also reaches the other replicas only within the sync interval. Key revocations are separate: they reach every replica that shares the gateway's Redis at once (see the release notes).

On the OpenAI-compatible chat API, a blocking budget refusal is HTTP 429 with an `error` object whose `type` is `rate_limit_error`. Its `code` is `org_budget_exceeded` for an organization's budget (including hosted org budgets), or `budget_exceeded` for the gateway's config-file budget tracker; `message` explains which budget was exceeded. Throttle refusals use `budget_throttled` and `Retry-After` instead. These are not the 402 credit or agent-run-budget errors.

### 📡 Observability

Prometheus scraping is self-hosted only: enable `prometheus.enabled` and send the admin token to `/-/metrics` (or the configured `prometheus.path`). The hosted gateway exposes no Prometheus scrape endpoint. Duration is measured in milliseconds, and cost counters in microdollars.

| Signal             | Emitted as                                                                                                                                            | Default exporter                              | Cardinality safe     |
| ------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------- | -------------------- |
| Request duration   | `agentcc_request_duration_ms` histogram                                                                                                               | Prometheus                                    | yes (bounded labels) |
| Request counter    | `agentcc_requests_total{model,provider,status}`                                                                                                       | Prometheus                                    | yes                  |
| Error counter      | `agentcc_errors_total{model,provider}`                                                                                                                | Prometheus                                    | yes                  |
| Token usage        | `agentcc_tokens_input_total{model,provider}`, `agentcc_tokens_output_total{model,provider}`                                                           | Prometheus                                    | yes                  |
| Cost               | `agentcc_cost_microdollars_total{model,provider}`                                                                                                     | Prometheus                                    | yes                  |
| Cache hit/miss     | `agentcc_cache_hits_total{type=exact\|semantic}`, `agentcc_cache_misses_total`                                                                        | Prometheus                                    | yes                  |
| Uptime             | `agentcc_uptime_seconds`                                                                                                                              | Prometheus                                    | yes                  |
| Distributed traces | OpenTelemetry spans (W3C Trace Context propagated)                                                                                                    | OTLP over HTTP (`http/protobuf`)              | via OTel collector   |
| Access logs        | Structured JSON with request ID + redaction                                                                                                           | stdout (ship it with your log collector)      |                      |
| Audit events       | JSON lines: request, budget, guardrail                                                                                                                | stdout (to a SIEM through your log collector) |                      |
| Response headers   | `x-agentcc-request-id` · `x-agentcc-trace-id` · `x-agentcc-latency-ms` (every response) · `x-agentcc-provider` · `x-agentcc-cost` · `x-agentcc-cache` | HTTP                                          | per-request          |

### 🧠 Routing and resiliency

The gateway's own routing is set in its config file (`routing`). For an organization's API keys, the organization's strategy, retry, provider failover, circuit breakers and model map run over the organization's own providers only, with circuit breakers of its own, and never reach the gateway's own providers or keys. Its model fallback chains do too, with one exception: where the gateway offers Future AGI credits and the organization turned them on, a chain's next model can be one the gateway serves on Future AGI credits, billed to the organization's credits. No hop moves a request between the organization's own keys and Future AGI credits: a request stays in the billing mode it started in, and a hop that would change it is skipped, unless the gateway's operator sets `managed.mixed_billing_chains: true` (off by default).

| Mechanism            | Setting (file under [`internal/routing/`](./internal/routing/))        | What it does                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| -------------------- | ---------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Round-robin**      | `default_strategy: round-robin`, the default (`roundrobin.go`)         | Cycles through a model's targets in turn                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| **Weighted**         | `default_strategy: weighted` (`weighted.go`)                           | Picks targets in proportion to their `weight`                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| **Least latency**    | `default_strategy: least-latency` (`latency.go`)                       | Picks the target with the lowest moving-average (EWMA) latency                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| **Cost-optimized**   | `default_strategy: cost-optimized` (`costopt.go`)                      | Picks the target that serves the model cheapest                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| **Adaptive**         | `default_strategy: adaptive` (`adaptive.go`)                           | Picks the target with the best recent latency, with a penalty for errors (`routing.adaptive`)                                                                                                                                                                                                                                                                                                                                                                                                                             |
| **Fastest (race)**   | `default_strategy: fastest`, `routing.fastest` (`race.go`)             | Sends to several providers at once and returns the first valid answer                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| **Complexity-based** | `routing.complexity` (`complexity.go`)                                 | Scores the prompt and sends it to the model of its tier                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| **Conditional**      | `routing.conditional_routes` (`conditional.go`)                        | Rule-based routing on request fields. Not usable from the config file on this release: every operator starts with `$`, which the loader's `${VAR}` expansion removes, so the gateway starts without the routes; an organization's `conditional_routes` are stored but not applied either                                                                                                                                                                                                                                  |
| **Provider lock**    | `x-agentcc-provider-lock`, `routing.provider_lock` (`providerlock.go`) | Pins a request to one provider the key may use                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| **Access groups**    | `routing.access_groups` (`accessgroups.go`)                            | Limits the models the keys of a group may call, with aliases; set in the config file only (an org config's `routing.access_groups` is refused)                                                                                                                                                                                                                                                                                                                                                                            |
| **Mirror (shadow)**  | `routing.mirror` (`mirror.go`)                                         | Copies matching successful non-streaming requests to a second model, without changing the answer. Set in the config file only (an org config's `routing.mirror` is refused). It copies only the operator's own traffic (internal keys and license-authorized requests), unbilled, on the gateway's own provider credentials: never a request made with an organization's key, and never to a managed provider (config load refuses such a rule). Results are kept for `/-/shadow/stats` only with `capture_results: true` |
| **Model fallback**   | `routing.model_fallbacks` (`modelfallback.go`)                         | `gpt-4o → claude-sonnet → gemini` cascades on error/timeout; an organization's chains run unless it sets `fallback_enabled: false`                                                                                                                                                                                                                                                                                                                                                                                        |
| **Failover**         | `routing.failover` (`failover.go`)                                     | Tries the next provider when one fails                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| **Circuit breaker**  | `routing.circuit_breaker` (`circuitbreaker.go`)                        | Opens after `failure_threshold` failures; after `cooldown` a half-open probe decides whether it closes                                                                                                                                                                                                                                                                                                                                                                                                                    |
| **Retry**            | `routing.retry` (`retry.go`)                                           | Retries on the same provider with exponential backoff and jitter                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| **Health monitor**   | (`healthmonitor.go`)                                                   | Tracks each provider's successes and errors (`/-/health/providers`)                                                                                                                                                                                                                                                                                                                                                                                                                                                       |

### 🔄 Format translation — cross-provider round-trip

Call the gateway in one format, route to a different upstream. Translator packages live under [`internal/translation/`](./internal/translation/):

| Translator              | Covers                                                                                               |
| ----------------------- | ---------------------------------------------------------------------------------------------------- |
| `translation/anthropic` | OpenAI ↔ Anthropic — `cache_control` blocks, tool_use / tool_result blocks, `finish_reason` mapping |
| `translation/gemini`    | OpenAI ↔ Gemini — multi-part content, `finish_reason` mapping                                       |

Azure OpenAI, Bedrock, Cohere, and Vertex AI are handled as **native provider packages** (not translators) — the gateway speaks each one directly via its package under [`internal/providers/`](./internal/providers/), preserving SigV4 signing, Azure deployment-name routing, and similar provider-specifics.

### 🔒 Secrets resolution

A provider's `api_key` can name a secret in a vault instead of holding it, as a URI the gateway resolves at start-up (the `secrets` section configures each backend). Resolver implementations live under [`internal/secrets/`](./internal/secrets/):

| Backend                 | URI scheme    | File               |
| ----------------------- | ------------- | ------------------ |
| AWS Secrets Manager     | `aws-sm://`   | `secrets/aws.go`   |
| Azure Key Vault         | `azure-kv://` | `secrets/azure.go` |
| GCP Secret Manager      | `gcp-sm://`   | `secrets/gcp.go`   |
| HashiCorp Vault (KV v2) | `vault://`    | `secrets/vault.go` |

Any value in the config file can also read an environment variable with `${VAR}`.

Combines with provider-key rotation (`POST /-/admin/providers/{id}/rotate` · `/promote` · `/rollback`): the rotation lives until restart, so update the deployment config with the new key before the next restart or rollout.

### ✨ Other notable capabilities

| Capability                               | Detail                                                                                                                                                                                                                                                                                                                                                                                                   |
| ---------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Upstream header scrubbing**            | Strips provider-identifying headers before returning to client — for OpenAI, strips `openai-organization`, `openai-processing-ms`, `openai-version`, `x-request-id` ([`providers/openai/openai.go`](./internal/providers/openai/openai.go))                                                                                                                                                              |
| **Anthropic prompt-caching passthrough** | `cache_control` blocks preserved in the OpenAI ↔ Anthropic translator ([`translation/anthropic/types.go`](./internal/translation/anthropic/types.go))                                                                                                                                                                                                                                                   |
| **Token estimate**                       | `/v1/count_tokens` estimates tokens with a heuristic ([`internal/tokenizer/`](./internal/tokenizer/))                                                                                                                                                                                                                                                                                                    |
| **Config reload**                        | `POST /-/reload` re-reads the config file's routing (strategy, targets, fallbacks, retry, failover, circuit breakers, mirror) and model database overrides; providers, keys and the rest are read at start-up                                                                                                                                                                                            |
| **Cost tracking**                        | Uses the LiteLLM pricing DB bundled with the gateway; emits `agentcc_cost_microdollars_total`                                                                                                                                                                                                                                                                                                            |
| **Per-tenant spend sync**                | [`internal/tenant/spend_sync.go`](./internal/tenant/spend_sync.go)                                                                                                                                                                                                                                                                                                                                       |
| **Node registry**                        | `GET /-/cluster/nodes` lists the node that answers ([`internal/cluster/`](./internal/cluster/)); membership is per node                                                                                                                                                                                                                                                                                  |
| **Edge worker**                          | With `edge.enabled`, `GET /-/admin/edge/config` returns an edge worker script (routing to the nearest region, weighted balancing across regions, edge caching) built from the `edge` config ([`internal/edge/`](./internal/edge/))                                                                                                                                                                       |
| **Privacy redactor**                     | Logs/traces redaction, separate from the PII guardrail ([`internal/privacy/redactor.go`](./internal/privacy/redactor.go))                                                                                                                                                                                                                                                                                |
| **Streaming**                            | SSE; plugins can run at boundaries ([`internal/streaming/`](./internal/streaming/)). The realtime WebSocket relay answers 501 on this release                                                                                                                                                                                                                                                            |
| **Traffic mirroring**                    | Self-hosted, operator traffic only: the config file's `routing.mirror` copies matching requests made with internal keys or license authorization to a second model, never an organization key's request and never to a managed provider; with `capture_results` the results are at `/-/shadow/stats` ([`routing/mirror.go`](./internal/routing/mirror.go))                                               |
| **AgentCard**                            | `/.well-known/agent.json` exposed for A2A discovery                                                                                                                                                                                                                                                                                                                                                      |
| **W3C Trace Context**                    | `traceparent` / `tracestate` handled via the [`otel`](./internal/otel/) subsystem                                                                                                                                                                                                                                                                                                                        |
| **Webhooks**                             | The platform delivers `request.completed`, `error.occurred`, `guardrail.triggered` and `budget.exceeded` (once per budget level per period, or, when the gateway names no level, once per error code per UTC day) from the request log, signed with the webhook's secret, retried every 30 s with a doubling backoff; email alerts add error-rate, p95-latency and spend thresholds checked every minute |
| **Structured errors**                    | OpenAI-compatible `{error: {message, type, code, param}}` shape                                                                                                                                                                                                                                                                                                                                          |

### 🖥️ Deployment surface

| Target                  | Single-binary |                    Size |         HA          | Notes                                                                                                                                                                                                                       |
| ----------------------- | :-----------: | ----------------------: | :-----------------: | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 🐳 Docker               |      ✅       |           30.8 MB image |          —          | `futureagi/agentcc-gateway:vX.Y.Z` on Docker Hub, one tag per release (also `vX.Y`, and `latest` for the newest; [all tags](https://hub.docker.com/r/futureagi/agentcc-gateway/tags)); mount a config at `/app/config.yaml` |
| ☸️ Kubernetes (Helm)    |      ✅       |                       — | ✅ replicas + Redis | HPA-ready, rolling upgrades, PDBs, liveness/readiness probes                                                                                                                                                                |
| ⚡ Bare metal           |      ✅       |          28.6 MB binary |         ✅          | single binary + YAML config                                                                                                                                                                                                 |
| ☁️ AWS                  |      ✅       |                       — |         ✅          | Fargate · EKS · EC2; SigV4 native for Bedrock                                                                                                                                                                               |
| ☁️ GCP                  |      ✅       |                       — |         ✅          | Cloud Run · GKE; Vertex auth native                                                                                                                                                                                         |
| ☁️ Azure                |      ✅       |                       — |         ✅          | ACI · AKS; Azure OpenAI native auth                                                                                                                                                                                         |
| 🌊 Edge worker          |      ✅       | the image or the binary |         ✅          | Built-in <code>edge</code> subsystem — generates an edge worker script for the regions you list                                                                                                                             |
| 🔒 On-prem, restricted network | ✅ | the image or the binary | ✅ | Calls only what its config names: your providers (at start, the model list of one whose `models` it does not list), and when set, the control plane, Redis, the OTel collector, a secret store, cache backends, guardrail services, webhooks and alert channels, MCP servers and A2A agents. For a provider marked `managed: true`, it re-reads that provider's model catalogue every 6 h (`managed.catalog_refresh`) and, for an OpenRouter catalogue, its models' endpoints every 6 h (`catalog.endpoints_refresh`); `0` turns each off and keeps the bundled snapshot |

Historical v2 input measurement (not a measurement of the composed 7C candidate):

Sizes of this release's code on linux/amd64, measured 2026-10-24 by the release gate on a Google Cloud n2d-standard-8 VM. The image built from [`Dockerfile`](./Dockerfile) (`docker build -f agentcc-gateway/Dockerfile agentcc-gateway`) is 30,817,763 bytes uncompressed (`docker image inspect --format '{{.Size}}'`). Compressed, as a registry stores its layers after a push (what `docker pull` downloads, and what the CI size budget in [`image_size_budget.json`](../scripts/image_size_budget.json) checks), it is 10.0 MB on linux/amd64 and 8.9 MB on linux/arm64. Its `/app/agentcc-gateway` is 28,594,338 bytes, compiled with go.mod's toolchain (go1.26.10, which the Dockerfile sets as `GOTOOLCHAIN`), and byte-identical to the release binary that [`scripts/release/build-reproducible.sh`](./scripts/release/build-reproducible.sh) builds with `GOOS=linux GOARCH=amd64` and the same Go. On linux/arm64 the release binary is 26,542,242 bytes, and the image (`docker buildx build --platform linux/arm64`) is 28,753,379 bytes, its binary byte-identical to that release binary.

Historical MCP input measurement (not a measurement of the composed 7C candidate):

Sizes of this release's code on linux/amd64, measured 2026-10-25 by the release gate on a Google Cloud n2d-standard-8 VM. The image built from [`Dockerfile`](./Dockerfile) (`docker build -f agentcc-gateway/Dockerfile agentcc-gateway`) is 32,677,347 bytes uncompressed (`docker image inspect --format '{{.Size}}'`). Compressed, as a registry stores its layers after a push (what `docker pull` downloads, and what the CI size budget in [`image_size_budget.json`](../scripts/image_size_budget.json) checks), it is 10.6 MB on linux/amd64 and 9.5 MB on linux/arm64. Its `/app/agentcc-gateway` is 30,453,922 bytes, compiled with go.mod's toolchain (go1.26.10, which the Dockerfile sets as `GOTOOLCHAIN`), and byte-identical to the release binary that [`scripts/release/build-reproducible.sh`](./scripts/release/build-reproducible.sh) builds with `GOOS=linux GOARCH=amd64` and the same Go. On linux/arm64 the release binary is 28,180,642 bytes, and the image (`docker buildx build --platform linux/arm64`) is 30,391,779 bytes, its binary byte-identical to that release binary.

---

## 📦 Client SDKs

For caller routing, native Messages/Responses and audio examples, see the [gateway request recipes](../docs/gateway/api-quickstart.md).

No migration required. An OpenAI client works with its base URL set to the gateway's `/v1`.

```python
# Python — use the OpenAI SDK unchanged
from openai import OpenAI
client = OpenAI(base_url="http://localhost:8080/v1", api_key="sk-agentcc-...")
```

Or use the Agent Command Center clients, which add client-side retries, sessions and request metadata. Their base URL is the gateway's root, without `/v1`:

| Package           | Language   | Install                       |
| ----------------- | ---------- | ----------------------------- |
| `agentcc`         | Python     | `pip install agentcc`         |
| `@agentcc/client` | TypeScript | `npm install @agentcc/client` |

### 📨 Request and response headers

Request headers the gateway reads (all routes that read them; values over their bound are a 400, never cut):

| Header                                                            | Value                              | What it does                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| ----------------------------------------------------------------- | ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `x-agentcc-trace-id`                                              | string                             | Trace id; echoed on the response. Generated when absent.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `x-agentcc-session-id`                                            | string, ≤ 255                      | Groups requests into a session (logged as `session_id`).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `x-agentcc-session-name`                                          | string, ≤ 255                      | Logged as metadata `session_name`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `x-agentcc-session-path`                                          | string, ≤ 1,024                    | Logged as metadata `session_path`, e.g. `/search/rerank`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `x-agentcc-request-id`                                            | string, ≤ 255                      | Your own request id: logged as metadata `client_request_id` and echoed as `x-agentcc-client-request-id`. The response's `x-agentcc-request-id` is always the gateway's own id.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `x-agentcc-user-id`                                               | string                             | The end user (agent context).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `x-agentcc-metadata`                                              | JSON object                        | Your dimensions, logged and exported; gateway-owned keys (`org_id`, `key_*`, `cost`, `budget_*`, ...) are dropped.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `x-agentcc-property-<name>`                                       | string, ≤ 1,024                    | One dimension: `x-agentcc-property-env: prod` is `"env":"prod"` in `x-agentcc-metadata` (name lower-cased, ≤ 64 characters; the same blocklist). It wins over the same key in `x-agentcc-metadata`; `x-agentcc-request-id`, `-session-name` and `-session-path` win over a property of their key.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `x-agentcc-cache-ttl`, `-cache-namespace`, `-cache-force-refresh` | seconds / string / `true`          | Per-request cache controls.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `x-agentcc-provider-lock`                                         | provider id                        | Pins the provider; only one the key may use.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `x-agentcc-complexity-override`                                   | tier                               | Picks the complexity tier.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `x-agentcc-timeout` / `x-agentcc-request-timeout`                 | Go duration / ms                   | Shortens the request's timeout (never above the server's ceiling).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `x-agentcc-max-attempts`, `-retry-delay-ms`, `-backoff`           | see release notes N3               | Tighten the org's retries for this request.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `X-Guardrail-Policy` (alias `x-agentcc-guardrail-policy`)         | `log-only`, `disabled` or `strict` | The guardrail mode for this request's config-file rules (`guardrails.rules`, and an organization's overrides of those rules); checks defined only in an organization's config do not read it. Where such rules exist, the key must allow overrides (`allow_policy_override`), else 403; with RBAC on, the key's role needs `guardrails:override`. It takes a mode, not policy ids. `X-Guardrail-Policy` wins when both are sent.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `x-agentcc-config`                                                | JSON object, ≤ 16 KiB              | Several of the headers above in one: each key is a header name without `x-agentcc-` (`{"cache-ttl":300,"request-timeout":5000,"property-env":"prod"}`). It also takes the SDK's GatewayConfig sections (`cache`, `retry`, `fallback`, `load_balance`, `guardrails`, `routing`, `mirror`, `timeout`), each a JSON object or null: `cache.ttl`, `cache.namespace` and `cache.force_refresh` are applied as the cache headers; the other sections and cache fields are not read and are named in `x-agentcc-config-ignored`. A string `timeout` is the `x-agentcc-timeout` duration; a numeric one is named, not applied. `cache-ttl` and `cache.ttl` take whole seconds (`300`, `300.0` or `3e2`) or a duration string (`"5m"`); a fraction is refused. A header sent on its own wins. An unknown key, a wrong type, two keys that set the same header, a duplicate key, more than 32 sections and cache fields to name, or a cache field name that is not 1 to 64 letters, digits, `_`, `-` or `.` is a 400 `invalid_agentcc_config` naming it. Each value is then checked as its header is. |

Response headers:

| Header                                                                                                | When                                                                                                                                                                                                                       |
| ----------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `x-agentcc-request-id`, `x-agentcc-trace-id`, `x-agentcc-latency-ms`                                  | Every response, errors included.                                                                                                                                                                                           |
| `x-agentcc-client-request-id`                                                                         | When the request sent `x-agentcc-request-id`.                                                                                                                                                                              |
| `x-agentcc-config-ignored`                                                                            | When `x-agentcc-config` held GatewayConfig sections or cache fields the gateway does not read: their names, sorted, comma-separated (`cache.strategy,fallback,retry`).                                                     |
| `x-agentcc-provider`, `x-agentcc-model-used`                                                          | When a provider served the response (not on an error the gateway answers itself; `x-agentcc-model-used` is not set on embeddings).                                                                                         |
| `x-agentcc-timeout-ms`                                                                                | When the route resolved a timeout for a provider call (not on `/v1/messages`).                                                                                                                                             |
| `x-agentcc-cost`                                                                                      | When a provider response is priced; absent on a cache hit, which is not charged (not a header value of `0`).                                                                                                               |
| `x-agentcc-cache`                                                                                     | Cache outcome: `skip_org_disabled` when the request's org has not turned its cache on or it has no org config, `skip` on a `/v1/messages` or `generateContent` native pass-through. Streams bypass the cache.              |
| `x-agentcc-fallback-used`, `x-agentcc-routing-strategy`, `x-ratelimit-*-requests`                     | When they apply.                                                                                                                                                                                                           |
| `x-agentcc-guardrail-triggered: true`, `x-agentcc-guardrail-action`, `x-agentcc-guardrails-triggered` | When a guardrail check triggered without blocking: the action is `warn` if any check warned, else `log`; the second lists the checks, sorted, comma-separated. A block is a 403 with error type `guardrail_error` instead. |

Retry, failover and circuit-breaker status lists (`routing.retry.on_status_codes`, `routing.failover.on_status_codes`,
`routing.circuit_breaker.on_status_codes`) match the status the client gets (a provider's 5xx is answered as 502) or
the provider's own status, so `[503]` matches a provider outage and `[502]` still matches every provider 5xx.
`on_timeout` retries or fails over a provider timeout. A circuit breaker counts a provider timeout (504, or a
provider's 408 or 504) as a failure whatever its list.

---

## 🔧 Configuration

```yaml
# config.yaml
server:
  port: 8080

providers:
  openai:
    base_url: "https://api.openai.com"
    api_key: "${OPENAI_API_KEY}"
    api_format: "openai"
    models: [gpt-4o, gpt-4o-mini, o1]

  anthropic:
    base_url: "https://api.anthropic.com"
    api_key: "${ANTHROPIC_API_KEY}"
    api_format: "anthropic"
    models: [claude-sonnet-4, claude-3-5-haiku]

cache:
  enabled: true
  backend: redis
  default_ttl: 5m
  redis:
    address: "redis:6379"
  semantic: # self-hosted tier; each org's enabled/default_ttl still applies
    enabled: true
    backend: qdrant
    threshold: 0.85
    qdrant:
      url: "http://qdrant:6333"
      collection: "agentcc-cache"

guardrails:
  enabled: true
  rules:
    - name: "pii-detection"
      stage: "pre"
      action: "block"
    - name: "prompt-injection"
      stage: "pre"
      action: "block"
    - name: "secret-detection"
      stage: "pre"
      action: "block"

routing:
  model_fallbacks:
    gpt-4o: [claude-sonnet-4]
```

See [`config.example.yaml`](./config.example.yaml) for the full reference.

### CAT-2 catalogue activation

CAT-2 is **off by default**. To opt a gateway into the exact image-priced
Gemini, dynamic-router and Lyria catalogue contracts, set its startup config:

```yaml
managed:
  catalogue_families: [cat2] # [] disables; no other family name is accepted
```

This does not provision provider credentials, enable an organization, or grant
credits. A configured OpenRouter catalogue source, an opted-in organization and
working credit ledger are still required. The bundled catalogue has 34 callable
CAT-2 IDs; existing expiration, shutdown, endpoint and organization policies
continue to apply. Fusion is not enabled. With the setting off, these families
have no supported credit price and credit requests refuse before upstream I/O.
Blocked entries expose no price fields in the admin catalogue or kept-snapshot
HTTP views, and no priced endpoint rows. Persisted snapshots still retain exact
source prices for restart; the HTTP views are not raw persistence exports.
BYOK remains separate and does not spend Future AGI credits.

Alternatively pass `AGENTCC_MANAGED_CATALOGUE_FAMILIES=cat2` into the gateway
process. An **explicitly empty** variable overrides the file to off; an unset
variable leaves the file unchanged. Unknown names, duplicates and malformed
lists fail startup rather than falling back to an enabled policy. There is no
mixed-chain value or organization-level override.

**Restart every gateway replica** after changing this setting. It is read at
startup, not by organization sync or catalogue refresh. Each later catalogue or
endpoint refresh reapplies the startup choice, including to restored snapshots.
To disable, set `[]` (or the empty environment override) and restart. Existing
in-flight work on old replicas remains liable for settlement; rollouts are not
an instantaneous revocation of that work. `POST /-/reload` refuses a change to
this setting with `400 restart_required`, before changing any routing settings.

For Helm, set `agentccGateway.config.managed.catalogue_families: [cat2]` in your
values. The chart ships `[]`; changing the chart-owned config changes the pod
checksum and triggers a rollout. With `agentccGateway.existingConfigMap`, update
that ConfigMap and explicitly restart the gateway pods. An environment override
belongs in `agentccGateway.extraEnv`. In Standalone and Distributed, a `.env`
entry **does reach the gateway** through Compose `env_file` (Standalone's
supervised gateway inherits it). A Compose `environment` override also works.
Check for an existing `.env` value before rollout: `cat2` enables the families,
and an explicitly empty value forces them off.

The opt-in preserves exact source decimal prices and per-generation settlement:
Lyria Pro is **$0.08/generation**, Lyria Clip **$0.04/generation**, before the
existing 5% markup. Image-priced Gemini rates remain those of the exact listing;
dynamic routers retain the frozen-universe reservation and response-bound price
contract. Missing or invalid usage remains conservatively reserved/settled and
subject to the existing quarantine/reconciliation rules, never guessed as free.
Enabling CAT-2 also moves **every managed request, including token-only models,
onto the durable owned-attempt protocol**. The ledger must support that protocol;
this is not just a catalogue-display switch. BYOK still spends no Future AGI
credits. Test with it both on and off before you enable it in production.

**Mixed catalogue/token chains remain off.** A declared mixed fallback plan
(request `models[]`, organization or node model fallbacks) is refused before the
primary takes a hold or calls upstream: no credit entry is produced. A second
admission fence refuses any later crossing between ordinary token-priced and
CAT-2 inclusive managed attempts, even through a BYOK bridge. For an undeclared
provider failover or a later catalogue change, any already-dispatched primary's
actual liability still settles normally; the forbidden hop never dispatches.
Auto routing filters its gateway-chosen runners-up to the primary's pricing
family, so a mixed candidate pool does not itself cause a refusal. Same-family
retries and wholly token-priced chains keep their existing rules.
An explicit empty `model_fallbacks` entry now means **no chain** under every
identity; it does not inherit a chain from the resolved upstream model.
Admission and every dispatch path now resolve a request's fallback plan from
one source, in this order: the request's own `models[]`, else the organization's
`model_fallbacks`, else the node's `routing.model_fallbacks`. Behaviour changes:

- A request `models[]` replaces every configured chain even when it lists only
  the request's own model; that request now has no fallback chain.
- A node chain keyed by the resolved upstream model (for example
  `google/gemini-3.5-flash` for a request to `openrouter/google/gemini-3.5-flash`)
  now applies to non-streaming requests as it already did to streaming ones,
  and admission judges it. An explicit empty node entry means no chain.
- An internal-key request whose model no provider resolves now falls back
  through that same plan, so its own `models[]` is used before any configured
  chain.
- A request's plan is resolved for the request as routing first sent it: the
  model it asked for and the model its first attempt was routed to. A provider
  failover to another routing target, with its own `model_override`, no longer
  changes the plan. For example, a request for an alias whose first target
  sends `vendor/first`, with `models: ["vendor/first", "vendor/spare"]`, tries
  `vendor/spare` after the failover, not `vendor/first` again.

The legacy in-process mixed-chain tests do not constitute operator activation or
product acceptance.

### Structured decisions (bring your own key)

Decision models answer typed questions about a state (a yes/no probability, a
choice, a score) instead of generating text. The gateway serves them on three
routes, none streaming:

| Route                      | Body                                                                                                                                                            |
| -------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `POST /v1/systemone`       | System One: `{model, state, questions: {<id>: {type, instructions?, criteria?}}}`                                                                               |
| `POST /v1/decisions`       | `questions` as an array is OpenAI Decisions, sent to an `openai` provider's `/v1/decisions`; as an object it is System One, served as `/v1/systemone` serves it |
| `GET /v1/decisions/models` | The decision models the caller's organization holds a key for, with their price per million input tokens                                                        |

```json
{"model":"typesafe/jev-1.13.0","state":{"ticket":"Charged twice","reply":"Refunded the duplicate."},
 "questions":{"resolved":{"type":"noul","instructions":"Did the reply resolve the issue?"}}}
```

The body goes to the vendor byte for byte; the only rewrite strips the
`<provider>/` prefix from `model`. The vendor's 200 body comes back unchanged
with the usual `x-agentcc-*` headers. The gateway checks only the envelope (an
object, a `model`, a non-null `state`, a non-empty `questions` whose entries
carry a string `type`), so new question types and fields pass through. A body
in one format sent to a provider that speaks the other gets `422
format_mismatch`; nothing is translated. Bodies are capped at the server limit
or 32 MiB, whichever is smaller.

**Keys.** Third-party decision models run only on the customer's own provider
key. Future AGI holds no decision-model key, and a decision call never draws
Future AGI credits, on a managed key included: the vendor bills the customer's
key directly, and `x-agentcc-cost` is informational.

- Customers add the key under **Gateway > Providers**, with the TypeSafe,
  Perplexity Decider or Custom System One endpoint preset (`api_format:
  systemone`, a fixed model list, never probed for models). OpenAI Decisions
  (`gpt-6-luna`) use the organization's existing OpenAI provider.
- A `byok` or `managed` key is served only from its organization's own
  providers, in provider ID order. A `<provider>/` prefix or a provider lock
  narrows that to one of them; a lock naming any other provider is refused.
  No organization key means `403 decision_provider_not_configured`; so does a
  provider that holds the vendor's key but does not list the model, and the
  message then names that provider and says to add the model to it. One saved
  with a format that serves no decisions means `403
  decision_provider_wrong_format`. An enabled Generic System One provider with
  an explicit, valid organization-owned `base_url` may be keyless, for a
  self-hosted endpoint that needs no authentication. TypeSafe, Perplexity and
  OpenAI providers still require the organization's credentials.
- An internal key must send the end customer's own key in
  `x-agentcc-provider-key` and name the vendor in the model
  (`typesafe/…`, `perplexity/…`, `openai/…`); without it the call is refused
  with `403 byok_required`. The key is used for that one call. It is never
  logged, never put in metadata, request logs, mirrors or spans, and the
  provider built for it is cached under a hash of it. The vendor's default
  upstream is used, or only the `base_url` of a configured provider for that
  vendor; a configured provider's own key is never used. Customer keys may not
  send the header (`400 provider_key_header_not_allowed`).
- Licence tokens are refused: decision models are never a managed service.
- With auth off (a self-host with no key store), the configured provider that
  lists the model serves it with its configured key, which there is the
  operator's own.

Decisions never use conditional routes, model fallbacks, failover, retries or
the mirror. Vendor errors map onto the usual error envelope, with
`x-agentcc-upstream-status` set: a rejected key is `403 provider_key_rejected`,
`429` and `503 provider_overloaded` pass on `Retry-After`, timeouts are `504
gateway_timeout`, and a vendor's `400`/`422` text is passed on only when it
echoes neither the key nor the request's content. Organization calls are
request-logged with the question IDs and types, the state's size, the answers
and the usage; full bodies only when body logging is on and privacy mode
allows. Spans are evaluator spans carrying the format, question count and
question types, never the state or the instructions.

**Outbound proxy.** All System One and OpenAI decision calls follow the
gateway's `HTTP_PROXY`, `HTTPS_PROXY` and `NO_PROXY`, including configured
providers, organization providers and internal callers' own keys. Chat
connections remain direct, even on an OpenAI provider that also serves
decisions. An organization provider with its own `base_url` keeps its address
checks: its host must resolve, from the gateway, only to addresses the gateway
allows. That is checked before every call over HTTP/1.1, including a pooled
connection: a transient DNS failure fails the request even when that connection
is live. Over HTTP/2 the check runs whenever a connection is opened; a call on
an open connection reuses that check. A direct connection (a `NO_PROXY` host)
is checked again when it is dialled. These checks fail closed to preserve SSRF
protection. The proxy resolves the host again for itself, so its own egress
rules still apply.

---

## 🖥️ Self-host

| Deployment           | Status | Notes                                                                            |
| -------------------- | :----: | -------------------------------------------------------------------------------- |
| 🐳 Docker            |   ✅   | Single image, 30.8 MB uncompressed on linux/amd64 (see Deployment surface above) |
| ☸️ Kubernetes (Helm) |   ✅   | HPA-ready, rolling upgrades                                                      |
| ⚡ Bare metal        |   ✅   | Single binary                                                                    |
| ☁️ AWS / GCP / Azure |   ✅   | Fargate · Cloud Run · ACI                                                        |
| 🔒 On-prem, restricted network | ✅ | Calls only the providers and services its config names; a `managed: true` provider's model catalogue every 6 h, off with `managed.catalog_refresh: 0` and `catalog.endpoints_refresh: 0` (see Deployment surface above) |

---

## 🔬 Reproduce the benchmarks

The figures in [Benchmarks](#-benchmarks) come from three Go benchmarks in [`internal/server/`](./internal/server/). Run them from `agentcc-gateway/`:

```bash
GOMAXPROCS=4 go test ./internal/server -run '^$' -bench '^BenchmarkChatCompletion' -benchmem -count 40
```

- Take the median of each benchmark's 40 `ns/op` values. In the published run, `allocs/op` was the same in all 40 samples of each benchmark.
- Keep `GOMAXPROCS=4`: it is the `-4` in each benchmark's name, and it sets how many goroutines `BenchmarkChatCompletionParallel` runs.
- Your `ns/op` depends on your CPU and on what else the machine is running.

The published medians come from the release gate, [`scripts/scenarios/gates/W0_baseline.sh release`](./scripts/scenarios/gates/W0_baseline.sh). It needs `SCN_REDIS` and `SCN_REDIS_DB` for its scenario part (see the script's header). Its benchmark part builds the `internal/server` test binaries of this tree and of the dev commit pinned in [`release_bench_pin.txt`](./scripts/scenarios/gates/lib/release_bench_pin.txt), both with this tree's Go (go.mod's toolchain), runs `-test.run '^$' -test.bench '^BenchmarkChatCompletion' -test.benchmem -test.count 2` on each in 20 alternating rounds, and compares the two sides with [`bench_guard.py`](./scripts/scenarios/gates/lib/bench_guard.py).

---

## 🤝 Contributing

Agent Command Center is part of the [Future AGI monorepo](../README.md). See the top-level [CONTRIBUTING.md](../CONTRIBUTING.md) for setup, CLA, and PR conventions. The gateway's own build, run, test and scenario targets are in its [`Makefile`](./Makefile).

---

## 📄 License

Apache License 2.0 — see [LICENSE](../LICENSE) and [NOTICE](../NOTICE).

---

<div align="center">

**Built with ❤️ in Go by the [Future AGI](https://futureagi.com) team and contributors.**

[🌐 futureagi.com](https://futureagi.com) · [📖 docs](https://docs.futureagi.com/docs/prism) · [💬 Discord](https://discord.com/invite/n2tCUKBkAw) · [🐦 Twitter](https://twitter.com/futureagi)

</div>
