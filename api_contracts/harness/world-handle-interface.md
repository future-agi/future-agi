# World Handle Interface (v3.5)

**Date:** 2026-08-24 · **Status:** FROZEN — conform, don't redesign
**Owner / defects:** Khushal (the runtime provides the object).
**Consumers:** scenario code — `setup(world)`, `ready(world)`, sub-goal
`check(world, calls)` — at generation/proof time AND at run time.
**Pinned against:** seam contract ≥ v1.5, outbound-channels ≥ v1.2.
**Changelog:** 3.5 (2026-08-25) — errored-receipt row `capability_unavailable`
(a capability absent from EVERY world is a deterministic bundle/scenario
mismatch — it must not retire a healthy world or burn the cross-world
retry; `world_unavailable` had been doing double duty). Coverage
guarantee: the zero-turn case pinned as intended (`evidence_missing`,
single retry — a silent agent and an unobserved one are
indistinguishable here). Scheduler-side implementation of
`capability_unavailable` is a follow-up, not shipped with this text.
3.4 (2026-08-25) — the ready/check exception fold written
down (exceptions raised by `ready()`/`check()` classify as
`ready_broken`/`check_broken`, matching `run_check`'s broken-on-exception
rule — only `setup` had an explicit crash row); errored-receipt row
`driver_crashed` (the scheduler's own machinery failed while driving the
scenario — not the agent, not a check, not the call; domain `simulator`,
not retried). 3.3 (2026-08-25) — errored-receipt row `call_failed`
(the simulated-call machinery itself crashed — neither table covered it;
domain `infrastructure`, retried once on another world like a world
failure). 3.2 (2026-08-25) — all-over-cap rule (bare `state()` that
would return `{}` raises instead); errored-receipt rows for
`WorldUnavailable` (domain environment) and `WorldStateTooLarge` (domain
simulator). 3.1 (2026-08-25) — cap ruling: bare `state()` excludes
over-cap tables, explicit selector raises; OPEN DEFECT: the `http_tool`
shim wire format for `call()` is pinned nowhere — implementations raise a
typed error rather than guess, until the evidence layer pins it.
3.0 (2026-08-24) — evidence seam moved to a bundle field;
proof-time vs run-time handle scoping; baseline-measured state cap;
runner-convention alignment; errored-receipt table; non-SQL bundles become
a preflight rejection; full delta list. 2.0, 1.0 — superseded.

**Design rule:** the hosted world handle IS the shipped world surface
(`world/runtime.py`, the generation prompts, `SKILL.md`). Hosted execution
changes what backs it (a per-world postgres store), not the vocabulary.

## Two handles, one interface

The same interface is provided at two moments:

- **Proof time** (stage `generating_scenarios`/`validating_scenarios`,
  spine §5 step 3.5): the generation stage's acceptance gates run against
  world 0, freshly reset from the SAME sealed baseline the run worlds
  use — a proof transfers to run time by construction. The proof-time
  handle is INTENDED to always support `call` (the Solvable gate plays
  the reference solution through it) — but per the changelog's open
  defect, implementations raise until the `http_tool` shim wire format
  is pinned, so the Solvable gate cannot yet play solutions through the
  hosted handle. The reference solution's calls ARE the `calls`
  argument handed to checks at proof time.
- **Run time** (stage `running`): the scheduler's handle for
  `setup`/`ready`/`check` around the live simulated call. `setup`'s
  tool calls are NOT evidence (the runner clears them before the call
  starts, as the local runner does); the simulated call's observed tools
  are the `calls` argument.

## When each function runs

Per scenario, on its world: reset → `setup(world)` (may write, may
`call`; 60s) → `ready(world)` (read-only; 15s) → the simulated call →
each sub-goal's `check(world, calls)` (the sub-goal checkers, distinct
from the generation-time acceptance gates; read-only; 60s each).

**Return conventions** (these are the normative rules; `folder.py` and
`checks.py` are the runners and are aligned to them — delta below):

- `ready`: `None` / `True` / `""` / whitespace → ready. Non-empty string
  → not ready (scenario `errored`, code `ready_not_ready` — a
  precondition failing on the shared sealed baseline is a generation
  defect). Bare `False` or any other value → broken (`ready_broken`).
- `check`: `None` / `True` / `""` / whitespace → held. Non-empty string →
  not held (the string goes to the receipt's `sub_goals[].reason`). Bare
  `False` → not held with reason `"False"` (an agent result, matching
  `checks.py`). Any other value → broken (`check_broken`).
- Exceptions and budget overruns → scenario `errored`; traceback to the
  artifact spool; the world is discarded and re-provisioned (a
  half-applied world is never reused).

**Errored-receipt fields** (outbound-channels Channel 2; all rows use
`stage: "running"`, `domain: "simulator"` unless noted):

| condition | `failure.code` |
|---|---|
| `setup` exception | `setup_crashed` |
| `setup`/`ready`/`check` budget overrun | `setup_timeout` / `ready_timeout` / `check_timeout` |
| `ready` returned a reason | `ready_not_ready` |
| `ready`/`check` broken value | `ready_broken` / `check_broken` |
| zero calls captured (see coverage) | `evidence_missing` |
| handle misuse raised (below) | `world_usage` |
| `WorldUnavailable` raised (world cannot serve the scenario) | `world_unavailable` — domain `environment`, not simulator |
| `WorldUnavailable` where the capability was never provisioned for ANY world (empty `public` schema, `call` under `tool_trace`) | `capability_unavailable` — domain `environment`; deterministic bundle/scenario mismatch: do NOT retire the world and do NOT retry on another (every world is identical) (v3.5) |
| `WorldStateTooLarge` raised (check should have used `query()`) | `state_too_large` |
| the simulated-call machinery crashed (not the agent, not a check) | `call_failed` — domain `infrastructure`; retried once on another world (v3.3) |
| the LiveKit dispatch was never acknowledged within the ack ladder's +60 s budget (`c3-call-affinity.md` v0.4 §4.5) | `voice_dispatch_unacknowledged` — domain `infrastructure`; retried once on another world (like `call_failed`) |
| `ready`/`check` raised an exception | `ready_broken` / `check_broken` — the broken-value rows cover exceptions too, matching `run_check` (v3.4) |
| the scheduler's own machinery failed while driving the scenario | `driver_crashed` — not retried (v3.4) |

`failure.message` carries the reason/traceback summary; a check's
not-held string never goes to `failure` — it is `sub_goals[].reason`.
An `evidence_missing` scenario gets the same single retry-on-another-
world as a world failure (`scenario_attempt` 2); a second occurrence is
final.

**Execution model:** scenario code is exec'd in-process by the runner,
one worker thread per world. Budgets are enforced by a watchdog that
cancels the in-flight postgres operation (psycopg cancellation) and
abandons the thread; a CPU-bound runaway cannot be killed — its thread
leaks (bounded by scenario count), its world is discarded, and the job
TTL is the backstop. This boundary guards against *mistakes, not
malice* — scenario code is harness-authored, never customer-supplied.

## The `World` object

```python
class World(Protocol):
    world_index: int          # diagnostics ONLY — never branch on it
    rng: random.Random        # the only sanctioned randomness

    def state(self, table: str | None = None) -> dict[str, list[dict]]: ...
    def put(self, collection: str, record: dict, *, key: str = "") -> dict: ...
    def change(self, collection: str, key: str, changes: dict, *, by: str = "") -> int: ...
    def drop(self, collection: str, key: str = "", *, by: str = "") -> int: ...
    def call(self, name: str, arguments: dict | None = None) -> "Call": ...
    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict]: ...
```

Signatures are a superset of the shipped `GeneratedWorld` (`put` gains a
return value, `state` gains a selector, `query` is new); keyword-only
markers match the shipped class.

- **Backing:** the world's logical postgres database, via short-lived
  **autocommit** connections — one per operation, none held (this is
  what lets `reset` drop the database; `PostgresStore` already works this
  way). Writes are immediately visible to the agent's processes; reads
  see committed data only. `reset` invalidates the handle; the runner
  builds a fresh one per scenario.
- `state()` — snapshot `{table: [row_dict, ...]}` of the `public` schema
  (non-public via `query()`). Every table appears, empty as `[]`; rows
  ordered by primary key where one exists, otherwise **unordered — never
  index positionally**. `state("bookings")` selects one table.
  **Cap:** a table whose **baseline** row count exceeds 5,000 raises
  `WorldStateTooLarge` on **explicit** access (`state("big_table")`);
  bare `state()` simply excludes such tables from its snapshot — one
  seeded audit table must not make the primary read verb inert for the
  whole run. If exclusion would leave the snapshot EMPTY while the
  schema is not, bare `state()` raises `WorldStateTooLarge` naming the
  excluded tables — it never returns `{}` (the forbidden vacuous
  observation) through the exclusion path either. The exclusion is
  enforced at the READ (the store selects only the included tables),
  never by discarding materialized rows. Measured at baseline freeze, so it is deterministic and
  provable at generation time; nothing the agent does during a call can
  change which tables raise. A postgres world whose
  `public` schema holds zero tables raises `WorldUnavailable` — `state()`
  never returns `{}` (an empty snapshot reads as an observation and makes
  negative checks pass vacuously). The reserved `_alk_conformance` table
  never appears. The shipped `state_object` merge (in-memory generated
  collections) does not exist in hosted worlds — state is the store,
  full stop.
- **Value types** (identical for `state()` and `query()`; psycopg3
  defaults): `numeric→Decimal`, `int→int`, `text→str`, `bool→bool`,
  `timestamp/timestamptz→datetime` (tz-aware for timestamptz),
  `date→date`, `json/jsonb→dict|list`, `uuid→UUID`, `bytea→bytes`,
  arrays→`list`, `NULL→None`. Compare `Decimal` via `Decimal`/`int()`/
  `float()`, never with a string.
- `put` — inserts; returns the stored record **including its generated
  key**. Inserting into a collection that is not a table raises
  `WorldUsageError` (hosted worlds cannot invent tables — scenario data
  lives in the schema the migrations made).
- `change`/`drop` — update/delete; return affected count. `by` is
  **required** whenever `key` is not the primary-key value
  (`WorldUsageError` otherwise, matching the store's rule).
- `call` — plays one of the agent's own tools against this world and
  returns a `Call`. At proof time: always available. At run time:
  available under an `http_tool` evidence seam; under `tool_trace` it
  raises `WorldUnavailable` (the tools live inside the agent process) —
  run-time scenario code should not need it anyway, since setup runs at
  proof time too and data methods cover the delta.
- `query` — read escape hatch: psycopg3, `%s` positional params, one
  statement. Executed on a `SET TRANSACTION READ ONLY` connection — that
  is the guard; the token rule (first non-comment token `SELECT`/`WITH`/
  `VALUES`, no `FOR UPDATE`) is a friendliness check on top, so a
  data-modifying CTE fails at the database, not silently.
- **Read-only handles:** `ready` and `check` receive a handle whose
  `put`/`change`/`drop`/`call` raise `WorldReadOnly`.
- **Exceptions** (`fi.alk.harness.world.errors`): `WorldReadOnly`,
  `WorldReservedName`, `WorldQueryRejected`, `WorldStateTooLarge`,
  `WorldUnavailable`, `WorldUsageError`.
- **Non-SQL-only bundles do not reach this interface:** a hosted
  `kind: process` bundle without a postgres-protocol capability is
  rejected at preflight (spine §2e, `no_sql_store`) — the job fails in
  `validating_environment` instead of every scenario erroring in
  `running`.

## The `calls` argument

Ordered list of observed tool calls; each entry is exactly the shipped
`Call` dataclass — so `folder.py::_RUNNABLE` loads the spooled
`calls.json` unchanged:

```python
Call(name: str, arguments: dict, result: Any = None, ok: bool = True,
     error: str = "", refused: bool = False, at: float = 0.0)
```

- **Evidence seam:** declared by the bundle —
  `runtime.evidence_seam: "http_tool" | "tool_trace"` (spine §2a; authored
  by the env-creation stage, which knows the repo). Exactly one source is
  active; entries are never merged, so no duplicates.
- **Coverage guarantee:** if the simulator's transcript recorded ≥1
  conversational turn (the simulator session owns the turn count — that
  is the signal) and zero calls were captured, the scenario is `errored`
  (`evidence_missing`). An empty list is never handed to checks: "the
  agent never called X" must not be manufactured from an agent nobody
  observed. A genuinely zero-turn session (the agent never answered at
  all) is ALSO `evidence_missing`/`simulator` with the single retry —
  intended: a silent agent and an unobserved agent are indistinguishable
  at this seam, and a graded verdict must never be minted from either
  (v3.5).
- `ok` = completed without error; `refused` = the world declined (the
  agent *tried*). Derivation: `http_tool` — refusal response →
  `refused=True, ok=True`; crash → `ok=False` + `error`. `tool_trace`
  V1 — `refused` mirrors `not ok`; a trace cannot distinguish refusal
  from crash, and checks under trace seams must treat them as one.
- `result` — parsed JSON where the source captured JSON, else a string;
  both `result` (string form) and `error` truncated at 2,000 chars.
  Checks must tolerate the string form. `at` — epoch seconds; `0.0` =
  unmeasured; never fabricated.
- One entry per attempt (agent retries appear individually); list order
  is evidence order; no extra fields.

## Determinism rules

- `world.rng` is seeded `job.seed + scenario_index` (the gateway
  guarantees `seed` is a concrete integer — spine §1) at each scenario
  execution start; one stream across `setup`/`ready`/`check`; a retry
  re-seeds identically.
- `setup` always runs from a fresh reset and must not depend on any
  earlier scenario or on `world_index`.

## Implementation deltas (the complete list)

- `fi/alk/harness/world/errors.py` — new module, six exception types.
- World handle: `world_index`, `rng`, `state(table=…)` selector +
  baseline-measured cap + empty-schema raise + `_alk_conformance`
  exclusion, `put` returns the record, `query()` (new — the store's
  `Held.query` raise is replaced for postgres), read-only sub-handles,
  proof-time vs run-time `call` scoping.
- `PostgresStore`: five deltas — `add` returns the record with its key;
  a read-only session query path; `state(only=…)` read-side selection;
  `table()` (with a typed `Held` fallback and a `Store`-protocol entry);
  the `_select_ordered` extraction.
- `checks.py`/`folder.py`: align to the return conventions above (the
  one behavioral change: `check` returning `""`/whitespace counts as
  held; bare `False` from `ready` becomes broken).
- Spooled `calls.json` keeps exactly the `Call` fields; `_RUNNABLE`'s
  world-restore half is local-SDK-only — the hosted spool carries
  `calls.json` alone, and hand-running a hosted check re-creates the
  world from the sealed baseline, not from a snapshot file.
- `SKILL.md`: two line-level edits — the `state_object` dict-shape note
  (dead in hosted worlds) and a sentence scoping "prefer `world.call` in
  setup" to proof time. The vocabulary itself is unchanged.
- Spine v1.5: `runtime.evidence_seam` bundle field; `no_sql_store`
  preflight; §5 step 3.5 (generation + proof after baseline freeze).
