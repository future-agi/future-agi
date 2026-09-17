# Environments API contracts

Status as of 2026-09-16:

- Sections 1 to 3 (list, delete, run) are on branch `feat/th-7962-environments-list-api`, PR #2858.
- Sections 4 and 5 (intake, structured preflight) are implemented but not yet in a PR. Shapes are final unless the preflight review changes them.


Base path: `/simulate/api/`. All endpoints need the normal authenticated session or API key headers, plus the organization/workspace headers the rest of the simulate API uses. Timestamps are ISO 8601 with timezone. IDs are UUID strings.

## 1. Environments list

`GET /simulate/api/harness-environments/?page=1&limit=25`

Query: `page` (default 1), `limit` (1..100, default from pagination settings).

Response `200`:

```json
{
  "count": 42,
  "next": "…?page=2&limit=25",
  "previous": null,
  "total_pages": 2,
  "current_page": 1,
  "results": [
    {
      "id": "0b1c…",
      "name": "ride-voice-agent",
      "description": "Books and manages rides over the phone",
      "source_kind": "github",
      "agent_type": "voice",
      "status": "completed",
      "stage": "completed",
      "scenario_count": 12,
      "tools_count": 7,
      "last_updated": "2026-09-16T10:12:03+00:00",
      "created_at": "2026-09-14T08:00:00+00:00"
    }
  ]
}
```

Field notes:

- `source_kind`: `github` | `archive` | `provider` | `remote` (`archive` is a folder upload).
- `agent_type`: `voice` | `chat`.
- `status`: `building` | `running` | `completed` | `failed`. `stage` is the fine-grained pipeline stage if you want a progress bar.
- `description` and `tools_count` are `null` until authoring has produced a contract.
- `last_updated` moves only on real content changes (contract, scenarios, terminal state), not on every poll. It is `null`-safe: rows without a recorded change fall back to `created_at`.
- Sorted newest change first.

## 2. Delete an environment

`DELETE /simulate/api/harness-environments/{id}/`

Response `204` with no body. If a run is live it is cancelled first. `404` `{ "detail": "Environment not found" }`.

## 3. Run a simulation on an environment

`POST /simulate/api/harness-environments/{id}/run/` with no body.

Reuses the saved contract and scenario suite. Response `202`:

```json
{
  "environment_id": "0b1c…",
  "job_id": "0b1c…",
  "run_id": "9f2a…",
  "state": "queued",
  "stage": "queued"
}
```

Poll progress on `GET /simulate/api/harness-jobs/{job_id}/`. Errors come back as `{ "error": "<code>", "message": "…", "retryable": bool }` with a 4xx/5xx.

## 4. Intake options (what each source door accepts)

`GET /simulate/api/harness-environments/intake/`

Read this once when the create flow opens instead of hardcoding what is supported.

Response `200`:

```json
{
  "sources": [
    {
      "kind": "github",
      "label": "GitHub repository",
      "requires": ["repository"],
      "optional": ["ref", "commit_sha"],
      "hosts": ["github.com"],
      "unsupported_hosts": ["gitlab.com", "bitbucket.org"],
      "visibility": ["public", "private"],
      "private_auth": "github_app",
      "private_auth_configured": true
    },
    {
      "kind": "archive",
      "label": "Code upload",
      "requires": ["archive_artifact_id"],
      "upload": {
        "endpoint": "/simulate/api/harness-jobs/sources/",
        "mode": "folder",
        "archive_formats": [],
        "max_compressed_bytes": 268435456
      }
    },
    {
      "kind": "provider",
      "label": "Hosted platform",
      "requires": ["agent.connector", "agent.mode"],
      "connectors": [
        { "id": "vapi",        "label": "Vapi",        "modality": "voice", "target_field": "assistant_id", "modes": ["connect_only", "provider_import"], "credentials": ["VAPI_API_KEY"] },
        { "id": "retell",      "label": "Retell",      "modality": "voice", "target_field": "agent_id",     "modes": ["connect_only", "provider_import"], "credentials": ["RETELL_API_KEY"] },
        { "id": "retell_chat", "label": "Retell chat", "modality": "chat",  "target_field": "agent_id",     "modes": ["connect_only", "provider_import"], "credentials": ["RETELL_API_KEY"] }
      ]
    },
    {
      "kind": "remote",
      "label": "Running agent",
      "requires": ["endpoint"],
      "accepts_secret_refs": false
    }
  ]
}
```

What this means for the UI:

- **GitHub**: `repository` is `owner/name`, not a full URL. Private repos need `visibility: "private"` and an `installation_id` of the Future AGI GitHub App; if `private_auth_configured` is false, hide or disable the private option. GitLab/Bitbucket are not supported. `ref` is any branch, tag or commit SHA; omit it and the repo's default branch (`HEAD`) is used. `commit_sha` overrides `ref` for the actual fetch, and a mismatch between the two returns `409 github_commit_mismatch`. A `ref` that does not exist on the remote is not caught at preflight: it fails during acquisition as `502 github_clone_failed`.
- **Code upload**: a project *folder*, sent as multipart to `upload.endpoint` with repeated `files` and `paths` fields (one `paths` entry per file, repository-relative). `archive_formats` is empty: zip is not accepted; do not offer it, and uploading a single `.zip`/`.tar`/`.tar.gz`/`.tgz`/`.rar`/`.7z` file returns `400` with `upload the expanded project folder; archives are not supported`. The response gives `source_id`, which you send as `source.archive_artifact_id`.

  Three limits apply. Enforce the first two client-side, because they are the ones a browser can measure:

  | Limit | Value | Response when exceeded |
  |---|---|---|
  | Total raw bytes | 200 MiB | `413` `source may not exceed 200 MiB` |
  | File count | 5000 | `413` `source may contain at most 5000 files` |
  | Compressed archive | `max_compressed_bytes` (256 MiB) | `413` `source_archive_too_large` |

  `max_compressed_bytes` is measured after gzip, so it is not something the UI can check up front and is effectively unreachable given the 200 MiB raw cap. Size the client-side guard on the raw total.
- **Hosted platform**: only the connectors listed. Send `source: {"kind": "provider"}` (or omit `source`; the backend fills it), `agent.connector`, `agent.mode`, and `agent.config[target_field]` with the assistant/agent ID. Bland and ElevenLabs are not connectors.
- **Running agent** is a different door from hosted platform: `source: {"kind": "remote", "endpoint": "https://…"}`. It must not carry `agent.secret_refs`.

### Upload response (`POST /simulate/api/harness-jobs/sources/`, multipart)

```json
{ "source_id": "c2e1…", "name": "my-agent", "file_count": 143, "total_bytes": 918233 }
```

## 4b. Hosted platform: voice agent fields

For the hosted-platform door, `agent` accepts a `call_direction` alongside the existing fields:

```json
{
  "source": { "kind": "provider", "visibility": "public" },
  "agent": {
    "connector": "vapi",
    "mode": "connect_only",
    "call_direction": "inbound",
    "config": { "assistant_id": "asst_9f2c1188" },
    "secret_refs": { "VAPI_API_KEY": { "manager": "platform-vault", "key": "…", "version": "1", "purpose": "target_provider" } }
  }
}
```

- `call_direction`: `inbound` (the simulated caller dials the agent) or `outbound` (the agent dials the simulated caller). Optional.
- Accepted for the voice connectors `livekit`, `vapi`, `retell`, and for `auto` which is unresolved at admission. Sending it with `retell_chat` returns `400` naming the voice connectors, because a chat target has no call to place.
- **It is validated but does not yet change run behaviour.** The harness execution path has no direction handling, so treat the toggle as recorded intent, not an effect. Do not describe it to users as changing who speaks first until the plumbing lands.

Mapping from the UI panel to the request body:

| Panel field | Request |
|---|---|
| `agentType: "voice"` + `provider: "retell"` | `connector: "retell"` |
| `agentType: "chat"` + `provider: "retell"` | `connector: "retell_chat"` |
| `provider: "vapi"` | `connector: "vapi"` (voice only; `agentType` is redundant) |
| `agentId` | `config.assistant_id` for Vapi, `config.agent_id` for Retell |
| `apiKey` | never sent inline; exchange it at `/harness-jobs/secret-values/` and send the returned ref in `secret_refs` |
| `repoUrl` (optional) | `source.kind: "github"` with `repository: "owner/name"`, which is permitted alongside `connect_only` |
| `callDirection` | `agent.call_direction` |

`agentType` itself is not a request field. It is load-bearing only for Retell, where it selects `retell` versus `retell_chat`.

### Connectors that are not supported

`connector` accepts exactly `livekit`, `vapi`, `retell`, `retell_chat`, `auto`. Anything else returns `400` with a message naming that set, for example:

```json
{ "agent": { "connector": ["bland is not a supported connector; choose one of livekit, vapi, retell, retell_chat, auto"] } }
```

This means the panel's `bland`, `elevenlabs`, and the entire chat roster (`openai_assistants`, `langgraph`, `crewai`, `claude_agents`) cannot be submitted **through the hosted-platform door**. Gate them in that panel.

### Chat agents: what actually runs

Two separate questions. The request contract is the permissive one; ALK is the binding one.

**The API accepts a chat agent from a repository** with `connector: "auto"` and no provider credentials:

```json
{
  "source": { "kind": "github", "repository": "acme/chat-agent", "ref": "main", "visibility": "public" },
  "agent": { "connector": "auto", "config": {}, "secret_refs": {} }
}
```

**But acceptance is not execution.** ALK can only drive a chat target that already exposes a conversational ingress:

- `kind` must be `http` or `websocket`, and `http` requires both a port and a path.
- `protocol` must be `fi.alk` or `openai_chat` (an OpenAI chat-completions shaped envelope).
- ALK containerizes the repository's existing process but **never generates an endpoint the repository does not implement**, and never generates agent behaviour.

So a repository works for chat when it ships a server exposing an OpenAI-compatible or `fi.alk` chat endpoint. A repository that only calls a vendor SDK from library code, with no HTTP server of its own, cannot be driven, and neither can a hosted product reached solely through its own proprietary API.

Applied to the panel's chat roster:

| Platform | Connect by ID | From a repository |
|---|---|---|
| OpenAI Assistants | No connector | Only if the repo serves its own `openai_chat` or `fi.alk` endpoint |
| LangGraph Cloud | No connector | Its deployment URL is a `remote` source, but the endpoint must speak `fi.alk` or `openai_chat`, which the LangGraph API does not |
| CrewAI | No connector | Only if the repo wraps the crew in a compatible HTTP endpoint |
| Claude Agents | No connector | Same condition |
| Retell chat | `retell_chat` + `RETELL_API_KEY` | n/a |

Practical consequence: **`retell_chat` is the only chat target that works out of the box.** Everything else requires the customer's repository to already expose a compatible chat endpoint. Do not promise chat support for these four platforms on the strength of the request validating.

Note that this failure surfaces late. The serializer accepts the job, and preflight cannot catch it either, because ingress detection happens during authoring inside the sandbox. The job fails there, not at admission.

## 4c. Creating a job (the request body)

`POST /simulate/api/harness-jobs/` -> `202` with the job read DTO. Poll `GET /simulate/api/harness-jobs/{job_id}/`.

Unknown fields are rejected, so send only what is listed here.

| Field | Required | Notes |
|---|---|---|
| `schema_version` | no | defaults to `futureagi.harness-job.v1` |
| `source` | see matrix below | omit only for the hosted door |
| `agent` | yes | `connector`, plus `mode`/`config`/`secret_refs` per door |
| `artifacts` | **yes** | `level` is required: `metadata-only`, `traces`, `traces-and-recordings`, `full` |
| `scenario_count` | no | default 10, range 1..200 |
| `run_id`, `seed`, `platform_run_id`, `metadata` | no | |
| `runtime`, `security`, `retry` | no | full defaults applied when omitted |

Create refuses a job whose connector is missing its credentials. Preflight reports the same thing as unmet requirements instead, so use preflight to drive a readiness panel and create only once it is clean.

### Which `source` and `mode` per door

| Door | `source` | `connector` | `mode` |
|---|---|---|---|
| Code upload | `{"kind": "archive", "archive_artifact_id": "…"}` | `livekit` / `auto` | **omit** |
| GitHub repo | `{"kind": "github", "repository": "owner/name"}` | `livekit` / `auto` | **omit** |
| Hosted platform | omit, or `{"kind": "provider"}` | `vapi` / `retell` / `retell_chat` | **required**: `connect_only` or `provider_import` |
| Hosted + repo | `{"kind": "github", "repository": "owner/name"}` | `vapi` / `retell` / `retell_chat` | optional; send `connect_only` |
| Running agent | `{"kind": "remote", "endpoint": "https://…"}` | `auto` | **omit** |

Two rules produce every cell above:

- `mode` is only accepted for Vapi and Retell. Sending it with `livekit` or `auto` returns `400 provider mode is supported only for Vapi and Retell`.
- A `provider` source (including an omitted `source`) requires `mode` to be `connect_only` or `provider_import`, otherwise `400 provider sources require a connected Vapi or Retell agent ID`.

Nothing infers `mode`. It has no default and is never filled in server-side, unlike `connector` (which accepts `auto`) and `source` (which is synthesized for the hosted door). The UI sets it from which panel the user is in; the user never sees the word.

### Worked examples

**Code upload, LiveKit**

```json
{
  "source": { "kind": "archive", "archive_artifact_id": "c2e1…" },
  "agent": {
    "connector": "livekit",
    "config": { "livekit_url": "wss://acme.livekit.cloud" },
    "secret_refs": {
      "LIVEKIT_API_KEY": { "manager": "platform-vault", "key": "harness-livekit_api_key-…", "version": "1", "purpose": "target_provider" },
      "LIVEKIT_API_SECRET": { "manager": "platform-vault", "key": "harness-livekit_api_secret-…", "version": "1", "purpose": "target_provider" }
    }
  },
  "artifacts": { "level": "traces-and-recordings" },
  "scenario_count": 10
}
```

**Private GitHub repo, on a branch**

```json
{
  "source": {
    "kind": "github",
    "repository": "acme/support-agent",
    "ref": "release/2.1",
    "visibility": "private",
    "installation_id": "48211903"
  },
  "agent": { "connector": "auto", "config": {}, "secret_refs": {} },
  "artifacts": { "level": "traces" }
}
```

**Hosted platform, no repo**

```json
{
  "agent": {
    "connector": "vapi",
    "mode": "connect_only",
    "config": { "assistant_id": "asst_9f2c1188" },
    "secret_refs": {
      "VAPI_API_KEY": { "manager": "platform-vault", "key": "harness-vapi_api_key-…", "version": "1", "purpose": "target_provider" }
    }
  },
  "artifacts": { "level": "traces-and-recordings" }
}
```

`source` is absent on purpose: the backend fills in `{"kind": "provider", "visibility": "public"}`.

### Attaching a repo to a hosted agent

Permitted, and the repo is genuinely cloned and shipped, but **the hosted agent is still what gets tested**. The repo is authoring context only; its code never runs. Under `environment_backed` this inverts, but that mode is not offered (see 4b).

One caveat worth surfacing in the UI: if the attached repo contains Google Vertex ADC signatures, the job is refused with `422 credential_file_required` demanding a `GOOGLE_APPLICATION_CREDENTIALS` upload, even though the repo is only read and never executed. Attaching a repo can fail a job that would have succeeded without it.

## 4d. LiveKit (no intake entry)

LiveKit does not appear in `GET /intake/` because it is not a source kind. It is a **connector that only ever arrives with code**, so build it as part of the code door, not as its own tile.

There is no ID-based path to a LiveKit agent, enforced twice: a `provider` source requires `vapi`/`retell`/`retell_chat`, and `mode` is only accepted for Vapi and Retell. So a source (`github` or `archive`) is **mandatory**.

Three inputs, and they are not all the same kind of thing:

| Input | Where it goes | Secret? |
|---|---|---|
| `LIVEKIT_URL` | `agent.config.livekit_url` | **no**, plain text |
| `LIVEKIT_API_KEY` | `agent.secret_refs` | yes |
| `LIVEKIT_API_SECRET` | `agent.secret_refs` | yes |

The URL must be readable in the clear because the platform derives the sandbox's allowed egress host from it. It is accepted in `secret_refs` too, but `config.livekit_url` is the intended place.

All three are mandatory. There is no partial credit: submit two of three and create fails with the missing aliases listed.

Two behaviours to design around:

- **These three keys are what makes `connector: "auto"` resolve to LiveKit.** If the authored contract says voice and all three aliases are present, the connector is set to `livekit`. Prefer naming the connector explicitly rather than relying on that.
- **LiveKit credentials are stripped from the authoring environment** and reach only the execution sandbox. A mistyped secret therefore passes authoring silently and fails later when a call is placed. Catch it at preflight, not at create.

## 4e. Exchanging secrets for refs

`POST /simulate/api/harness-jobs/secret-values/` -> `201`

Credentials are never sent inline in a job. Exchange them here first and put the returned refs in `agent.secret_refs`.

Request:

```json
{ "environment_values": { "VAPI_API_KEY": "sk-live-…" } }
```

Response:

```json
{
  "secret_refs": {
    "VAPI_API_KEY": {
      "manager": "platform-vault",
      "key": "harness-vapi_api_key-3f9a2c…",
      "version": "1",
      "purpose": "target_provider"
    }
  }
}
```

Pass each returned object through unchanged as the value of the matching alias in `agent.secret_refs`.

Limits and validation:

- At most 100 values per call, 1 MiB total across all values, 64 KiB per value.
- Names must be alphanumeric plus underscore, and must not start with a digit.
- Runner-reserved names are rejected: `DOCKER_HOST`, `FI_API_KEY`, `FI_BASE_URL`, `FI_SECRET_KEY`, `HARNESS_PLATFORM_API_KEY`, `HARNESS_PLATFORM_SECRET_KEY`, `HARNESS_PLATFORM_URL`, `HOME`, `PATH`, `PYTHONPATH`.
- Values are encrypted, scoped to the submitting organization, and resolved only inside the selected hosted job.

## 4f. Accepted but inert

Two fields validate and then do nothing. Do not surface either, and do not describe them to users as working:

- **`agent.call_direction`** (`inbound` / `outbound`): validated against the connector, but the execution path has no direction handling.
- **`agent.mode: "environment_backed"`**: appears only in the request serializer; no platform code branches on it, and `config.lifecycle_manifest` is validated and never read again. Whether ALK implements it inside the sandbox cannot be determined from the platform repo.

## 5. Preflight

`POST /simulate/api/harness-jobs/preflight/`

Request: the same body as creating a job, plus optional `credential_values` (raw key values to verify live; never stored, never echoed). Minimal example:

```json
{
  "schema_version": "futureagi.harness-job.v1",
  "source": { "kind": "github", "repository": "acme/support-agent", "ref": "main", "visibility": "public" },
  "agent": { "connector": "auto", "config": {}, "secret_refs": {} },
  "scenario_count": 5,
  "seed": 42,
  "runtime": { "isolation": "dedicated_vm", "cpu_units": 4, "memory_mb": 8192, "parallelism": 1, "concurrency_weight": 1, "max_duration_seconds": 600, "network_policy": "live" },
  "security": { "untrusted_source": true, "read_only_source": true, "allow_privileged": false, "allow_host_runtime_control": false, "allowed_egress_domains": [] },
  "retry": { "max_infrastructure_attempts": 2, "initial_backoff_seconds": 1, "max_backoff_seconds": 15, "retryable_domains": ["infrastructure"] },
  "artifacts": { "level": "full", "retention_days": 30 },
  "credential_values": { "LIVEKIT_URL": "wss://…", "LIVEKIT_API_KEY": "…", "LIVEKIT_API_SECRET": "…" }
}
```

Response `200`, whether or not the checks passed:

```json
{
  "state": "failed",
  "ready_to_submit": false,
  "checks": [
    { "id": "source",              "label": "Source reachable",                 "status": "passed",  "detail": "143 files scanned", "missing": [], "fix": null },
    { "id": "credentials_present", "label": "Target credentials",               "status": "failed",  "detail": "2 required credential(s) not provided", "missing": ["LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"], "fix": "Add LIVEKIT_API_KEY, LIVEKIT_API_SECRET under target credentials" },
    { "id": "credential_files",    "label": "Credential files",                 "status": "skipped", "detail": "the source does not require a credential file", "missing": [], "fix": null },
    { "id": "credentials_valid",   "label": "Credentials accepted by provider", "status": "failed",  "detail": "LiveKit rejected LIVEKIT_API_KEY (HTTP 401)", "missing": ["LIVEKIT_API_KEY"], "fix": "Replace LIVEKIT_API_KEY with a key the provider accepts" },
    { "id": "provider_target",     "label": "Provider agent reachable",         "status": "skipped", "detail": "no hosted provider agent to look up", "missing": [], "fix": null }
  ],
  "credentials": {
    "scanned_files": 143,
    "detected_connectors": ["livekit"],
    "requirements": [
      { "environment_name": "LIVEKIT_URL", "purpose": "target_provider", "required": true, "status": "configured" },
      { "environment_name": "LIVEKIT_API_KEY", "purpose": "target_provider", "required": true, "status": "missing" }
    ],
    "credential_choices": [],
    "probe": [
      { "provider": "livekit", "label": "LiveKit", "aliases": ["LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"], "ok": false, "message": "…" }
    ]
  },
  "effective_parallelism": 1,
  "snapshot": { "name": "…", "digest": null, "engines": { "…": {} }, "runtimes": { "…": [] } }
}
```

How to render it:

- `state` is `connected` when no check failed, otherwise `failed`. A "testing" state is yours to show while the request is in flight; the endpoint is synchronous.
- `checks` is always these five ids, in this order. `status` is `passed` | `failed` | `skipped`. `skipped` means the check does not apply to this source kind (for example `source` on a hosted-platform target). `missing` is always an array (env var names or file aliases). `fix` is a one-line hint on failure, `null` otherwise.
- The preflight-fail screen is `checks.filter(c => c.status === "failed")`.
- A bad repository, a revoked GitHub App installation, or an expired upload is now a **failed `source` check on a 200**, not an error response.
- `ready_to_submit` is `state === "connected"`; kept for older callers.
- `credentials`, `effective_parallelism` and `snapshot` are unchanged from before and still available if you need the per-variable requirement table.

Two things still return an error body instead of a check, because they mean the request itself is malformed and Run rejects them identically: an unsupported `secret_refs` manager, and more egress domains than the sandbox allows. Shape: `{ "error": "egress_domain_limit_exceeded", "message": "…", "retryable": false }` with `400`.
