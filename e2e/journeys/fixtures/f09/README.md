# F09 identity preparation

**BLOCKED for runtime provisioning on the inspected 708J-early candidate.** This
package supplies data-only plans, strict readback oracles and fixed read-only SQL
projections. It does **not** yet supply accepted users, browser contexts, a key
issuer, same-person scope GET/close, or a non-retaining secret handoff. Importing
it makes no network, database, process, environment or filesystem calls.

The sole shared-fixture writer is `journeys-main-708`. Consumers import
`f09` from `e2e/journeys/fixtures/index.ts`, or named exports from this directory's
`index.ts`. Do not fall back to `ScopeActors`/`provisionActor`: that path fetches
`/accounts/keys/` and borrows the legacy organization key.

## Candidate binding and stop condition

Source root, read-only:
a local checkout of the head below

Inspected clean HEAD: `da4c81cc623f909ce38f53e01b557ee8374d5adb`.
The following paths and line numbers are relative to that exact root. These are
observations of the candidate, not acceptance of deferred 733 branches.

| Contract                   | Actual source binding                                                                                                    | Consequence                                                                                                                                                                             |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Organization roles         | `futureagi/tfc/constants/levels.py:16-37`                                                                                | Owner 15, Admin 8, Member 3, Viewer 1; workspace Admin is not org Admin.                                                                                                                |
| Effective role readback    | `futureagi/accounts/views/user.py:784-803`; `futureagi/tfc/permissions/utils.py:41-73`                                   | User-info `ws_level` is effective: Owner 15, Admin 8, Member 3, Viewer 1 for this roster. It is not the stored workspace membership level.                                              |
| Invite roles               | `futureagi/accounts/serializers/rbac.py:34-51`                                                                           | `org_level` is separate from `workspace_access[].level`. No direct role writes are supplied.                                                                                            |
| Signup bootstrap           | `futureagi/accounts/utils.py:370-391`                                                                                    | Still creates a system key automatically. Runtime provisioning must stop BEFORE signup, not discover this after minting.                                                                |
| Forbidden legacy bootstrap | `futureagi/accounts/views/keys.py:45-72`; `e2e/lib/provisioning.ts:16-40`                                                | GET `/accounts/keys/` creates/returns a system key. Never call or reuse it here.                                                                                                        |
| Actual issuance route      | `futureagi/accounts/urls.py:97`; `frontend/src/utils/axios.js:366-370`                                                   | POST `/accounts/key/generate_secret_key/`. No guessed L1 route.                                                                                                                         |
| Actual issuance request    | `futureagi/accounts/serializers/org_api_key.py:31-32`; `futureagi/accounts/views/keys.py:260-284`                        | Serializer accepts only `key_name` (max 100), rejects unknown fields, and assigns `type=user`, `user=request.user`. It does not expose the required final personal scoped-key contract. |
| One-time response          | `futureagi/accounts/views/keys.py:297-307`                                                                               | The `result` contains `key_id`, `key_name`, raw and masked key pair fields. F09 never fetches, stores, logs, hashes or attaches this response.                                          |
| UI request                 | `frontend/src/sections/keys/view/CreateApiKey.jsx:57-80`; `frontend/src/sections/keys/view/keyScopes.js:1-6,36-39,68-69` | Production request is `{key_name}`; scopes are prototype-only. Prototype controls are not final-733 evidence.                                                                           |
| Legacy owner boundaries    | `futureagi/accounts/views/keys.py:132-136,207-211,241-245`                                                               | List and enable/disable are organization-bound, not same-person-bound. These are NOT gateway scope GET/close contracts.                                                                 |
| Safe key projection        | `futureagi/accounts/models/user.py:304-348`; `futureagi/tfc/utils/base_model.py:143-153`                                 | Table `accounts_orgapikey`; UUID identity/owner/org, type, enabled and deleted are sufficient for attribution/census. Never select secret columns.                                      |

The authoritative early manifest is
a local early-candidate `MANIFEST.json` (lines 1567-1599):
FA-733-COMPOSE is **DEFER**, operation `NONE_DEFERRED`. Branch names and a
source-accepted component cannot supply the missing composed contract.

`requireF09Runtime(candidateHead: unknown): never` refuses the inspected head
with `F09-CANDIDATE-733-ABSENT`; every other value/head is
`F09-CANDIDATE-UNBOUND`. There is no runtime-ready override. A future adapter must
call this before any signup or issuance. Changing the source binding requires
review of the real replacement candidate, not setting a caller-provided boolean.

## Inputs and outputs

### `identityPlan(input): IdentityPlan`

Exact input fields: `runId` (fresh, nonzero UUID), `browser` (`chromium`, `firefox`
or `webkit`), `profile` (`stock` or `opt-in`), `apiOrigin`, `appOrigin`. Origins
must be literal HTTP loopback origins (`localhost`, `127.0.0.1` or `[::1]`), with
an optional valid port, no credentials/path/query/fragment. They are planning
constraints, **not** a socket guard or permission to launch a browser.

Output is frozen: `kind=f09-identity-plan`, the validated inputs, an
`isolationId` incorporating run/browser/profile, and eight role slots, ordered
A Owner/Admin/Member/Viewer then B Owner/Admin/Member/Viewer. Each has an
`e2e-f09-` label. No email, password, session, storage state or key is generated.
Browser/profile labels are isolation dimensions only, not a runtime admission
claim. Reusing a run ID is not freshness; the eventual runner owns uniqueness.

### `bindIdentityReadback(plan, rows): IdentityRoster`

Requires the original in-process plan and exactly eight rows. Each row has only
`slot`, `isolationId`, `userId`, `organizationId`, `workspaceId`, `contextId`,
`orgLevel`, `wsLevel`. IDs are nonzero UUIDs. It checks exact slots and effective
roles, two distinct orgs and default workspaces, eight distinct people and
contexts, same-org workspace membership, and matching run/browser/profile.

The output is a detached frozen in-process roster. Serialization does not
preserve its brand. **Neither the brand nor caller-supplied rows are authority or
proof**: the admitted adapter must read actual user-info/membership and associate
each row with its actual guarded browser context. Synthetic oracle rows are used
only in the control file and must never be submitted as product evidence.

### Signup census

`signupCensusQuery(roster): ReadOnlyQuery` returns `{text, values}` for exactly the
two bound organizations. Its fixed SELECT uses a left join, so zero-key orgs
remain present, and counts **all** key rows, including disabled/deleted keys.
It selects no key material and accepts no caller SQL or database URL.

`assertZeroKeySignup(roster, rows)` accepts exactly two rows with
`organization_id`, `total_rows`, `system_rows`, `user_rows`, `other_rows`, all
counts numeric zero. Missing/extra/duplicate/foreign rows, string counts and
unknown fields fail. Output is safe org IDs and `totalKeys=0` only.

This is the full-cohort, post-bootstrap/pre-explicit-issue oracle. The future
runtime adapter still owes real bootstrap checkpoints before each subsequent
provisioning step and a fresh census taken at the required boundary. A later
zero count cannot prove that a hard-deleted historical key never existed.

### Personal-key attribution (not issuance or scope authorization)

`personalOwnershipQuery(roster, slot, keyId): ReadOnlyQuery` selects only
`id`, `user_id`, `organization_id`, `type`, `enabled`, `deleted`, bound by org and
key UUID. `assertPersonalOwnership(roster, slot, keyId, rows)` requires exactly
one row owned by that same person in that org, `type=user`, enabled and not
deleted. Output contains safe IDs only. Org/system/MCP/ingest keys, another
person's key (even in the same org), foreign-org keys and secret-bearing rows
fail. This does not prove scopes, hashing, session-only issuance, gateway
propagation, GET/close ownership, revocation or non-retention.

Queries must be executed by the accepted loopback, read-only database adapter
under the inherited runtime guard. This package deliberately opens no connection
and exports no generic write, executor, pass-producing callback or financial API.
It never accepts a raw key pair. Arbitrary strings cannot be reliably classified
as real secrets; preventing exposure requires the non-retaining product boundary,
not a synthetic prefix or a redaction-after-capture filter.

## Invariants and controls

- No `ScopeActors`, `provisionActor`, environment credentials, saved profiles,
  shared org keys, automatic minting or SQL identity/balance writes.
- Unknown fields fail before retention; errors contain fixed codes, not rejected
  values. Accessors are rejected rather than invoked. No credential hashing.
- Every query is a fixed parameterized SELECT; there is no query-string input.
- Plans and assertions are not executed journeys or authorization certificates.
- No financial credit, payment, signing or webhook fixture exists here. Money
  and authentic/tampered webhook-signature controls belong to F05/F12 and T09's
  independent financial reviewer. F09 rejects a supplied signature field; that
  control is **not** cryptographic signature verification.
- No cleanup mutation is required for these data-only helpers. Actual resource
  teardown, revocation and failure-path cleanup remain part of the missing
  admitted runtime adapter; never hard-delete keys to make the census pass.

`f09.controls.test.ts` contains tests-first controls F09-01 through F09-17:
candidate refusal, exact roster, run/browser/profile separation, unsafe origins,
credential/org-key/auto-mint/money/signed-envelope misuse, malformed inputs,
org-Admin distinction, duplicate persons/contexts, cross-org workspaces,
credential-bearing/forged readbacks, fixed census SQL, nonzero/missing census,
fixed personal-key metadata SQL, borrowed/retired keys, duplicate key rows,
Owner effective-level semantics and no getter/coercion execution. Every literal
is synthetic test data, never a real secret or card number.

No test was executed in this authoring lane. Only Node syntax parsing, Markdown
AST parsing, whitespace checks and normal commit hooks are allowed. Test-first
commits are ordering evidence, **not** observed RED/PASS evidence. In a separately
admitted guarded venue, the control entrypoint is `node --test` on
`e2e/journeys/fixtures/f09/f09.controls.test.ts`; runtime journey discovery and
real browser/database fallibility/restoration remain owed.

## Completion boundary

F09 remains BLOCKED, not accepted or runtime-ready, until the candidate includes
the reviewed FA-733 composition. The next writer must bind the **actual**
L1-CORE serializer and UI, session-only explicit scoped issuance and same-person
GET/close contracts; implement real isolated signup/invite/profile provisioning;
await bootstrap checkpoints; run census; add non-retaining secret use with
revocation/cleanup; and execute controls plus browser/profile journeys under the
accepted guard with independent review. Do not guess missing fields/routes,
import a deferred branch, enable prototype scopes, or weaken the zero-key rule.
