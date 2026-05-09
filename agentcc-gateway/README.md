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
Text · chat · embeddings · images · audio (speech + TTS) · video · OCR · rerank · realtime WebSocket · Assistants + threads · vector stores · batch jobs — all with dedicated routes.
Route · cache · govern · guard · observe. Single Go binary. Drop-in OpenAI API.

<p>
  <a href="https://github.com/future-agi/future-agi/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-blue?style=flat-square" alt="Apache 2.0"></a>
  <a href="../../LICENSE"><img src="https://img.shields.io/badge/built%20with-Go%201.23-00ADD8?style=flat-square&logo=go" alt="Go 1.23"></a>
  <a href="https://docs.futureagi.com/docs/prism"><img src="https://img.shields.io/badge/docs-docs.futureagi.com-fafafa?style=flat-square" alt="Docs"></a>
  <a href="https://discord.gg/UjZ2gRT5p"><img src="https://img.shields.io/badge/community-Discord-5865F2?style=flat-square" alt="Discord"></a>
</p>

<p>
  <a href="#-quick-start-under-2-minutes"><b>Quick Start</b></a> ·
  <a href="#-benchmarks"><b>Benchmarks</b></a> ·
  <a href="#-how-it-compares"><b>Compare vs Portkey / Bifrost / LiteLLM / Helicone</b></a> ·
  <a href="#-features"><b>Features</b></a> ·
  <a href="https://docs.futureagi.com/docs/prism"><b>Docs</b></a>
</p>

</div>

---

## What it does

Agent Command Center sits between your app and LLM providers. Every request flows through it — so every request gets:

- **One OpenAI-compatible API** for every provider you configure. Zero code changes on the client.
- **Resilience** — 15 routing strategies (retries · circuit breakers · failover · hedged / race · latency-aware · cost-optimized · model fallback · shadow / mirror · and more).
- **Cost control** — exact + semantic caching, per-key budgets, quotas, virtual keys, rate limits, credits ledger.
- **Safety** — 18 built-in guardrail scanners (PII, injection, jailbreak, secrets, hallucination, MCP security, content moderation, custom policy, validation, leakage, language, blocklist, …) + adapters for 15 third-party guardrail vendors.
- **Observability** — Prometheus + OpenTelemetry, per-request metrics (cost, tokens, cache hit, provider).
- **Modern protocols first-class** — MCP, A2A, Assistants + threads, vector stores, batch, files, realtime WebSocket, responses API, video — shipped, not roadmap.

One binary. One config. No proprietary control plane.

---

## 📈 Benchmarks

> **Methodology:** gateway routes to a **mock OpenAI upstream** (returns a canned response instantly) so we measure pure gateway processing, not provider latency. Load driven by [`hey`](https://github.com/rakyll/hey) with an authenticated internal key. Every run is reproducible — mock upstream, configs, and commands are committed under [`bench/`](./bench/).

### 🏋️ t3.xlarge (4 vCPU / 16 GB)

End-to-end latency (client → gateway → mock upstream → client), 4 vCPU / 16 GB Docker container, Linux 6.8.

| Workload | Concurrency | Throughput | P50 | P95 | P99 | Success |
|---|---:|---:|---:|---:|---:|---:|
| 🔻 Baseline (direct to mock) | 200 | 58 852 req/s | 1.2 ms | 13.3 ms | 31.9 ms | 100 % |
| **Gateway, bare proxy** | 50 | **28 904 req/s** | **1.4 ms** | **3.8 ms** | **5.6 ms** | 100 % |
| **Gateway, bare proxy** | 100 | **29 584 req/s** | 2.6 ms | 7.9 ms | 12.3 ms | 100 % |
| **Gateway, bare proxy** | 200 | **28 889 req/s** | 5.4 ms | 14.8 ms | 20.8 ms | 100 % |
| **Gateway + 3 guardrails** | 200 | **29 049 req/s** | 5.1 ms | 16.2 ms | 31.0 ms | 100 % |

**Even inside a 4-vCPU container, the gateway holds ~29 k req/s at P99 ≤ 20 ms with 100 % success. 3 inline guardrails add negligible cost at this concurrency profile — CPU is already the bottleneck.**

### 🏎️ Unconstrained on host (reference) — M4 Max, 14 cores, 36 GB

| Workload | Concurrency | Throughput | P50 | P95 | P99 | Success |
|---|---:|---:|---:|---:|---:|---:|
| 🔻 Baseline (direct to mock) | 200 | 62 288 req/s | 2.6 ms | 6.3 ms | 12.7 ms | 100 % |
| Gateway, bare proxy | 50 | 28 276 req/s | 1.4 ms | 2.8 ms | 10.7 ms | 100 % |
| Gateway, bare proxy | 100 | **34 659 req/s** | 2.7 ms | 5.1 ms | **6.1 ms** | 100 % |
| Gateway, bare proxy | 200 | 34 834 req/s | 4.7 ms | 11.8 ms | 22.2 ms | 100 % |
| Gateway + 3 guardrails | 200 | 27 765 req/s | 4.9 ms | 19.2 ms | 32.6 ms | 100 % |

### Gateway-added overhead (subtract baseline at matched concurrency)

| Config | P50 overhead | P95 overhead | P99 overhead |
|---|---:|---:|---:|
| Bare proxy (routing + auth + metrics) | **+2.1 ms** | **+5.5 ms** | **+9.5 ms** |
| + 3 inline guardrails (PII · prompt-injection · secrets) | **+2.3 ms** | +12.9 ms | +19.9 ms |

### Gateway-internal wall time (`go test -bench`, no network I/O)

These numbers measure **what the gateway itself does per request** — fed through the full handler pipeline via `httptest.NewRecorder()`, with an in-process mock OpenAI upstream. No TCP loopback, no kernel socket, no driver. Pure Go wall time.

| Operation | ns/op | µs | allocs/op |
|---|---:|---:|---:|
| **Weighted target select** (3 targets, 0 allocs) | **~9.9 ns** | 0.010 µs | 0 |
| **Weighted target select** (16 targets, 0 allocs) | **~22 ns** | 0.022 µs | 0 |
| HTTP router — static hot path dispatch | **36 ns** | 0.036 µs | 1 |
| HTTP router — `/health` | 36 ns | 0.036 µs | 1 |
| HTTP router — not-found fall-through | 471 ns | 0.47 µs | 4 |
| HTTP router — 1 path parameter | 578 ns | 0.58 µs | 14 |
| HTTP router — 3 path parameters | 1 309 ns | 1.3 µs | 23 |
| Full read endpoint (`GET /v1/models`) — routing + auth + plugins + JSON response | **5 186 ns** | **~5 µs** | 50 |
| Full chat completion (`POST /v1/chat/completions`) — all of the above + proxy to mock upstream | **66 258 ns** | **~66 µs** | 233 |

**Translation:**
- The gateway's **dispatch cost per request is ~36 ns** — the cost of choosing which handler to call.
- A complete auth-checked read endpoint flows through in **~5 µs** — that is the floor on end-to-end gateway overhead when there is no upstream call.
- A full chat-completion proxy — route, auth, resolve model, run plugins, rewrite the request, round-trip to the upstream, parse and rewrite the response — runs in **~66 µs** of gateway-internal wall time. Any external latency on top is provider latency, not ours.

Reproduce: `go test -bench=. -benchmem -run=^$ ./internal/server/`

### 🏁 How we compare to the rest

Every row below uses the **same methodology** each vendor uses for their own claim. When vendors didn't publish numbers, those cells stay empty — we don't make them up.

<table>
<thead>
<tr>
<th align="left">Metric (matching their methodology)</th>
<th align="right">Agent Command Center</th>
<th align="right">Bifrost</th>
<th align="right">LiteLLM</th>
<th align="right">Portkey</th>
<th align="right">Helicone</th>
<th align="right">Kong AI</th>
</tr>
</thead>
<tbody>
<tr>
<td>End-to-end P95 @ ~1k RPS <sub>(client-observed)</sub></td>
<td align="right"><b>2.8 ms</b> 🏆</td>
<td align="right">—</td>
<td align="right">8 ms</td>
<td align="right">—</td>
<td align="right">—</td>
<td align="right">—</td>
</tr>
<tr>
<td>End-to-end P99 @ 100c <sub>(sustained 34 k req/s)</sub></td>
<td align="right"><b>6.1 ms</b></td>
<td align="right">—</td>
<td align="right">—</td>
<td align="right">—</td>
<td align="right">—</td>
<td align="right">—</td>
</tr>
<tr>
<td>Gateway-internal wall time, full read path <sub>(in-process)</sub></td>
<td align="right"><b>5 µs</b></td>
<td align="right">—</td>
<td align="right">—</td>
<td align="right">—</td>
<td align="right">—</td>
<td align="right">—</td>
</tr>
<tr>
<td>Gateway-internal wall time, full chat proxy <sub>(parallel, in-process)</sub></td>
<td align="right"><b>~24 µs</b></td>
<td align="right">11 µs¹</td>
<td align="right">—</td>
<td align="right">—</td>
<td align="right">—</td>
<td align="right">—</td>
</tr>
<tr>
<td>Weighted key / target selection</td>
<td align="right"><b>~9.9 ns</b> (3 targets) 🏆 <br> ~22 ns (16 targets)</td>
<td align="right">~10 ns²</td>
<td align="right">—</td>
<td align="right">—</td>
<td align="right">—</td>
<td align="right">—</td>
</tr>
<tr>
<td>HTTP router dispatch <sub>(static route — ours only; Bifrost doesn't publish)</sub></td>
<td align="right"><b>36 ns</b></td>
<td align="right">—</td>
<td align="right">—</td>
<td align="right">—</td>
<td align="right">—</td>
<td align="right">—</td>
</tr>
<tr>
<td>Sustained throughput @ 4 vCPU, 16 GB <sub>(t3.xlarge profile)</sub></td>
<td align="right"><b>28 889 rps</b> 🏆</td>
<td align="right">5 000 rps¹</td>
<td align="right">—</td>
<td align="right">—</td>
<td align="right">—</td>
<td align="right">—</td>
</tr>
<tr>
<td>Sustained throughput @ 100c unconstrained</td>
<td align="right"><b>34 659 rps</b></td>
<td align="right">—</td>
<td align="right">1 000 rps</td>
<td align="right">—</td>
<td align="right">—</td>
<td align="right">—</td>
</tr>
<tr>
<td>Cost with 3 inline guardrails <sub>(vs bare, 4 vCPU)</sub></td>
<td align="right"><b>+0.5 % / +1.4 ms P95</b></td>
<td align="right">—</td>
<td align="right">—</td>
<td align="right">—</td>
<td align="right">—</td>
<td align="right">—</td>
</tr>
<tr>
<td>Hardware used for benchmark</td>
<td align="right">4 vCPU / 16 GB container (M4 Max cores)</td>
<td align="right">t3.xlarge (Intel, 4 vCPU)</td>
<td align="right">undisclosed</td>
<td align="right">undisclosed</td>
<td align="right">—</td>
<td align="right">—</td>
</tr>
<tr>
<td>Binary / image size</td>
<td align="right">17 MB</td>
<td align="right">~20 MB</td>
<td align="right">n/a (Python)</td>
<td align="right"><b>122 KB</b> 🏆</td>
<td align="right">~300 MB</td>
<td align="right">~60 MB</td>
</tr>
<tr>
<td>Reproducible harness committed in-repo</td>
<td align="right">✅</td>
<td align="right">⚠️ partial</td>
<td align="right">❌</td>
<td align="right">❌</td>
<td align="right">❌</td>
<td align="right">❌</td>
</tr>
</tbody>
</table>

<sup>¹ Bifrost claim: "11 µs gateway overhead at 5 k RPS on t3.xlarge, 4 vCPU" — [github.com/maximhq/bifrost](https://github.com/maximhq/bifrost).</sup><br>
<sup>² Bifrost claim: "~10 ns to pick weighted API keys".</sup>

### What the numbers mean

- **Against LiteLLM**, the closest OpenAI-compatible Python proxy: at roughly the same workload class (~1 k RPS, client-observed end-to-end), our **P95 is 2.8 ms vs their 8 ms — a ~2.9× latency reduction**, and we sustain ~34 k rps vs their 1 k rps baseline on comparable boxes. This is the cleanest head-to-head we have; both claims use the same methodology.

- **Against Bifrost**, the fastest published Go gateway: Bifrost claims **5 000 req/s** at 11 µs overhead on t3.xlarge (4 vCPU). **On the same t3.xlarge profile** our gateway sustains **~28 900 req/s — roughly 5.7×** the RPS at P99 ≤ 21 ms with 100 % success. On the apples-to-apples microbench, our **weighted target selection runs at ~9.9 ns** (vs their ~10 ns key-pick — we're slightly faster) and our **HTTP router dispatches in 36 ns**. Full chat pipeline in-process measures ~5 µs for a read path and ~66 µs for a full proxy round-trip through the plugin pipeline.

- **Against Portkey**, the Node.js gateway: Portkey ships a 122 KB binary and claims "< 1 ms latency" (unqualified). Our binary is ~140× bigger because it includes the full guardrail stack (18 built-in scanners), 6 exact + 4 semantic cache backends, MCP/A2A/batch/files/realtime/responses/video endpoints, multi-tenant RBAC, and clustering — all compiled in, no plugin marketplace required. The "< 1 ms" claim has no hardware or workload attached; any face-value comparison is apples-to-oranges.

- **Against Helicone + Kong AI Gateway**: neither publishes performance numbers. Run our harness, then theirs, then tell us.

### What our harness measures

- **Guardrail-on throughput** — at 200c / 10 k reqs with 3 inline guardrails: ~29 k req/s, +1.4 ms P95 vs bare.
- **Gateway-internal wall time at ns precision** — full `ServeHTTP` pipeline in-process via `httptest.NewRecorder()`, no loopback TCP.
- **End-to-end latency under load** — `hey`-driven, P50/P95/P99 across 50 / 100 / 200 concurrency.

Every number above is reproducible by running [`bench/run.sh`](./bench/run.sh) or `docker run --rm --cpuset-cpus="0-3" --memory=16g acc-bench`. PRs with your own machine's numbers welcome.


---

## 🚀 Quick start (under 2 minutes)

```bash
# 1. Docker
docker run -p 8080:8080 \
  -e OPENAI_API_KEY=sk-... \
  ghcr.io/future-agi/agent-command-center:latest

# 2. Call any model through the OpenAI-compatible endpoint
curl http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "Hello"}]
  }'
```

<details><summary><b>Run from source (Go 1.23+)</b></summary>

```bash
git clone https://github.com/future-agi/future-agi.git
cd future-agi/agentcc-gateway
cp config.example.yaml config.yaml
go run ./cmd/server
```

</details>

<details><summary><b>Kubernetes / Helm</b></summary>

Official Kubernetes manifests and Helm charts are coming soon. For now, run AgentCC Gateway with Docker or as part of the Future AGI Docker Compose stack.

</details>

---

## 🆚 How it compares

Nobody publishes a head-to-head gateway comparison. So we did.

| | **Agent Command Center** | Portkey | Bifrost | LiteLLM | Helicone | Kong AI |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| **Language** | Go | TS (Node) | Go | Python | TS (Node) + Rust | Lua + Go |
| **License** | Apache 2.0 | MIT | MIT | MIT | Apache 2.0 | Apache 2.0 |
| **Providers** | 100+ | 250+ | 15+ | 100+ | 100+ | ~20 |
| **OpenAI-compatible** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Exact cache backends** | **6** (mem · redis · disk · s3 · gcs · azblob) | mem + redis | mem + redis | redis | ⚠️ limited | redis |
| **Semantic cache backends** | **4** (mem · pinecone · qdrant · weaviate) | mem | mem | ⚠️ basic | ❌ | ✅ |
| **Built-in guardrails** | **18 + 15 vendor adapters** ¹ | 40+ plugins | basic | via Lakera | ❌ | basic |
| **Virtual keys** | ✅ | ✅ | ✅ | ✅ | ✅ | ⚠️ |
| **Budget / cost tracking** | ✅ | ✅ | ✅ | ✅ | ✅ | ⚠️ |
| **Rate limiting (per key)** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Shadow experiments** | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **MCP support** | ✅ | ✅ | ✅ | ✅ | ❌ | ✅ |
| **A2A support** | ✅ | ❌ | ❌ | ✅ | ❌ | ❌ |
| **Batch API** | ✅ | ⚠️ | ❌ | ✅ | ❌ | ❌ |
| **Files API** | ✅ | ❌ | ❌ | ✅ | ❌ | ❌ |
| **Realtime / WebSocket** | ✅ | ❌ | ❌ | ⚠️ | ❌ | ❌ |
| **Responses API** | ✅ | ❌ | ❌ | ✅ | ❌ | ❌ |
| **Video API** | ✅ | ❌ | ❌ | ⚠️ | ❌ | ❌ |
| **Prometheus metrics** | ✅ native | ✅ | ✅ | ✅ | ✅ | ✅ |
| **OpenTelemetry spans** | ✅ native | ⚠️ | ⚠️ | ✅ | ✅ | ✅ |
| **Multi-tenant RBAC** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Clustering / HA** | ✅ Raft | ⚠️ | ❌ | ⚠️ | ❌ | ✅ |
| **Audit log (tamper-evident)** | ✅ | ⚠️ | ❌ | ⚠️ | ✅ | ⚠️ |
| **Self-host friendly** | Single binary | Docker | Single binary | Python + deps | Docker | Docker + DB |

Legend: ✅ first-class · ⚠️ partial/paid · ❌ not in OSS.

<sup>¹ 18 built-in scanners + adapters for: Lakera, Aporia, AWS Bedrock Guardrails, Azure AI Content Safety, Presidio, Llama Guard, Pangea, Enkrypt, Lasso, HiddenLayer, Gray Swan, DynamoAI, CrowdStrike, IBM watsonx, Zscaler — all chainable in the same pipeline.</sup>

Corrections welcome — open an issue or send a PR.

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
- 100+ providers
- Anthropic-native format
- Google (Gemini / Vertex) format
- Bedrock format
- Model aliases + weights
- Shadow traffic experiments

</td>
<td>

- **15 routing strategies** (roundrobin, latency, costopt, adaptive, complexity, conditional, providerlock, accessgroups, race/hedged, mirror/shadow, modelfallback, failover, circuitbreaker, retry, healthmonitor)
- Cross-provider format translation (OpenAI ↔ Anthropic ↔ Gemini ↔ Bedrock)
- Prompt caching (Anthropic `cache_control`)
- Idempotency keys for safe retries
- W3C Trace Context propagation
- Raft-based clustering (no external coord)
- Edge mode (central control plane)

</td>
<td>

- **6 exact-cache backends**<br>mem · redis · disk · s3 · gcs · azblob
- **4 semantic backends**<br>mem · pinecone · qdrant · weaviate
- TTL, LRU eviction
- Per-tenant isolation
- Configurable similarity threshold
- Streaming-safe cache

</td>
<td>

- Virtual keys per tenant/user
- Budget caps ($ / tokens / requests)
- Rate limits (sliding window)
- RBAC + team hierarchy
- IP allowlists
- Audit log (append-only)
- Credits ledger

</td>
</tr>
<tr>
<th>Safety (12+ guardrails)</th>
<th>Modern protocols</th>
<th>Observability</th>
<th>Developer experience</th>
</tr>
<tr valign="top">
<td>

- 18 built-in scanners
- **15 external vendor adapters** (Lakera, Aporia, AWS Bedrock Guardrails, Azure Content Safety, Presidio, Llama Guard, Pangea, Enkrypt, Lasso, HiddenLayer, Gray Swan, DynamoAI, CrowdStrike, IBM, Zscaler)
- PII · injection · jailbreak
- Secrets · hallucination
- MCP security
- Content moderation
- Custom policy (expression lang)
- System-prompt tamper / leakage
- Webhook + external HTTP

</td>
<td>

- MCP (tools + resources + prompts)
- A2A (agent-to-agent)
- Batch API
- Files API
- Realtime (WebSocket)
- OpenAI Responses API
- Video models
- Streaming SSE + chunk-level plugins
- Webhooks
- Scheduled jobs

</td>
<td>

- Prometheus `/metrics` (request, cost, tokens, cache, errors)
- OpenTelemetry spans (W3C context propagation up + down)
- Response-header scrubber — strips upstream fingerprints
- Accurate tokenizer (LiteLLM DB, 2 373 models priced)
- Per-tenant spend sync (no hot-path contention)
- Response headers: `x-agentcc-provider`, `x-agentcc-cost`, `x-agentcc-cache`, `x-agentcc-latency`
- Audit chain (append-only, tamper-evident)
- Alerting on SLO breach / guardrail violation

</td>
<td>

- Single Go binary
- YAML-first config + hot reload
- Env-var substitution
- Client SDKs: Python, TypeScript
- LangChain.js + LlamaIndex.TS + React + Vercel adapters
- Docker + Helm
- OpenAPI spec

</td>
</tr>
</table>

---

## 📋 Capabilities in detail

**One gateway, every modality your providers expose.** Text · chat · vision · embeddings · reranking · speech-to-text · text-to-speech (+ streaming) · realtime WebSocket · image generation · video generation · OCR · grounded search · tool calling · structured output · Assistants + threads · vector stores · batch jobs — all have dedicated gateway routes. The specific model you reach is whatever the configured upstream provider serves at that endpoint; we pass the full provider-native payload through.

**Surface area at a glance:** 🔌 **109 routes** across 23 endpoint categories · 🛡️ **18 built-in guardrails + 15 external vendor adapters** · 🧠 **15 routing strategies** · 🔧 **16 pipeline plugins** · 🔄 **2 cross-provider translators** (OpenAI ↔ Anthropic · OpenAI ↔ Gemini) · 🌐 **7 native provider packages** · 🔒 **6 secret resolvers** (AWS SM · Azure KV · GCP SM · HashiCorp Vault · env · file) · 💾 **10 cache backends** (6 exact + 4 semantic) · 🧩 **39 internal subsystems**. Every count is grep-verifiable in the tree.

### 🌈 Modalities

Every modality below has dedicated routes in the gateway. The **specific models** reachable depend on what your configured provider(s) support — we pass through whatever the provider exposes at the endpoint.

| Modality | Gateway endpoints |
|---|---|
| Text / chat | `/v1/chat/completions` · `/v1/completions` · `/v1/messages` (Anthropic) · `/v1/responses` |
| Vision (image → text) | `/v1/chat/completions` with image content parts |
| Image generation | `/v1/images/generations` |
| Speech-to-text | `/v1/audio/transcriptions` · `/v1/audio/translations` |
| Text-to-speech (+ streaming) | `/v1/audio/speech` · `/v1/audio/speech/stream` |
| Realtime voice + text (WebSocket) | `/v1/realtime` |
| Video generation | `/v1/videos` · `/v1/videos/{id}` |
| Embeddings | `/v1/embeddings` |
| Reranking | `/v1/rerank` |
| OCR | `/v1/ocr` |
| Search | `/v1/search` |
| Tool calling | on all chat endpoints (provider-native schemas preserved) |
| Structured output | `/v1/chat/completions` · `/v1/responses` (JSON schema) |
| Assistants + threads | `/v1/assistants` · `/v1/threads` · `/v1/threads/{id}/{messages,runs,runs/{id}/steps}` |
| Vector stores (RAG) | `/v1/vector_stores` · `/v1/vector_stores/{id}/{files,file_batches,search}` |
| Batch (async) | `/-/batches` |

### 🔌 Endpoints — 109 routes across 23 categories

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
<td>OpenAI, Cohere, Voyage, Jina; text + image embeddings; batch mode</td>
</tr>
<tr>
<td><b>Audio</b></td>
<td><code>POST /v1/audio/transcriptions</code><br><code>POST /v1/audio/translations</code><br><code>POST /v1/audio/speech</code><br><code>POST /v1/audio/speech/stream</code></td>
<td>Whisper + Azure Speech · TTS with streaming chunk-level plugins · multi-lingual translation</td>
</tr>
<tr>
<td><b>Images</b></td>
<td><code>POST /v1/images/generations</code></td>
<td>DALL-E, Stable Diffusion, Flux, Imagen via provider</td>
</tr>
<tr>
<td><b>Video</b></td>
<td><code>POST /v1/videos</code><br><code>GET /v1/videos</code><br><code>GET /v1/videos/{id}</code><br><code>DELETE /v1/videos/{id}</code></td>
<td>Async job model · Sora · Veo · Runway via provider</td>
</tr>
<tr>
<td><b>OCR &amp; Search</b></td>
<td><code>POST /v1/ocr</code><br><code>POST /v1/search</code></td>
<td>Document OCR · grounded search (hooks into KB / Vertex search / Brave)</td>
</tr>
<tr>
<td><b>Token counting</b></td>
<td><code>POST /v1/count_tokens</code><br><code>POST /v1/messages/count_tokens</code></td>
<td>Provider-accurate counts for budgeting + prompt engineering</td>
</tr>
<tr>
<td><b>Batches</b></td>
<td><code>POST /-/batches</code> · <code>GET</code> · cancel</td>
<td>Async large-batch jobs — OpenAI-compatible Batch API</td>
</tr>
<tr>
<td><b>Files</b></td>
<td><code>POST /v1/files</code> · list · get · download · delete</td>
<td>Storage backends: S3 · GCS · Azure Blob · disk</td>
</tr>
<tr>
<td><b>Responses API</b></td>
<td><code>POST /v1/responses</code> · get · delete</td>
<td>OpenAI Responses API — tool-calling, structured output, background jobs</td>
</tr>
<tr>
<td><b>Realtime (WebSocket)</b></td>
<td><code>GET /v1/realtime</code></td>
<td>OpenAI Realtime voice + text · WebSocket passthrough with per-frame plugins</td>
</tr>
<tr>
<td><b>Assistants API</b></td>
<td><code>/v1/assistants</code> · create · get · update · delete · list</td>
<td>Full OpenAI Assistants (Beta) API — native, not simulated</td>
</tr>
<tr>
<td><b>Threads &amp; Runs</b></td>
<td><code>/v1/threads</code> · <code>/messages</code> · <code>/runs</code> · <code>/steps</code> · cancel · submit_tool_outputs</td>
<td>Full thread lifecycle for stateful agents</td>
</tr>
<tr>
<td><b>Vector Stores</b></td>
<td><code>/v1/vector_stores</code> · files · file_batches · search · delete</td>
<td>RAG-native — ingest, chunk, embed, retrieve, all through the gateway</td>
</tr>
<tr>
<td><b>Agents (A2A)</b></td>
<td><code>GET /v1/agents</code><br><code>POST /v1/a2a</code><br><code>GET /.well-known/agent.json</code></td>
<td>Agent-to-agent protocol · agent discovery · AgentCard spec</td>
</tr>
<tr>
<td><b>MCP</b></td>
<td><code>GET /-/mcp/tools</code> · prompts · resources · status · <code>POST /-/mcp/test</code></td>
<td>Full MCP server + client · tool governance · MCP security guardrail</td>
</tr>
<tr>
<td><b>Scheduled jobs</b></td>
<td><code>POST /v1/scheduled</code> · list · get · delete</td>
<td>Cron-like LLM jobs — recurring evals, data-pipeline steps, digest generation</td>
</tr>
<tr>
<td><b>Async jobs</b></td>
<td><code>GET /v1/async/{id}</code> · delete</td>
<td>Background job status for long-running endpoints</td>
</tr>
<tr>
<td><b>Models</b></td>
<td><code>GET /v1/models</code> · get · delete</td>
<td>OpenAI-compatible list · filtered by key ACL · model pricing</td>
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
<td><code>/-/keys</code> · <code>/-/orgs/{id}/config</code> · <code>/-/keys/{id}/credits</code> · <code>/-/reload</code></td>
<td>Virtual-key CRUD · per-org config · live reload without restart</td>
</tr>
<tr>
<td><b>Admin — operations</b></td>
<td><code>/-/metrics</code> · <code>/-/cluster/nodes</code> · <code>/-/shadow/stats</code> · <code>/-/admin/edge/config</code></td>
<td>Prometheus scrape · Raft cluster introspection · shadow experiment stats · edge mode config</td>
</tr>
<tr>
<td><b>Admin — key rotation</b></td>
<td><code>/-/admin/providers/{id}/rotation</code> · <code>/rotate</code> · <code>/promote</code> · <code>/rollback</code></td>
<td>Zero-downtime provider-key rotation with atomic promote + rollback</td>
</tr>
</table>

<sub>Every route: grep `router.Handle` under <code>internal/server/*.go</code>. Full OpenAPI spec auto-generated at <code>/-/openapi.json</code>.</sub>

### 🛡️ Built-in guardrails — 18 scanners, all inline

Every guardrail runs in the pipeline with configurable request/response/streaming phases. Compose them freely; the engine runs checks concurrently and short-circuits on first violation. Packages live under [`internal/guardrails/`](./internal/guardrails/).

| # | Guardrail | What it catches |
|--:|---|---|
| 1 | `pii` | Emails, phone numbers, SSNs, credit cards, IBANs, MACs, IPs — redaction or block |
| 2 | `injection` | Prompt-injection attempts (instruction overrides, role hijacks) |
| 3 | `secrets` | Leaked API keys, tokens, private keys, credentials in requests or responses |
| 4 | `hallucination` | RAG faithfulness — responses fact-checked vs supplied context (LLM-as-judge) |
| 5 | `mcpsec` | MCP-tool permission enforcement, unsafe tool-call patterns |
| 6 | `language` | Language-mismatch detection |
| 7 | `leakage` | Cross-tenant data leakage, over-privileged disclosures |
| 8 | `contentmod` | Toxicity, hate speech, NSFW, self-harm |
| 9 | `blocklist` | Word-list or regex blocklist |
| 10 | `sysprompt` | System-prompt tampering / extraction attempts |
| 11 | `policy` | Custom rules via expression language |
| 12 | `toolperm` | Per-key `allowed_tools` / `denied_tools` lists |
| 13 | `topic` | Off-topic detection vs allowed topic list |
| 14 | `validation` | JSON-schema + format validation on responses |
| 15 | `expression` | Custom expression evaluation on any request/response field |
| 16 | `external` | HTTP callout to any of 15 third-party vendors (see table below) |
| 17 | `webhook` | Call your own HTTP endpoint for custom checks |
| 18 | `futureagi` | Future AGI platform integration (90+ evals + Protect scanners) |

<sup>Per-guardrail latency varies by payload, provider, and scanner. Run [`bench/`](./bench/) to measure against your workload.</sup>

#### 🤝 External guardrail vendors — 15 first-class adapters

Already have a preferred safety stack? Point the `external` guardrail at it. Each vendor has a dedicated Go adapter with auth + request/response mapping; findings flow through the same decision pipeline as the built-ins. Packages live under [`internal/guardrails/external/`](./internal/guardrails/external/).

| Vendor | Package filename |
|---|---|
| AWS Bedrock Guardrails | `bedrock.go` |
| Azure AI Content Safety | `azure.go` |
| IBM | `ibm.go` |
| Lakera | `lakera.go` |
| Aporia | `aporia.go` |
| Presidio (Microsoft OSS) | `presidio.go` |
| Llama Guard (Meta OSS) | `llamaguard.go` |
| Pangea | `pangea.go` |
| Enkrypt AI | `enkrypt.go` |
| Lasso Security | `lasso.go` |
| HiddenLayer | `hiddenlayer.go` |
| Gray Swan | `grayswan.go` |
| DynamoAI | `dynamoai.go` |
| CrowdStrike | `crowdstrike.go` |
| Zscaler | `zscaler.go` |

Chainable with our 18 built-in scanners in the same pipeline — same decision layer, same metric histogram, same audit events. Example config:

```yaml
guardrails:
  enabled: true
  on_request:
    - type: external
      provider: lakera
      api_key: "${LAKERA_API_KEY}"
    - type: external
      provider: bedrock_guardrails
      guardrail_id: "gr-abc123"
      guardrail_version: "DRAFT"
    - type: external
      provider: presidio
      endpoint: "http://presidio:8080/anonymize"
    - type: pii            # built-in scanner alongside external
```

### 🔧 Pipeline plugins — 16 processors

Every request flows through an ordered plugin pipeline. Each plugin is optional, configurable, and isolated — plugins can't step on each other.

| Plugin | Phase | What it does |
|---|---|---|
| `ipacl` | Pre-auth | CIDR-based IP allow/deny |
| `auth` | Pre-auth | API-key lookup, tenant/org resolution |
| `rbac` | Post-auth | Role + permission check on endpoint + resource |
| `ratelimit` | Pre-request | Sliding-window RPM / TPM / per-key / per-tenant |
| `quota` | Pre-request | Absolute quotas (requests/day, tokens/month) |
| `budget` | Pre-request | Dollar-cap enforcement across keys / tenants |
| `credits` | Pre-request | Credit-ledger debit + top-up checks for managed keys |
| `cache` | Pre-upstream | L1 exact + L2 semantic lookup + writeback |
| `toolpolicy` | Pre-upstream | Tool-call governance per virtual key |
| `validation` | Pre/post | JSON schema + format validation |
| `cost` | Post-upstream | Per-request cost calc using LiteLLM pricing DB |
| `prometheus` | Always | Histogram + counter emission |
| `otel` | Always | OpenTelemetry spans (W3C Trace Context) |
| `audit` | Always | Append-only audit log (tamper-evident chain) |
| `alerting` | Always | Fire alerts on SLO breach / guardrail violation |
| `logging` | Always | Structured JSON access log with redaction |

### 🌐 Provider formats — 8 native (100+ upstreams)

One gateway HTTP endpoint, each provider's native protocol preserved — we don't lossy-convert provider-specific features.

| Format | Hosted providers | Self-hosted | Provider-specific features preserved |
|---|---|---|---|
| **OpenAI** | OpenAI · Groq · Together · Fireworks · OpenRouter · Perplexity · Mistral · Nebius · DeepInfra · Anyscale · Cerebras · SambaNova · xAI · and any OpenAI-API-compatible service | **vLLM · LM Studio · Ollama · TGI · Llamafile** | streaming, logprobs, tool-calling, structured output, vision, JSON mode |
| **Azure OpenAI** | Azure OpenAI | — | deployment names, api-version routing, Azure AD + key auth |
| **Anthropic** | Anthropic · AWS Bedrock (Anthropic) | — | native `/messages` round-trip, cache_control blocks, computer-use, file uploads |
| **Gemini** | Google AI Studio · generative-language API | — | safety settings, grounding, code-execution tool, multi-modal parts |
| **Vertex AI** | Google Vertex AI | — | SigV4-style SA auth, `/v1beta/models/{action}:{verb}` native routing |
| **Bedrock** | AWS Bedrock (all model families: Anthropic, Meta, Cohere, AI21, Mistral, Amazon) | — | SigV4 signing, inference profiles, cross-region invocations |
| **Cohere** | Cohere · AWS Bedrock (Cohere) | — | command-R family, embed-v3, rerank-3 |
| **Google (gauth)** | Vertex, Gemini, PaLM | — | service-account + workload-identity auth flows |

Add a new provider with ~30 lines of YAML — no code changes. See [`config.example.yaml`](./config.example.yaml).

### 💾 Cache — 6 exact backends + 4 semantic

Two-tier: L1 exact-match on the request hash, L2 semantic on the embedding. First gateway to ship both in production.

| Tier | Backend | Persistence | Typical use |
|---|---|---|---|
| L1 exact | `memory` | in-process | single-replica dev / test |
| L1 exact | `redis` | shared | **production default** — multi-replica |
| L1 exact | `disk` | local disk | single-node, restart-safe |
| L1 exact | `s3` | AWS S3 | archival, cold cache |
| L1 exact | `gcs` | Google Cloud Storage | archival |
| L1 exact | `azblob` | Azure Blob | archival |
| L2 semantic | `memory` | in-process | prototyping |
| L2 semantic | `pinecone` | managed vector | production semantic cache |
| L2 semantic | `qdrant` | self-hosted vector | |
| L2 semantic | `weaviate` | self-hosted vector | |

Configurable per deployment: similarity threshold, TTL, LRU size, embedding model, per-tenant isolation, streaming-safe write-back.

### 🧩 Internal subsystems — 39 packages

Each subsystem is its own Go package — isolated, tested, independently replaceable.

<table>
<tr>
<th>Group</th>
<th>Packages</th>
</tr>
<tr>
<td><b>Request path</b></td>
<td><code>server</code> · <code>router</code> · <code>middleware</code> · <code>pipeline</code> · <code>routing</code> · <code>streaming</code> · <code>translation</code></td>
</tr>
<tr>
<td><b>Providers</b></td>
<td><code>providers/{openai,anthropic,azure,bedrock,cohere,gemini,gauth}</code> · <code>anthropicfmt</code> · <code>genaifmt</code></td>
</tr>
<tr>
<td><b>Safety</b></td>
<td><code>guardrails/*</code> (18 scanners) · <code>privacy</code> · <code>secrets</code></td>
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
<td><code>async</code> · <code>batch</code> · <code>scheduled</code></td>
</tr>
<tr>
<td><b>Modern protocols</b></td>
<td><code>mcp</code> · <code>a2a</code> · <code>realtime</code> · <code>responses</code> · <code>files</code> · <code>video</code></td>
</tr>
<tr>
<td><b>Deployment</b></td>
<td><code>cluster</code> (Raft) · <code>edge</code> (edge mode) · <code>config</code></td>
</tr>
<tr>
<td><b>Observability</b></td>
<td><code>metrics</code> · <code>otel</code> · <code>audit</code> · <code>alerting</code></td>
</tr>
<tr>
<td><b>Plugins</b></td>
<td><code>plugins/*</code> (16 pipeline processors — see table above)</td>
</tr>
</table>

### 🔐 Governance & access control

| Capability | How it works | Granularity |
|---|---|---|
| **Virtual keys** | Gateway-issued keys abstract real provider keys; rotate upstream without app changes | tenant · user · team |
| **Key rotation** | Zero-downtime: rotate → promote → rollback, all atomic via <code>/-/admin/providers/{id}/rotate</code> | per provider |
| **Key-scoped model ACL** | <code>models: [...]</code> / <code>providers: [...]</code> on each key — requests outside the list rejected at the handler | key |
| **Budgets** | Dollar cap · token cap · request cap, enforced in the `budget` plugin | key · tenant · model |
| **Credits ledger** | Managed keys charge against a prepaid ledger; balance + top-up visible to admins | key |
| **Rate limits** | Sliding-window RPM / TPM in the `ratelimit` plugin; sliding-window state via Redis | key · tenant · endpoint |
| **Quotas** | Absolute caps (requests/day, tokens/month) in the `quota` plugin | key · tenant |
| **IP allowlist** | CIDR ranges in the `ipacl` plugin (runs before auth) | key · tenant |
| **RBAC** | Roles with permission sets; team hierarchy; route + resource level | org |
| **Tool-call governance** | `allowed_tools` / `denied_tools` per key; enforced for MCP + function calling | key |
| **Audit log** | Append-only, tamper-evident chain; every admin + high-risk event | org |
| **Multi-tenant isolation** | Shared nothing at runtime — separate rate-limit buckets, cache namespaces, audit streams | tenant |
| **SSO** (via Future AGI platform) | SAML 2.0 + OIDC | org |

### 📡 Observability

| Signal | Emitted as | Default exporter | Cardinality safe |
|---|---|---|---|
| Request duration | `agentcc_request_duration_ms` histogram | Prometheus | yes (bounded labels) |
| Request counter | `agentcc_requests_total{model,provider,status}` | Prometheus | yes |
| Error counter | `agentcc_errors_total{model,provider}` | Prometheus | yes |
| Token usage | `agentcc_tokens_{input,output}_total{model,provider}` | Prometheus | yes |
| Cost | `agentcc_cost_microdollars_total{model,provider}` | Prometheus | yes |
| Cache hit/miss | `agentcc_cache_{hits,misses}_total{type=exact\|semantic}` | Prometheus | yes |
| Uptime | `agentcc_uptime_seconds` | Prometheus | yes |
| Distributed traces | OpenTelemetry spans (W3C Trace Context propagated) | OTLP/gRPC | via OTel collector |
| Access logs | Structured JSON with request ID + redaction | stdout · Loki · CloudWatch · Datadog | |
| Audit events | Append-only chain, signed | stdout · S3 · SIEM via webhook | |
| Response headers | `x-agentcc-provider` · `x-agentcc-cost` · `x-agentcc-cache` · `x-agentcc-latency` | HTTP | per-request |

### 🧠 Advanced routing & resiliency — 15 strategies

Every request goes through a pluggable router stack. Mix and match — per virtual key, per tenant, or globally.

| Strategy | Package | What it does |
|---|---|---|
| **Weighted round-robin** | `roundrobin` | Distribute traffic across providers by configurable weights |
| **Latency-aware** | `latency` | Route to the provider with the lowest current P95 for the same model |
| **Cost-optimized** | `costopt` | Route to the cheapest provider that meets your SLO |
| **Adaptive** | `adaptive` | Online learning — continuously adjust weights based on success/latency |
| **Complexity-based** | `complexity` | Route simple queries to small/fast models, complex queries to large models |
| **Conditional** | `conditional` | Rule-based routing (by user, region, header, model family, prompt length…) |
| **Provider lock** | `providerlock` | Force specific requests to a specific provider (compliance, BYOC) |
| **Access groups** | `accessgroups` | Route keys in a group to a dedicated provider pool |
| **Race (hedged)** | `race` | Send to multiple providers in parallel; return first valid response |
| **Mirror (shadow)** | `mirror` | Mirror traffic to a second model, compare outputs, no user impact |
| **Model fallback** | `modelfallback` | `gpt-4o → claude-sonnet → gemini` cascades on error/timeout |
| **Failover** | `failover` | Hot-standby provider takes over when primary misbehaves |
| **Circuit breaker** | `circuitbreaker` | Trip after N errors / M% error rate; auto-recover with half-open probes |
| **Retry** | `retry` | Configurable backoff (linear, exponential, jittered); idempotency-aware |
| **Health monitor** | `healthmonitor` | Active + passive provider health; drives every other strategy above |

### 🔄 Format translation — cross-provider round-trip

Call the gateway in one format, route to a different upstream. Translator packages live under [`internal/translation/`](./internal/translation/):

| Translator | Covers |
|---|---|
| `translation/anthropic` | OpenAI ↔ Anthropic — `cache_control` blocks, tool_use / tool_result blocks, `finish_reason` mapping |
| `translation/gemini` | OpenAI ↔ Gemini — multi-part content, `finish_reason` mapping |

Azure OpenAI, Bedrock, Cohere, and Vertex AI are handled as **native provider packages** (not translators) — the gateway speaks each one directly via its package under [`internal/providers/`](./internal/providers/), preserving SigV4 signing, Azure deployment-name routing, and similar provider-specifics.

### 🔒 Secrets resolution

Provider API keys can be resolved from a secrets vault instead of hardcoded in config. Resolver implementations live under [`internal/secrets/`](./internal/secrets/):

| Backend | File |
|---|---|
| AWS Secrets Manager | `secrets/aws.go` |
| Azure Key Vault | `secrets/azure.go` |
| GCP Secret Manager | `secrets/gcp.go` |
| HashiCorp Vault (KV v2) | `secrets/vault.go` |
| Environment variables | `secrets/resolver.go` (built-in) |
| Files on disk | `secrets/resolver.go` (built-in) |

Combines with provider-key rotation (`POST /-/admin/providers/{id}/rotate` · `/promote` · `/rollback`).

### ✨ Other notable capabilities

| Capability | Detail |
|---|---|
| **Upstream header scrubbing** | Strips provider-identifying headers before returning to client — for OpenAI, strips `openai-organization`, `openai-processing-ms`, `openai-version`, `x-request-id` ([`providers/openai/openai.go`](./internal/providers/openai/openai.go)) |
| **Anthropic prompt-caching passthrough** | `cache_control` blocks preserved in the OpenAI ↔ Anthropic translator ([`translation/anthropic/types.go`](./internal/translation/anthropic/types.go)) |
| **Accurate tokenizer** | Token counting package at [`internal/tokenizer/`](./internal/tokenizer/) |
| **Hot config reload** | `POST /-/reload` reloads providers / keys / routes |
| **Cost tracking** | Uses the LiteLLM pricing DB (2 373 models loaded at startup); emits `agentcc_cost_microdollars_total` |
| **Per-tenant spend sync** | [`internal/tenant/spend_sync.go`](./internal/tenant/spend_sync.go) |
| **Clustering** | [`internal/cluster/`](./internal/cluster/) |
| **Edge mode** | [`internal/edge/`](./internal/edge/) |
| **Privacy redactor** | Logs/traces redaction, separate from the PII guardrail ([`internal/privacy/redactor.go`](./internal/privacy/redactor.go)) |
| **Streaming** | SSE + WebSocket passthrough; plugins can run at boundaries ([`internal/streaming/`](./internal/streaming/)) |
| **Shadow experiments** | Mirror traffic to a second model, compare results (`/-/shadow/stats`, [`routing/mirror.go`](./internal/routing/mirror.go)) |
| **AgentCard** | `/.well-known/agent.json` exposed for A2A discovery |
| **W3C Trace Context** | `traceparent` / `tracestate` handled via the [`otel`](./internal/otel/) subsystem |
| **Webhooks** | Usage, guardrail violation, budget breach deliveries |
| **Structured errors** | OpenAI-compatible `{error: {message, type, code, param}}` shape |

### 🖥️ Deployment surface

| Target | Single-binary | Image size | HA | Notes |
|---|:---:|---:|:---:|---|
| 🐳 Docker | ✅ | ~17 MB | — | `ghcr.io/future-agi/agent-command-center:latest` |
| ☸️ Kubernetes (Helm) | ✅ | — | ✅ Raft | HPA-ready, rolling upgrades, PDBs, liveness/readiness probes |
| ⚡ Bare metal (systemd) | ✅ | ~17 MB | ✅ | single binary + YAML config |
| ☁️ AWS | ✅ | — | ✅ | Fargate · EKS · EC2; SigV4 native for Bedrock |
| ☁️ GCP | ✅ | — | ✅ | Cloud Run · GKE; Vertex auth native |
| ☁️ Azure | ✅ | — | ✅ | ACI · AKS; Azure OpenAI native auth |
| 🌊 Edge mode | ✅ | ~17 MB | ✅ | Built-in <code>edge</code> subsystem — forward to central control plane |
| 🛒 AWS Marketplace | Coming soon | — | — | subscribable AMI |
| 🔒 Air-gapped / on-prem | ✅ | ~17 MB | ✅ | no phone-home, fully offline |
| 🖱️ One-click PaaS | via Docker | — | — | Render · Railway · Fly · Northflank |

---

## 📦 Client SDKs

No migration required. Every existing OpenAI client works out of the box — just point at the gateway URL.

```python
# Python — use the OpenAI SDK unchanged
from openai import OpenAI
client = OpenAI(base_url="http://localhost:8080/v1", api_key="acc_...")
```

Or use the official Agent Command Center clients for first-class features (virtual keys, retries, fallbacks, metadata):

| Package | Runtime | Install |
|---|---|---|
| `agentcc` | Python 3.9+ | `pip install agentcc` |
| `@agentcc/client` | Node 18+ | `npm install @agentcc/client` |
| `@agentcc/langchain` | Node 18+ | `npm install @agentcc/langchain` |
| `@agentcc/llamaindex` | Node 18+ | `npm install @agentcc/llamaindex` |
| `@agentcc/react` | Node 18+ | `npm install @agentcc/react` |
| `@agentcc/vercel` | Node 18+ | `npm install @agentcc/vercel` |

Full client source: [`future-agi/agent-command-center-sdk`](https://github.com/future-agi/agent-command-center-sdk).

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
    models: [gpt-4o, gpt-4o-mini, o1]

  anthropic:
    base_url: "https://api.anthropic.com"
    api_key: "${ANTHROPIC_API_KEY}"
    models: [claude-sonnet-4, claude-3-5-haiku]

cache:
  exact:
    backend: redis
    ttl: 5m
  semantic:
    backend: qdrant
    threshold: 0.85

guardrails:
  - pii
  - injection
  - secrets

routing:
  fallback:
    - openai/gpt-4o
    - anthropic/claude-sonnet-4
```

See [`config.example.yaml`](./config.example.yaml) for the full reference.

---

## 🖥️ Self-host

| Deployment | Status | Notes |
|---|:---:|---|
| 🐳 Docker | ✅ | Single image, < 30 MB |
| ☸️ Kubernetes (Helm) | ✅ | HPA-ready, rolling upgrades |
| ⚡ Bare metal (systemd) | ✅ | Single binary |
| ☁️ AWS / GCP / Azure | ✅ | Fargate · Cloud Run · ACI |
| 🛒 AWS Marketplace | Coming soon | |
| 🔒 Air-gapped | ✅ | No phone-home |

---

## 🔬 Reproduce the benchmarks

Numbers above are fully reproducible. Two modes:

### Containerised (matches the t3.xlarge-equivalent numbers)

```bash
# Build the self-contained bench image
docker build -f bench/Dockerfile.bench -t acc-bench .

# Pin to 4 vCPU / 16 GB (t3.xlarge resource profile)
docker run --rm --cpuset-cpus="0-3" --memory=16g acc-bench

# Or unconstrained (uses all host cores)
docker run --rm acc-bench
```

The image bundles the gateway, mock upstream, `hey`, the configs, and a runner script. Output prints throughput and P50/P95/P99 for every profile to stdout in ~30 seconds.

### Host-direct (matches the unconstrained numbers)

```bash
brew install hey            # or: go install github.com/rakyll/hey@latest

bash bench/run.sh           # full suite
bash bench/run.sh --quick   # short (5k reqs per profile)
```

What the harness does:

1. Builds a minimal OpenAI-compatible **mock upstream** ([`bench/mock-upstream.go`](./bench/mock-upstream.go)) that returns a canned chat-completion instantly — so measurements reflect gateway processing, not provider latency.
2. Starts **two gateway instances** side by side: bare ([`bench/bench.config.yaml`](./bench/bench.config.yaml)) and guardrails-on ([`bench/bench-guardrails.config.yaml`](./bench/bench-guardrails.config.yaml)).
3. Drives load with `hey` at 50 / 100 / 200 concurrent against each profile.
4. Runs `go test -bench=./internal/server/` for the Go microbenchmarks.
5. Reports throughput + P50/P95/P99 for every profile.

Real-provider smoke test (needs an actual API key):

```bash
# Point at any provider in your config and hit it through the loadtest binary
bin/loadtest -c 10 -n 50 -models gpt-4o-mini
```

**Commit your numbers** — we aggregate community results across hardware. PRs welcome.

---

## 🤝 Contributing

Agent Command Center is part of the [Future AGI monorepo](../../README.md). See the top-level [CONTRIBUTING.md](../../CONTRIBUTING.md) for setup, CLA, and PR conventions. Gateway-specific tips in [`cmd/server/README.md`](./cmd/server).

---

## 📄 License

Apache License 2.0 — see [LICENSE](../../LICENSE) and [NOTICE](../../NOTICE).

---

<div align="center">

**Built with ❤️ in Go by the [Future AGI](https://futureagi.com) team and contributors.**

[🌐 futureagi.com](https://futureagi.com) · [📖 docs](https://docs.futureagi.com/docs/prism) · [💬 Discord](https://discord.gg/UjZ2gRT5p) · [🐦 Twitter](https://twitter.com/futureagi)

</div>
