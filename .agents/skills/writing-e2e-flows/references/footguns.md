# Footguns

Read when a run fails for a reason you did not predict, or when a check in the plan does not behave
the way the design doc implies. Grouped, but each row stands alone: **symptom → cause → fix**.

## Catalog and annotation

1. **`yarn catalog` exits 1 with `missing flow annotation on "…"`** → a test carries `@flow` but no
   `flowAnnotation` → add the annotation; `e2e/scripts/flow-catalog.mjs:extractFlows` fails the whole
   suite's catalog, not just that spec.
2. **A new spec never appears in `FLOWS.md`, and nothing errors** → the `tag` array omits `'@flow'` →
   add it. Untagged tests still _run_ (that is how `harness/**` stays out of the catalog), so the
   silence is by design.
3. **`FLOWS.md` renders the id twice and quarantining by id does nothing** → the title is not exactly
   `<ID>: <sentence>` → fix the title. `renderCatalog` strips `${id}: ` from the heading, and
   `e2e/lib/quarantine.ts:grepInvertPattern` matches the id as a substring of the composed title.
4. **A wrong `area` silently opens a new section in `FLOWS.md`** → nothing validates that `area`
   equals the `flows/<area>/` directory; only the five fields' presence and id uniqueness are checked
   → keep them equal yourself.
5. **`yarn catalog` exits 1 with `duplicate flow id`** → two specs claim the same id → take the next
   free one for that area from `FLOWS.md`.
6. **CI fails at "Flow catalog is current", before any container starts** → a title, tag or
   annotation changed without regenerating → `cd e2e && yarn catalog` and commit `FLOWS.md` in the
   same commit as the spec.
7. **`catalog:check` disagrees between two machines** → `FLOWS.md` was hand-edited, or annotation
   strings carry trailing whitespace or markdown the repo's prettier reflows → regenerate, never
   hand-write; keep annotation strings plain.
8. **`yarn catalog` fails on `Map.groupBy`** → Node older than 21 → the workspace requires Node
   ≥ 22.18. The catalog needs `node_modules` but not a running stack.

## Timeouts and waiting

9. **A slow run dies as a bare test timeout and you cannot tell which poll ran out** → chained
   budgets outran the 120 s per-test default → call `test.setTimeout(...)` as the first statement,
   sized as the sum of the budgets plus navigation plus each `UI_READY`, with the arithmetic in a
   comment.
10. **A UI step passes alone and flakes in the full suite** → the 10 s expect default was used for a
    browser wait → pass `{ timeout: UI_READY }` (60 s) on every UI `expect`, `toHaveURL` and
    `waitForResponse`. Three local workers slow the single-Granian backend several-fold.
11. **An eval assertion times out with no failure message worth reading** → the flow polled a row
    _count_ → poll a terminal state (`.toBe('completed')`) so "arrived and failed" is distinguishable
    from "not arrived yet".
12. **A CDC-fed read never returns** → the wrong budget → `POLL.CDC_VISIBLE` (180 s) covers every
    PeerDB-mirrored table; `POLL.SPAN_VISIBLE` (15 s) only covers what fi-collector writes directly.

## ClickHouse and Postgres

13. **A ClickHouse read returns a stale row** → no `FINAL` → add it. `spans`, `traces` and every
    CDC-fed table are ReplacingMergeTree, and a row updated through a lifecycle lands as several
    versions.
14. **A `spans` lookup by trace matches nothing** → `spans.trace_id` bound as a UUID → bind
    `{t:String}`; it is the dashed UUID as a string.
15. **A curated-trace lookup fails on an unknown column** → `traces` has no `trace_id` column → it is
    keyed by `id`, bound `{t:UUID}`.
16. **A count comparison is always false** → ClickHouse `count()` arrives as a string in JSONEachRow
    → `Number(rows[0].n)`.
17. **The curated `traces` row is missing right after the spans appear** → the collector writes it as
    a separate best-effort insert after the span batch → give it its own
    `expect.poll(..., POLL.SPAN_VISIBLE)`.
18. **`probe.apiList` returns `undefined` or throws on a valid endpoint** → it hard-codes the
    `{ result: { table } }` envelope (`e2e/lib/state-probe.ts:StateProbe.apiList`) → call
    `actor.api.get<T>()` and type the real envelope.
19. **`yarn typecheck` rejects a `probe.pg<Row>(...)` call whose `Row` is an `interface`** → `pg`'s
    type parameter is constrained to `pg.QueryResultRow`, and an `interface` has no implicit index
    signature, so it does not satisfy the constraint → declare the row shape as a **type alias**
    (`type Row = { id: string }`), not an `interface`. `probe.ch<T>` has no such constraint, so the
    same shape works there either way.

## Isolation and assertions

20. **An assertion passes for the wrong reason, or breaks when another spec runs first** → it
    anchored on "the first row", a count, or an empty state → `actor` is worker-scoped and shared by
    every spec that worker runs, and nothing is ever deleted. Mint
    `e2e-<flow>-${testInfo.workerIndex}-${Date.now().toString(36)}` and assert on exactly that;
    measure a delta where prior rows already satisfy the check.
21. **A filter assertion passes with the filter broken** → `toContainText` → assert the exact set
    with `toHaveText([...])` / `toEqual([...sorted])`.
22. **A second custom model create returns `MODEL_NAME_ALREADY_EXISTS`** → the org already registered
    that `(model_name, provider)` and `actor` is shared across specs in a worker → use a different
    gateway-mapped model name than the neighbouring flow does.

## Stack, auth and containers

23. **Provisioning 400s, or login is blocked by reCAPTCHA** → the stack was addressed as
    `127.0.0.1` → use `localhost`; the bypass keys off `Host` containing `localhost`, with the
    `futureagi.com` signup domain as the second belt. Attach mode against a non-OSS stack fails here
    by design.
24. **A saved custom model fails at create, not inside the workflow** → the backend runs a live
    completion before persisting, and the judge runs in the `worker` container → its `api_base` must
    be `http://agentcc-gateway:8080/v1`, not a host URL. `E2E.*` are host addresses that
    container-side code cannot resolve.
25. **An eval never completes against the mock** → a shipped template was used → they all need the
    judge to compose its own JSON. Author a template whose instructions are a literal JSON reply, map
    `{{output}}` to `fi.span.kind` on the **child** span, and set `sampling_rate: 100` (the form
    defaults to 50).
26. **A spec fails on a managed stack against code that exists in your branch** → managed mode runs
    published `:latest` images → `bin/e2e build backend|frontend|collector` and boot with the printed
    version variable. CI is not the same: it always builds the frontend from source, but a backend,
    collector or gateway your PR does not touch still runs as the released image — so an `e2e/`-only
    PR exercises your frontend against a released backend. The `docker-compose.dev.yml` overlay is
    not a substitute — it hardcodes `FAST_STARTUP`, which skips migrations.
27. **Annotation-score or simulate assertions never see a row** → `model_hub_score` and
    `simulate_agent_definition` mirrors cannot be created on a fresh stack (known drift, allow-listed
    as a warning in `bin/e2e`) → do not assert on those surfaces, and do not assert on unfiltered
    eval graphs, which read a dropped table.
28. **A harness self-test fails and takes your PR with it** → `harness/**` runs in every
    `bin/e2e test` and in CI alongside the flows → run `bin/e2e test harness/` first after a boot to
    separate "the stack is wrong" from "my flow is wrong".

## Tooling

29. **A spec with a type error runs anyway** → Playwright strips types without checking, and CI runs
    neither `yarn typecheck` nor `yarn selftest` → run `cd e2e && yarn typecheck` yourself. The
    tsconfig covers `lib`, `flows` and `harness` only; a helper anywhere else is not typechecked.
30. **Passing `--grep-invert` un-quarantines the suite** → the CLI flag _replaces_ the config's
    `grepInvert` → use `E2E_INCLUDE_QUARANTINED=1` or `E2E_LIVE_LLM=1` instead.
31. **A quarantined flow starts running again on its own** → `expires` is a plain date-string compare
    and the 45-day cap is not enforced by code → quarantine is a countdown; re-check it, do not let
    it lapse silently. A missing or unparseable `.quarantine.json` fails open and quarantines
    nothing.
32. **`bin/e2e test` fails before Playwright starts** → `yarn install` or
    `npx playwright install chromium` was never run in `e2e/` → do both once. CI installs with
    `--frozen-lockfile`, so never bump `package.json` and the lockfile independently.
