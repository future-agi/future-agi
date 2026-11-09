# F05 money preparation

**Source-prepared, BLOCKED for private-ledger/runtime admission.** Import `f05`
from `e2e/journeys/fixtures/index.ts`, or named exports from this directory's
`index.ts`. Imports perform no network, DB, filesystem, environment or process
operations. `journeys-main-708` is the sole shared-fixture writer.

This package now supplies a **source-bound private ledger projection**, a
single-connection repeatable-read/read-only capture adapter, exact private-money
and replay oracles, **display-only** public wallet/statement reconciliation, and
a **non-dispatchable ledger-service grant plan**. `private-mapping.json` is the
column/semantic authority with model:line and migration citations. The mapping
is partial because the candidate has **no durable release/void, spend-person or
complete immutable-dispatch journal** to project. Those fields fail closed.

This is source preparation, **not independently reviewed or runtime admitted**.
No actual grant, payment fulfilment, worker replay, commit-before-ack proof,
financial acceptance or WALKED_PASS is claimed. Controls were committed before
their implementation; **all execution stays NOT_RUN** in this static-only lane.

## Source binding

Read-only source root:
`<WORKSPACE>/omega-launch/worktrees/fa-dev-int-708j-early`

Current F05 candidate: `a0372df842d35c91c0c6fa9a98a96c6199ccd436` (708J r2).
The continuation reads actual private models/migrations/services at that head;
all new citations are in `private-mapping.json`. No private column is guessed.
The qualified schema remains an explicit deployment binding, not an assumed
`public` namespace. Private source was read only; no candidate file was changed.

The following table is **historical public-contract provenance at the original
head `da4c81cc623f909ce38f53e01b557ee8374d5adb`**, not a new r2 runtime receipt.
The independently confirmed gateway public contract digest is
`0a68726266f903edc8a92d5ad36922197d06ba09ac9814a4d67a17bf45839aaf`, with reader
`8034fcdfc25d6cbb1e416e4febc89ef2cfa372b1`. That reader pins the old source head;
rebind/review against the composed tuple before integrated execution.

| Boundary                     | Source citation                                                                                                        | Meaning                                                                                                                                                                                                 |
| ---------------------------- | ---------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Account table and tenant     | `e2e/flows/gateway/managed-optin-credits.spec.ts:163-169`                                                              | Existing account SELECT uses `usage_managedcreditaccount`, `organization_id`, granted and settled totals. It does not prove a deployed schema name.                                                     |
| Account version/ack          | `futureagi/agentcc/management/commands/agentcc_managed_credits.py:120-146`                                             | `version` and `gateway_acked_version` are the public account-read contract. CLI commentary about retry behavior is not adopted as runtime truth.                                                        |
| Account timestamp            | `futureagi/agentcc/views/managed_credits.py:503-515,533-540`                                                           | Account updates set `updated_at`; push is a distinct ledger operation. F05 only reads.                                                                                                                  |
| Balance decomposition        | `futureagi/agentcc/views/managed_credits.py:323-374`                                                                   | Available credit includes wallet-funded platform spend, not merely granted minus gateway spend. Missing account defaults in the API are not absence proof.                                              |
| Grant route                  | `futureagi/agentcc/urls_billing.py:51-55`                                                                              | POST `/agentcc/managed-credits/grant/`.                                                                                                                                                                 |
| Grant body                   | `futureagi/agentcc/serializers/managed_credits.py:75-76,157-163,315-328`                                               | Explicit org, exact six-place USD amount, note, idempotency key. Positive micros, USD maximum 1,000,000,000.                                                                                            |
| Grant authority              | `futureagi/agentcc/views/managed_credits.py:977-992`                                                                   | Staff session or gateway admin authority; **no API key**, including a staff person's key. F05 chooses a future isolated staff-session adapter, never imports an admin token.                            |
| Ledger-service seam          | `futureagi/agentcc/views/managed_credits.py:1019-1051`                                                                 | Actual `ledger.grant(org, amount, idempotency_key=..., actor=..., note=...)`; not a balance UPDATE. First create 201, identical replay 200; differing amount must conflict.                             |
| Public entry projection      | `futureagi/agentcc/serializers/managed_credits.py:439-475,544-551`                                                     | Includes request/provider/cost_source/markup; omits private cost/key/author/note columns and does not expose per-request release, terminal count, snapshot or full idempotency lineage.                 |
| Complete statement semantics | `futureagi/agentcc/views/managed_credits.py:378-438`; `futureagi/agentcc/serializers/managed_credits.py:63-73,487-519` | UTC half-open month; opening is all prior entries; closing includes every kind, including `usage_debit`; only `spend` groups by model **and** estimate flag. All eight credit kinds remain distinct.    |
| Settlement wire contract     | `api_contracts/gateway/examples/managed-settlement-v1.schema.json:5-85`                                                | Redis outbox spend/void strings contain request/key/run, hold/billed/markup, provider/cost_source, snapshot and policy. **Not** a private PostgreSQL projection or evidence that a worker committed it. |
| Existing reader limitation   | `e2e/lib/state-probe.ts:22-28,55-57`                                                                                   | Generic PG pool/query; it does not enforce a loopback URL, read-only role, transaction snapshot or no-provider socket boundary. F05 does not instantiate it.                                            |

The upstream request is
`<WORKSPACE>/futureagi/tmp/journeys-708j/FOUNDATION.json:146-156` (F05).
It explicitly requires an owner-supplied reviewed projection and prohibits
invented private columns. The earlier F09 preparation also records the missing
composed 733 identity contract in `../f09/README.md:23-46`. No historical
component receipt substitutes for this early tuple's missing runtime binding.

## PROJECTION contract and fail-closed boundary

`F05_PROJECTION_REQUIREMENTS` remains the full **consumer requirement**, not a
claim that the candidate stores every requested field.
`F05_CANDIDATE.projectionBinding` is `SOURCE-MAPPED-PARTIAL-REVIEW-OWED`.
`private-mapping.json` distinguishes mapped columns from unsupported semantics.
The following requirements remain the acceptance bar; absence is never zero.

| Required per-request output                   | Required semantics                                                                                                                                       |
| --------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Org/person/key/run/request identity           | Exact test-owned cohort, immutable server attribution; not a key's current mutable run binding.                                                          |
| Estimate/hold/debit/release/settlement/refund | Separate integer-micros observations at each actual boundary; no inferred release from aggregate account movement or a guessed hold-minus-bill equation. |
| Idempotency and terminal multiplicity         | Real persisted identity and terminal count across retry/cancel/race/replay; distinguish pending, spend, void and refusal.                                |
| Snapshot and policy                           | The actual dispatch-bound catalogue snapshot/policy version; never the current catalogue.                                                                |
| Provider, `cost_source`, markup               | Settlement-bound provider/source, `markup_micros`, `markup_bps`; preserve null/unknown rather than defaulting.                                           |
| Account push                                  | `updated_at`, version/ack plus real push outcome/time and gateway readback; version equality alone is not liveness.                                      |
| Statement completeness                        | Independent full cohort, complete pre-month opening history, all monthly entries and all pages, observed in a consistent snapshot.                       |
| Replay/fault evidence                         | Real worker commit before acknowledgement, durable replay and exactly-once settlement/void, not a fabricated row or pass-producing callback.             |

`privateLedgerQuery(scope)` still raises `F05-PRIVATE-PROJECTION-UNBOUND`.
The explicit `privateLedgerQuery(scope, PRIVATE_PROJECTION)` overload emits only
the fixed schema-qualified, tenant-bound private SELECT. Cloned descriptors,
caller SQL, arbitrary columns and forged scopes fail. The constant is source
binding, **not an independent review receipt**. `requirePrivateFields` refuses
release/hold/terminal-void/person/R14/unknown fields rather than fabricating them.

`requireF05Runtime(candidateHead): never` still raises the same error at this
head, and `F05-CANDIDATE-UNBOUND` at any other head. There is no override or fake
signed admission. The future journey runner must pass runtime admission before
provisioning or connecting. The source capture adapter and data-only controls
are not permission to bypass that gate or to execute during this static lane.

### Private snapshot adapter and exact-money boundary

`capturePrivateLedger(scope, PRIVATE_PROJECTION, admittedLocalPool)` is prepared
for the guarded integrated venue. The pool must explicitly name a numeric
loopback host and an isolated `e2e_` database; connection strings, custom streams
and implicit environment discovery are refused. It leases **one connection**,
begins `REPEATABLE READ READ ONLY`, and checks isolation/read-only state, database,
backend PID and snapshot before/after. Schema columns/types, global idempotency
constraint, migration identities, account and full org history share that
transaction. It always rolls back/releases; failure destroys the lease.

A fixed **zero-row** `UPDATE ... WHERE false` inside a savepoint must fail with
SQLSTATE `25006`; success invalidates the capture. This is an attempted-write
negative control, never a balance setter. The real native control is NOT_RUN.
Every monetary integer is cast to SQL text before driver decoding. NULL stays
NULL. Captures over 10,000 rows fail, never silently paginate/truncate. No notes,
Stripe refs, upstream-key IDs, raw attempts JSON or credentials are selected.

`exactPrivateMoney` accepts only private-schema-normalized entries and known
micros fields. Object brands protect against accidental public-float use, not
fabrication/authenticity: synthetic controls are not financial evidence.
`assertReplayPayload` rejects changed amount/context under the same identity;
`assertIdempotentReplay` checks all projected immutable fields and row IDs.
The product currently verifies only org/kind on replay, and its drain logs a
billed mismatch then ACKs. This oracle **detects** that discrepancy; it does not
claim the product refuses conflicting payloads or quietly repair the ledger.

`assertWalletDisplay` joins by entry UUID plus exact request/kind and tenant
context, checks full supplied cohort/null parity, and compares public floats
only at an explicit display precision. `privateStatement` computes exact totals
from **capture.entries**, never `requestEntries` or a public page;
`assertStatementDisplay` checks public statement display totals and exact
model/estimated/count joins. Both public comparisons return `DISPLAY_ONLY` and
`moneyReconciled=false`, even at six display places. Exact-money assertions use
private integer values only. Full-history completeness and the public barrier
remain distinct runtime obligations.

Legacy request joins use exact `(organization_id, request_id)`. No suffix
parsing/compacted attempt index yields immutable financial identity. R14's
supplementary DARK design needs its own composed journal/public-row/ledger tuple
mapping, including evicted dispatches. Durable void/release and spend-person
attribution do not exist in the inspected ledger model. `created_by` is not a
substitute for the person who dispatched a request.

No signature protocol is established by the public candidate. Original-looking
and tampered signature fields are both rejected. These are **structural refusal
controls, not cryptographic signature verification**; real signed payment
webhook controls belong to F12's accepted TEST boundary. There are no card values
or payment-provider calls in this fixture.

## Inputs and outputs

### `moneyScope(input): MoneyScope`

Exact input keys: `candidateHead`, `runId`, `organizationId`, `personId`,
`apiKeyId`, `keyType`, `fixtureKind`, `apiOrigin`, `databaseSchema`, `requestIds`.

- IDs are nonzero lowercase UUIDs. Request cohort is nonempty, duplicate-free,
  at most 200 UUIDs. `keyType` is exactly `user`; `fixtureKind` is exactly
  `test-owned-loopback`. These declarations do **not** prove actual ownership.
- `apiOrigin` is an HTTP literal loopback origin (`localhost`, `127.0.0.1`,
  `[::1]`), optional valid port; no userinfo, query, path, fragment or numeric
  alias. This is not a DNS/socket guard.
- `databaseSchema` is an explicit deployment-supplied identifier, restricted to
  lowercase letters/digits/underscore, starting with a letter or underscore,
  at most 63 characters. No default/search-path assumption. A syntax-valid
  schema is not proof of database ownership; runtime must bind the real isolated
  DB identity and schema. `public` in controls is synthetic oracle input only.
- Output is a detached frozen in-process scope, with a frozen request cohort.
  Copies/serialized scopes are rejected by downstream helpers. This brand is
  accidental-misuse protection, not authorization or a signed attestation.

Only plain data is accepted. Unknown fields, symbol keys, accessors, sparse or
property-extended arrays fail; error messages contain fixed codes only. Data
must originate from trusted JSON decoding, not executable Proxy objects.
Arbitrary strings cannot be reliably classified as real secrets: do not load
secrets in the first place. Credential fields are structurally forbidden; no
hashing, logging or after-capture redaction is supplied.

### Account read-only probe descriptor and oracle

`accountQuery(scope): ReadOnlyQuery` returns frozen `{text, values}`. It SELECTs
only organization ID, granted total, settled spend, version, ack version and
UTC `updated_at` from the explicitly quoted schema-qualified account table,
using `$1::uuid` for the tenant. Int64 values are returned as text. The timestamp
uses six fractional digits so microsecond changes are not lost through JS Date.
There is no database URL, executor callback, caller SQL or `SELECT *`.

`assertAccount(scope, rows)` requires exactly one row with exactly those six
fields. Missing/duplicate/foreign rows fail, never become zero. Canonical signed
int64 strings are required; settled spend and versions must be nonnegative;
ack greater than account version is inconsistent. Output has safe org ID,
integer strings, timestamp and `pushState=acked|pending` derived from version
comparison only. Granted total is signed because adjustments can create debt.
No available-balance inference is made without platform-spend accounting.

`assertAccountUnchanged(scope, before, after)` compares **every** projected field,
including timestamp and both versions. It is a two-boundary equality witness,
not proof that a transient write and restore never occurred. Actual no-write
proof additionally needs the admitted producer's mutation/transaction witnesses.

The future runner must bind a read-only DB role/transaction, repeatable snapshot,
timeouts and fail-closed socket guard before execution. Query errors or absent
schema/account must remain failures, not successful empty/zero captures.

### `grantPlan(scope, {amountMicros, grantId})`

Returns a frozen plan with `dispatchable=false`, `authority` set to
`isolated-staff-session-required`, loopback origin, POST, the source-pinned grant
path and body `{org_id, amount_usd, note, idempotency_key}`. Input amount is a
canonical positive integer string, at most 1,000,000,000,000,000 micros. `grantId`
is a UUID chosen once for the logical grant and reused on replay. The key is
`e2e-f05-<run UUID>-<grant UUID>`. Callers must not reuse a logical grant UUID for
a different org/amount or mint a new UUID to hide a failed replay. Changed amount
under the same key must exercise the actual product's conflict behavior.

The body labels the credit **synthetic, not payment fulfilment**. No API key,
admin credential, session, raw secret, card, signature, balance setter or direct
SQL mutation is accepted. There is no grant execution function. An admitted
adapter still owes actual staff login/non-retaining use, 201/200/conflict and
idempotency assertions, authoritative rereads and cleanup via the ledger service.
Do not restore by overwriting balances or deleting ledger entries.

### `usdToMicros(decimalLexeme)`

Converts an exact decimal string to a canonical int64-micros string via BigInt.
It refuses numbers, nonfinite/exponent notation, leading zeros, negative zero,
more than six places and overflow. It never rounds. The candidate's `UsdField`
passes Decimal to a renderer that converts it to binary float **on the server**.
Even preserving the original numeric token cannot recover lost micros.
`usdToMicros` is arithmetic for independently exact decimal strings, never an
exactness admission for wallet/statement JSON. Use the private SQL-text micros
projection for every runtime exact-money assertion; public numbers are display
observations only.

### `reconcileStatement(scope, input)`

This is the **legacy synthetic arithmetic oracle**, not an API wire decoder,
private DB schema or runtime financial source. It remains for the original
controls. Runtime comparison instead uses the private projection and
`privateStatement`/`assertStatementDisplay`. Exact outer keys:

- `organizationId`, `month` (`YYYY-MM`, year 0001..9998), `currency` exactly USD.
- `expectedEntryIds`: independently witnessed complete ledger entry cohort
  through the month's exclusive end, including all opening history.
- `entries`: exactly that cohort, no missing/extra/duplicate/foreign IDs, at most
  10,000. Above that bound fails closed; it is never a truncation or last-page
  success. Each row has exactly `id`, `organization_id`, `occurred_at`, `kind`,
  `amount_micros`, `model`, `markup_micros`, `usage_estimated`.
- Timestamps are valid UTC with six fractional digits. Amounts are canonical
  int64 strings, markup is nonnegative or null. Models are test-owned
  `e2e-f05-` labels (or empty for non-spend). This intentionally does not accept
  arbitrary production model names. `usage_estimated` is an explicit boolean.
- `statement` has exactly `opening_balance_micros`, `closing_balance_micros`,
  `credits_by_kind` and `spend_by_model`. All eight credit keys are mandatory.
  Each spend group has exactly `model`, `estimated`, `count`, `spend_micros`,
  `markup_micros`. Counts are positive safe integers; amounts are integer strings.

The oracle independently sums all pre-month amounts, every monthly credit kind,
monthly spend and markup grouped by `(model, usage_estimated)`, and every monthly
entry into closing. `usage_debit` affects closing but does not get incorrectly
classified as gateway model spend. Null markup contributes zero **only** to the
statement sum, matching the source aggregation; it does not certify request
markup presence. Positive debit/hold/reversal/expiry or negative grant/topup/
refund/release signs fail. Unknown kinds, bad boundaries and wrong group/count/
markup/totals fail. No floating-point amount arithmetic is used.

Output is safe `{kind: 'oracle-only-not-financial-acceptance', organizationId,
month, entryCount, closingBalanceMicros}`. An input's self-consistency does not
prove authentic/comprehensive capture. Deriving `expectedEntryIds` from the same
partial page would be circular. Obtaining the independent cohort, all pages,
private `usage_estimated` projection and consistent snapshot remain admission
requirements. Public statement comparison is now explicitly display-only, never
lossless money proof.

## Controls and completion boundary

`f05.controls.test.ts` contains F05-01..F05-18, committed before implementation:
closed runtime gate; person-only scope; loopback and schema misuse; forbidden
credentials/direct writes/signatures; getter refusal; exact account SQL;
absence/version/type/timestamp/no-write corruption; refusal to invent private
request SQL; source-bound grant plans/replay identity/amounts; decimal precision;
complete statement, grouping, tenant, count, UTC boundary, signature and secret
field controls. All literals are synthetic. No cryptographic or live secret
handling result is claimed. In an independently admitted guarded venue, the
control entrypoint is `node --test e2e/journeys/fixtures/f05/f05.controls.test.ts`.
Do not run it locally under this lane's static-only restriction.

Admission remains **BLOCKED** until main/gateway money owners deliver:

1. Independent review of this source-bound private projection and binding of
   final tuple, actual DB schema/role and capture code. Product/composition owners
   must close absent durable release/void, person-at-dispatch and complete R14
   financial-journal authority; a fixture must not invent those columns.
2. The final-733 isolated identity/ownership fixture plus a non-retaining local
   staff-session grant adapter. F09 is not runtime-ready on this early tuple.
3. Actual read-only account/request/full-statement captures with safe lossless
   amount handling, real DB snapshot and no-write witnesses; no synthetic rows
   presented as financial evidence.
4. Real worker replay and commit-before-ack fault through the existing F03/F08
   runtime owner, restored controls and exactly-once money reconciliation.
5. Main WALKS-D2's **named independent no-Skill PORT-708J-W02 G10/G12 reviewer**,
   with independence from implementers/receipt executors recorded. The role is
   ruled at OWNER-RULINGS T09; no person's appointment is invented here.
6. Executed tests and journey/fallibility/restoration receipts under the accepted
   loopback/native egress guard, then independent review. Tests-first commits
   and syntax checks alone do not prove RED/GREEN or financial acceptance.

`private.controls.test.ts` and `statement-display.controls.test.ts` add F05P-01
through F05P-18, committed tests-first. They cover float-path misuse, missing or
renamed columns, absent semantics, mismatched replay, direct-balance input,
read-only write refusal, snapshot drift, cleanup, public joins and statement
display. Synthetic transport controls do not prove PostgreSQL enforced anything;
execute the same native zero-row write probe on the admitted database later.

No original assertion or product contract was weakened (713). Normal hooks and
syntax/AST/whitespace checks only; all tests/native controls stay NOT_RUN. Private
source was read for mapping, never executed. No provider, production, payment,
cloud, browser, stack, suite or build operation is performed by authoring.
