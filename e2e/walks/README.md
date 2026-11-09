# Gateway walk harness

The harness for the ruling 706 gateway walks: W00, the default-off census, and
W01-W19, one walk per launch feature (the walk plan, GATEWAY-WALK-PLAN-706). A
walk is a flow under `flows/walks/` that runs in Chromium **and** Firefox on a
real Standalone (or Distributed, or hosted-shape) stack built from the head it
claims, with every role of a fresh organization and an org B that must end
byte-identical.

## Running walks

```bash
# on a cloud VM only (e2e/walks/vm/run-walks.sh does all of this):
E2E_STACK=standalone E2E_WALK_PINS=1 E2E_GATEWAY_PROFILE=stock bin/e2e up
(cd e2e && yarn playwright test --config playwright.walk.config.ts --grep @walk-W00)
```

- `playwright.walk.config.ts` (H1) has two projects, `chromium` and `firefox`.
  Each test provisions its own ScopeActors org set, so the browsers never share
  an org. At most 3 workers per Standalone VM (`E2E_WALK_WORKERS` 1-3): the
  recipe's "workers = cores" is for unit suites; 4 or more browsers overwhelm the
  single-Granian Standalone backend. Parallelism comes from more VMs.
- A walk imports `test` from `lib/walk/fixtures.ts`. `walk.open(actor, label)`
  gives a signed-in context with the boundary installed before its first page.
  At teardown the fixture writes `walk-receipt.json` and fails the test on any
  non-local request that is not ASSETS-PENDING.
- Walk flows carry `@walk` and `@walk-Wnn`. They are ordinary `@flow`s, so they
  are listed in `FLOWS.md` and run in the main config too, in Chromium only.

## J00 result composition (HR-JRN-03)

`playwright.census.config.ts` runs **one** W00 dependency for each browser, then
runs journey specs and `walks/census-consumer.spec.ts`. Do not add W00 to another
project or import its spec. Select the consumer project(s), without `--no-deps`:

```bash
# On the cloud VM after building/starting the attested stock stack:
export E2E_CENSUS_RUN_ID="hr03-$(date +%s)-$$"
export E2E_CENSUS_RESULTS="$PWD/census-results"
# E2E_WALK_PRODUCT_SHA and E2E_WALK_IMAGES must name the verified checkout and
# walk-images.mjs running attestation. Never copy them from an old result.
(cd e2e && yarn playwright test --config playwright.census.config.ts \
  --project chromium --project firefox --grep 'JRN-E2E-000.L1|@census-consumer')
```

Set the run ID once outside Playwright, and use a new one for every invocation.
Results live outside Playwright's output directory, in one exclusive file per
run/profile/browser. Publishing twice refuses rather than overwriting evidence.
The result carries hashes of A/B and their org set; those are **W00's** orgs,
not the independently provisioned journey actors. Both browsers mint their own
orgs. Consumers read without minting another W00 roster:

```ts
import { currentCensusBinding } from "../../lib/walk/census-runtime";
import { readCensusResult } from "../../lib/walk/census-result";

// Inside J00.L1, BEFORE the existing non-executable-leg skip. Add browserName
// to the fixture destructuring. No new census, no imported spec, no skip.
if (leg.id === "L1") {
  if (browserName !== "chromium" && browserName !== "firefox")
    throw new Error("W00_RESULT_BROWSER");
  const result = readCensusResult(currentCensusBinding(browserName));
  expect(result.ui).toHaveLength(224); // 2 phases x 4 roles x 7 pages x 2 widths x 2 schemes
  await testInfo.attach("w00-consumed", {
    contentType: "application/json",
    body: JSON.stringify({
      digest: result.digest,
      orgSet: result.orgSet,
      binding: result.binding,
    }),
  });
  return;
}
```

This consumes W00's implemented contract, **not** the remaining J00 release
obligations listed below. Main must reconcile the L1 annotation/pass criteria
rather than treating the reader as proof of the missing UI pages. At product
`3184d9212bb2e7c18acd8fa2d981f61d395fc7a3`,
`futureagi/agentcc/services/managed_models_default.py:90-93` writes nothing on
default-OFF, so every such gateway profile requires **exactly zero** rows and
active versions and a **null** mirror. Lines 98-103 write exactly one only for a
fresh org on a declared-on deployment whose gateway offers credits (49-59).
That version enables managed models (67-76); it cannot satisfy J00's current
combination of "one version" and "managed models off". W00 remains tagged
`@new-org-default-off`; the default-on flow is separate.

`readCensusResult(expectedBinding, directory?, expectedOrgSet?)` throws
`CensusResultError` with `reason` (ABSENT, DIGEST, RUN, PROFILE, BROWSER, STACK,
DEFAULT, ORG_SET, PARTIAL, VERDICT, VALUE or MALFORMED). It independently checks
the current checkout, image attestation and live stack instance. Its canonical
digest is an integrity check, not a signature. No absent/partial result is a pass.
Publication happens after W00's assertions and browser boundary finalization,
only when `E2E_CENSUS_RESULTS` is set. Ordinary main-config walks still assert
everything but do not require the attested result-publication environment.
W00 disables screenshots/traces/video and requests digest-only ScopeActors
attachments; ordinary fixture defaults remain unchanged. The result records all
224 UI observations and both backend phases, plus the complete verdict inventory.

Round 2 uses **w00-result-v2**; v1 evidence is refused. Per phase and catalogue,
`backend[].catalogues.cat2.api/gateway` contains each exact CAT-2 ID once, with
`disposition: absent | blocked-unpriced`, observed state and null MTok prices.
The deliberate BYOK-visible blocked state is not an exposure: default-off means
absent OR state=blocked AND no input/output/cache/billed MTok price in BOTH
catalogues. `credits[]` additionally records every ID's real request after the
console-created key: 403 permission_denied, before/after mock inference counts
both zero. Unknown-model/authentication errors and served requests do not pass.
The mock counts inference POSTs, including unscoped managed requests; it
cannot be reset by a reader. A fresh synthetic user-message marker isolates
W00's counts from unrelated inference and the other browser's requests; the
marker is not retained in results. Counter self-tests include positive calls.
The reader requires these records and OFF catalogue verdicts with matching counts.
Each Models UI cell must contain exactly one `managed_models` UI verdict `OFF`
whose detail is `kind: "absent-with-notice"`, `control: "ABSENT"`, the visible
notice `Future AGI credits are off for this organization.`, and a server readback
from `/agentcc/managed-models/` with boolean `enabled: false`. Main's 754 ruling
permits this kind for no other feature or page. The witness and its notice/server
objects have closed fields: a switch in any state, extra `switchState`, a second
managed UI row without the kind, a missing notice or an enabled server all fail.
No OFF state is inferred from absence alone.
`receipt` is an informational digest linking the separately landed receipt,
not an independently verified receipt signature. The result digest binds its
own full payload. The reader API and J00.L1 wiring above are unchanged.

## Pieces

| Item                   | Files                                                                                                                     | What it gives                                                                                                                                                                                                                                                                                                | Its test                                            |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------- |
| H1 runner and receipts | `playwright.walk.config.ts`, `lib/walk/fixtures.ts`, `lib/walk/receipt.ts`, `scripts/walk-contact-sheets.mjs`             | Projects, worker cap, and a receipt per test with realHead, dirty, product identity (`E2E_WALK_PRODUCT_SHA`), profile, mounted config `sha256:` digest, boundary ledger, empty icons (ASSETS-PENDING or PRODUCT-BUG) and screenshots. Contact sheets of 16 shots each.                                       | `harness/walk-harness.spec.ts` (runner)             |
| H2 ScopeActors levels  | `lib/scope-actors.ts`                                                                                                     | Owner (15) by signup, and Admin (8), Member (3) and Viewer (1) by the public invite. `test.use({ scopeRoster: { admin, secondMember, orgB: [levels] } })` adds actors; the default stays the original four.                                                                                                  | live through W00                                    |
| H3 RedisProbe          | `lib/redis-probe.ts`                                                                                                      | Read-only: `GET HGETALL HGET EXISTS TTL PTTL SMEMBERS XLEN XRANGE XINFO(GROUPS/CONSUMERS) XPENDING SCAN`, only on `<prefix>mc:{org}`, `cap:{org}` and `approval:{org}` keys of owned orgs plus `mc:outbox_orgs` and `mc:needs_push`, through `bin/e2e compose exec` (Standalone `app`, Distributed `redis`). | offline H3 cases                                    |
| H4 ledger fixtures     | `lib/ledger-seed.ts`                                                                                                      | Admin-token grant and adjust, the `agentcc_managed_credits` command, and a test-mode top-up with a synthetic `cs_test_e2e_walk_*` session through `fulfil_topup`. Every entry is labelled "walk fixture, not a purchase".                                                                                    | offline H4 case                                     |
| H5 gateway profiles    | `lib/profile.ts`, `stack/gateway-profiles.json`, `stack/gateway.<profile>.yaml`, `scripts/gateway-profile.mjs`, `bin/e2e` | `mcp`, `mcp-catalog`, `mcp-signin`, `mcp-otel`, `pricefeed` (stock plus a declared delta, refused by the inspector if anything else). `onekey-cat` is owed. `bin/e2e` refuses, before anything starts, a profile whose gateway keys or overlays this head lacks.                                             | offline H5 cases, `harness/managed-mock.spec.ts` P4 |
| H8 two replicas        | `stack/docker-compose.mcp-replica.yml` (`E2E_GATEWAY_REPLICAS=2`)                                                         | `agentcc-gateway-2` in the app's network namespace, on 8081 (host 28091), sharing the app's Redis (db 4) and syncing from the control plane.                                                                                                                                                                 | offline H8 case                                     |
| H11 strict browsers    | `lib/walk/browsers.ts`                                                                                                    | The 643 Firefox prefs and Chromium's own-traffic flags (`E2E_WALK_STRICT_BROWSER=0` turns them off), recorded in every receipt.                                                                                                                                                                              | offline H11 case                                    |
| H11b install links     | `lib/walk/browsers.ts`                                                                                                    | `cursor:`, `vscode:` and `claude:` links captured (`protocolLinks(page)`) and never opened.                                                                                                                                                                                                                  | offline H11b case                                   |
| H12 boundary           | `lib/walk/boundary.ts`, `stack/docker-compose.walk-pins*.yml` (`E2E_WALK_PINS=1`)                                         | Ports published on loopback, networks as shipped, and licence, telemetry, Ollama, Stripe and APP_URL pinned. Every non-local browser request is aborted and recorded by origin. Font and Iconify hosts are ASSETS-PENDING; anything else fails the walk.                                                     | offline H12 cases                                   |
| H13 census             | `flows/walks/default-off-census.spec.ts` (WALK-E2E-000), `lib/walk/census.ts`                                             | W00: OFF, ON (fails) or ABSENT (this head lacks it) per feature, in the UI, API, storage, gateway and Redis lanes, before and after the first key. Org B is byte-identical at the end.                                                                                                                       | offline H13 cases                                   |
| H14 hosted shape       | `walks/hosted/run-hosted.sh`, `hosted-env.mjs`, `docker-compose.hosted-walk.yml`                                          | W19: local images behind a loopback registry, a synthetic env, phased migration under a readiness poller, and the rehearsal driver. Refused on a head without `deploy/docker-compose.hosted.yml`.                                                                                                            | offline H14 case                                    |
| H15 export sinks       | `lib/export-sinks.ts`, `lib/integration-export.ts`                                                                        | gw/RF-UITRUTH-PLAT2 d3bcfd30a2, byte for byte, for W17 on Distributed.                                                                                                                                                                                                                                       | offline H15 case                                    |
| H16 launchers          | `walks/launch/<group>.sh`, `launch.sh`, `groups.json`, `walks/vm/*.sh`                                                    | One VM per group, with the recipe's flags. See below.                                                                                                                                                                                                                                                        | offline H16 case                                    |
| H17 strict kit         | `walks/kit/strict-kit.sh` (`WALK_STRICT=1`)                                                                               | Re-verifies `egress-sinkhole-r574b.tar` (sha256 31a52264...849f7), then runs named mode, `lockdown.sh run` over the stack's bridges, `count-attempts.sh --expect zero`, and the audit copy. Exits 81, 82 and 83 are failures. Only a walk phase may run under it.                                            | offline H17 cases                                   |

## Mocks from gw/E2E-WALK-MOCKS-1 (integration seams)

This harness names the mock services but does not define them:

- `mcp-mock` (agentcc-mcpmock with RFC 7591 `/register`, RFC 7009 `/revoke` and
  a read of what it received). Started by `e2e/stack/docker-compose.mcp-e2e.yml`,
  from the MCP line plus the mocks lane, for the `mcp`, `mcp-signin` and
  `mcp-otel` profiles.
- `otlp-sink` (OTLP capture, read API on 127.0.0.1) for `mcp-otel`; `siem-sink`
  (HEC-shaped plus generic JSON) for W14; the synthetic K2 authorization server
  for W10. Managed overlays always include `e2e/stack/docker-compose.walk-mocks.yml`,
  preserving the extended mock-LLM routes even without the opt-in.
  `E2E_WALK_MOCKS=1` opts into starting the services selected by
  `E2E_WALK_MOCK_SERVICES="otlp-sink siem-sink ..."`; it does not add the overlay.
- The mock-llm routes for image output, Lyria, dynamic routers, usage.cost
  overrun and per-attempt 503 (H6).

Until a file exists on the head, the profile or flag that needs it is refused
before the stack starts, and the error names where it comes from.

## Launching (H16)

```bash
e2e/walks/launch/vm-1.sh plan   # what ships, the scan; no VM
e2e/walks/launch/vm-1.sh        # host-guard, HEAD and clean-tree checks, detached launch
~/.futureagi-local/bin/omega-cloud job status --lane gw-walk-vm-1   # before any wait or pull
```

Every group launches with the same flags: `--repo <wt>@<HEAD> --skip-nested
--standard --machine n2d-standard-32 --life 4h --timeout 150m --keep 60m
--on-lost fail`. The prepare step runs the futureagi uv sync, `go mod download
all` (go.sum restored), and the frontend and e2e yarn installs, then installs
the Playwright Chromium and Firefox builds into `$HOME/ms-pw`. The gwtest stack
is not started: walks bring up their own stack, and under the egress kit every
running container must be on a selected walk network.

- Pulls land in `e2e/walk-landed/<group>` under the repository by default;
  set `WALK_PULL_ROOT` to choose another receipt directory.
- Exit 77: wait 60 s and retry, at most 30 times. Exit 74: blocked, not retried.
- A lost or vm-gone job is finished: keep its receipt and never wait on or pull
  it. Run `lease release` when the group is done.
- Set `WALK_KIT_ARCHIVE` to the reviewed kit archive, or place it at
  `e2e/walk-kit/egress-sinkhole-r574b.tar` relative to the repository. The
  existing checksum check still applies.
- `WALK_STRICT=1` ships the kit archive alone and wraps each walk phase in
  `strict-kit.sh`. It refuses while a research lane holds a pack slot.

| Group       | Lane          | Stack and profiles                                         | Walks                   | Strict for acceptance |
| ----------- | ------------- | ---------------------------------------------------------- | ----------------------- | --------------------- |
| gate        | gw-walk-int2  | none: typecheck, catalog, selftest, offline harness        | -                       | -                     |
| dryrun-int2 | gw-walk-int2  | Standalone; every profile, refused where the head lacks it | W00 on INT-2 c99dc71f35 | none (ASSETS-PENDING) |
| vm-1        | gw-walk-vm-1  | Standalone stock                                           | W00-W04                 | W01, W02              |
| vm-2        | gw-walk-vm-2  | onekey-cat, pricefeed, stock                               | W06, W05                | W06                   |
| vm-3a       | gw-walk-vm-3a | mcp, mcp-signin, mcp-catalog; 2 replicas                   | W07-W10                 | W07-W10               |
| vm-3b       | gw-walk-vm-3b | mcp, mcp-otel; 2 replicas                                  | W11-W16                 | W13, W14              |
| vm-4        | gw-walk-vm-4  | Distributed stock                                          | W17, W18                | W17, W18              |
| vm-5        | gw-walk-vm-5  | hosted shape                                               | W19                     | W19                   |

Strict acceptance also needs main's offline-assets wrapper. Until it lands, font
and Iconify aborts are ASSETS-PENDING and a walk run is a rehearsal, not
acceptance.

## Owed

- **onekey-cat profile delta.** No head names the CAT-2 operator setting yet
  (INT-4). W00 now pins the exact 34-ID list from the CAT-2 lane in
  `lib/walk/census-contract.ts`; the opt-in profile delta remains separate.
- **Head features.** On INT-2, mcp, mcp-signin and mcp-otel need the gateway
  `connectors` key and the mcp-mock overlay (the MCP line). mcp-otel also needs
  G16's `trace_content` and `otlp-sink`, and pricefeed needs G8-A
  `public_price_feed`.
- **H8 inspector rules.** The managed-mock inspector does not yet accept
  `AGENTCC_REDIS_*` or the replica container. Cross-replica behaviour is
  unproven until it does.
- **H14 details.** The schema-deadline variable name and the rehearsal's
  loopback Postgres port must be read from G18's command when W19 is written.
- **Strict runs.** Every strict acceptance run waits for main's assets wrapper.
- **W01-W19 specs.** They are written against this harness once their heads
  converge (A9).

## Round-two compatibility and limits

- Stock/r7alt CLI paths still need no Node; r7alt on Distributed retains exit 1
  and its original message. New profile discovery requires Node; an unknown
  profile now exits 2 with the expanded profile list, and unavailable profiles
  exit 3. `runner-compat.test.mjs` and `walk-regressions.spec.ts` pin these seams.
- Image receipts record local content-addressed image IDs against the build
  manifest, not just a git comparison. Distributed explicitly tags and validates
  backend, frontend, collector and gateway. A stale/registry image is refused.
- Iconify 4.1.1's class-less placeholder is identified through its React owner;
  the real installed package is the positive and loaded/unrelated-span control.
- Top-up fixtures carry the fixture note through TopupPayment.intent_ref (stored
  as `intent:walk fixture, not a purchase`); their returned receipt includes it.
- H8 is not a Django push fan-out: Django pushes to its primary URL; the second
  replica gets keys/config via the bounded 60-second sync. W08/W11 must use
  POLL.GATEWAY_SYNC. `harness/replica-sharing.spec.ts` requires real mock traffic
  to spend a shared Redis ledger in both directions before sharing is claimed.
- Cloud-mode is **not accepted**. Contract 713 reads only profile.env/profile.json,
  including the attempt's compose project. No signing file is read and the CLI
  refuses activation until main announces acceptance. W01/W02 purchases remain
  owed; fixture grants are never purchase proof.
- AUTH-E2E-004 is not present on INT-2. `landing-fixtures.ts` provides deterministic
  reversed UUID/default/preference/role cases and a SAML org-scope handoff; its
  fixture self-test is not a substitute for the eventual browser sign-in flow.
- Missing W00 UI pages remain owed: virtual servers, connectors catalogue and
  guardrail check history. HR-JRN-03 adds the gateway admin catalogue read and
  exact CAT-2 absence assertions on both catalogue APIs. The budget page's
  descriptive marker remains RECORDED; configuration assertions, not the marker,
  prove caps OFF.
- W01-W19 remain reserved as WALK-E2E-001 through WALK-E2E-019. No placeholder
  skipped test counts as implemented flow coverage. Mocks integration remains
  owed until gw/E2E-WALK-MOCKS-1 is review-clean; its seams are unchanged.
