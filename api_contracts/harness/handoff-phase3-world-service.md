# Phase 3 handoff — the world service (Jayasurya)

## What you're building, in one paragraph

The harness's serving half. Phase 2 (Khushal, in parallel) builds worlds and
persists them as rows; your service takes those rows and runs live
simulations against them: it materializes a world's master database, stamps a
disposable copy per simulation, answers the agent's tool calls against that
copy mid-call, grades the outcome, and cleans up. It runs inside the existing
harness FastAPI process, alongside the current v0 code, which stays untouched
and working until Phase 4 cuts over.

## Ground rules

- **The interface bible is `api_contracts/harness/phase-seam-contracts.md`,
  revision v3** — check the revision note at its top; it ships beside this
  handoff (uncommitted by design; `api_contracts/harness/` is canonical,
  `.claude/harness-phase1/` is a mirror). Any needed deviation is a
  conversation with Khushal, never a local edit. Seam A tells you the exact shapes of what you read; Seam B is the
  HTTP surface you implement, response bodies and status codes included. It
  survived two adversarial review rounds — when it seems pedantic, it's
  because an ambiguity there was found to make two implementers diverge.
- Branch `feat/harness-phase3-world-service` off
  `feat/harness-phase1-foundations`; PR targets that branch (GitHub
  retargets it automatically when the base PR merges AND its branch is
  deleted — if the branch lingers, retarget by hand). Conventional commits, DCO (`git
  commit -s`), no co-author trailers.
- Rebase when Phase 1 or 2 lands on `feat/harness`. Khushal's Phase 2 edits
  the same package — the agreed seam: **your routes live in a separate
  router module that `app.py` includes with one line** (e.g.
  `src/harness/service/routes.py`), so the collision surface between you two
  is a single import.

## What already exists for you (branch `feat/harness-phase1-foundations`)

- **StoreManager** — `futureagi/harness/src/harness/world/stores/manager.py`.
  Materialize (advisory-locked, idempotent), clone (55006 retry), FORCE drop,
  quotas (`QuotaExceeded.kind`), `sweep_temps`, `copies()`, `app_dsn_for`.
  Read its tests (`harness/tests/test_store_manager.py`) — they run live
  against Postgres in CI and document every behavior you'll lean on,
  including that 20 concurrent clones of one master are safe (WAL_LOG;
  the store is pinned postgres:16 — don't add serialization).
- **The internal persistence API** — `futureagi/simulate/views/rl_internal.py`
  under `/simulate/api/rl-harness/`. You are its main consumer: world/scenario
  reads (`GET worlds/<id>/`, `GET scenarios/<id>/`,
  `GET world-copies/by-token/`, `GET world-copies/?call_execution_id=`),
  copy writes (create / PATCH / call-log append). Auth: bearer
  `INTERNAL_API_SECRET`. The call-log endpoint carries the copy's live
  `state` in the same write, is fenced after grading starts (409), and the
  copy PATCH 409s status writes on terminal rows — your writers treat those
  409s as "already settled", per the contract. Also there for B0:
  `PATCH worlds/<id>/` (accepts `master_db_name`, `master_materialized_at`,
  `status`).
- **Auth middleware on the harness side** — every route including your
  `/internal/*` ones already requires the bearer (`src/harness/ui/app.py`;
  exceptions: `/healthz`, and everything when `HARNESS_AUTH_DISABLED=1`
  with no secret set — the standalone-dev escape hatch).
  One obligation the middleware doesn't cover: register a FastAPI exception
  handler so an unparseable body answers `400 {"error": "invalid JSON
  body"}`, not FastAPI's default 422 (Seam B preamble).
- **The runtime you're wrapping** — don't rebuild what exists:
  `src/harness/world/runtime.py` (`GeneratedWorld`, handler execution, the
  `Call` record, refusal detection), `src/harness/checks.py` (sub-goal vs
  world-check conventions — they differ on `True`, deliberately),
  `src/harness/world/expectations.py` (`check_state`). One thing you DO
  have to build: **a per-copy store adapter**. `PostgresStore` is a
  `ContainerStore` — constructing a `GeneratedWorld` with `kind="postgres"`
  boots a Docker container (`world/stores/container.py:104`), and its only
  DSN override (`ALK_POSTGRES_DSN`, `world/stores/postgres.py:76`) is
  process-global, so it cannot carry a per-copy DSN — setting it per copy
  in a multi-copy process is exactly the cross-talk this rebuild kills, and
  it WILL pass a single-copy unit test. Write a small store whose `start()`
  is a no-op and whose connection comes from
  `StoreManager.app_dsn_for(db_name)`, injected via
  `GeneratedWorld(store=…)` (the kwarg at `runtime.py:161`). Reuse
  `postgres.py`'s `_tables`/`_adapt`/record helpers; do not reuse its
  lifecycle. `snapshot.restore()` is folder-based and unusable on this
  path.

## Build order (suggested)

1. **A small internal-API client** in the harness package. Bearer
   `INTERNAL_API_SECRET` (already on the harness container); base URL is
   the **backend**, so follow `platform.py`'s `HARNESS_PLATFORM_URL` idiom
   (`platform.py:34`; the dev compose sets it to `http://backend:80`) — NOT
   `HARNESS_INTERNAL_URL`, which is the backend→harness direction. Name
   yours e.g. `HARNESS_BACKEND_URL` (falling back to
   `HARNESS_PLATFORM_URL`) and add it to the harness service's env block.
   `platform.py` authenticates with FI keys; yours uses the internal
   secret.
2. **B0 materialize** — derive the name (A1 formula), call StoreManager,
   PATCH the world row (`PATCH worlds/<id>/` accepts `master_db_name` +
   `master_materialized_at`) unconditionally when it lacks the name (the
   contract's crash-healing rule). One wrinkle: B0's 422 needs `stage`, and
   `StoreManagerError` only embeds it in the message (`manager.py:387`) —
   either parse `" failed at <stage>: "` or ask Khushal to add a `stage`
   attribute; do not invent a different vocabulary.
3. **B1 prepare** — the row-first three-step, the full prior-copy answer
   table (including the stale-`provisioning` recovery and the
   `dropped|expired` lease refusals), `hc_<epoch>_<hex>` naming, gate
   semantics. Expose it as a **plain function plus the route wrapper** — the
   Phase 2 builder lives in your process and will call the function directly
   for gates rather than HTTP-ing itself.
4. **B2 hook** — token registry in memory; the mechanical response rule
   (dict/list pass through, everything else `{"result": str}`, `{"error"}`
   never on a 200); synchronous call-log+state write per call; rehydrate on
   miss (copy's `state`, never the world's; no `setup` re-run); bounded
   execution (`HARNESS_HANDLER_TIMEOUT_SECONDS`, default 20 — must stay
   under the backend's 30s forward timeout and the app role's 30s
   statement_timeout).
5. **B3 grade** — flip to `grading` first (the fence does the rest), drain
   10s, evaluate: three check families but only **two** prefixes —
   `world:<name>` and `state:<path>` (with its extraction rule); sub-goal
   verdicts carry the BARE sub-goal name per A3, which is what rollups join
   on. `eval:` verdicts (suite evals) are explicitly OUT of Phase 3's scope
   — `run_config["suite_evals"]` is carried data, nothing in your service
   runs them. Persist verdicts, drop, `graded`; replay from the row when
   already graded.
6. **B4 drop / B5 janitor sweep** — B4's `final_status` semantics; B5 is
   the DB-side reconciliation Phase 4's janitor calls with `live_db_names`.

## Testing expectations

- Your lane is `futureagi/bin/test-harness` — it auto-detects the store on
  localhost:15433 (`futureagi/bin/test up` starts it) and CI runs it live.
  StoreManager patterns in `futureagi/harness/tests/test_store_manager.py`
  show the fixture/cleanup discipline (leave no databases behind, scope
  assertions to your own names).
- Test against the CONTRACT, not your implementation: every Seam B response
  shape and status code, the prior-copy table in B1, the fence, the drain,
  rehydrate-after-restart (kill your in-memory registry and hook again), the
  200-with-text rule for every failure flavor, in-process worlds end to end.
  Mock the internal API with recorded shapes for unit speed; a handful of
  integration tests can run against the real backend in the compose test
  stack if you want them (ask Khushal for the lane wiring).
- Phase exit (from the plan): the world service serves gate/voice/chat
  copies alongside the untouched v0 path; Phase 2's gates can run through
  your prepare/grade functions; nothing about v0 behavior changes.

## Known sharp edges (all review-sourced; the contract has the details)

- `store_kind`: rows say `inprocess`, the harness's own key is `in_process`
  — map at the boundary. `sqlite` rows cannot exist (refused at save), but
  refuse loudly anyway if you ever see one.
- In-process copies: no database, `db_name: ""` — every DB-flavored code
  path must branch; B5 skips them; isolation = fresh store from snapshot.
- The sub-goal check convention (`True` = held) vs world-check convention
  (`True` = failure) — match `checks.py` exactly; the contract bolds this
  because a reviewer proved the natural reading gets it backwards.
- Verdict names: ≤255 characters, prefix preserved when truncating;
  sub-goal names are UNPREFIXED. Empty verdict lists are never reported.
- The contract's new `HARNESS_*` env vars that are yours to provision
  (defaults cover code, compose does not set them yet):
  `HARNESS_GATE_LEASE_SECONDS`, `HARNESS_PREPARE_STALE_SECONDS`,
  `HARNESS_HANDLER_TIMEOUT_SECONDS` — add them to the harness service in
  the dev compose and to `harness-ci.yml` if a test needs a non-default.
- Tokens are bearer capabilities — never log them, and never put them in
  error messages.
