# F12 payment fixtures

**Source preparation only; private TEST runtime admission is BLOCKED.** Import
`f12` from `e2e/journeys/fixtures/index.ts`, or named exports from this directory's
`index.ts`. `journeys-main-708` is the sole writer. No import performs network,
database, environment, filesystem or process operations. Importing does not
allocate a signing key.

This package prepares source-bound request descriptors, a deliberately synthetic
signature oracle, payment/replay/default-card/consent oracles, and a public
consent-table SELECT descriptor. It does **not** provide an accepted private
payment adapter, authentic financial receipts, a mounted checkout, a real claim
barrier, or permission to dispatch requests. No private EE source was opened.
Tests were committed first; **no tests were executed** in this static-only lane.

## Binding and source authority

Read-only candidate root:
a local checkout of the HEAD below

HEAD: `da4c81cc623f909ce38f53e01b557ee8374d5adb`. The following citations are
relative to that root and head. This is the T01 early source tuple, not the final
733 composition, a served image, an enabled cloud profile or financial acceptance.

| Boundary                    | Source citation                                                                                                                                             | Contract used                                                                                                                                                                                                                                                                           |
| --------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Public checkout seam        | `futureagi/agentcc/views/managed_credits.py:15-28,161-166,189-227`                                                                                          | `create_session(org,user,amount_usd,success_url,cancel_url)`; landing `fulfil(org,session_id)` uses the private once-only top-up path. Missing cloud billing yields 503, not purchase proof.                                                                                            |
| Checkout requests           | `futureagi/agentcc/urls_billing.py:36-49`; `futureagi/agentcc/serializers/managed_credits.py:246-256`; `futureagi/agentcc/views/managed_credits.py:829-859` | Owner/Admin checkout POST; positive cent precision, maximum USD 99,999,999.99, success/cancel URLs. Starting checkout credits nothing.                                                                                                                                                  |
| Pending and positive return | `futureagi/agentcc/views/managed_credits.py:460-467,863-886`                                                                                                | Scoped top-up ledger existence is authoritative; pending response has `session_id:null`, `status:pending`, `credited:false`. Positive response returns the confirmed ID. **GET can trigger fulfilment**, so it is not read-only.                                                        |
| Card routes                 | `api_contracts/openapi/swagger.json:93541-93739,93941-94078`                                                                                                | GET list, POST empty-body collect, PUT setup-session confirmation, DELETE card, POST card default. No invented direct card-write endpoint.                                                                                                                                              |
| Card shapes                 | `api_contracts/openapi/swagger.json:189182-189308`                                                                                                          | Public card metadata includes ID and `is_default`; confirmation uses `session_id` and returns `payment_method_id`/`set_as_default`. Collection returns a checkout URL, not an assumed session-ID field.                                                                                 |
| Public webhook route        | `api_contracts/openapi/swagger.json:94675-94689,189524-189580`                                                                                              | `/usage/v2/stripe-webhook/` is published as signature-authenticated with an id/type/data request. This does **not** expose the accepted raw signing protocol, local secret injection, event mapping or private persistence projection. The synthetic signer below is NOT that protocol. |
| Consent version/terms       | `futureagi/agentcc/auto_reload_terms.py:1-35`; `futureagi/agentcc/serializers/managed_credits.py:259-312`                                                   | Exact `auto-reload-v1` on EVERY enabled save. Missing/stale consent is refused; disabling needs no consent.                                                                                                                                                                             |
| Save/revoke atomicity       | `futureagi/agentcc/views/managed_credits.py:473-540`                                                                                                        | Shared account lock, receipt plus settings in one transaction; disable clears active version/time while preserving receipt history. No ledger total writes here.                                                                                                                        |
| Public consent table        | `futureagi/agentcc/models/auto_reload_consent.py:19-45`                                                                                                     | Explicit `agentcc_auto_reload_consent` relation; actor/identifier, version, accepted time, threshold and amount. This is public source, not guessed private schema.                                                                                                                     |
| Real claim obligation       | `futureagi/agentcc/services/auto_reload_consent.py:1-12,33-73`                                                                                              | Private claim path MUST call current-consent predicate immediately under its account lock, before reserving. Source documents an integration requirement, **not proof** that the deployed worker calls it. Already-claimed payment may complete after disable.                          |

Upstream request: `<WORKSPACE>/futureagi/tmp/journeys-708j/FOUNDATION.json:198-202`
(F12), covering J11 W02-01..08/CLOUD-01..04 and J29-GW-A04. T04 assigns shared
fixtures to this tree; T05/733 forbid org/system-key borrowing and auto-minting;
T09 requires the independent PORT-708J-W02 G10/G12 financial-review role
appointed separately. No person's appointment is invented. 713 safety is
unchanged; 744/745 do not waive runtime evidence or 708 add.59 egress guards.

## Scope, inputs and non-dispatchable plans

`paymentScope(input)` takes exactly:

- `candidateHead` equal to the head above;
- nonzero lowercase UUID `runId`, `organizationId`, `personId`;
- `authority:'person-session'`, `fixtureKind:'test-owned-loopback'`;
- `apiOrigin`, `appOrigin`: canonical HTTP literal loopback origins only
  (`localhost`, `127.0.0.1`, `[::1]`, optional valid port). No userinfo, path,
  query, fragment, numeric host alias or external host.

The detached frozen in-process scope is accidental-misuse protection, not
ownership attestation. Copies/JSON-roundtripped scopes are refused. There is no
key-creation operation, key material, org credential, implicit consent or balance
setter. Scope declarations do not provision a person or authenticate their role.
An eventual adapter must use a real isolated Owner/Admin person session and
non-retaining capture; it must not use the legacy `/accounts/keys/` bootstrap.

All inputs are plain decoded data. Exact keys, no symbols/getters, no sparse or
property-extended arrays; failures emit fixed error codes, never rejected values.
Executable Proxies are outside this data boundary. A regex cannot reliably tell
whether arbitrary bytes are a real secret: **never load real secrets**. Forbidden
secret fields are structurally rejected; controls use synthetic placeholders,
not real credentials. No card number, CVC, raw provider object or client secret is
accepted or emitted. Cards use opaque fixture references only; no TEST PAN is
needed and no actual card-entry UI is claimed.

`testReference(scope, kind, uuid)` yields a run-bound synthetic label for kind
`session|payment|event|card`. These are not vendor-minted IDs. A future accepted
producer must map actual isolated TEST IDs to these labels consistently, in
memory, across each capture. This package has no such mapping/ownership adapter.

Every plan returns frozen `{dispatchable:false, authority, origin, method, path,
bodyJson}`. No plan has an executor, headers, credential, fetch or SQL mutation.
`bodyJson` contains exact JSON numeric decimal lexemes built with BigInt, not a
rounded Number. Preserve those bytes in a future admitted adapter.

- `checkoutPlan(scope,{amountMicros,successPath,cancelPath})`: POST public checkout.
  Amount is a positive canonical integer string, in whole cents, within the
  serializer's maximum. The actual organization's minimum still needs producer
  readback; source-wide cent validity does not imply product admission. Paths
  are caller-selected absolute local paths (letters/digits/underscore/hyphen/
  slash only), at most 512 characters, without `//`, query, escape or traversal.
  The fixture prefixes `appOrigin`; it does not mount or claim those UI routes.
- `checkoutReturnPlan(scope,sessionId)`: GET the source-pinned status route. This
  may fulfil and credit, so never execute it as a read-only observation.
- `cardPlan(scope,input)`: exact forms `{operation:'collect'|'list'}`, or
  `{operation:'confirm',sessionId}`, or `{operation:'default'|'remove',cardId}`.
  Only run-bound references are accepted. These endpoints may contact billing
  infrastructure in an unmodified deployment, even on GET; plans stay inert.
- `settingsPlan(scope,{lowBalanceMicros,autoReload})`: PUT settings, low balance
  null or nonnegative integer micros. Enabled autoReload has exactly `enabled`,
  `consentVersion`, `thresholdMicros`, `amountMicros`; version must be current.
  Disabled autoReload has **only** `enabled:false`, omitting consent. Amounts use
  the public maximum of 1,000,000,000 USD; the actual minimum/default-card/limits
  checks remain product obligations. Explicit negative-body mutations belong to
  a future reviewed local adapter, not a generic override in this fixture.

## Signing contract: synthetic only

`createLocalSigner(scope, {})` allocates 32 fresh random bytes **on invocation**.
Each instance/run has its own memory-only key; supplied secrets/options are
refused. Nothing reads an environment variable, file or secret store. The key is
never returned, serialized or logged. `dispose()` overwrites its Buffer and
refuses further sign/verify calls; call it in `finally`. This is best-effort
process-memory cleanup, not a guarantee about native crypto copies. This lane
only parsed source, so generated no signing keys.

- `sign({eventId,sessionId,paymentId,amountMicros,outcome}, issuedAt)` returns
  `{protocol:'f12-local-oracle/v1',body,issuedAt,signature}`. Outcome is exactly
  `paid|cancelled`; amount has checkout cent bounds; IDs must be run-bound.
- Body is canonical JSON with run/org/person plus the event. HMAC-SHA256 covers
  the protocol, newline, integer issuance seconds, newline, and exact body.
- `verify(envelope, now)` rejects invalid/tampered signatures using equal-length
  constant-time comparison, wrong protocol/scope, extra fields, body over 8 KiB,
  future or over-300-second-old envelopes. Times are explicit nonnegative safe
  integers: consumers must supply a trusted monotonic test clock mapping.
- Valid duplicate signatures deliberately verify again. Cryptographic validity
  is **not** payment/event idempotency. No local `seen` set pretends otherwise.

This envelope is NOT a provider signature, an accepted payment webhook payload,
a signing-secret provisioning interface, or proof of private webhook refusal.
It must never be sent to the product webhook route. The actual producer owes a
reviewed raw-body/header protocol and run-local signing-secret binding; both
remain `UNBOUND`.

## Safe projections and oracles

All these oracles output `kind:'oracle-only-not-financial-acceptance'`. Their
self-consistency does not authenticate capture or prove its completeness. No
private payment/event/default-card table name, column or SQL is invented.
`F12_PROJECTION_REQUIREMENTS` is a **proposed**, not admitted, capture contract.

### Payment and return

`assertPaymentOutcome(scope, expectation, capture)` has exact expectation keys
`scenario`, `sessionId`, `paymentId`, `eventId`, `amountMicros`, `deliveryIds`.
Scenario is `invalid-signature|tampered-signature|cancelled|unknown-pending|
credited|duplicate-replay|concurrent-replay`. Delivery IDs are preselected UUIDs:
0 for cancel/unknown, 1 for invalid/tampered/credited, 2 for replay. They must be
independently bound before capture, never inferred from a partial result.

Capture has exactly `organizationId`, `before`, `after`, `deliveries`,
`returnResponse`. Each snapshot has:

- `grantedMicros` signed int64 string, `settledMicros` nonnegative int64 string;
- `defaultCardId` null or run-bound card label;
- `payments`: exact scoped rows `{id,sessionId,status}`;
- `events`: exact scoped rows `{id,paymentId}`;
- `credits`: exact scoped rows `{id,paymentId,sessionId,amountMicros}`, UUID ledger
  entry IDs, canonical positive cent amounts.

This is a fresh **logical-payment** cohort, not the entire organization's
history: before has zero matching credit/event rows, and one pending payment
(or already-cancelled payment for cancellation-return, or none for unknown).
Whole-account baseline totals may be nonzero. Cancellation covers the no-credit
return after cancellation, not a guessed provider cancellation endpoint.

Each delivery is `{id,eventId,signatureValid,entered,returned}`, with positive
interval length in a common producer clock. IDs must match the exact planned
set. Replay uses the same event/payment/session: sequential replay intervals
must not overlap; concurrent replay intervals must genuinely overlap. A label
or `Promise.all` alone is not evidence. Invalid/tampered deliveries must be
rejected and leave all projected state unchanged.

Positive/replayed cases require one paid payment, **one** persisted event,
**one** attributed credit for the expected amount, exact granted delta, unchanged
settled spend and default card, and the full public `returnResponse` envelope
`{status:true,result:{session_id,status,credited}}`. Pending returns must hide
unverified IDs. Missing/extra/duplicate/foreign rows, numeric money, malformed
amounts, wrong signatures, missing overlap, false positive returns and 503 fail.

The selected-field equality is not proof of no transient write-and-restore or
of untouched unprojected account fields. Actual transaction/mutation witnesses,
complete consistent snapshots, payment-intent/session cross-event deduplication
and broader replay after restart remain producer/runtime obligations.

### Cards

`assertDefaultCards(scope,input)` takes exactly `organizationId`, independent
`expectedCardIds`, `expectedDefaultCardId`, `apiCards`, `authoritativeCards`.
Both projections contain only `{id,is_default}` rows, with exactly the expected
IDs and either zero defaults or the single expected default. Null is explicit;
missing/extra/duplicate/foreign cards and a default not in the set fail. These
are **normalized projections**, not complete raw API responses: the reviewed
producer must strip only the documented metadata at the emitter and refuse
unexpected secret fields, not sanitize captured logs afterwards. Real card
collection, default selection, removal and their refresh/cancel captures are
not inferred from synthetic rows.

### Consent and claims

`consentQuery(scope,schema)` returns a frozen `{text,values}` SELECT descriptor
for the public table only. The explicit lowercase schema is syntax-validated;
there is no default/search-path assumption or database URL. Tenant is `$1::uuid`;
int64 micros are text and timestamps retain six fractional digits. There is no
limit/truncation: a future adapter must capture complete history under a
read-only role/consistent snapshot, and the oracle refuses more than 10,000 rows.
An absent relation/query error cannot become a successful empty result.

`assertConsentTransition(scope,input)` has exact fields `action`,
`expectedReceiptId`, `before`, `after`, `beforeReceipts`, `afterReceipts`.

- `action` is `enable|revoke|reject`. Expected new UUID receipt ID is mandatory
  only for enable; null otherwise.
- Normalized account states are `{enabled,version,acceptedAt,thresholdMicros,
amountMicros}`. This shape is a proposed private-account projection, not SQL.
- Receipt rows are exactly the public SELECT's `id`, `organization_id`,
  `actor_id`, `actor_identifier`, `consent_version`, `accepted_at`,
  `threshold_micros`, `amount_micros`. This bounded fixture uses one isolated
  person; actor ID may be null in preserved history, never in active consent.
- Enabled saves, including changed amount/threshold and already-enabled saves,
  append exactly one new current receipt matching actor/version/time/amount/
  threshold. Old receipt IDs/values may never be rewritten, removed or reused.
- Revoke disables and clears active version/time without changing receipt
  history. Rejected consent saves preserve all selected state/history. The
  `active` result uses the actual public predicate's receipt match; a legacy
  enabled boolean alone never implies current consent.

`assertClaimRace(scope,input)` checks a **proposed ordering capture**, with exact
fields `scenario`, `policyEnabled`, `disableCommitted`, `claimLockAcquired`,
`receiptCurrentUnderLock`, `claimCount`, `paymentStartedCount`. The admitted
producer must bind these to real lock/commit observations, not local timers.

- `disable-before-claim`: policy enabled, disable committed strictly before
  claim lock, no current consent under lock, zero claims/payment starts.
- `claim-before-disable`: policy enabled, reverse ordering, current consent
  checked under lock, exactly one claim/payment start. The terms allow this
  payment to complete; this oracle does not certify its eventual settlement.
- `policy-off`: explicit effective OFF, null lock/disable times, no current
  consent witness, zero claims/starts. Returns `positiveClaimCovered:false`;
  OFF is never positive purchase/card/auto-reload proof. A real source-bound
  effective-policy observation and no-attempt counters remain owed.

## Controls, cleanup and outstanding admission

`f12.controls.test.ts` and `f12.projections.test.ts` define 26 test cases in
F12-01..20 groups. They were committed before implementation. Controls cover
runtime refusal, org-key/secret/direct-balance misuse, exact loopback/scope,
source-bound plans, explicit consent, per-instance keys, invalid/tampered/stale/
foreign signatures, replay/double-credit/extra-event/interval corruption,
cancellation/unknown/positive returns, card exact sets, public SELECT scoping,
consent append/revoke/no-write, and both lock orderings versus policy OFF.

In a separately admitted guarded venue, entrypoints are the two `*.test.ts`
files with `node --test`. **Do not execute them locally under this lane's
static-only restriction.** Syntax checks are not RED/GREEN or typecheck proof.

Cleanup now is scope garbage collection and signer `dispose()` only: no fixture
starts a server, grants credit, changes a card, writes a balance or touches a DB.
Future real cleanup must use reviewed product cancellation/removal/ledger
services with authoritative rereads; never SQL UPDATE/reset/delete of money or
consent history. Original captured logs are never filtered or rewritten.

`requireF12Runtime(head)` always refuses this early tuple with
`F12-PRIVATE-BINDING-UNBOUND`; another head gets `F12-CANDIDATE-UNBOUND`.
`privatePaymentQuery(scope)` emits no SQL and refuses identically.
`autoReloadBarrier(scope)` refuses `F12-CLAIM-BARRIER-UNBOUND` rather than
substitute an in-memory lock. No flag, callback, signature or synthetic receipt
can bypass these guards.

Cloud billing/runtime/identity owners and an independent money reviewer owe:

1. An opaque reviewed local TEST checkout/card/webhook adapter, exact source and
   callable signatures, request/response mapping, inert loopback routing and
   effective provider-free deployment. Published URLs alone are insufficient.
2. Run-generated in-memory TEST signing secret provisioning into the **actual**
   verifier, raw-body/header protocol, invalid/tampered/refused controls and safe
   non-retaining captures. Never reuse this synthetic protocol as provider proof.
3. Public reviewed authoritative payment/event/default-card/private-account
   projections, real schema/capture ownership, exact complete cohorts, safe
   TEST-ID mapping and actual ledger-service fulfilment, including session /
   payment-intent / multiple-event deduplication and concurrent delivery.
4. Final-733 fresh identity/session binding, final per-arm tuple, source-built
   served image and all-context/native no-egress guards. Self-hosted 503 remains
   absence only; it cannot replace the cloud purchase/card arm.
5. Actual claim-under-account-lock race barrier and both disable orderings, or
   a source-bound effective policy-OFF/no-claim witness with all missing positive
   cells still NOT_RUN. The public integration comment is not executed proof.
6. Guarded executed controls/restoration and journey receipts, plus a
   **named independent G10/G12 financial reviewer**. No local
   suite, browser, Docker, build, cloud, payment/vendor or production operation
   is authorized by these source files.
