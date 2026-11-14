# Claude SDK error localizer

The existing `process_single_error_localization` activity now creates a Claude
Agent SDK localizer by default. Eligibility checks, task statuses, billing,
dataset/log updates, and the frontend result envelope are retained. Set
`ERROR_LOCALIZER_BACKEND=legacy` to explicitly select the original localizer.
There is no automatic fallback to another provider on SDK/gateway failure.

## Runtime and configuration

The SDK and bundled CLI are pinned to **0.2.139**, matching the ALK harness.
The runtime uses MCP v1 (`mcp>=1.26.0,<2`), aiohttp, and the existing media
dependencies (Pillow, pydub, ffmpeg, and storage configuration). Python 3.13+
also needs `audioop-lts`. These are declared in the backend dependencies.

The Claude-shaped CLI model is `claude-sonnet-4-6`; **every upstream request is
rewritten to `vertex_ai/gemini-3.7-flash` before reaching AgentCC**. The alias
does not select an Anthropic model and does not depend on a global gateway
alias configuration. Direct Vertex/Bedrock/Foundry SDK transports are disabled.

| Setting | Resolution / default |
| --- | --- |
| `ERROR_LOCALIZER_BACKEND` | `claude_agent_sdk`; rollback: `legacy` |
| Gateway URL | `ERROR_LOCALIZER_AGENTCC_URL`, then `AGENTCC_BASE_URL`, `AGENTCC_INTERNAL_URL`, `AGENTCC_GATEWAY_URL`, then `http://agentcc-gateway:8080` |
| Gateway key | `ERROR_LOCALIZER_AGENTCC_API_KEY`, then `AGENTCC_HARNESS_API_KEY`, `AGENTCC_API_KEY`, `AGENTCC_INTERNAL_API_KEY` |
| `ERROR_LOCALIZER_TIMEOUT_SECONDS` | `90`; covers the complete SDK session |
| `ERROR_LOCALIZER_MAX_TURNS` | `10` |
| `ERROR_LOCALIZER_SDK_BUDGET_USD` | `1`; conservative CLI limit priced using its Claude alias, **not** Gemini billing |

The URL/key must identify the same gateway deployment. Existing ALK host
settings (`AGENTCC_BASE_URL` and `AGENTCC_HARNESS_API_KEY`) work directly.
Supply settings to the **worker process**, which executes the task. The
localizer does not load a developer `.env` file or change process-global env.

Activation requires deploying the new code and dependencies to the workers.
No backend or worker restart is performed by this implementation or its tests.

## Agent and media transport

Each task gets a fresh SDK session, isolated temporary configuration directory,
and three in-process MCP tools:

- `inspect_input`: paginated sentence/region/timestamp catalog; full-image context.
- `inspect_units`: actual sentence text, image patches, or native audio segments.
- `submit_findings`: schema-validated output with registered, inspected unit IDs.

The agent may inspect sibling inputs but localizes one selected field. Source
offsets, image coordinates, and audio timestamps come from the existing Python
chunker. The model cannot supply replacement locations. Results use the existing
`LocalizerResult`, including `orgSen`, `orgPatch`, and `orgSegment` metadata.
Unlocalizable cases require an explanation and return a skip reason. Missing or
invalid submissions, upstream errors, turn limits, and timeouts fail the task;
they do not synthesize a successful whole-input finding.

The Python SDK drops audio MCP content, and the current gateway's Anthropic
translator extracts only text from nested tool results. A per-run loopback
adapter bridges both gaps:

1. Inspection tools return opaque references to a per-run media registry.
2. The adapter expands exact references in tool results into **top-level user
   media blocks** after the CLI has processed the message.
3. AgentCC's base64 `source` transport preserves the actual MIME type through
   the canonical data URI into Gemini `inlineData`. Audio uses `audio/mpeg`;
   it is not relabeled JPEG or reduced to a transcript. The Anthropic-shaped
   carrier uses `type: image`, which this gateway accepts for base64 sources
   with non-image MIME types. This is an AgentCC compatibility extension.

The adapter binds only to loopback with an ephemeral token and forwards only
the Messages/count-tokens endpoints to the configured gateway. It is cleaned up
with the SDK session. Built-in SDK tools and external MCP/config discovery are
disabled. The actual gateway key stays in the adapter rather than the CLI.

Gateway-bound token counts are priced using the existing Gemini model pricing
helper, not the CLI's Claude-alias cost estimate. The task's existing metering
path consumes this cost. This remains token-based estimated pricing, not a
reconciliation against gateway invoices.

## Simulation conversation audio

Simulation task creation snapshots the same transcript serializer used by the
call-details drawer: provider/direction-corrected speakers and timestamps with
`recording_offset_ms` applied. Audio mapped from `voice_recording`,
`stereo_recording`, `assistant_recording`, or `customer_recording` (including
`call.*` aliases) uses those utterance boundaries instead of fixed-size chunks.
Per-speaker recordings use only that speaker's turns; arbitrary audio mappings
cannot inherit the call's timing. Other sources keep the existing chunker.

Both conversation sides can be inspected as context. Only normalized
`assistant` (the customer's tested agent) utterances are eligible findings.
Python rejects simulator findings, whole-recording findings, and findings on
sibling text inputs in this mode. Ranking is unchanged. `orgSegment` retains
the playback URL and exact start/end times, and adds the source utterance ID,
speaker role, and transcript content. Overlapping turns retain their original
boundaries; a combined-recording clip can therefore contain both voices.

Missing, invalid, or out-of-recording times are never guessed. If no timed agent
utterances can be inspected, the agent must return unlocalizable (or the task
fails if it cannot finish). Existing queued simulation audio tasks without a
snapshot must be recreated to obtain utterance data. The legacy backend is
rejected for simulation audio because it cannot enforce speaker attribution.
No schema migration or frontend change is required.

## Validation

Unit tests exercise source locations, invalid submissions, rollback selection,
credential precedence, native media preservation, and incomplete SDK runs. A
real SDK/CLI test uses a local fake gateway to verify the MCP tool loop and raw
audio at the HTTP boundary without model credentials or S3.

From `futureagi`, with the backend test dependencies installed:

```sh
ENV_TYPE=test SECRET_KEY=localizer-test-only CH_ENABLED=false \
  NO_STARTUP_DB_MUTATIONS=true python -m pytest --confcutdir=ee/tests \
  ee/tests/test_localizer_claude_harness.py \
  ee/tests/test_error_localizer.py ee/tests/test_error_localizer_skip.py
```

Synthetic live tests are opt-in and exercise real Gemini text/image/audio
inference. They stub storage/chunk fixtures to avoid writes to S3 or databases:

```sh
ENV_TYPE=test SECRET_KEY=localizer-test-only CH_ENABLED=false \
  NO_STARTUP_DB_MUTATIONS=true LOCALIZER_LIVE_ENV_FILE=/path/to/alk.env \
  python -m pytest --confcutdir=ee/tests \
  ee/tests/test_localizer_claude_harness.py -m live_llm
```

`LOCALIZER_LIVE_ENV_FILE` reads only AgentCC connection settings, exclusively
inside the opt-in tests. Never commit keys or the referenced environment file.

The dependency lock adds the SDK's exact ALK-locked package metadata and keeps
all existing package versions. A full re-resolution currently requires fetching
the repository's unrelated `en-core-web-sm` wheel from GitHub. EE's pre-existing
requirements export drift is intentionally not refreshed as part of this change.
