synced-from: internal-docs@6cd9e3b (2026-08-27)

# Standards checklist

Distilled rules, grouped by review lens. Load the section for the lens you are working — not the
whole file.

Rules a linter or formatter already enforces are **not** here: the mechanical gates in step 4 cover
import order, formatting, unused names, bare `except:`, missing React keys, and exhaustive-deps
warnings. What remains is what a machine cannot decide.

`§ref` legend — `01` minimal code · `02` API design · `03` architecture & layers · `04` OSS/EE
boundary · `05` typing · `06` errors & failure modes · `07` pull requests & review · `08` naming &
git · `09` testing & scenarios · `10` API contracts & types · `PB` review playbook · `CB` review
bible. Severity is the **default** for a rule; the actual call depends on blast radius and on
attribution (see the foot of this file).

## Migrations & data

| §ref | Rule                                                                                     | How it shows in a diff                                                                                                    | Severity |
| ---- | ---------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- | -------- |
| PB   | A migration must not depend on an enterprise-only app                                    | `dependencies = [("<ee app>", …)]` in a migration outside the enterprise tree; an in-function try/except does not save it | P0       |
| PB   | A new migration parents on the app's current tail                                        | `makemigrations --check --dry-run` reports two leaf nodes                                                                 | P0       |
| PB   | Large-table migrations set `atomic = False` and batch                                    | `RunPython` iterating a large table, or `.update()` over a full queryset, in one transaction                              | P1       |
| PB   | One bad row must not abort the whole run                                                 | `RunPython` loop with no per-row error handling                                                                           | P2       |
| PB   | Never backfill guessed values into log, audit or append-only tables                      | Migration computing approximations into history; prefer null                                                              | P2       |
| PB   | Migrations are idempotent, re-runnable and reverse-safe                                  | `RunPython` without `reverse_code`; inserts with no uniqueness guard                                                      | P2       |
| PB   | A data migration runs only after the reading code is deployed everywhere                 | PR body silent on deploy ordering for a schema-plus-code change                                                           | P2       |
| 09   | A migration PR answers: table size and lock, rollback, open-build behaviour, boots after | `migrations/*.py` in the diff and the body answers none of these                                                          | P2       |
| 09   | Cover rollback: migration down, flag off, old client still served                        | Forward-only migration with no reverse and no stated rollback plan                                                        | P2       |

## OSS/EE boundary

| §ref | Rule                                                                                           | How it shows in a diff                                                            | Severity |
| ---- | ---------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------- | -------- |
| 04   | No enterprise imports outside registered boundary modules and the enterprise tree              | An `import` of the enterprise package appearing in an open-source module          | P1       |
| 04   | The enterprise-import allowlist may only shrink                                                | The diff adds an entry to the boundary allowlist                                  | P1       |
| 04   | New code defaults to open; the enterprise tree is for genuine enterprise capability            | New files under the enterprise tree with no enterprise-only rationale in the body | P2       |
| 04   | No per-call-site guarded import plus `if X is not None` scattered through the code             | `except ImportError:` then `X = None`, then a presence check at each use          | P2       |
| 04   | A guard covers every enterprise symbol the file imports                                        | One guarded import plus a second bare enterprise import in the same file          | P1       |
| 04   | The failure arm binds every guarded name                                                       | An `except ImportError` branch that leaves one imported name unassigned           | P1       |
| 04   | Fallback stubs match the real symbol's spelling, arity and shape                               | Stub signature differs from the symbol it stands in for                           | P1       |
| 04   | Define a typed port with an enterprise adapter and an open null-object, selected once          | A capability wired by raw enterprise symbols at each call site                    | P2       |
| 04   | Fail-closed vs fail-open for a capability is decided once, in the null-object                  | Two call sites choosing different defaults for the same missing capability        | P2       |
| 04   | Gate on the deployment-mode helper at the boundary, never on an enterprise-imported symbol     | Enterprise enum or class used as the condition that gates open code               | P1       |
| 04   | Do not thread the mode check through business logic or extend open paths with inline branches  | Mode checks appearing inside services, views or serializers                       | P2       |
| 04   | The open build boots, serves, registers workflows and migrates with the enterprise tree absent | A boundary or guard change with no assertion that the open path works             | P0       |

## Contracts, types & codegen

| §ref | Rule                                                                                                        | How it shows in a diff                                                                                                                         | Severity |
| ---- | ----------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- | -------- |
| 10   | Request and response shapes live in serializers and nowhere else                                            | A view returning a dict literal, or mutating `serializer.data` after the fact                                                                  | P1       |
| 10   | Regenerate and commit the generated client artifacts in the same diff as the serializer                     | A serializer changed while the generated contract and schema files are untouched                                                               | P1       |
| 10   | Never hand-edit a generated contract or type artifact                                                       | A generated file edited without its generator header changing                                                                                  | P1       |
| 10   | Never read a typed response through a `??` or `\|\|` fallback chain                                         | Two candidate field names coalesced on an API result                                                                                           | P1       |
| 02   | Never rename a field, param, route or enum value in place                                                   | Serializer field or `source=` renamed, path string edited, enum member value changed                                                           | P1       |
| 02   | Never change a field's type or nullability in place                                                         | Field class swapped, `allow_null` flipped, model field retyped with no new sibling                                                             | P1       |
| 02   | Never make an optional input required, and never tighten validation on existing input                       | `required=True` added, default removed, choices narrowed, `max_length` reduced                                                                 | P1       |
| 02   | Never remove a field, route, param or enum value without the deprecation contract                           | Deleted serializer field or route; the schema diff shows a removal                                                                             | P1       |
| 02   | Design enums open so clients tolerate unknown values                                                        | An exhaustive frontend switch with no default over a response enum                                                                             | P3       |
| 05   | Public service and selector signatures are fully annotated; `Any` must not pass through more than one layer | A new or changed cross-module `def` missing param or return annotations; an `Any`-typed value forwarded through two or more layers unvalidated | P2       |
| 10   | A JSON field with known keys becomes a typed nested serializer                                              | New JSON field whose keys are documented, or read by name downstream                                                                           | P2       |
| 10   | New endpoints are registered in the contract-coverage set in the same diff                                  | A route added while the coverage registry is unchanged                                                                                         | P1       |

## Server/API layer

| §ref | Rule                                                                                        | How it shows in a diff                                                                                                                    | Severity |
| ---- | ------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- | -------- |
| 02   | Exhaust extend, optimize and batch before adding a route                                    | A new route whose handler re-queries resources that already have endpoints                                                                | P2       |
| 02   | Do not publish experimental, internal, debug or UI-control surface                          | A new public route or field exposing internals; the body says it may change                                                               | P2       |
| 02   | Version branching lives in serializers and request adapters, never in services              | A version check inside a service, selector or domain module                                                                               | P2       |
| 03   | Views, handlers and activities route and (de)serialize, then delegate                       | A view gaining loops, ORM filters, multi-branch policy or transaction management                                                          | P2       |
| 03   | Business logic lives in services (writes) and selectors (reads) as HTTP-free functions      | A service taking the request object, or importing serializers                                                                             | P2       |
| 03   | Never put business logic in serializers, model save, signals or managers                    | Diff touches `save()`, a signal receiver, serializer `create/update/validate`, or a manager                                               | P2       |
| 03   | Models own their own shape and self-only validation, with no cross-entity orchestration     | A model module importing services or HTTP clients; `save()` writing other rows                                                            | P2       |
| 03   | Reads do not write: a `get_*`, `resolve_*` or `find_*` never mutates state as a side effect | Such a function's body containing `.save()`, `.update(`, `.create(` or an in-place counter bump; record usage in a separate explicit call | P1       |
| 09   | New API surface denies by default and paginates                                             | A new route with no permission classes, or an unpaginated list response                                                                   | P1       |
| PB   | The declared query serializer is consumed; no raw re-parse of query params                  | Direct `request.GET` integer parsing in a view that declares a query serializer                                                           | P2       |
| CB   | Query params come from the validated container; non-numeric input 400s; page size is capped | Manual parse of paging params with no maximum enforced                                                                                    | P2       |

## Correctness & logic

| §ref | Rule                                                                                                    | How it shows in a diff                                                                                                                    | Severity |
| ---- | ------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- | -------- |
| CB   | Grade a new F821 or F405 in the gate delta as P0 — it is a probable runtime `NameError`, not a lint nit | The gate already reports it; this row sets the severity. Settle a star-import F405 by importing the module and checking the name resolves | P0       |
| CB   | A computed value must be used in the decision it feeds                                                  | A value computed then ignored while a different quantity is passed on                                                                     | P0       |
| PB   | The value recorded must equal the value that actually ran                                               | A log row or audit record writing a configured default while execution used a resolved one                                                | P1       |
| PB   | Save-then-hydrate round-trips are symmetric                                                             | Serialize and deserialize transforms that do not mirror each other; a reload that flips grouping or boolean logic                         | P1       |
| PB   | Soft-delete filters are consistent across primary path, fallback path and migration                     | A deleted-flag filter present in one branch and absent from its sibling                                                                   | P2       |
| PB   | An auth failure must never silently downgrade to a weaker path                                          | A catch around authentication falling through to an anonymous branch                                                                      | P1       |
| PB   | A guard is dead if the failure happens a layer above it                                                 | try/except around a call whose module import or graph build fails first                                                                   | P1       |
| CB   | Code must match its own tests; if they disagree, decide which is the spec                               | A new test's expectation contradicts the behaviour you traced                                                                             | P1       |
| CB   | Display-path transforms prove nothing about the wire path                                               | A hydration map changed with no matching change to the request builder                                                                    | P2       |
| CB   | Empty and zero inputs fall through sanely                                                               | A `None` stringified into a query; an empty collection rendered as an empty `IN ()`                                                       | P2       |
| PB   | Use `git diff -w` to surface logic hidden inside reindents                                              | A reindent hunk that still contains changed statements                                                                                    | P2       |

## Data modelling

| §ref | Rule                                                                            | How it shows in a diff                                                                     | Severity |
| ---- | ------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ | -------- |
| PB   | A queryable dimension is a column or foreign key, not a key inside a JSON blob  | Filtering on a nested JSON key with no functional index                                    | P2       |
| CB   | Filterable dimensions such as status, mode or version are columns               | List filters reading JSON keys                                                             | P2       |
| CB   | No schema-less blobs across layers                                              | A dict written in one module and read by key in another with no declared type              | P2       |
| 03   | Pass the object or key you already hold; do not launder identity and re-query   | An id passed down, then re-fetched with `.filter(...).first()` — unordered, over many rows | P2       |
| 03   | Return values; do not mutate arguments, `self` or the request as a side channel | A helper setting attributes on the request and returning nothing                           | P2       |

## Performance

| §ref | Rule                                                                                    | How it shows in a diff                                                                 | Severity |
| ---- | --------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------- | -------- |
| 02   | Kill N+1s: annotate aggregates, and prefetch everything the serializer touches          | A method field or nested serializer dereferencing a relation with no matching prefetch | P2       |
| 02   | No unbounded full-queryset serialization, and no per-item external call in a loop       | A whole queryset handed to a many-serializer; an HTTP or model call inside a `for`     | P2       |
| 02   | Every collection endpoint paginates by default                                          | A list view with pagination disabled, or a bare many-serializer response               | P2       |
| PB   | No deep offset paging; paginate the heavy part, charts included                         | Offset growing with page number; a chart endpoint scanning the whole period            | P2       |
| PB   | No payload bloat: keep truncation on multi-kilobyte per-row blobs                       | A removed slice on serialized text; a new filter on an unindexed column                | P2       |
| CB   | Aggregate in the database, not in a Python loop over the period's rows                  | A `for` loop accumulating counts or sums in a view or service                          | P2       |
| CB   | No export that materializes a whole table in memory                                     | A full queryset realized into a list inside an export view                             | P2       |
| CB   | Cache changes state the staleness window and blast radius; the key includes every input | A cache key missing the tenant, mode or version the value depends on                   | P2       |

## Errors & failure modes

| §ref | Rule                                                                                               | How it shows in a diff                                                                | Severity |
| ---- | -------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------- | -------- |
| 06   | Fail-open or fail-closed is chosen on purpose, stated at the call site, consistent across siblings | A new try/except with no comment or log stating the stance                            | P2       |
| 06   | Security, permission, auth and quota checks fail closed                                            | A catch around an entitlement check that returns allowed, or treats absent as allowed | P1       |
| 06   | Best-effort side effects fail open but log the exception                                           | A catch around metering or telemetry that passes silently                             | P2       |
| 06   | Any absorbed exception is logged with its stack trace                                              | `except Exception:` followed by pass, return or continue with no exception log        | P2       |
| 06   | Catch the specific exception you can handle                                                        | A broad `except Exception` in new code where a specific one is available              | P2       |
| 06   | Re-raise what you cannot meaningfully handle                                                       | A catch converting an unrecoverable error into a default return                       | P2       |
| 06   | Initialize to a safe default before any try, branch or loop that might assign it                   | A name assigned only inside a `try` and read in the `except` or after it              | P1       |
| 06   | Make the null branch a real handled case: log, return or raise                                     | A default of `None` whose attribute is then read unconditionally downstream           | P2       |
| 06   | Wrapping existing code in a new conditional is a behaviour change; call it out                     | Previously unconditional statements now under a new guard, with a silent body         | P2       |
| CB   | Swallowed errors log at warning or exception level, never debug, on billing or auth paths          | A debug-level log inside a catch on a money or access path                            | P2       |
| CB   | Streaming and long responses handle or signal errors after headers are sent                        | A streaming generator with no error sentinel                                          | P2       |

## Frontend

| §ref | Rule                                                                                        | How it shows in a diff                                                                   | Severity |
| ---- | ------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- | -------- |
| PB   | A refactor must not drop behaviour versus base                                              | `git diff -w` shows removed handlers, props or branches in a PR labelled refactor        | P2       |
| PB   | No local fork of a shared export                                                            | A copied helper alongside a comment discouraging re-import of the original               | P2       |
| PB   | Delete dead frontend code                                                                   | Config arrays that are always empty; hook fields the API never returns; orphaned banners | P3       |
| PB   | URL and local-storage persistence preserve the user's choice and never erase unknown values | A decoder that drops unrecognized tokens; a write that overwrites with partial state     | P2       |
| PB   | An `eslint-disable` is a silenced alarm — fix the smell instead                             | A disable comment added, especially for prop types or component-export rules             | P2       |
| CB   | Effects are not a state machine                                                             | Competing hydration effects coordinated by refs and dirty flags                          | P2       |
| CB   | Dependency arrays are honest; a very long one is a refactor signal                          | An effect with a sprawling dependency list, or a silenced exhaustive-deps warning        | P2       |
| CB   | Every mutation invalidates exactly the query keys it stales                                 | A mutation with no invalidation, or one that invalidates everything                      | P2       |
| CB   | Component files stay component-sized; helper files do not export components                 | A helpers module exporting markup; a component-export rule disabled                      | P3       |

## Comments

| §ref | Rule                                                                  | How it shows in a diff                                                                  | Severity |
| ---- | --------------------------------------------------------------------- | --------------------------------------------------------------------------------------- | -------- |
| PB   | No stale or lying comments asserting behaviour the code does not have | A comment describing what a value tracks beside code recording something else           | P2       |
| PB   | No commented-out code, and no leftover TODO, FIXME or HACK            | Commented-out blocks or a new marker comment with no ticket                             | P3       |
| 01   | Comments are held to the same liability bar as code                   | A paragraph of justification added above a one-line change; that belongs in the PR body | P3       |

## Naming & git

| §ref | Rule                                                                         | How it shows in a diff                                                       | Severity |
| ---- | ---------------------------------------------------------------------------- | ---------------------------------------------------------------------------- | -------- |
| 08   | One ticket per branch, and the ticket is linked so it closes on merge        | The body has no closing reference, or carries an unfilled placeholder        | P2       |
| 08   | Identifiers match the surrounding idiom: casing, prefixes, domain vocabulary | A foreign casing convention, or a new synonym for an existing domain term    | P3       |
| 08   | Name for meaning, not type; no undecodable abbreviations                     | Names such as `tmp`, `data2`, `orig`, or single letters outside a tight loop | P3       |
| 08   | Booleans read as predicates; functions read as verbs                         | A boolean without an `is_`/`has_`/`can_` prefix; a function named as a noun  | P3       |
| 08   | Rename when meaning drifts — a stale name is a lie that compiles             | A function body changed so its name no longer describes it                   | P3       |

## File size & structure

| §ref | Rule                                                                            | How it shows in a diff                                                                         | Severity |
| ---- | ------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- | -------- |
| PB   | Do not pile onto monster files or hundred-line methods; push work into services | Lines added to a module already thousands of lines long, or to an oversized method             | P2       |
| CB   | This PR must not make the biggest file bigger                                   | The diff's largest addition lands in the app's largest existing module                         | P2       |
| 03   | If a unit needs "and" to describe, split it                                     | One function that parses input, decides policy and persists; its test needs everything at once | P2       |
| 03   | Prefer narrow typed arguments over keyword grab-bags                            | A new signature taking `**kwargs`, or a dict parameter with documented keys                    | P2       |
| 03   | No shared utils, helpers or base class before a second real caller              | A new helpers module or base class with a single consumer                                      | P3       |

## Testing & scenarios

| §ref | Rule                                                                                          | How it shows in a diff                                                             | Severity |
| ---- | --------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- | -------- |
| 09   | Tests land in the same PR as the code; no "tests in a follow-up"                              | Source files changed, no test files changed, and the body promises tests later     | P2       |
| 09   | A PR with no tests and no stated reason is not ready for review                               | No test diff and no justification anywhere in the body                             | P2       |
| 09   | Cover the failure and fallback path first, not just the happy path                            | New error branches with no test that reaches them                                  | P2       |
| 09   | A test must fail if the change is reverted                                                    | Assertions that do not touch the changed behaviour; status-code-only checks        | P2       |
| 09   | A manual test-plan checkbox is not a test                                                     | The body says "tested manually" or ticks a checklist with no test file in the diff | P2       |
| 09   | Cover boundary, empty and null: zero, one, max, off-by-one, absent where a value was expected | No test with an empty collection or a null input for the changed function          | P2       |
| 09   | Cover invalid input: malformed, out-of-range, wrong type, hostile                             | New input parsing with no negative test                                            | P2       |
| 09   | Cover dependency failure and assert the chosen stance                                         | A new external call with no test of its failure                                    | P2       |
| 09   | Cover concurrency, idempotency and re-run: the second delivery is safe                        | A new emit, charge or create path with no duplicate-call test                      | P2       |
| 09   | Cover the open build with the enterprise tree stripped, as a real assertion                   | A boundary change with no test that runs without the enterprise package            | P2       |
| 09   | Cover realistic volume, not three rows                                                        | A list or aggregation change tested only at toy scale                              | P2       |
| 09   | A bug fix adds the exact reproducing test                                                     | A fix PR with no new test function                                                 | P2       |
| CB   | Tests assert content, not just status; assert what is returned and what is excluded           | A test body whose only assertion is a status code                                  | P2       |
| CB   | Do not stub out the exact path the test claims to protect                                     | A patch applied to the resolver under test, leaving its real branches uncovered    | P2       |

## PR hygiene

| §ref | Rule                                                                                  | How it shows in a diff                                                                             | Severity |
| ---- | ------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- | -------- |
| 07   | One PR, one logical change; split it if the title needs "and"                         | A title joining two changes; a diff spanning unrelated modules                                     | P2       |
| 07   | Size the PR so a reviewer can read, reason about and QA it in about a day             | A diff in the thousands of lines with mixed concerns                                               | P2       |
| 07   | Bulk reindents, formatter sweeps, moves and renames go in their own commit or PR      | `git diff --stat -w` far smaller than `git diff --stat`                                            | P2       |
| 01   | No drive-by refactors or "while I'm here" cleanups bundled with a feature change      | Hunks in files unrelated to the ticket                                                             | P2       |
| 07   | Stacked PRs each build, pass and stand alone                                          | The body says tests pass only once another PR lands                                                | P2       |
| 07   | The body states what changed, why, and how it was verified                            | An empty or untouched template body; no verification section                                       | P2       |
| PB   | Body claims must match the diff                                                       | A claimed change absent from the diff, a test count that does not match, or a claim already merged | P2       |
| PB   | The type-of-change declaration matches the diff                                       | A feature diff declared as a bug fix                                                               | P3       |
| PB   | Check for another open PR touching the same files with the opposite approach          | Overlapping open PRs on the same paths                                                             | P2       |
| PB   | The branch must not be behind its base; verify base currency before trusting the diff | A large behind-count from the left-right rev-list                                                  | P2       |
| CB   | Cross-repo coupling makes deployment ordering part of the review                      | A referenced paired change elsewhere with no ordering statement                                    | P2       |

## Infra & rollout

| §ref | Rule                                                                                       | How it shows in a diff                                                                   | Severity |
| ---- | ------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------- | -------- |
| CB   | Name the images and services that build from a touched Dockerfile or compose file          | A shared build file edited without the body listing its consumers                        | P2       |
| CB   | No per-build work that belongs in the base image                                           | Dependency installation placed after the source copy                                     | P2       |
| CB   | Flag irreversible state transitions such as a database major-version bump on a live volume | An image tag major bump on a volume-backed data service                                  | P1       |
| CB   | One change per pin: a version bump and a base-image switch are two changes                 | A tag diff changing both the version and the variant suffix                              | P2       |
| CB   | Config mounts read-only; compose defaults explicit; empty-variable fallbacks fail loudly   | A mount without the read-only flag; a default that silently points at the wrong database | P2       |
| CB   | State dirty-data interaction, deploy ordering, rollback and retry idempotency              | A migration, billing or emit change whose body answers none of these                     | P2       |
| 01   | Justify every new dependency, abstraction or pattern with a present requirement            | A new package added; a new base class, registry or plugin hook with one user             | P2       |

## Security

| §ref | Rule                                                                                          | How it shows in a diff                                                                                      | Severity |
| ---- | --------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- | -------- |
| 08   | Never commit secrets or known-default tokens                                                  | Placeholder credentials or high-entropy strings in config, env or compose files                             | P0       |
| 08   | Committed config references a secret's name, never its value                                  | A literal key or password in settings, compose, or a committed env file                                     | P0       |
| CB   | No secret defaults that look real; credentials are encrypted at rest, not merely encoded      | Base64 applied to a credential; a realistic-looking key literal as a default                                | P1       |
| CB   | Escape user-controlled strings for their sink                                                 | CSV writes without neutralising leading formula characters; interpolated SQL; shell invocation with a shell | P1       |
| CB   | Authorization scope comes from the membership-checked queryset, never a raw tenant filter     | A filter taken straight from a request header with no membership check                                      | P1       |
| CB   | No network rule wider than needed; no password-less user beyond localhost; host ports audited | An open CIDR in compose or a security group; trust authentication enabled                                   | P1       |
| CB   | Egress from sandboxed execution is proxied or allowlisted                                     | Sandbox network configuration with unrestricted outbound access                                             | P1       |

---

## Severity

- **P0 — blocks.** Broken new code this PR introduced, the open build cannot deploy or migrate, data
  loss or corruption, a committed secret.
- **P1 — blocks.** A verified correctness or contract bug: wrong value recorded, response drift, a
  crash on a scheduled path, a boundary that breaks the open build, undeclared E2E coverage on a
  behaviour change.
- **P2 / P3 — should fix, does not block.** Edge cases, performance, consistency, hygiene.

The severity in each row is the default. Raise it when the blast radius is money, access or data;
lower it when the affected path is unreachable in practice — and say which you did.

## Attribute every finding

Every finding carries one of three labels, and only the first may block:

- **introduced by this PR** — the diff created it.
- **pre-existing, touched** — it is in a file or function this PR changes. Worth raising, worth
  fixing here, but it is not what this PR broke.
- **pre-existing, elsewhere** — found while reading around the change. Report it once, at the bottom,
  and do not let it lead the review.

A pre-existing finding does not become blocking because it is severe, and an introduced finding does
not become non-blocking because the PR already merged. Merge status changes the remedy, not the
attribution.
