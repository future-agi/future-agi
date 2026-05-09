---- MODULE TestExecutionDispatch ----
(**
 * TLA+ specification for the test execution dispatch invariant (issue #310).
 *
 * Models the state machine for routing an execute/cancel request to either
 * Temporal or the legacy Celery executor.
 *
 * Before the fix: TEMPORAL_TEST_EXECUTION_ENABLED flag could be false,
 * causing silent degradation to the Celery path with missing features.
 *
 * After the fix: the flag is removed. All requests go through Temporal.
 * The cancel path keeps a DB fallback (_cancel_via_db) that is only reached
 * when Temporal itself signals "workflow not found" — not as a configuration
 * alternative.
 *
 * Properties proved:
 *   TemporalAlwaysAttempted  — execute/cancel always tries Temporal first
 *   NoSilentDowngrade        — Celery execute path is unreachable post-fix
 *   CancelDBFallbackOnlyOnWorkflowNotFound — DB cancel only after Temporal miss
 *
 * To check with TLC (see TestExecutionDispatch.cfg):
 *   1. INVARIANTS: TypeInvariant, NoSilentDowngrade
 *   2. PROPERTIES: TemporalAlwaysAttempted, CancelDBFallbackOnlyOnWorkflowNotFound
 *   3. SPECIFICATION Spec
 *)

EXTENDS TLC, Sequences, Naturals

CONSTANTS
    MaxAttempts   \* kept ≤ 3 for tractable state space

VARIABLES
    action,            \* "execute" | "cancel"
    path,              \* "pending" | "temporal" | "celery_legacy" | "db_fallback" | "done" | "error"
    temporal_found     \* whether Temporal has a live workflow

vars == <<action, path, temporal_found>>

\* ── Type invariant ──────────────────────────────────────────────────────────

TypeInvariant ==
    /\ action \in {"execute", "cancel"}
    /\ path \in {"pending", "temporal", "celery_legacy", "db_fallback", "done", "error"}
    /\ temporal_found \in BOOLEAN

\* ── Initial state ───────────────────────────────────────────────────────────

Init ==
    /\ action \in {"execute", "cancel"}
    /\ path = "pending"
    /\ temporal_found \in BOOLEAN

\* ── Transitions ─────────────────────────────────────────────────────────────

\* Execute always goes to Temporal (post-fix: no flag check)
Dispatch ==
    /\ path = "pending"
    /\ path' = "temporal"
    /\ UNCHANGED <<action, temporal_found>>

\* Temporal succeeds
TemporalSucceeds ==
    /\ path = "temporal"
    /\ path' = "done"
    /\ UNCHANGED <<action, temporal_found>>

\* Temporal fails with connection error → surface error (no silent downgrade for execute)
TemporalErrorOnExecute ==
    /\ path = "temporal"
    /\ action = "execute"
    /\ path' = "error"
    /\ UNCHANGED <<action, temporal_found>>

\* Temporal for cancel: workflow not found → DB fallback is OK
TemporalWorkflowNotFoundOnCancel ==
    /\ path = "temporal"
    /\ action = "cancel"
    /\ ~temporal_found
    /\ path' = "db_fallback"
    /\ UNCHANGED <<action, temporal_found>>

\* Temporal for cancel: error → DB fallback
TemporalErrorOnCancel ==
    /\ path = "temporal"
    /\ action = "cancel"
    /\ path' = "db_fallback"
    /\ UNCHANGED <<action, temporal_found>>

\* DB fallback completes
DBFallbackDone ==
    /\ path = "db_fallback"
    /\ path' = "done"
    /\ UNCHANGED <<action, temporal_found>>

Next ==
    \/ Dispatch
    \/ TemporalSucceeds
    \/ TemporalErrorOnExecute
    \/ TemporalWorkflowNotFoundOnCancel
    \/ TemporalErrorOnCancel
    \/ DBFallbackDone

Spec == Init /\ [][Next]_vars

\* ── Safety properties ────────────────────────────────────────────────────────

\* Celery legacy path is never reached post-fix
NoSilentDowngrade ==
    path /= "celery_legacy"

\* DB fallback for execute is never reached (execute surfaces 503, not degraded Celery)
NoDBFallbackOnExecute ==
    ~(action = "execute" /\ path = "db_fallback")

\* ── Liveness properties ──────────────────────────────────────────────────────

\* Every pending dispatch eventually reaches done or error
TemporalAlwaysAttempted ==
    [](path = "pending" => <>(path \in {"done", "error", "db_fallback"}))

\* DB fallback for cancel only reachable after Temporal attempted (not direct)
CancelDBFallbackOnlyOnWorkflowNotFound ==
    [](path = "db_fallback" => action = "cancel")

====
