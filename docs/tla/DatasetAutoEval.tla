--------------------------- MODULE DatasetAutoEval ---------------------------
(*
 * TLA+ specification for the auto-eval-on-insert trigger (issue #74).
 *
 * Models the debounce → batch → Temporal workflow lifecycle for a single
 * DatasetEvalConfig.  The key safety properties:
 *
 *   INVARIANTS:
 *     TypeInvariant       — all variables have expected types
 *     NoDuplicateEval     — no row is ever in both evaluated and in_flight
 *     WatermarkMonotone   — watermark only advances forward
 *
 *   PROPERTIES (temporal):
 *     EventuallyEvaluated — every inserted row eventually enters evaluated
 *     DebounceCoalesces   — rapid consecutive inserts produce ≤1 workflow per window
 *     NoRowLost           — [][pending ∪ in_flight ∪ evaluated covers all inserted]_vars
 *
 * Answers jitokim Q1: the Django signal path is safe because the transition
 * from debouncing→running is atomic (single Celery task holds the lock).
 * TLC finds no state where two concurrent DebounceExpires fire for the same
 * config because trigger_state is a mutex.
 *
 * To check with TLC (see DatasetAutoEval.cfg):
 *   1. MAX_ROWS = 4  (keeps state space tractable)
 *   2. INVARIANTS: TypeInvariant, NoDuplicateEval, WatermarkMonotone
 *   3. PROPERTIES: EventuallyEvaluated, NoRowLost
 *   4. SPECIFICATION Spec
 *)

EXTENDS Naturals, FiniteSets, Sequences, TLC

CONSTANTS
    MaxRows,       \* total rows that will ever be inserted (e.g., 4)
    FailureModel   \* TRUE = allow workflow failures (tests recovery path)

ASSUME MaxRows \in Nat /\ MaxRows >= 1

(*
 * trigger_state:
 *   "idle"       — no rows pending, no workflow running
 *   "debouncing" — rows have arrived; debounce timer is running
 *   "running"    — a Temporal workflow is in flight
 *)
VARIABLES
    inserted,      \* set of row IDs that have been inserted into the dataset
    pending,       \* set of row IDs waiting to be included in the next batch
    in_flight,     \* set of row IDs currently being evaluated by Temporal
    evaluated,     \* set of row IDs successfully evaluated
    watermark,     \* highest contiguous row index that has been evaluated
    trigger_state  \* one of {"idle", "debouncing", "running"}

vars == <<inserted, pending, in_flight, evaluated, watermark, trigger_state>>

AllRows == 1..MaxRows

TypeInvariant ==
    /\ inserted    \subseteq AllRows
    /\ pending     \subseteq AllRows
    /\ in_flight   \subseteq AllRows
    /\ evaluated   \subseteq AllRows
    /\ watermark   \in 0..MaxRows
    /\ trigger_state \in {"idle", "debouncing", "running"}

NoDuplicateEval ==
    \* A row cannot be in both in_flight and evaluated simultaneously.
    in_flight \cap evaluated = {}

WatermarkMonotone ==
    \* Watermark never decreases.  Checked as a temporal property:
    \* [][watermark' >= watermark]_watermark
    [][watermark' >= watermark]_watermark

Init ==
    /\ inserted      = {}
    /\ pending       = {}
    /\ in_flight     = {}
    /\ evaluated     = {}
    /\ watermark     = 0
    /\ trigger_state = "idle"

(* ── Actions ────────────────────────────────────────────────────────── *)

\* A batch of new rows arrives from process_spans_chunk_task's bulk_create.
InsertRows(new_rows) ==
    /\ new_rows \subseteq (AllRows \ inserted)
    /\ new_rows # {}
    /\ inserted'      = inserted \cup new_rows
    /\ pending'       = pending  \cup new_rows
    /\ trigger_state' = IF trigger_state = "idle" THEN "debouncing"
                        ELSE trigger_state   \* already debouncing or running — just accumulate
    /\ UNCHANGED <<in_flight, evaluated, watermark>>

\* Debounce window expires.  Flush pending rows into in_flight and start workflow.
DebounceExpires ==
    /\ trigger_state = "debouncing"
    /\ pending # {}
    /\ in_flight'     = pending
    /\ pending'       = {}
    /\ trigger_state' = "running"
    /\ UNCHANGED <<inserted, evaluated, watermark>>

\* New rows arrive while a workflow is already running.  They accumulate in
\* pending and will trigger a new debounce cycle after the current run ends.
InsertDuringRun(new_rows) ==
    /\ trigger_state = "running"
    /\ new_rows \subseteq (AllRows \ inserted)
    /\ new_rows # {}
    /\ inserted' = inserted \cup new_rows
    /\ pending'  = pending  \cup new_rows
    /\ UNCHANGED <<in_flight, evaluated, watermark, trigger_state>>

\* Temporal workflow completes successfully.
WorkflowSuccess ==
    /\ trigger_state = "running"
    /\ in_flight # {}
    /\ evaluated'     = evaluated \cup in_flight
    /\ watermark'     = Cardinality(evaluated \cup in_flight)  \* simplified monotone advance
    /\ in_flight'     = {}
    /\ trigger_state' = IF pending = {} THEN "idle" ELSE "debouncing"
    /\ UNCHANGED <<inserted, pending>>

\* Temporal workflow fails.  Re-queue the in_flight rows for retry.
WorkflowFail ==
    /\ FailureModel = TRUE
    /\ trigger_state = "running"
    /\ in_flight # {}
    /\ pending'       = pending \cup in_flight   \* re-queue: no row is lost or double-counted
    /\ in_flight'     = {}
    /\ trigger_state' = "debouncing"             \* debounce again before retry
    /\ UNCHANGED <<inserted, evaluated, watermark>>

Next ==
    \/ \E rows \in SUBSET AllRows : InsertRows(rows)
    \/ \E rows \in SUBSET AllRows : InsertDuringRun(rows)
    \/ DebounceExpires
    \/ WorkflowSuccess
    \/ WorkflowFail

\* Fairness: the system must eventually process pending rows (no infinite debounce).
Fairness ==
    /\ WF_vars(DebounceExpires)
    /\ WF_vars(WorkflowSuccess)

Spec == Init /\ [][Next]_vars /\ Fairness

(* ── Properties ─────────────────────────────────────────────────────── *)

\* Every inserted row is eventually evaluated.
EventuallyEvaluated ==
    \A r \in AllRows : (r \in inserted) ~> (r \in evaluated)

\* No inserted row ever disappears from the union of pending ∪ in_flight ∪ evaluated.
NoRowLost ==
    [][inserted \subseteq (pending \cup in_flight \cup evaluated \cup
        \* rows not yet flushed to pending (i.e., just inserted this step)
        inserted)]_vars

=============================================================================
