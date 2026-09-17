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

- **GitHub**: `repository` is `owner/name`, not a full URL. Private repos need `visibility: "private"` and an `installation_id` of the Future AGI GitHub App; if `private_auth_configured` is false, hide or disable the private option. GitLab/Bitbucket are not supported.
- **Code upload**: a project *folder*, sent as multipart to `upload.endpoint` with repeated `files` and `paths` fields (one `paths` entry per file, repository-relative). `archive_formats` is empty: zip is not accepted; do not offer it. The response gives `source_id`, which you send as `source.archive_artifact_id`. `max_compressed_bytes` is the server limit after compression.
- **Hosted platform**: only the connectors listed. Send `source: {"kind": "provider"}` (or omit `source`; the backend fills it), `agent.connector`, `agent.mode`, and `agent.config[target_field]` with the assistant/agent ID. Bland and ElevenLabs are not connectors.
- **Running agent** is a different door from hosted platform: `source: {"kind": "remote", "endpoint": "https://…"}`. It must not carry `agent.secret_refs`.

### Upload response (`POST /simulate/api/harness-jobs/sources/`, multipart)

```json
{ "source_id": "c2e1…", "name": "my-agent", "file_count": 143, "total_bytes": 918233 }
```

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
