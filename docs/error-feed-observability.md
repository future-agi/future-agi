# Error Feed worker observability

Investigation and F6 grouping execute in `future-agi/omega-error-feed-worker`.
The workers export traceAI spans directly to internal FutureAGI
Observe projects. Investigation and grouping can share a project while retaining separate traces. Django remains the authority for claims, leases, billing,
checkpoints and publication. Telemetry is diagnostic and never substitutes for
those durable records.

## Configuration

Configure both the investigation and grouping deployments at runtime:

```sh
OMEGA_OBSERVABILITY_ENABLED=true
FI_API_KEY_FILE=/run/secrets/futureagi-api-key
FI_SECRET_KEY_FILE=/run/secrets/futureagi-secret-key
# Optional: collector base URL or full OTLP HTTP endpoint for your environment.
# FI_BASE_URL=https://your-collector
```

Use credentials for the internal observability workspace. Do not pass customer
API keys through claims. `FI_API_KEY` and `FI_SECRET_KEY` are also accepted for
local development. Leave `OMEGA_OBSERVABILITY_ENABLED` unset to disable tracing.
The investigation deployment defaults to `error-feed-investigation`; the grouping
deployment defaults to `error-feed-grouping`. Feature preparation and severity
traces belong to the grouping project. To choose other names, set `FI_PROJECT_NAME`
on each deployment; matching names select a shared internal project.

Keep Error Feed scanning disabled on both internal telemetry projects: scanning
worker traces would create a feedback loop. This is an operator configuration
requirement, not an automatic change to customer scan settings. No backend
migration or new Django environment variable is required.

## Finding an attempt

Filter Observe by `error_feed.attempt_id` to find an investigation, grouping or
severity attempt, or `error_feed.feature_attempt_id` for feature preparation.
These values are the existing private claim IDs and match the corresponding
Django attempt records. `error_feed.organization_id`, `error_feed.workspace_id`
and `error_feed.project_id` refer to the source scope, not the internal telemetry
project. Investigation also records `error_feed.job_id` and
`error_feed.trace_id` (the customer trace being investigated).

`user.id` is the source claim's `organization_id`: the customer organization
is the user of the internal Error Feed service. It is attached to the root and
all descendant agent, tool, decision and model spans for both workers. This
supports organization-level user filtering across source projects. No end-user
identity is inferred from customer trace content. Concurrent claims keep their
organization identities isolated; independent roots reset identity context.
Claims also carry the source organization display name (falling back to name)
and project name. These become `user.name`, `error_feed.organization_name`, and
`error_feed.project_name` on roots and descendants. Deploy the updated Django
claim services/serializer to supply these names; older claims remain supported.

Every attempt has a new telemetry trace. Customer trace IDs are attributes,
not telemetry parent IDs. Investigation retries share `session.id` derived
from the job ID. Grouping and severity sessions use their attempt ID; feature
preparation uses its feature attempt ID. All descendants inherit the session.

Investigation spans nest controller, child and verifier stages, evidence tools,
and AgentCC model calls. Grouping, feature preparation and severity have
separate roots. LLM spans record requested/routed models, tokens, cache usage,
gateway request IDs, HTTP status and reported cost. Missing cost remains
unknown; explicit zero is preserved. Paid-call receipts in Django remain the
billing source of truth, including reuse of a previously settled result.

Content is off by default. Set `OMEGA_OBSERVABILITY_CAPTURE_CONTENT=true` to
capture actual work inputs, agent responses, model requests/responses and tool
arguments/results in `input.value` / `output.value`. Each JSON field is bounded
to 32 KiB with explicit truncation metadata. Customer evidence and recording
URLs may be included. Control inputs exclude lease tokens; known credential
object keys are redacted, but free-form content is not automatically scrubbed.
Exception messages/stacks remain excluded.

`llm.model_name` uses the routed model; the requested alias remains available.
Known AgentCC response charges populate `gen_ai.cost.total` so Observe uses
AgentCC's six-decimal USD accounting rather than estimating it again. Missing
charges remain unknown in gateway metadata, though Observe may estimate them.
Usage is emitted only on model spans, avoiding double counting. The current
Observe trace list reads root-span usage and needs child aggregation for trace
totals; session detail can aggregate all member spans. A supported customer failure is a successful investigation;
an operationally failed investigation is marked as a telemetry error.
Exporter/configuration failures must not fail a claim. Both daemons drain
active work before a bounded exporter shutdown.

## Validation and rollout

Build and test the worker branch before releasing its image. Its pinned traceAI
SDK and dependencies are bundled from a committed npm lockfile into a verified
tarball; the Docker install stays offline. Enable the environment settings on
both worker deployments after selecting both internal projects and mounting keys.
Run one investigation and one grouping job, then confirm nested spans and
source attempt IDs in Observe. This requires live workspace credentials; local
mock-provider and OTLP receiver tests do not certify production delivery.

References: [Observe quickstart](https://docs.futureagi.com/docs/observe/quickstart),
[traceAI](https://docs.futureagi.com/docs/observe/concepts/traceai), and
[collector endpoints](https://docs.futureagi.com/docs/observe/reference/export-formats).

Feature preparation uses the trace name `error_feed.prepare_findings_for_grouping`: it prepares embeddings and lookup features from investigation findings before grouping compares them.
