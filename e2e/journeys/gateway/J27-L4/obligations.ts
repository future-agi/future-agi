/** Source-authoring disposition, NOT runtime admission or a test replacement.
 * Authority: the J27/L4 packet (PACKET.json)
 * Shared contracts: ../README.md, ../_shared/contract.ts, FOUNDATION.json F01-F19.
 * Foundation commit: 7543e2d995fe58b620745aa81ce1a76c8855ef0e.
 * Every full assertion remains NOT_WALKED. C06/C07 have data-only detector tests;
 * neither their expected failures nor their synthetic restored tests cover a row.
 * No skipped/fixme/unconditional-pass live placeholder is registered.
 * Arrays use JSON literals so AUTHOR.json can reconcile them without executing TS.
 */
export const ROW_OBLIGATIONS = [
  {
    "id": "L4-O00", "row": "L4-A00", "status": "NOT_WALKED",
    "reasonKind": "missing runner fault hook", "foundation": ["F01", "F17", "F19"],
    "owner": "shared walk runtime owner and main",
    "reason": "No accepted candidate/LM/tuple/image/config/parts binding and reporter join for H1, 59k, 18d, census, CLEAN lister and actual journey starts/terminals. The inspected W20 interface admits W/HR IDs, not journey specs; no walk config exists in this candidate. A JSON constant or oracle test cannot certify these identities.",
    "requiredSteps": ["bind exact runtime/candidate and actual two-browser inventory", "join H1, native counters, aborted-host ledger and independent certificate", "C00 red and restored exact-runtime controls"]
  },
  {
    "id": "L4-O01", "row": "L4-A01", "status": "NOT_WALKED",
    "reasonKind": "missing runner fault hook", "foundation": ["F03", "F04", "F09"],
    "owner": "shared walk runtime, HOSTED-OPS and person-owned identity fixture owners",
    "reason": "No accepted actual-serving-instance restart action, routing/census/incarnation reader or live-stream barrier. MockUpstream latency delays before response and the mock emits its SSE synchronously; neither can witness an active streamed run plus durable hold at closure. Ordinary provisionActor reads org keys, not the required explicit person-owned key fixture.",
    "requiredSteps": ["mint synthetic person-owned fixture and hold live stream at observed event", "join request/run/stream/hold and ingress-to-serving-instance", "restart resolved serving incarnation with unchanged image digest", "C01 red non-serving or omitted restart and restored real restart"]
  },
  {
    "id": "L4-O02", "row": "L4-A02", "status": "NOT_WALKED",
    "reasonKind": "missing runner fault hook", "foundation": ["F03", "F07", "F19"],
    "owner": "shared walk runtime and HOSTED-OPS",
    "reason": "No accepted full replica/autoscaling census or continuous local probe producer across real close/drain/stop/start/readiness. C06 detector fixtures check exact reported refusals, disconnections and sampled intervals but do not measure a real interruption or rule out a hidden standby. The W20 sample explicitly uses two replicas and is not the L4 topology.",
    "requiredSteps": ["prove one gateway with autoscaling off before and after", "capture every continuous local request/stream probe", "report all measured interruptions without redundancy claim", "bind C06 reporter controls to accepted originals and restore real case"]
  },
  {
    "id": "L4-O03", "row": "L4-A03", "status": "NOT_WALKED",
    "reasonKind": "missing runner fault hook", "foundation": ["F02", "F03", "F04", "F05"],
    "owner": "shared walk runtime, gateway admission and billing owners",
    "reason": "No accepted close/drain action during an observed live stream/hold, drain/pre-ready probe hook or authoritative run/hold/debit/delivery projections. Existing aggregate gateway credits and request logs cannot establish zero new effects. The exact candidate refusal status/body is unpinned and no response fallback is supplied.",
    "requiredSteps": ["observe active streamed event and durable hold", "acknowledge admission close before stop", "probe drain and pre-ready admission with zero new runs/holds/debits/mock deliveries", "C02 admission-open red and unchanged restored checks"]
  },
  {
    "id": "L4-O04", "row": "L4-A04", "status": "NOT_WALKED",
    "reasonKind": "missing runner fault hook", "foundation": ["F03", "F04", "F07", "F19"],
    "owner": "HOSTED-OPS and shared walk runtime",
    "reason": "No reviewed lifecycle seam exposing close acknowledgement, bounded drain obligations, durable handoff/terminal accounting, authoritative parts/state revalidation and process mutation. No accepted unresolved-drain/state-drift or bypass-drain mutant. DRAIN_DEADLINE_MS has no bound product contract; a generic poll timeout would invent an SLA.",
    "requiredSteps": ["active run then close then bounded drain", "account every pre-close run", "revalidate authoritative state and parts before actual service mutation", "C03 unresolved-drain and state-drift refusal", "C03 separate bypass-drain red and full restored lifecycle"]
  },
  {
    "id": "L4-O05", "row": "L4-A05", "status": "NOT_WALKED",
    "reasonKind": "missing runner fault hook", "foundation": ["F03", "F16", "F19"],
    "owner": "main Stage B, HOSTED-OPS and shared walk runtime",
    "reason": "No accepted gateway lifecycle action or bound served EE branch. EE-served requires a separate actual daemon restart with Stage B state-volume/host/JWKS/union/parts readiness. No private EE source was opened; no fake daemon or gateway-only substitution is implemented. UNKNOWN or mixed EE/edge declarations cannot select absence.",
    "requiredSteps": ["gateway restart unconditionally", "fully restore gateway baseline", "if served, distinct EE live-run close/drain/restart/recovery", "bind C01 and C03 separately per applicable target"]
  },
  {
    "id": "L4-O06", "row": "L4-A06", "status": "NOT_WALKED",
    "reasonKind": "missing product hook", "foundation": ["F03", "F04", "F05", "F09", "F19"],
    "owner": "gateway run/stream owner and main Stage B",
    "reason": "No accepted authoritative run terminal/handoff reader, stream/resume cursor lineage, retry identity contract or RECOVERY_DEADLINE_MS binding. Public RequestRecord.agent_run_id is a log field, not proof of durable run terminal state; it cannot account every live run or exclude event gaps. Orphan/wrong-join/duplicate-retry fault hooks are missing.",
    "requiredSteps": ["freeze exact pre-close run cohort", "join original request/run/stream and any resume cursor", "require exactly one contracted outcome per run within fixed deadline", "C04 specific wrong-run/orphan/duplicate red and original-candidate restore"]
  },
  {
    "id": "L4-O07", "row": "L4-A07", "status": "NOT_WALKED",
    "reasonKind": "unpinned wire body", "foundation": ["F02", "F03", "F05", "F07", "F19"],
    "owner": "gateway billing, shared runtime and main-named independent money reviewer",
    "reason": "No public reviewed authoritative hold/settlement/settled-marker/ledger row projection, per-request charge/release contract or SETTLEMENT_DEADLINE_MS. StateProbe has no Redis reader; account/admin totals are aggregates, not conservation authority. Persistent volume identity and no-reseed witnesses plus synthetic duplicate-debit/unaccounted-hold fault seams are missing. No private ledger columns are guessed.",
    "requiredSteps": ["capture before/during/after/bounded-settlement state with pre-existing balances separate", "join org/request/run/hold and reconcile exact integer amount deltas", "prove one settlement or release and no duplicate debit after retry", "preserve Redis and data volume identities", "C05 exact violating hold/debit red and clean-case full restart restore"]
  },
  {
    "id": "L4-O08", "row": "L4-A08", "status": "NOT_WALKED",
    "reasonKind": "missing runner fault hook", "foundation": ["F03", "F04", "F05", "F19"],
    "owner": "shared walk runtime, gateway readiness and billing owners",
    "reason": "No configured readiness/sync timeline hook or admission-reopen witness and no accepted new-request stream/run/settlement fixture. A new HTTP 200 or completed mock response cannot prove readiness gating or money recovery. The same original-candidate assertions must run after removing each defect; synthetic oracle restored tests cannot replace these runs.",
    "requiredSteps": ["observe real readiness and sync before admission reopens", "new local request streams and obtains durable terminal/settlement", "full unchanged restored checks for C02/C03/C04/C05 on clean isolated case state"]
  },
  {
    "id": "L4-O09", "row": "L4-A09", "status": "NOT_WALKED",
    "reasonKind": "unpinned wire body", "foundation": ["F03", "F16", "F19"],
    "owner": "main LM-708/Stage B and shared walk runtime",
    "reason": "No admitted served/admitted/parts manifest and actual process/container census projection with concrete local /omega probe paths. Packet specifies only a pattern; guessing concrete endpoints or treating an empty route list as absent is forbidden. C07 synthetic detector tests use opaque probe slots, not HTTP receipts; actual no-EE/no-edge and all-404 restored proof remains owed.",
    "requiredSteps": ["bind explicitly attested absent tuple, never UNKNOWN", "complete real EE/edge process and container census", "exact nonempty manifest-bound local route set returns 404", "record ee_restart NOT_APPLICABLE_ABSENT without waiving gateway restart", "C07 detector reds plus real absent-topology restored evidence"]
  },
  {
    "id": "L4-O10", "row": "L4-A10", "status": "NOT_WALKED",
    "reasonKind": "missing runner fault hook", "foundation": ["F01", "F17", "F19"],
    "owner": "shared browser walk runtime and main app-side Stage A/B",
    "reason": "No accepted exact-runtime all-context observer/app-local-inert receipt join or isolated nonlocal-name sink controls for this candidate and both browsers. A consumer must not invent an egress guard, contact external hosts or self-classify blocked attempts. Accepted original runtime C08 qualification must be consumed later.",
    "requiredSteps": ["bind all-local/inert app half and all-context browser half", "zero ordinary attempts including blocked/aborted/sinkholed", "C08 designated page/worker/service-worker/WebSocket reds and unchanged two-browser restore"]
  }
] as const;

export const CONTROL_OBLIGATIONS = [
  { "id": "C00", "status": "OWED", "rows": ["L4-A00"], "reasonKind": "missing runner fault hook", "owed": ["L4-O00"], "negative": "Exact-runtime mismatched identity, missing participant final and cut-output admission/collector controls must reject.", "restored": "Exact pins and complete native originals, never repaired captures." },
  { "id": "C01", "status": "OWED", "rows": ["L4-A01", "L4-A02", "L4-A05"], "reasonKind": "missing runner fault hook", "owed": ["L4-O01", "L4-O02", "L4-O05"], "negative": "Non-serving target or omitted restart must fail actual serving-incarnation/causal checks.", "restored": "Restart resolved real serving process and measure one-replica interruption." },
  { "id": "C02", "status": "OWED", "rows": ["L4-A03", "L4-A08"], "reasonKind": "missing runner fault hook", "owed": ["L4-O03", "L4-O08"], "negative": "Reviewed admission-open/early-reopen defect must create a forbidden effect caught by unchanged checks.", "restored": "Original admission refuses without effects, then admits after actual readiness." },
  { "id": "C03", "status": "OWED", "rows": ["L4-A04", "L4-A05", "L4-A08"], "reasonKind": "missing runner fault hook", "owed": ["L4-O04", "L4-O05", "L4-O08"], "negative": "Unresolved drain and changed authoritative parts/state each refuse mutation; a separate bypass-drain mutant must fail causal ordering, not pass on fake refusal.", "restored": "Resolve obligation and restore authoritative state, then unchanged bounded real lifecycle." },
  { "id": "C04", "status": "OWED", "rows": ["L4-A06", "L4-A08"], "reasonKind": "missing runner fault hook", "owed": ["L4-O06", "L4-O08"], "negative": "Isolated actual workload orphan/wrong stream join/duplicate retry must identify the exact run.", "restored": "Every original pre-close run has one contracted outcome and valid cursor lineage." },
  { "id": "C05", "status": "OWED", "rows": ["L4-A07", "L4-A08"], "reasonKind": "missing runner fault hook", "owed": ["L4-O07", "L4-O08"], "negative": "Accepted synthetic financial mutation adds duplicate settlement/debit or unaccounted hold, identified by exact reconciliation.", "restored": "Clean isolated case before entire restart, with original contract and one settlement/release per hold." },
  { "id": "C06", "status": "ORACLE_AUTHORED_LIVE_RESTORE_OWED", "rows": ["L4-A02"], "reasonKind": "missing runner fault hook", "owed": ["L4-O02"], "spec": "reporting-controls.spec.ts", "variants": 6, "negative": "Six separately inventoried synthetic report defects fail targeted unchanged oracle assertions; not executed during authoring.", "restored": "Each has an independent clean synthetic fixture using the same assertions. Actual probe-derived one-replica restored report remains owed." },
  { "id": "C07", "status": "ORACLE_AUTHORED_LIVE_RESTORE_OWED", "rows": ["L4-A09"], "reasonKind": "unpinned wire body", "owed": ["L4-O09"], "spec": "reporting-controls.spec.ts", "variants": 8, "negative": "Eight separately inventoried synthetic absence defects cover EE process, edge container, non-404, missing/incomplete census, missing/duplicate route receipt and wrong tuple; not executed.", "restored": "Each has a fresh all-404 synthetic fixture. Real selected absence census/local denial receipts remain owed; does not satisfy served EE." },
  { "id": "C08", "status": "OWED", "rows": ["L4-A10"], "reasonKind": "missing runner fault hook", "owed": ["L4-O10"], "negative": "Consume accepted exact-runtime isolated all-context nonlocal-name sink failures with zero external delivery.", "restored": "Remove planted attempts, zero ordinary attempts and inert/local app in both browsers." }
] as const;

/** L4 has no literal state_protocol key. These requirements are sourced from
 * assertion_inventory (especially A03/A04/A06/A07/A08), execution_sequence_for_later_admitted_stage
 * and outputs; this object never claims to have captured those snapshots.
 */
export const L4_STATE_AND_OUTPUT_POLICY = {
  "executionStatus": "NOT_WALKED",
  "executionAuthorized": false,
  "productAccepted": false,
  "snapshots": ["state-before.json", "state-during-drain.json", "state-after.json", "financial-reconciliation.jsonl after fixed settlement interval"],
  "joinKeys": ["synthetic organization", "request_id", "run_id", "stream_id and contracted cursor", "hold identity", "settlement identity"],
  "preservation": "Same Redis/data volumes through measured restart; no flush/reseed. Separate pre-existing balance. Read unavailable state as unavailable, never zero. Fresh state for restored control only before its entire restart.",
  "ordering": ["live streamed event and durable hold", "close acknowledged", "bounded drain", "all pre-close runs accounted", "authoritative parts/state revalidated", "actual serving restart", "configured readiness/sync", "admission reopen", "new stream/run outcome and settled finance"],
  "deadlinePolicy": "DRAIN_DEADLINE_MS, RECOVERY_DEADLINE_MS and SETTLEMENT_DEADLINE_MS must be bound to product contracts; no invented timeout, POLL substitution or widening.",
  "eeBranch": "Gateway restart mandatory. Separate served-EE case after gateway fully restored, or explicit attested no-process/local-404 absent case. UNKNOWN or mixed topology refuses dispatch.",
  "runtime": "<WORKSPACE>/futureagi/tmp/gw-walk-59k-kit",
  "runtimePolicy": "Preserve W20 invocation/scratch/reporter/context/fixture entry points; admit additive journey selection and one-replica hosted shape through owner, never a consumer dispatcher.",
  "browsers": ["chromium", "firefox"],
  "workers": 1,
  "isolation": "Disjoint synthetic orgs per browser and case; one fault family at a time on exclusive stack; no shared fault host with L3/L5/L6.",
  "requiredOutputs": ["RESULT.json", "binding.json", "executed-inventory.json", "assertions.json", "timeline.jsonl", "run-stream-join.jsonl", "financial-reconciliation.jsonl", "interruption.json", "ee-restart-or-absence/", "controls/", "restored-green/", "per-invocation-logs/", "certificate/", "receipt.json", "EGRESS708.txt"],
  "outputRoot": "<WORKSPACE>/omega-launch/plan/JOURNEYS-708/packets/J27/L4/evidence/{candidate_head}/{tuple}/{invocation_id}",
  "custody": "Accepted runner lands immutable separate per-invocation originals; no sanitizing, rewriting, splitting or fabricated receipts. This source writes no output files and edits no walked list.",
  "fallback": "Unrun/unqualified/missing hooks -> NOT_WALKED. Real executed assertion failure -> WALKED_FAIL. Only complete same-tuple exact-digest 59k/CLEAN/no-Skill/money-reviewed evidence can support WALKED_PASS; oracle-only fixtures never do.",
  "release": "L4 always applies. Missing gateway restart proof holds release; EE absence, dark UI and source acceptance do not waive it. Main owns final disposition and notification."
} as const;
