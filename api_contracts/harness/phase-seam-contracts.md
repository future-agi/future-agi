# Harness rebuild — phase seam contracts

The input/output formats at every phase boundary, frozen before the Phase 2/3/4
handoffs so the pieces fit without negotiation. Phase 2 (builder, Khushal),
Phase 3 (world service, Jayasurya) and Phase 4 (hosted runs, Azain) implement
against THIS document; a change to any shape here is a cross-team conversation,
not a local edit.

Companion documents: `frontend-contract.md` (FE ↔ backend) and the Rebuild Plan
artifact (design rationale, decisions D1–D11). The "already implemented"
references below live on branch `feat/harness-phase1-foundations` (PR #2256)
and its amendment commits — read that branch, not main.

Revision note: v3 after two adversarial review rounds (44 + 29 findings folded
in). The Phase-1 code amendments this document assumes, all on the branch:
`world_checks`/`checks` are dict columns and `solution` is a list column (with
the create views' defaults matching); `max_turns` defaults 10; `GET
worlds/<id>/`, `GET world-copies/?call_execution_id=` and sqlite-refusal at
world save exist; `RLWorldCopy.state` exists and rides the call-log write; the
call-log endpoint is fenced once grading starts; the copy PATCH refuses status
writes on terminal rows with a 409.

---

## Seam A — what Phase 2 writes, Phase 3 executes

Phase 2's builder persists worlds and scenarios through the internal API
(`/simulate/api/rl-harness/`). Phase 3's world service reads them back and
must be able to run them without any file, folder, or catalogue — every
column below is self-contained.

### A1. `RLWorld` column payloads

| column | shape | notes |
|---|---|---|
| `status` | `building` \| `saved` \| `failed` | Materialize and prepare refuse any world not `saved` (422). |
| `store_kind` | `"postgres"` (default) or `"inprocess"` | Saving `"sqlite"` is refused with a 400 at the create endpoint — a sqlite world has no servable snapshot, so it must never exist as a row. Mapping note: the harness's own store key is `in_process`; Phase 2 maps it to the column value `inprocess` at save, and Phase 3 maps back (the harness's `open_store` alias table accepts both). |
| `schema_scripts` | `list[str]` — ordered SQL scripts | Exactly the `schema` list the store captured (`store.json["schema"]` today). Replayed in order into a fresh database before rows. `[]` for `inprocess`. |
| `snapshot` | `{"rows": {<table>: [<row dict>, …]}, "counters": {<sequence>: <int>}}` | The Held format, verbatim. `counters` are Postgres sequence last-values (`{}` for `inprocess`). Sequence names are restored schema-qualified and quoted (`public."<name>"`) — mixed-case names round-trip. |
| `state` | `{}` or `{"state_object": <any JSON>}` | The BUILD-TIME state object. `{}` = the world has none. `state_object` may be ANY JSON value (state objects are opaque, not always dicts). At prepare, this seeds the copy's own `state` (A4); at serve time the copy's is authoritative. Store collections win over state keys on collision (runtime rule, unchanged). |
| `handlers` | `{<tool_name>: <python source>}` | Each source defines `handle(args, db)`. `db` exposes `query / one / execute / collections / records / find / add` and `db.state`. Executed with `ToolError` and `json` in scope. Tool names MUST match `[A-Za-z0-9_\-]{1,128}` — the public hook route's URL pattern enforces exactly that, so a dotted or longer name is unreachable (404 at the router). |
| `tool_specs` | `[{"name": str, "description": str, "parameters": {<arg>: {"type": str, "values": [...] \| null}}}]` | The manifest form. `type` is a free-form Python-ish string defaulting to `"str"` (recommended vocabulary: `str, int, float, bool, list, dict`); `values` is ALWAYS present, `null` = unconstrained. No required-marker — every arg is optional-nullable when republished to the agent. `name` obeys the same charset rule as `handlers`. |
| `world_checks` | `{<name>: <python source>}` | Each defines `check(world)` or `check(world, calls)`. **Return `None` = held; ANY other return value (including `True`) is the failure sentence; raising = broken.** Matches `run_world_check` exactly — and is deliberately NOT the sub-goal rule (A2); do not unify them. |
| `refusal_signature` | `str` | A handler result matching it is recorded as a refusal while still reaching the agent unchanged. |
| `master_db_name` | `str` | Written by the **world service** when materialization is confirmed (B0) — Phase 2 leaves it `""` at save. Naming: `hm_<uuid.hex of the world id, lowercase, no hyphens>_v<version>` — 38 chars at v1–v9. The StoreManager accepts only `^(hm_|hc_|ht_)[a-z0-9_]{1,60}$`, so the hyphenated UUID form is refused; the full 32-hex id is used because a truncated id that collides would silently serve another world's data. |
| `master_materialized_at` | datetime, nullable | Written together with `master_db_name` by the world service. |

Manifest fields with no serve-time meaning (`agent`, `source_root`, `notes`,
`tables` counts, `sequences`, the manifest's own `store`/`tools` lists) do NOT
get columns; Phase 2 parks whatever it wants back inside `RLContract.data`.
Suite-eval definitions are carried in `RLEnvironment.run_config["suite_evals"]`
as `[{"name": str, "required_inputs": [str], "minimum_score": float|null}]` —
that named key is the contract; nothing else reads `run_config` at serve time.

### A2. `RLScenario` column payloads

| column | shape |
|---|---|
| `name` | `str` — the upsert idempotency key (unique per environment) and the key sub-goal rollups report under. MUST NOT begin with `world:`, `state:` or `eval:` (Phase 2 rejects such names at upsert) — those prefixes are reserved by A3's verdict-naming rule. |
| `instruction` | `str` — what the simulated caller wants |
| `persona` | The Persona dict: `{name, gender, age_group, occupation, location, personality, communication_style, accent: str; keywords, languages: list[str]; multilingual: bool; metadata: dict; scripted_caller: dict|null}`. Valid iff `name, personality, communication_style, accent, languages, keywords` are present. |
| `variables` | `dict[str, str]` — extra prompt slots |
| `solution` | `[{"tool": str, "arguments": dict}, …]` — the known-good step list the gates replay |
| `sub_goals` | `[{"name": str, "what": str, "check": str, "judged": str}, …]` — **full SubGoal objects, denormalized.** `check` is Python source defining `check(world, calls)`; deterministic iff non-blank, judged-only otherwise. **Sub-goal check convention: return `None` OR `True` = held; any other return is the failure sentence; raising = broken. This deliberately differs from A1's `world_checks` rule (`True` fails there) — `run_check` and `run_world_check` disagree in the harness; match each exactly.** The FE contract's `settled_by` is derived (`"code"` if `check` else `"a judge"`), carried by no column. |
| `checks` | ONE object: `{<"table.count" \| "table.column">: <expected>, …}` — the state-expectation mapping, evaluated in a single `check_state` call (B3 gives the verdict rule) |
| `setup_code` / `ready_code` | Python source defining `setup(world)` / `ready(world)`; empty string = no-op |
| `max_turns` | `int`, default **10** |
| `gate_status` / `gate_results` / `proved_at` | Gate bookkeeping. `gate_results` shape: `{"latest": {<verdict list, A3>}, "attempts": [{"at": iso8601, "world_id": "<uuid>", "passed": bool, "failures": [str]}, …]}` — attempts ACCUMULATE across proves and **survive the upsert's gate reset** (Phase-2 deliverable: today's upsert wipes `gate_results`; Phase 2 amends it to preserve `attempts`). `world_id` is in each entry because a re-proof against a new world is a new first-try (see build-stats). |

Dropped from the v0 Scenario deliberately: `use_case`, `tests`,
`background_noise` — no column, no consumer in v1; if the builder wants them
back they go in `persona.metadata` or `RLContract.data`, never new columns
without amending this document.

### A3. The canonical verdict

```json
{"name": "item-added", "kind": "code|judged|broken|eval",
 "passed": true, "reason": "", "score": null}
```

- `score` is emitted **only** for `kind: "eval"`; a present score makes the
  platform render the verdict as a metric and **ignore `passed`** (that is how
  `_store_reported_evaluations` branches), so never set both meaningfully.
- **Names are unique within one call's verdict list**, guaranteed by
  prefixing: sub-goal verdicts use the bare sub-goal name (which A2 forbids
  from starting with a reserved prefix); world checks are `world:<name>`;
  state expectations are `state:<path>`; suite evals are `eval:<name>`.
  Names are ≤255 characters AFTER prefixing — Phase 3 truncates to 255
  keeping the prefix. `broken` maps to `passed: false`, never a skip.
- `RLWorldCopy.verdicts` stores a list of exactly these.
- Reporting: `POST /simulate/api/rl-harness/call-executions/<id>/rl-verdicts/`
  with body `{"evaluations": [{"name", "passed", "reason", "score"?}, …]}` —
  `kind` folds into a reason prefix (`"[broken] …"`) when it isn't `code`.
  The endpoint 400s on duplicate names (the prefix rule prevents them) and on
  an empty list — **an empty verdict list is not reported; the caller skips
  the POST.** Reporting requires the reported-evals `EvalTemplate` to be
  seeded (a deployment dependency); a 500 there is a deployment fault —
  Phase 4 records it (see C3's `harness_error`) and does not retry in-line.

### A4. Copy lifecycle ownership

`RLWorldCopy.status` transitions — every transition has exactly one writer:

```
(create) → provisioning        world service (row created BEFORE the clone — B1)
provisioning → ready           world service (clone + setup + ready succeeded)
provisioning → failed          world service (clone/setup/ready failed; error set)
ready → in_call                backend ingestion — the ALK SDK's case-start
                               status callback (mark_alk_sim_call_ongoing, an
                               ORM write, NOT an internal-API PATCH)
ready|in_call → grading        world service (grade begins)
grading → graded               world service (verdicts persisted, DB dropped)
provisioning|ready|in_call|failed → dropped    world service via B4
provisioning|ready|in_call|failed → expired    janitor via B4 (final_status)
```

**Terminal statuses (`graded`, `dropped`, `expired`) are never overwritten —
and the enforcer is the copy PATCH endpoint**, which refuses a `status` write
on a terminal row with `409 {"error": "copy is <status>", "status": …}`
(implemented). Every status writer treats that 409 as "already settled", not
an error. `grading` rows are protected differently: B4 refuses them (409) and
the janitor skips them until `expires_at + HARNESS_GRADE_GRACE_SECONDS` (env,
default 120) — a grade that outlives its grace is abandoned to direction ①.
`failed` exits only to `dropped`/`expired`.

`token` is generated by the platform (model default) at row creation and
returned by the create endpoint — nobody else mints tokens. **The token is a
bearer capability over a live copy** — anyone holding it can execute tools
and change what grading sees. It is NEVER returned to a tenant-facing caller:
C1's `cases[]` deliberately omits it; the agent obtains it solely through
C2's room-config lookup. Because B2 carries it in URL paths, deployments MUST
redact `/simulate/harness-hook/<token>/…` path segments from access logs, and
room-config responses are never logged with their bodies.

`call_log` entries are appended by the world service only, via
`POST world-copies/<copy_id>/call-log/`, **synchronously within each hook
call, before the hook response returns** — a lost log breaks the verdict
(`call_log_lost`, never a false pass). The same write carries the copy's
live state: body `{"entries": […], "state": {…}?}` — `state` is the copy's
current `{"state_object": …}` and lands on `RLWorldCopy.state` in the same
locked save, which is what makes restart-rehydration sound (B2). The
endpoint refuses appends once the copy leaves `ready|in_call`
(`409 {"error": "copy is <status>"}`, implemented) — an abandoned handler
must not corrupt the evidence behind a computed grade. Entry shape (the
`Call` record): `{"name": str, "arguments": dict, "result": any, "ok": bool,
"error": str, "refused": bool, "at": float}`.

---

## Seam B — the world service HTTP surface (Phase 3 serves, Phase 4 + backend call)

All routes live on the harness under `/internal/`, behind the Phase-1 bearer
middleware. Baseline responses on EVERY route, in addition to each route's
own list: `401 {"error": "unauthenticated"}` (bad/absent bearer; the
`HARNESS_AUTH_DISABLED=1` dev escape hatch bypasses this), `503 {"error":
"INTERNAL_API_SECRET is not configured"}`, `400 {"error": "invalid JSON
body"}` — **Phase 3 must register a FastAPI exception handler producing that
exact 400; FastAPI's default is a 422 `{"detail": …}`, which is not this
contract's shape.** Callers reaching the harness THROUGH the backend
forwarder can additionally see `502 {"error": "harness unreachable"}`. The
`{"error": …}` envelope is reserved for these transport/lifecycle refusals —
it never appears in a 200 body.

### B0. `POST /internal/materialize`

Request: `{"world_id": "<uuid>"}`
Reads: `GET worlds/<world_id>/` (status, store_kind, version, schema_scripts,
snapshot).

The world service derives `master_db_name` from the world's id and version
(A1 formula), calls `StoreManager.materialize` (idempotent under its
per-master advisory lock), and then **unconditionally** PATCHes
`master_db_name` + `master_materialized_at` whenever the row does not already
carry that exact name — regardless of whether THIS call did the building. A
crash between rename and PATCH is therefore healed by the next call.
`materialized` reports only whether this call built it.

- 200 `{"master_db_name": "hm_…", "materialized": true|false}`. For
  `store_kind: "inprocess"`: 200 `{"master_db_name": "", "materialized":
  false}` — nothing to materialize.
- 404 `{"error": "world not found"}`
- 422 `{"error": "world is <status>"}` (not `saved`), or
  `{"error": "...", "stage": "create|load|verify|rename"}` on a failed build
  (temp dropped; the stage vocabulary is the StoreManager's own).
- 429 (B1's shape).

Callers: Phase 2 at world save (eager — build latency is the right place to
pay it); Phase 4's initiation defensively (idempotent no-op when done).

### B1. `POST /internal/prepare`

Stamp one copy for one case. Called by Phase 4's initiation (per case, before
anything dials) and by gates (Phase 2, `purpose: "gate"`).

Request:
```json
{"environment_id": "<uuid>", "world_id": "<uuid>", "scenario_id": "<uuid>",
 "purpose": "gate|voice|chat",
 "run_test_id": "<uuid>?", "call_execution_id": "<uuid>?",
 "expires_at": "<iso8601>"}
```
`expires_at` is **required** at this route (the Phase-1 create endpoint stays
permissive on purpose — Phase 3 owns this validation; the janitor's
`expires_at IS NULL` branch is the backstop for rows that dodge it). Run
copies: the backend computes it (run deadline + grace). Gate copies: now +
`HARNESS_GATE_LEASE_SECONDS` (env, default 900).

Reads: `GET worlds/<world_id>/` (version → master name; store_kind;
schema_scripts/snapshot/state/handlers for in-process builds) and
`GET scenarios/<scenario_id>/` (setup_code, ready_code).

Internal order — **row first, then clone** (a crash can never leave a
database without a row): ① `POST world-copies/` → row at `provisioning`,
token + copy_id returned, `db_name` sent in the create as
`hc_<unix epoch>_<uuid.hex of copy_id>` (46 chars; epoch-first exactly like
`ht_` temps, so the janitor can age it from the name — `pg_database` records
no creation time); ② clone from the master, run `setup(world)`, assert
`ready(world)`; ③ `PATCH world-copies/<id>/` → `status: "ready"` + `state`
seeded from the world's build-time state — or `status: "failed"` + `error`.

Concurrency note: parallel prepares against one master are safe — Postgres
15+'s WAL_LOG createdb strategy does not require exclusive template access,
and the harness-store is pinned `postgres:16`; the live test suite exercises
20 concurrent clones of one master. Do not "fix" clone concurrency.

- 201 `{"token": "<uuid>", "copy_id": "<uuid>", "db_name": "hc_…"}`
  (`db_name: ""` for `inprocess` — nothing is cloned; isolation comes from a
  fresh in-process store per copy restored from the world snapshot).
- Existing-copy answers, by the prior copy's status (the DB constraint is
  deliberately status-blind — one copy row per call_execution, ever):
  - `ready|in_call|grading|graded` → 200, same body (idempotent retry)
  - `failed` → 422 carrying the stored error — the run must not start on it
  - `provisioning` younger than `HARNESS_PREPARE_STALE_SECONDS` (env,
    default 120) → `409 {"error": "prepare in flight"}` — caller retries
  - `provisioning` older → the world service PATCHes it `failed`
    (`error: "prepare abandoned"`) and answers that 422
  - `dropped|expired` → `422 {"error": "copy is <status>", "stage": "lease"}`
    — the case is not re-preparable; Phase 4 fails the CallExecution rather
    than redialing
  For `purpose: "gate"` there is NO idempotency (no call_execution): every
  call stamps a fresh copy, and the calling phase drops it via B4 when the
  gate finishes.
- 404 `{"error": "environment|world|scenario|run test|call execution not found"}`
- 422 `{"error": "...", "stage": "clone|setup|ready", "scenario": "<name>",
  "traceback": "..."}` — the run must refuse to start. Also
  `{"error": "world is <status>"}` for a non-`saved` world.
- 429 `{"error": "...", "kind": "copy|build", "used": n, "limit": n}` —
  StoreManager quota (`kind` picks the ceiling: copy = wait and retry;
  build = a materialize stampede, back off harder).

### B2. `POST /internal/hook/<token>/<tool>`

The live tool call. The backend's public `HarnessHookView`
(`/simulate/harness-hook/<token>/<tool>`) forwards here verbatim. Token is a
canonical hyphenated UUID; tool names obey A1's charset rule.

- Request body: the tool's arguments as a plain JSON object, verbatim.
- 200 — mechanical response rule: a `dict` or `list` return value IS the
  body; any other value (str, int, float, bool, None) becomes
  `{"result": "<value as string>"}`. `bytes` is not a permitted handler
  return. **Refusals, unknown-tool answers, handler errors and rehydration
  failures all use the same `{"result": "<text the agent should hear>"}`
  form** — never `{"error": …}` on a 200; the machine truth
  (`ok/refused/error`) lands in the call log and is what grading reads.
- 404 `{"error": "unknown token" | "copy is <status>" | "copy is expired"}`
- 408 `{"error": "handler timed out"}` — the bounded executor killed it; the
  call log records the timeout; the copy survives. The ceiling is
  `HARNESS_HANDLER_TIMEOUT_SECONDS` (env, default 20) and MUST stay below
  the backend forwarder's 30s `NON_STREAMING_TIMEOUT` and the app role's
  30s `statement_timeout` — otherwise the agent sees a 502 instead of this
  408.

**Restart/rehydrate (normative):** the world service holds no durable
per-token state. On a hook call for an unknown-in-memory token it
rehydrates: `GET world-copies/by-token/` (db_name, status, **state**) →
`GET worlds/<world_id>/` (handlers, tool_specs, refusal_signature,
world_checks; `snapshot` is ignored for postgres copies — the copy's
database is the truth) → rebuild the runtime world against
`app_dsn_for(db_name)` → restore the COPY's `state` (not the world's — the
copy's is the live one, kept current by every call-log write). `setup(world)`
is **not** re-run. Rehydration failure answers 200 `{"result": "<recoverable
sentence>"}` with `ok: false` + `error` in the call log — mid-call, never a
5xx.

### B3. `POST /internal/grade`

Request: `{"token": "<uuid>"}` or `{"call_execution_id": "<uuid>"}` — the
**world service** resolves the latter via `GET
world-copies/?call_execution_id=` on the internal API; callers send either.
Reads: `GET scenarios/<id>/` (sub_goals, checks, solution) and
`GET worlds/<id>/` (world_checks) when not already live in memory.

**Drain rule:** grade flips the copy to `grading` first (every subsequent
hook call 404s, and the fenced call-log endpoint refuses stragglers — an
abandoned handler's append lands nowhere), waits up to 10s for in-flight
handlers, then reads state. Abandoned handlers die when the drop terminates
their connections (`WITH (FORCE)`); that exception is expected and never
reaches an agent.

Grading inputs, against the copy's database + call log + copy `state`:
deterministic sub-goal checks (`check(world, calls)`, A2's convention), the
state-expectation mapping (ONE `check_state(world.state(), checks)` call —
store collections merged with the copy's state object, store winning; every
failure string becomes one verdict named `state:<path>` where `<path>` is
the substring before the first `": "` — `check_state` prefixes every failure
with its expectation key and emits at most one failure per key, so these
names are unique by construction; no failures → one passed
`state:expectations` verdict), and `world_checks` (A1's convention, named
`world:<name>`). Then: verdicts persisted on the copy row, database dropped,
status `graded`.

- 200 `{"verdicts": [<canonical verdict>, …], "replayed": false}`
- 200 `{"verdicts": […], "replayed": true}` — copy already `graded`
- 404 `{"error": "unknown token" | "no copy for call execution"}`
- 409 `{"error": "copy is <status>"}` — a terminal `dropped|expired` row;
  the caller records it and does not retry
- 500 `{"error": "..."}` — grading broke; the caller records it (C3) and
  never blocks result ingestion; the copy falls to the janitor with the
  error on the row.

### B4. `POST /internal/drop`

`{"token": "<uuid>", "final_status": "dropped"|"expired"}` (default
`"dropped"`; only the janitor sends `"expired"`).
→ 200 `{"dropped": true|false}` — `true` means the row carried a `db_name`
and the drop was issued (`DROP DATABASE IF EXISTS` is unconditional; no
existence probe is implied). Idempotent. Status: written only from
`provisioning|ready|in_call|failed`; a `grading` row is refused with
`409 {"error": "copy is grading"}` (the janitor honors the grace rule in
A4); terminal rows keep their status (the PATCH's 409 is treated as settled)
while the DB drop still runs. 404 `{"error": "unknown token"}`.

### B5. `POST /internal/janitor/sweep`

The DB-side reconciliation route — direction ② of C3's janitor cannot be
built from the backend (StoreManager is harness-process-only), so the world
service exposes it:

Request: `{"live_db_names": ["hc_…", …], "older_than_seconds": n}` — the
caller (Phase 4's janitor) computes `live_db_names` from every non-terminal
`RLWorldCopy.db_name`.
→ 200 `{"dropped_copies": [str], "dropped_temps": [str]}` — the world
service lists `StoreManager.copies()`, parses each `hc_` name's epoch
(exactly like `ht_` temps), and force-drops any older than the cutoff and
not in `live_db_names`; `sweep_temps` runs in the same call. In-process
copies have no database and are untouched by this route.

---

## Seam C — run initiation and result flow (Phase 4 ↔ 2 & 3)

### C1. Initiation input/output

One service function, reachable from both doors (D11: the environment's run
button and `RunTestExecutionView` when `run_test.rl_environment` is set):

Input: `environment_id`, scenario selection (`scenario_ids[]` or
all-gate-passed), simulator overrides (optional).
Sequence (all-or-nothing before dialing): balance check → org admission cap →
`POST /internal/materialize` (idempotent) → shadow `Scenarios`/Dataset rows
ensured (idempotent via `RLScenario.platform_scenario`) → RunTest +
TestExecution + pre-created CallExecutions (positional order = scenario
order) → **`/internal/prepare` per case** (B1) → room template stamped →
`start_simulation_runner_workflow`.

Output: `{"run_test_id", "test_execution_id",
"cases": [{"scenario_id", "call_execution_id"}]}` — **no tokens** (A4: the
token never reaches a tenant-facing surface; nothing downstream needs it —
room-config resolves via the call_execution). A 422 from any prepare names
the scenario; already-stamped copies are dropped (B4) before returning.

### C2. Room binding (Contract 1, platform side)

- Room template: `hosted-{run_id}-i{index}-{test_case_id}` — the `-i{index}-`
  insertion is the one ALK-side change; Azain owns/vetoes it. Parser
  constraints: index ≤ 5 digits, test_case_id ≤ 128 chars of
  `[A-Za-z0-9_.\-]`, whole room ≤ 255.
- `GET /simulate/harness-hook/room-config/<room>`: parse `index` → the run's
  CallExecutions in pre-created positional order → copy by `call_execution` →
  ```json
  {"tools_api_url": "https://<backend>/simulate/harness-hook/<token>",
   "session_token": "<token>",
   "caller_identity_prefixes": ["fagi-simulator-", "sip-caller-"]}
  ```
  Only answers for runs in flight; unknown room → 404 naming the room;
  malformed → 400 naming the room. The route and parser are implemented;
  **Phase 4 owns `_resolve_room` AND this success body** (the current stub
  answers 501 on the unreachable resolved branch). Responses carry a bearer
  capability — never log them.

### C3. Result hooks and cleanup

- Case start (ALK status callback → `mark_alk_sim_call_ongoing`): copy
  `ready → in_call`; missing/failed copy → CallExecution `status = FAILED`.
- Case end (result PATCH → `ingest_alk_sim_result`): after the result is
  stored → `POST /internal/grade` (B3) → verdicts reported via `rl-verdicts`
  (A3). Grade failure never blocks ingestion.
- **Failure labels**: `world_copy_missing`, `harness_grading_failed`,
  `harness_verdicts_unreported` all land in
  `CallExecution.call_metadata["harness_error"]` (a string,
  last-writer-wins) — never `error_message`, which `_apply_payload` owns and
  can clobber. `world_copy_missing` additionally sets `status = FAILED`.
- Run end (`finalize_hosted_execution`): sweep every remaining copy —
  grade-if-possible (B3), reporting each graded copy's verdicts through
  `rl-verdicts` exactly as the case-end path does, else drop (B4). Copies
  with no `call_execution` (gates) are never reported. **The sweep runs in
  EVERY finalize branch — completed, cancelled, failed — before the activity
  returns** (the completed branch returns early today; Phase 4 hooks the
  sweep ahead of every return).
- **Janitor** (Temporal Schedule, Phase 4), two directions, both required —
  the bounding constant is `HARNESS_MAX_LEASE_SECONDS` (env, default 86400),
  which caps every lease however it was computed:
  ① row-driven: any non-terminal copy past `expires_at` (or with
  `expires_at IS NULL` and older than the max lease) → B4 with
  `final_status: "expired"`; `grading` rows only after the grace rule (A4).
  ② DB-driven: compute `live_db_names` from non-terminal rows → B5. Covers
  databases whose rows were lost and pre-row crashes (B1's row-first order
  makes those near-impossible; B5 makes them impossible).

---

## Build-efficiency observability (Phase 2, for the Khushal + Rishav track)

Frozen shapes the metrics depend on:

- `RLEnvironmentMessage.tools` = `[{"name": str, "ok": bool, "ms": int}, …]`,
  one entry per tool call in call order; `phase` set faithfully on every row.
- `RLScenario.gate_results.attempts` accumulates across proves, each entry
  carrying `world_id` (A2).

Contract: `GET /simulate/api/rl-harness/environments/<id>/build-stats/`,
computed on read:

```json
{"turns_by_phase": {"understand": 6, "build": 41, "scenarios": 18},
 "tool_calls_by_phase": {...}, "wall_seconds_by_phase": {...},
 "world_versions": 2, "scenario_count": 12,
 "gate_first_try_pass_rate": 0.83, "repeated_tool_calls": 7}
```

Formulas (normative — the regression tooling compares these numbers):
- `turns_by_phase`: count of messages with `role: "assistant"` per phase —
  one assistant row is one turn; user/system/tool rows are not turns.
- `wall_seconds_by_phase`: `max(created_at) − min(created_at)` over that
  phase's rows; `0` when fewer than two rows. Elapsed time including idle.
- `tool_calls_by_phase`: Σ `len(tools)` per phase.
- `repeated_tool_calls`: consecutive same-`name` entries within a turn's
  `tools` list.
- `gate_first_try_pass_rate`: over scenarios with ≥1 attempt **against
  their current `world_id`**, the fraction whose first such attempt passed;
  `null` when no scenario qualifies.
- `world_versions`: count of the environment's world rows.

Phase 2's obligations: the two shapes, the endpoint, its test, and the
attempts-preservation amendment to the scenario upsert. The comparison
tooling (same agent built N times, turn-count regression) is Rishav-track
work on top of this endpoint, not a phase deliverable.
