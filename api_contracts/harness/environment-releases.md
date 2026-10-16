# Environment Releases, Suites, Agent Bindings and Run Manifests (v0.3)

**Date:** 2026-10-15 (v0.3 adds §7.8) · **Status:** DRAFT for agreement
(milestone M0 of `prebuilt-agent-environments-implementation.md`)
**Producers:** environment and scenario authoring in ALK (`EnvironmentRelease`,
`SuiteRelease`); the platform (`AgentBinding`, `RunManifest`, compositions).
**Consumer:** the hosted guest, which assembles a run bundle from these objects.
**Pinned against:** bundle-producer-contract v1.1, world-handle-interface v3.5,
hosted-execution-seams v1.15.
**Implementation:** `fi.alk.harness.releases` in ALK is the only sealer and
verifier. Producers call it; nobody reimplements a digest. The first release is
`fi.alk.environments.appointment_booking`
(`python -m fi.alk.environments.appointment_booking.build --out DIR`).

## 1. Why

Today one artifact carries everything, so nothing can be reused or compared:

- One sealed `environment-bundle.v2` holds the world, the tool handlers, the
  agent's own process and the scenarios. Its digest covers `scenarios/`
  (bundle-producer §2.1), so editing one scenario re-keys the environment.
- Handlers call the agent's code (`from agent import SchedulingAgent` in every
  shipped template handler), so the world cannot exist without that agent. The
  shipped healthcare release registers no handlers at all: its 13 tools are
  runtime-owned, so none of its scenario steps was executed when it was proved.
- A platform environment is an authoring `HostedHarnessJob` bound to one agent
  through `run_test.agent_definition` / `agent_version` and `payload.agent`. A
  run copies that job (`create_selected_harness_run`, `services/hosted_harness.py:232`).
- A run does not record its simulator: the simulator model is read from process
  environment at dispatch (`hosted_harness_gateway.py:255-256`), and the
  sandbox image digest is only on the attempt.

Consequence: two runs cannot prove they used the same environment, suite and
simulator, and a second agent cannot run without re-authoring.

## 2. Objects

```text
EnvironmentRelease E   world, tools, handlers, policy       (no agent, no scenarios)
  └─ extension E+      E plus tools/data E lacks, from the customer's implementation
SuiteRelease S         scenarios proved against exactly one environment digest
AgentVersion A         existing platform model; identified by its configuration digest
AgentBinding B         how A meets E (mode, tool routing, rendering)
RunManifest R          E (+E+) + S + A + B + simulator + scorer + runtime, frozen per run
Composition            mutable named pointer to (E, S, A, B); the UI's "adapted environment"
```

Every released object is immutable and addressed by `sha256:` digest. A
composition is the only mutable object; each run snapshots it into a manifest.

### 2.1 EnvironmentRelease — `futureagi.environment-release.v1`

Layout (every file except `release.json` is listed in `files[]`; symlinks,
`.DS_Store`, `__pycache__/` and `*.pyc` are refused at sealing):

```text
release.json             manifest
tools.json               canonical tool definitions
policy.json              rules and parameters
world/                   a world snapshot (fi.alk.harness.world.snapshot layout)
  manifest.json            tool_implementation "environment", runtime_tools []
  world.sqlite             schema, invariants and seed
  handlers/<tool>.py       def handle(args, db), one per tool
  world.py
```

Reusing the snapshot layout means `restore()` and `prove()` work on a release
unchanged. How the hosted runtime serves this world is pinned with the tool
server (§8).

`release.json`:

| Field | Rule |
|---|---|
| `schema_version` | `futureagi.environment-release.v1` |
| `digest` | §3 |
| `name` | slug, e.g. `appointment_booking` |
| `extends` | `null`, or `{"digest", "name"}` of the parent release (§2.2) |
| `handlers` | `{tool: {"mode"}}`; `mode` is `environment`, `customer_source` or `customer_spec` |
| `source` | required iff a handler is `customer_source`: `{"digest"}` of the customer source tree it imports |
| `files` | bundle v2 `files[]` records |
| `provenance` | `{generator, generator_version, derived_from}` |
| `metadata` | free JSON |

`tools.json` is a list of `{name, description, parameters, mutates_state, returns}`:
`parameters` is a JSON Schema object, `returns` a JSON Schema or `null`.

`policy.json` is `{"rules": [...]}`, each rule `{id, text, class, parameters}`:

| `class` | Meaning | Who may change it |
|---|---|---|
| `system_invariant` | Data integrity the handlers enforce (no double-booked slot, no refund above the charge) | Nobody; a change is a new environment release |
| `configurable_environment_policy` | Business configuration the handlers enforce (whether identity checks apply, cancellation notice) | The suite's policy profile (§2.3), within `parameters[*].allowed` |
| `agent_policy` | A rule the agent is responsible for (confirm before cancelling) | The suite's policy profile decides whether it is scored |

Only `configurable_environment_policy` rules take `parameters`:
`{name: {"type", "default", "allowed"}}`, `type` one of `string`, `integer`,
`number`, `boolean`; `allowed` is optional. Parameter names are unique across
rules.

Configurable parameters live in the world store, in the reserved table
`environment_policy(name TEXT PRIMARY KEY, value TEXT)` holding JSON values, so
handlers read them through the same `db` as everything else. The release seals
the defaults there; the sealer refuses a release whose rows differ from the
`policy.json` defaults.

Sealing refuses:

- a tool without a handler, a handler without a tool, or world handlers that
  differ from `tools.json`;
- a world whose `tool_implementation` is not `environment`, or that has
  runtime-owned tools (their steps cannot be executed, so nothing can be proved);
- an `environment` handler that imports anything outside the standard library
  and `fi.alk.harness.world`, uses relative or dynamic import, or a world that
  binds source or keeps `state.json` without a `customer_source` handler.

Authoring rule (not mechanically checkable): no agent-internal tools
(handoffs, transfer-to-phase, end-call) in an environment.

### 2.2 Extensions (mixed environments)

An extension is a complete `EnvironmentRelease` whose `extends` names a
parent. It adds the tools the prebuilt environment lacks, implemented the same
way as a fully custom environment: bound to the customer's own code when the
source is available (`customer_source`), otherwise written by the harness from
the tool's specification (`customer_spec`).

The sealer verifies the parent, then refuses an extension that:

- removes or redefines a parent tool, or changes a parent handler's bytes;
- removes or changes a parent rule (so parent parameters and their defaults
  are unchanged);
- alters a parent table's schema or indexes, or adds, removes or changes rows
  in a parent table;
- extends an extension (one level in v1).

It may add tools, handlers, tables with their own rows, and rules. A run
carries the chain base-first.

### 2.3 SuiteRelease — `futureagi.suite-release.v1`

Layout:

```text
suite.json
scenarios/<name>/   scenario.json, setup.py, ready.py, checks/*.py (bundle-producer §2.13)
sub_goals.json
simulator_prompt.md
proofs.json         written by the sealer
provision.json      written by the sealer: the platform's scenario records
```

`suite.json`:

| Field | Rule |
|---|---|
| `schema_version` | `futureagi.suite-release.v1` |
| `digest` | §3 |
| `name` | slug |
| `environment` | `{"digest"}`: the exact release (or extension) every scenario was proved against |
| `parent` | `null`, or `{"digest"}` of the suite this one was derived from |
| `policy_profile` | `{"parameters": {...}, "scored_agent_rules": [rule ids]}` |
| `scenarios` | `[{key, path, origin, derived_from}]`, `path` is `scenarios/<name>` |
| `files`, `provenance`, `metadata` | as §2.1 |

`origin`:

| Value | `derived_from` | Files |
|---|---|---|
| `base` | `null` in a root suite; the parent key in a derived suite | identical to the parent scenario |
| `specialized` | the parent key | changed from the parent scenario |
| `custom` | `null` | new |

Sealing:

1. Verifies the environment release and checks the profile: parameters must
   exist and satisfy their type and `allowed`; `scored_agent_rules` must be
   `agent_policy` rule ids.
2. Checks every folder is listed, each `scenario.json`'s `scenario_key` equals
   its `key`, and each origin matches its files against the parent suite.
3. Copies the environment's world, writes the profile into
   `environment_policy`, and runs `prove()` on every scenario: ready, solvable,
   not vacuous, and no step assumed rather than executed. Any failure refuses
   the whole suite with each scenario's reason.
4. With a PostgreSQL DSN (a role that may create databases), proves every
   scenario again on PostgreSQL through the tool server's own execution path
   (§6.1). Each scenario runs on a fresh database cloned from the seeded
   template and is graded through the hosted world handle. It must hold with
   its reference solution, fail when nothing is done, and hit no `fault`.
   This is the engine and code a hosted run uses, so handler SQL that only
   SQLite accepts is refused here rather than in a run.
5. Writes `proofs.json` (environment digest, profile, per-scenario gates, and
   `runtime: {engine, server_version, scenarios}` or `null`), `provision.json`
   (`{"personas": [...]}`, each scenario's persona record exactly as the
   guest's provision request describes it, plus its `scenario_key`) and
   `suite.json`.

A suite runs only on its exact environment digest. A new environment release
needs a re-proof and a new suite release; scenario files may be identical.
Hosted runs require `proofs.json.runtime.engine == "postgres"`.

### 2.4 AgentBinding — `futureagi.agent-binding.v1`

A single `binding.json`. Every field is always present (defaults included), so
producers and the guest compute the same digest (§3).

| Field | Rule |
|---|---|
| `schema_version`, `digest` | §3 |
| `agent` | `{agent_definition_id, agent_version_id, configuration_digest, connector}`; `configuration_digest` is the canonical digest of `AgentVersion.configuration_snapshot`; `connector` is a resolved `HarnessAgentSerializer.connector` value (`livekit`, `vapi`, `retell`, `retell_chat`, `phone`) |
| `environment` | `{"digest"}`: the release (or extension) the binding targets |
| `mode` | `canonical`, `tool_routed` or `conversation_only` |
| `agent_tools` | `[]` unless `tool_routed`: the agent's admitted tools (§6.3), `[{name, description, parameters, admission}]`, `parameters` a JSON Schema object, `admission` `routed` or `replaced` |
| `tools` | `[]` unless `tool_routed`: one `ToolBinding` (§2.4.1) per `agent_tools` entry |
| `fixtures` | `{name: JSON}`; `{}` unless `tool_routed` |
| `aliases` | `{table: {agent value: environment value}}`, string keys and values; `{}` unless `tool_routed` |
| `rendering` | `{slot: value}` presentation values (brand, greeting, ID formats) that change no expected outcome |
| `prompt_changes` | must be `[]` in v1 |

Modes:

- `canonical`: the agent calls the environment's tools by their own names and
  schemas. Sealing refuses an agent tool the environment does not have.
- `tool_routed`: the agent keeps its own tools and `tools[]` maps each one onto
  environment operations (§2.4.1). A binding is used for runs only with a
  passing proof (§2.4.3).
- `conversation_only`: the agent reaches no environment tool; no world-state
  claims are made for the run.

#### 2.4.1 Mapping language

Declarative only (plan D3); arbitrary code goes through extensions (§2.2).
Implemented once, in `fi.alk.harness.bindings`, and used by both the tool
server and the proof.

```json
{"agent_tool": "book_visit",
 "steps": [
   {"id": "slot", "tool": "find_available_slots",
    "arguments": {"provider_id": {"arg": "doctor", "alias": "providers"},
                  "date": {"arg": "day", "convert": "date"}}},
   {"id": "booking", "tool": "book_appointment",
    "arguments": {"patient_id": {"arg": "patient.id"},
                  "slot_id": {"step": "slot", "path": "slots[0].slot_id"},
                  "reason": {"arg": "reason"}}}],
 "response": {"template": "Booked {{ steps.booking.appointment_id }}"}}
```

A `ToolBinding` is either mapped (`steps`, optional `response`) or
`{"agent_tool", "unsupported": "<reason>"}`. A step is `{id, tool, arguments}`:
`id` a slug unique in the binding entry, `tool` an environment tool,
`arguments` an object from environment argument names to values.

A value is exactly one of:

| Form | Meaning |
|---|---|
| `{"arg": "<JMESPath>"}` | extraction from the agent's arguments; a plain name is a rename |
| `{"step": "<id>", "path": "<JMESPath>"}` | a value from an earlier step's result in the same entry; `path` optional |
| `{"fixture": "<name>"}` | a value from `fixtures` |
| `{"const": JSON}` | a constant; allowed only for an argument the environment tool does not require |
| `{"concat": [value, ...], "separator": "<string>"}` | string join |
| `{"object": {key: value}}`, `{"list": [value, ...]}` | structured arguments |

Any value may add modifiers, applied in this order: `"convert": "<name>"`,
then `"map": {"<from>": JSON}` (keys compared as strings; an unmapped value is
`invalid`), then `"alias": "<table>"` (agent value to environment value; an
unknown value is `invalid`). Conversions are allowlisted: `string`, `integer`,
`number`, `boolean`, `lower`, `upper`, `trim`, `digits`, `date` (to
`YYYY-MM-DD` from ISO 8601, `MM/DD/YYYY`, `Month D, YYYY`, `D Month YYYY`),
`time` (to `HH:MM` from `HH:MM`, `H:MM AM/PM`, `Hpm`) and `datetime` (to
`YYYY-MM-DDTHH:MM`). A failed conversion is `invalid`. A missing or null value
for a required environment argument is `invalid`; for an optional one the
argument is omitted.

`response` is `{"template": "<string>"}`, rendered by Jinja2's
`SandboxedEnvironment` with `args`, `steps` (id to result) and `fixtures`, plus
the filters `json` and `alias_back(table)`; or `{"value": value}`, sent as JSON.
Absent, the response is the last step's result. A refusal answers with the
handler's reason.

**Execution.** One agent call runs its steps in order inside one database
transaction. The first refusal stops the call and rolls everything back
(`refused`); an exception in a handler or in the binding itself rolls back
(`fault`). Statuses are `ok`, `refused`, `invalid` (unknown agent tool,
non-object arguments, a value that cannot be produced), `unsupported` (an
`unsupported` entry) and `fault`. A `canonical` binding runs one step: the
environment tool of the same name with the arguments unchanged.

```python
run_bound_call(binding: AgentBindingV1, agent_tool: str, arguments: Any, *,
               run_operation: Callable[[str, dict], Any],
               environment_tools: Sequence[ToolDefinition]) -> BoundResult
# run_operation(tool, arguments) runs one handler inside the caller's
# transaction; it raises ToolError for a refusal. The caller owns the
# transaction and commits only when status == "ok".
BoundResult(status, content: str, result: Any, error: str,
            operations: list[OperationRecord])   # content: the rendered response
OperationRecord(step_id, tool, arguments, result, status, error)
```

#### 2.4.2 Sealing a `tool_routed` binding

`seal_agent_binding` refuses (`binding_invalid`, every reason listed) a binding
whose:
- `tools[].agent_tool` values are not exactly the `agent_tools` names, each once;
- step `tool` is not in the environment, or an argument is not in that tool's
  parameters;
- required environment argument (the tool's `parameters.required`) is missing,
  or its value contains a `const` or no `arg`, `step` or `fixture` leaf (the
  adapter must not help the agent, D3);
- `arg` path does not compile, or its first field is not a parameter of the
  agent tool;
- `step` reference is not an earlier step of the same entry;
- `fixture`, `alias` table or conversion is unknown, or a template does not
  compile in the sandbox.

#### 2.4.3 Binding proof — `futureagi.binding-proof.v1`

`prove_binding(environment_root, suite_root, binding, trajectories, *,
postgres_dsn)` proves a `tool_routed` binding against a suite on PostgreSQL,
through the tool server's own execution path. `trajectories` maps each
`scenario_key` to the reference solution in the agent's vocabulary,
`[{"tool": agent_tool, "arguments": {...}}]` (the harness writes it).

For every suite scenario, on a fresh world with the suite's profile and the
scenario's setup applied:
- `unscorable`: the scenario's reference solution uses an environment tool that
  no mapped step reaches; it needs no trajectory;
- otherwise the trajectory must hold the scenario's checks (`proved`), and every
  adversarial variant must fail: `nothing` (no calls), `skip:<i>` (call `i`
  removed) and `perturb:<i>` (every string leaf of call `i`'s arguments replaced
  by `zz-perturbed-<i>`, numbers increased by 7, booleans negated; keys, integer
  types and nulls kept), for every call `i` (1-based) whose entry reaches a tool
  with `mutates_state` and that was `ok` in the trajectory. A refused call
  changes nothing, so removing or garbling it proves nothing about the binding.
  The check is differential: a variant that holds is replayed canonically (the
  trajectory's recorded environment operations, with that call's operations
  removed or their environment arguments perturbed the same way). If the
  canonical variant also holds, the call is not needed under this profile and
  the variant is `exempt`; if it fails, the binding made it pass, which is
  `held`. A scenario where any variant is `held` is `failed` with the variants
  named; one with no trajectory is `failed`; trajectory keys not in the suite
  are refused (`binding_proof_invalid`).

```json
{"schema_version": "futureagi.binding-proof.v1",
 "binding": {"digest"}, "suite": {"digest"}, "environment": {"digest"},
 "scenarios": {"<key>": {"status": "proved" | "failed" | "unscorable",
                         "reason": "...",
                         "adversarial": {"nothing": "failed", "skip:1": "exempt", "perturb:2": "held"}}},
 "passed": true}
```

`passed` is true when no scenario is `failed` and no adversarial variant is `held`.
Runs use only `proved` scenarios of a `tool_routed` composition, and a binding
with no `proved` scenario is never composed (§7.5).

CLI: `python -m fi.alk.harness.binding_proof --environment DIR --suite DIR
--binding binding.json --trajectories FILE --postgres-dsn DSN --out FILE`; the
binding must be sealed; exit `0` passed, `3` not passed, `1` invalid input.

### 2.5 RunManifest — `futureagi.run-manifest.v1`

Frozen by the platform at run submission, before dispatch:

| Field | Content |
|---|---|
| `environment` | `[{digest, name}]`, base first, then the extension |
| `suite` | `{digest}` |
| `binding` | `{digest}` |
| `agent` | as in the binding |
| `selection` | `{scenario_keys, trials}`; keys unique, trials 1–20 |
| `simulator` | `{llm: {provider, model, location, thinking}, voice: {stt, tts, voice}, persona: {prompt_digest, temperature, interrupt_sensitivity, conversation_speed, finished_speaking_sensitivity, max_call_duration_in_minutes, initial_message_delay}}` |
| `scorer` | `{alk_version, evals: [{name, version}]}` |
| `runtime` | `{provider, image_name, image_digest}` |
| `digest` | §3 |

Before running, the guest calls `check_run_inputs`, which refuses delivered
releases that do not match the manifest:
- an environment chain that differs, or an extension without its base;
- a suite or binding proved against, or targeting, another environment;
- a binding agent that differs from the manifest's;
- selected keys missing from the suite.

## 3. Digests

One construction for every object, identical to bundle v2 §2d: sha256 over the
canonical JSON of the normalized manifest without `digest` and `files`, then,
for each `files[]` record in listed order, its canonical JSON prefixed by its
byte length as 8 bytes big-endian. Canonical JSON is
`json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)`
encoded UTF-8. Building the same release twice gives the same digest.

- **Releases** (environment and suite) are sealed only by
  `fi.alk.harness.releases`.
- **Bindings and run manifests** have no `files` and are produced by the
  platform with the same construction (`canonical_digest`,
  `services/hosted_harness.py`). The document must be normalized: every field
  in §2.4/§2.5 present, defaults included (`null`, `[]`, `{}`), because the
  guest recomputes the digest over its normalized model and refuses a
  mismatch.

## 4. Versioning

| Change | Object that changes |
|---|---|
| Tool name, argument mapping or response rendering | New `AgentBinding` |
| Brand, greeting or ID format with unchanged semantics | New `AgentBinding` (`rendering`) |
| Customer-specific persona or facts | New `SuiteRelease` |
| Add or remove scenarios | New `SuiteRelease` |
| Policy parameter or scored agent rule | New `SuiteRelease` (`policy_profile`) |
| Environment tools, data model or invariants | New `EnvironmentRelease`, then suite re-proof |
| Tool the environment lacks | New extension |

A new agent version reuses the suite. Only its tool schemas and stated policy
are compared with the previous version, with no scenario generation:

- unchanged: reuse the binding;
- tool schemas changed: new binding, re-prove only the affected scenarios;
- stated policy or capability changed: the user chooses a new suite, or keeps
  the current one as the comparison baseline.

Agent performance never triggers re-authoring.

## 5. Comparability

Two runs are a direct comparison when their manifests have equal `environment`,
`suite`, `selection.scenario_keys`, `simulator`, `scorer` and `runtime`.
`agent`, `binding` and `trials` may differ. `releases.compare` (CLI:
`python -m fi.alk.harness.releases compare A B`) returns the differing fields;
the report shows a comparison only when that list is empty and names the
fields otherwise.

## 6. Run assembly

The run sandbox receives the releases, the binding and the manifest instead
of an authoring archive:

```text
/work/releases/environment/<64 hex>/   one per chain entry (digest without "sha256:")
/work/releases/suite/
/work/binding.json
/work/run-manifest.json
```

`python -m fi.alk.harness.releases compose --releases /work/releases --binding
/work/binding.json --manifest /work/run-manifest.json --job /work/job.json
--output /work/bundle` replaces the simulation-only
`cp -a /work/authoring/environment-bundle /work/bundle`
(`hosted_harness_gateway.py:2182-2187`) for release-based runs; existing
environments keep their path. It refuses, before writing anything:
- any verification failure from §2.5 (`check_run_inputs`);
- a suite without a PostgreSQL proof (`suite_runtime_unproved`);
- a job connector that differs from the binding's;
- a combination it does not compose (`compose_unsupported`). It composes
  `canonical` and `tool_routed` bindings on `retell_chat`, `retell` and `vapi`.

The bundle it writes is an ordinary sealed `environment-bundle.v2`, so
everything from bundle preflight on is unchanged. Composition is
deterministic: the same inputs give the same bundle digest.

| Part | Content |
|---|---|
| `world-db` | managed PostgreSQL 16, store `world_db`, `template_database` baseline, seeded by `seed/world.sql`: the shared prefix (readiness sentinel, `_alk_tool_trace`), the request-binding tables (§6.2), the release world converted by the agent-first converter, then the suite profile written into `environment_policy` |
| `tool-server` | source process from `generated/tool-server` (§6.1) with the release's `tools.json`, `handlers/` and the run's `binding.json`, user `svc-tools`, one per world, environment `REQUEST_BINDING=1` |
| capabilities | `world_db` (postgres), `tools` (http, `tool-server`, health `/health`) |
| runtime | `kind: process`, `evidence_seam: tool_trace` |
| `metadata.provider_import` | `type` `retell` (connectors `retell`, `retell_chat`) or `vapi`; `source_target_id` from `job.agent.config` `agent_id` (Retell) or `assistant_id` (Vapi); public capability `tools`; `environment_tools` = the release's tool names (`canonical`) or the binding's `agent_tools` names (`tool_routed`); `target_modality` `chat` for `retell_chat`, else `voice`; `admission: "per_tool"`; `request_binding: true` |
| `metadata.release` | the manifest, environment, suite and binding digests, and the binding mode |
| files | the suite's `scenarios/`, `sub_goals.json`, `simulator_prompt.md`; the release's `handlers/`; a generated `contract.json` (agent name, `modality` `chat` or `voice`, the tools the agent calls: the release's for `canonical`, `agent_tools` for `tool_routed`) |

The guest then clones the customer's agent once per world (`provider_import`)
with per-tool admission (§6.3), pointing every admitted tool at that world's
`tools` capability. The existing Retell chat runner or hosted voice runner
drives each scenario, and checks grade the world through the hosted world
handle.

### 6.1 Tool server

`fi/alk/harness/release_tool_server.py` is copied into the bundle and runs on
the ALK interpreter, so it may import `fi.alk.harness.bindings` and
`fi.alk.harness.request_binding`. It runs the release's handlers against the
world's PostgreSQL. Handlers may use `?` placeholders, which it translates to
psycopg's. Their SQL must be valid on both SQLite and PostgreSQL (for example
`ON CONFLICT ... DO NOTHING`, never `INSERT OR IGNORE`); the runtime proof in
§2.3 enforces this. Every agent call goes through `run_bound_call` (§2.4.1)
with the run's binding, in one transaction.

| Route | Request | Response |
|---|---|---|
| `POST /provider/tools/...` (Vapi) | `{"message": {"type": "tool-calls", "toolCallList": [{id, function: {name, arguments}}], "call": {...}}}`; `arguments` an object or a JSON string; calls run in list order | always 200 `{"results": [{"toolCallId", "result"}` or `{"toolCallId", "error"}]}`, each a single-line string |
| `POST /provider/tools/...` (Retell) | `{name, call, args}`, or `{name, chat, args}` for chat agents | 200 with the rendered content, or `{"error": reason}` for any non-`ok` status; content over 15,000 characters is replaced by an error |
| `POST /provider/tools/<tool>` (args only) | the bare arguments; accepted only when `REQUEST_BINDING` is off | as Retell |
| `POST /tools/<name>` | `{id, arguments}`; accepted only when `REQUEST_BINDING` is off | `{id, status, content, result}`; 500 for `fault` |
| `POST /provider/events/...` | provider events, including Vapi messages other than `tool-calls` | 200 `{}` |
| `GET /health` | | `{ok, tools, agent_tools}` |

Every executed step is one `_alk_tool_trace` row, written after the call's
transaction ends: `name` and `arguments` are the environment tool's, plus
`agent_tool`, `provider_call_id`, `tool_call_id` and `status`; when the call
is not `ok`, every step row has `ok = false` and the call's status and error. A
call that fails before any step (`invalid`, `unsupported`) is one row named by
the agent tool.

### 6.2 Request binding

With `REQUEST_BINDING=1` every tool request is bound to the scenario currently
running on that world (plan D6). The release seed adds:

```sql
CREATE TABLE _alk_active_call (slot smallint PRIMARY KEY CHECK (slot = 1),
  token text NOT NULL, secret_sha256 text NOT NULL, scenario_key text NOT NULL,
  registered_at double precision NOT NULL);
CREATE TABLE _alk_tool_responses (provider_call_id text NOT NULL,
  tool_call_id text NOT NULL, response jsonb NOT NULL, at double precision NOT NULL,
  PRIMARY KEY (provider_call_id, tool_call_id));
CREATE TABLE _alk_stray_calls (id bigserial PRIMARY KEY, provider_call_id text,
  token text, tool text, arguments jsonb, reason text NOT NULL,
  at double precision NOT NULL);
ALTER TABLE _alk_tool_trace ADD COLUMN agent_tool text,
  ADD COLUMN provider_call_id text, ADD COLUMN tool_call_id text,
  ADD COLUMN status text;
```

World reset restores the template database, so all three tables start empty
for every scenario.

- **Secret.** The runtime generates one secret per world when it clones the
  agent (`runtime.metadata["provider_tool_secret"]`). The clone sends it on
  every admitted tool as the header `x-alk-tool-secret` (Vapi `server.headers`,
  Retell custom-tool `headers`), replacing the customer's headers.
- **Token.** Before each call or chat is created, the runner writes a fresh
  random token, the secret's SHA-256 and the scenario key into
  `_alk_active_call` (`request_binding.register_active_call`), and attaches the
  token at creation as `alk_binding` in the call's metadata and per-call
  variables: Retell `metadata` and `retell_llm_dynamic_variables` (create-web-call,
  create-chat); Vapi `assistantOverrides.metadata` and
  `assistantOverrides.variableValues` (`POST /call` has no top-level `metadata`).
  The tool server reads it from the
  first present of: header `x-alk-binding`; `call.metadata`, `chat.metadata`
  or `message.call.metadata` `.alk_binding`;
  `call.retell_llm_dynamic_variables.alk_binding`;
  `message.call.assistantOverrides.variableValues.alk_binding`.
- **Checks.** A request is executed only when an active call exists, the
  header's SHA-256 equals `secret_sha256`, and the token equals the active
  token. Otherwise it is recorded in `_alk_stray_calls` with the reason
  `no_active_call`, `bad_secret` or `stray`, never executed, and answered with
  an error (401 for `bad_secret`; the provider's error shape otherwise).
- **Idempotency.** When both the provider call ID (Vapi `message.call.id`,
  Retell `call.call_id` or `chat.chat_id`) and a tool call ID (Vapi
  `toolCallList[].id`) are present, the answer is stored in
  `_alk_tool_responses`; a repeated pair returns the stored answer without
  executing or recording again.
- After each call the runner reads `_alk_stray_calls` and records `stray_calls`
  (count, and each row's reason, tool, provider call ID and time) in the call's
  tool-trace artifact (`ArtifactKind.TOOL_TRACE`); receipts are unchanged.

### 6.3 Per-tool admission

`admit_provider_tools(provider, target) -> list[ToolAdmission]`
(`fi.alk.harness.provider_import`) is a pure function over the target that
`inspect_provider_target` returns. Adaptation shows its result and the clone
applies it, so both agree. `ToolAdmission` is `{name, provider_type, action,
reason}`; `name` is the name the agent calls in the clone.

| Action | Vapi | Retell (`general_tools`, `states[].tools`, flow `tools`) | In the clone |
|---|---|---|---|
| `routed` | `function` | `custom` | URL moved to the world's tool server, secret header set, Retell `args_at_root` off |
| `replaced` | `apiRequest`, `transferCall` (as `transfer_call`), `sms` (as `send_sms`), integrations that carry a `function` definition | `transfer_call`, `send_sms`, `check_availability_cal`, `book_appointment_cal` | a custom function with the same name, description and parameters (the tool's own schema, else the provider's documented one), routed as above |
| `kept` | `endCall`, `dtmf`, `voicemail`, `query` | `end_call`, `press_digit`, `extract_dynamic_variable` | unchanged: no effect outside the call |
| `disabled` | everything else (`handoff`, `mcp`, `code`, `bash`, `computer`, `textEditor`, unknown types) | everything else (`agent_swap`, `code`, `mcp`, unknown types) | removed |

A conversation-flow node that acts outside the call and cannot be rewired (a
`transfer_call`, `sms`, `code`, `agent_swap`, `bridge_transfer` or `mcp` node,
or a function node calling a disabled tool) makes both `admit_provider_tools`
and the clone fail with `provider_tool_uncontrolled`, naming every such node;
the clone creates nothing. Routed and replaced tools point at
`<tools capability>/provider/tools/<agent tool name>`. Providers document no
model-facing schema for most built-ins, so a replaced tool without its own
schema gets: transfer `{destination: string}` (enum of the configured
destinations), Retell `send_sms` `{content: string}`, otherwise an empty object
schema. `admitted_tool_definitions(provider, target)` returns the routed and
replaced tools as the clone exposes them (`[{name, description, parameters,
admission}]`, the binding's `agent_tools`). Bundles without
`admission: "per_tool"` keep today's behaviour, and `request_binding` requires
`per_tool` and a PostgreSQL capability (preflight). The provision receipt's
metadata carries the admission list; the world secret never appears in a
receipt.

## 7. Platform

### 7.1 Storage

Insert-only, content-addressed rows (`simulate/models/harness_release.py`):

| Table | Unique | Holds |
|---|---|---|
| `HarnessEnvironmentRelease` | `(organization, digest)`; `digest` among system releases | name, parent, archive object key, `release.json`, `tools.json`, `policy.json` |
| `HarnessSuiteRelease` | `(organization, digest)`; `digest` among system releases | name, environment release, parent suite, archive object key, `suite.json`, the personas from `provision.json` |
| `HarnessAgentBinding` | `(organization, digest)` | agent version, environment release, mode, `binding.json` |
| `HarnessRunManifest` | one per run job | digest, the frozen manifest |

A release with no organization is a system release any organization may
compose. Archives live at `harness-releases/<organization|system>/<environment|suite>/<64 hex>.tar.gz`.

### 7.2 Registration

`python manage.py register_harness_release --environment DIR --suite DIR
[--organization ID]` (an operator command, like `seed_system_evals`) stores a
sealed environment and a suite proved against it. It refuses:
- a suite proved against another environment;
- a suite without a passing PostgreSQL proof;
- a `provision.json` that does not match the suite;
- an extension whose parent is not registered, or a derived suite whose parent
  suite the organization cannot see.

Re-registering identical releases returns the existing rows.

### 7.3 Compositions

A composition (the UI's "adapted environment") is a completed
`HostedHarnessJob`, so environments, runs and results stay one concept. Its
payload is an ordinary validated hosted job (`HarnessJobCreateSerializer`)
whose `metadata.release_composition` holds the environment chain and suite
(`digest`, `name`, `object_key`), the binding document and, when the binding
came from adaptation, `alignment`, `binding_proof` and the agent's proved
`trajectories` (§7.5, §7.8). Its `RunTest`
and scenario rows are created from the suite's personas with the existing
`provision_alk_sim_run_test` (modality `text` for `retell_chat`, `voice` for
`retell` and `vapi`); the `RunTest` carries the bound agent version. The rows
are indexed from the suite's `scenario.json` documents (number, name,
instruction, persona, sub-goals), so chat and the Scenarios tab address them by
number. `create_composition` (`services/harness_releases.py`) builds it for
`retell_chat`, `retell` and `vapi` agents in `provider_import` mode, from a
given binding document (adaptation) or a `canonical` one it produces.

### 7.4 Runs

Runs use the existing `POST harness-environments/{id}/run/`. For a
composition, `create_selected_harness_run` freezes the run manifest (§2.5) into
the run job's `metadata.run_manifest` and a `HarnessRunManifest` row. The
frozen values come from:

| Field | Source |
|---|---|
| simulator | the deployment settings the gateway would use, and the `RunTest`'s simulator agent; `voice` for `retell` and `vapi` is `{stt: "provider/model", tts: "provider/model", voice}` (a half may be empty; `null` lets ALK choose), `null` for `retell_chat`. The gateway applies the frozen values and removes any setting frozen as unset |
| runtime | the sandbox provider's image, after the resource profile's override |
| scorer | that image, and the run test's eval configs |

At launch the gateway, for a job with `release_composition`:

- refuses a job without `run_manifest` (`run_manifest_missing`), and a launch
  whose selected image differs from the manifest's
  (`run_manifest_runtime_changed`);
- applies the manifest's simulator settings instead of the current ones;
- uploads the archives, `binding.json` and `run-manifest.json`, extracts them
  into `/work/releases` (§6), and runs `compose` and then the entrypoint, with
  no authoring step.

### 7.5 Adaptation

Adaptation binds a customer's agent to a release suite (plan §6). It never
changes the suite: a scenario the binding cannot reach is reported
`unscorable`, and a suite change is a separate scenario edit. It is a hosted
job whose payload is an ordinary validated hosted job for the agent, with
`metadata.adaptation = {environment: [{digest, name, object_key}], suite:
{digest, name, object_key}, agent_version_id}`.

**Launch.** The gateway delivers the releases as for a run (§6 layout, no
binding or manifest) and runs the control process:

```text
python -m fi.alk.harness.adaptation hosted --job /work/job.json \
  --releases /work/releases --output /work/authoring/adaptation
```

It reads the target-provider key from `/run/futureagi/secrets.json` (deleting
the file), inspects the agent (`inspect_provider_target`, then
`admit_provider_tools`, §6.3), starts a scratch PostgreSQL 16 cluster from the
snapshot's binaries, writes `profile.json` and then `inputs.json`, and waits for
`/work/authoring/.futureagi-build-finished.json`. The gateway then puts "Bind
this agent to the environment…" into the job's conversation, which starts the
environment's builder (`hosted_chat_entrypoint`, control-only) beside it, as for
a build. The builder opens the `bind-agent` skill with the adaptation tools; the
provider key never reaches it. Exit codes: `0` completed, `2` failed with
`result.json` written; both are settled from the output. Any other exit takes
the existing crash and infrastructure-retry path. `metadata.adaptation` also
carries the agent reference (§2.4). Adaptation jobs are followed as jobs
(`harness-jobs/{id}/`) and never listed as environments; a message to a
finished one is refused (409 `conversation_closed`).

**Mode.** `canonical` when every admitted tool is an environment tool with the
same name; `conversation_only` when none is admitted; otherwise `tool_routed`,
and the builder writes the binding.

**Tools** (the builder drafts; code decides):

| Tool | What code does |
|---|---|
| `world_rows` | at most 200 rows of one world table |
| `submit_binding` | `{binding, trajectories, extension_candidates}`; refused unless it seals (§2.4.2), every trajectory key is a suite scenario and every call names an admitted agent tool |
| `run_proof` | `prove_binding` (§2.4.3) on the base suite; per scenario `proved`, `failed` or `unscorable` with the reason |
| `report_conflicts` | `[{rule, agent}]`: every policy rule the agent's own instructions contradict or ignore; `rule` must be a policy rule id; an empty list when there are none |
| `finish_adaptation` | refuses until, for `tool_routed`, the current binding passed `run_proof` (no `failed`, at least one `proved`) and conflicts were reported; then code asks the person to approve (`yes`/`no`) and seals |
| `stop_adaptation` | ends the job failed: `adaptation_declined` after a declined approval, else `binding_unproved` |

The builder retries within its turn budget; there is no fixed round limit. The
approval is a builder question (`question_requested`, `{prompt, options}`,
answered by a `user_response` replying to it).

**Outputs** in `/work/authoring/adaptation/`:

| File | Content |
|---|---|
| `result.json` | `{schema_version: "futureagi.adaptation-result.v1", status: "completed" or "failed", failure: {code, message} or null, environment: [{digest, name}], suite: {digest, name, derived: false}, binding: {digest, mode}, alignment}` |
| `profile.json`, `inputs.json` | the inspected agent profile (secrets redacted) and its admission list; the release paths and scratch DSN the builder works with |
| `binding.json`, `binding-proof.json` | the sealed binding; the proof for `tool_routed` |
| `trajectories.json` | `tool_routed`: `{scenario_key: [{tool, arguments}]}`, the agent's own calls for every `proved` scenario, kept so a suite edit can re-prove the binding (§7.8) |
| `state.json` | the builder's latest binding, proof and conflicts, so a reopened session carries on |

`alignment` is `{mode, admission: [ToolAdmission], mapped: [{agent_tool,
steps: [environment tool]}], unsupported: [{agent_tool, reason}],
extension_candidates: [{agent_tool, reason}], unscorable: [{scenario_key,
reason}], conflicts: [{rule, policy, agent}]}`. Extension candidates are agent
tools no environment tool can serve; building them is step 5.

**Completion.** When the job's process exits, the gateway packs
`/work/authoring/adaptation` and `complete_adaptation`
(`services/harness_releases.py`): for `completed`, refuses a result whose suite
is not the base suite (`adaptation_result_invalid`) and a `tool_routed` result
(`binding_unproved`) unless `binding-proof.json` has `passed` and its binding,
suite and environment digests equal the binding's, the base suite's and the
binding's environment; then stores the binding (`HarnessAgentBinding`) and
creates the composition on the base suite with `trajectories.json`, records
`metadata.adaptation_result = {composition_id, suite_digest, binding_digest,
mode}` and completes the job; for `failed`, fails the job with
`result.failure` (or `adaptation_result_missing` when `result.json` is absent or
unreadable). `conversation_only` results complete the job with
`composition_id: null` and `compose: {code: "compose_unsupported"}`.

### 7.6 API

Under `/simulate/api/`, organization-scoped like the other harness routes.

| Route | Request | Response |
|---|---|---|
| `GET harness-releases/` | | `{results: [{id, digest, name, system, parent: {digest, name} or null, tools: [{name, description, mutates_state}], policy: {parameters: [{name, type, default, allowed}], agent_rules: [{id, text}]}, suites: [{id, digest, name, scenario_count, origins: {base, specialized, custom}, policy_profile}]}]}`; system releases and the organization's own |
| `POST harness-adaptations/` | `{suite_id, agent_version_id, agent, name, idempotency_key}` (the key in the body); `agent` as in a hosted job (`connector` `retell_chat`, `retell` or `vapi`, `mode: "provider_import"`, `config`, `secret_refs`); the agent definition's type must match the connector's modality (400 `agent_modality_mismatch`) | 202, the job (`HarnessJobReadSerializer`); the UI follows it like any harness job |
| `POST harness-compositions/` | as `harness-adaptations/` | 201 `{id}`: a `canonical` composition, without adaptation |
| `GET harness-environments/{id}/` | | adds `composition`: `null`, or `{environment: [{digest, name}], suite: {digest, name, parent_digest, policy_profile, scenarios: [{key, origin, derived_from}]}, binding: {digest, mode, agent_tools, tools}, agent: {agent_definition_id, agent_version_id, connector, name, version_name}, alignment, binding_proof: {passed, scenarios}}` |
| `GET harness-environments/{id}/runs/{run_id}/report/` | `?baseline=<run_id>`; a run is named by its test execution ID or its run job ID; the baseline may be a run of another composition in the organization | §7.7; 409 `not_a_composition` for other environments |

### 7.7 Run report

```json
{"run_mode": "tool_routed",
 "manifest": {"digest", "environment", "suite", "binding", "agent", "selection", "simulator", "scorer", "runtime"},
 "comparability": {"baseline_run_id", "comparable": false, "differing": ["simulator"]},
 "coverage": {"runnable": 7, "suite_scenarios": 7, "base_scenarios": 8,
              "unscorable": ["intake-forms-fallback-to-email"], "extension_tools": []},
 "use_cases": [{"use_case", "scenarios": 3, "trials": 3, "pass_1": 0.78, "pass_k": 0.67}],
 "scenarios": [{"scenario_key", "name", "use_case", "origin": "base",
                "trials": ["passed", "failed", "passed"], "pass_1": 0.67, "passed_all": false}],
 "environment_faults": 1}
```

- `run_mode` is the binding's mode; `origin` comes from the suite manifest.
- A trial is a scenario's receipt per trial index. `errored` trials are
  environment faults: counted in `environment_faults` and excluded from rates.
- `pass_1` is passed over passed plus failed trials. `pass_k` is the share of
  scenarios whose `k` = `selection.trials` trials all passed; a scenario with an
  environment fault is left out of `pass_k`.
- `comparability` is `null` without `baseline`. `differing` applies §5 to the
  two runs' manifests (`environment`, `suite`, `simulator`, `scorer`,
  `runtime`, `selection.scenario_keys`); `comparable` is true when it is empty.
- `coverage.base_scenarios` counts the root suite of the chain;
  `unscorable` comes from the composition's alignment; `runnable` is the suite's
  scenarios minus `unscorable`; `extension_tools` are the extension release's
  tools when the chain has one.
- A trial without a receipt is `null`: outside `pass_1`, and its scenario does
  not pass all trials. Rates are unrounded, `null` when nothing is counted.

### 7.8 Editing a composition's suite

A composition's chat runs the environment's builder on the composition. The
world, its tools, the policy and the binding are fixed. Only the suite changes,
and every saved change is a child `SuiteRelease` of the composition's suite
(§2.3), proved like any suite, with a `tool_routed` binding re-proved against
it. The composition then runs the child; its earlier runs keep the manifests
they froze (§7.4), and comparing across the change reports `suite` (§5).

**Workspace.** With no saved chat workspace, and on every chat restart (keeping
only the chat's own root dot files, as for authored environments), the gateway
restores `composition_workspace_archive`
(`services/harness_composition_edits.py`) into `/work/authoring`:

| Path | Content |
|---|---|
| `releases/environment/<64 hex>/`, `releases/suite/` | the composition's environment chain and current suite (§6 layout) |
| `binding.json`, `binding-proof.json` | the binding; its proof when `tool_routed` |
| `trajectories.json` | the composition's `trajectories` (§7.5) |
| `composition.json` | `{schema_version: "futureagi.composition-workspace.v1", composition_id, environment: [{digest, name}], suite: {digest, name}, binding: {digest, mode}}` |

The chat's revision (`authoring_revision`) is the composition's suite digest.

**Session.** ALK (`fi.alk.harness.composition_edit`) verifies the releases and,
the first time, lays out a working copy at the workspace root: the world with
the suite's policy profile applied, `contract.json` (the environment's tools in
the agent's modality), and the suite's `scenarios/`, `sub_goals.json` and
`simulator_prompt.md`. The builder opens on it with its scenario and suite
tools, without `change_world`, `declare_tool`, `amend_contract`, `add_rule`,
`drop_rule` and `fix_tool`. Scenarios are written in the environment's
vocabulary. A `tool_routed` composition adds `write_trajectory(scenario, calls)`
(agent tool names only): every reachable scenario added or rewritten needs the
agent's own trajectory.

**Saving.** `save_changes` (also run when a turn ends with unsaved changes):

1. Compares the working copy with the composition's suite by scenario document.
   Unchanged is `base` (the parent's folder copied byte for byte), rewritten is
   `specialized`, new is `custom`; `derived_from` is the scenario's own key when
   the parent has it. A missing scenario is dropped. When nothing changed
   (including `sub_goals.json` and `simulator_prompt.md`), nothing is sealed.
2. Seals the child (`parent` the composition's suite, same name and policy
   profile) and proves it on a scratch PostgreSQL.
3. For `tool_routed`, runs `prove_binding` (§2.4.3) against the child with the
   stored trajectories of its scenarios. A scenario no mapped step reaches is
   `unscorable` and needs none. Unless the proof passes with at least one
   `proved` scenario, the save is refused with `binding_unproved` and each failed
   scenario's reason (for example `no trajectory`).
4. Writes `composition-change/`: `suite/`, `binding-proof.json` (`tool_routed`),
   `trajectories.json` and `change.json` (`{schema_version:
   "futureagi.composition-change.v1", parent, suite, added, changed, removed,
   unscorable: {key: reason}}`), then checkpoints the workspace and publishes
   it. A refused save leaves nothing behind; a published one becomes the
   workspace's suite.

**Publishing.** Checkpoint promotion for a composition
(`promote_composition_checkpoint`) ignores an older checkpoint, refuses one
built on another suite (`conversation_checkpoint_stale`), and records one
without `composition-change/` as chat state only. Otherwise it checks the
change (`composition_change_invalid` unless `change.json` and the suite name
the current suite as parent; the §7.2 suite checks; the §7.3 binding check with
the new proof), registers the suite for the organization (`HarnessSuiteRelease`
with `parent` the current suite) and sets `metadata.release_composition`
`suite`, `binding_proof`, `alignment.unscorable` and `trajectories`, and
`authoring_revision` to the new suite digest. Scenario rows are re-indexed from
the suite: numbers stay, dropped rows are soft-deleted, new rows take the next
numbers and get dataset rows from the suite's `provision.json`. Publishing
answers `{applied, revision, scenario_count, added, removed, changed}` as for
authored environments.

## 8. Pinned later

| Item | Where | Step |
|---|---|---|
| Extension authoring from customer code or specification | this contract, `bundle-producer-contract.md` | 5 |
| Ordered timeline checks (plan D7) | this contract | 6 |
| Retell `X-Retell-Signature` verification, on top of the per-clone secret | this contract, §6.2 | 6 |
| Seeding system releases at deploy (shipping archives with the image) | rollout | 6 |
| Voice interaction profiles and code agents | this contract | 7 |

## 9. Open questions

1. Is one extension level enough for v1?
2. Should `runtime` differences block a comparison, or only be reported? This
   draft blocks, because the image carries the simulator code.
