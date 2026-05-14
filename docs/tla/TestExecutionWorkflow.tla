----------------------- MODULE TestExecutionWorkflow -----------------------
(*
 * Formal specification of the simulate TestExecutionWorkflow.
 *
 * Models the five-phase Temporal workflow that orchestrates simulation
 * test runs: setup → create call records → launch calls → evaluate → finalize.
 *
 * Sources:
 *   futureagi/simulate/temporal/workflows/test_execution_workflow.py
 *   futureagi/simulate/temporal/activities/test_execution.py
 *   futureagi/simulate/models/test_execution.py  (status enums)
 *
 * To check with TLC (see TestExecutionWorkflow.cfg for the full configuration):
 *   1. Set N_CALLS <- 3 in the model configuration
 *   2. INVARIANTS (pure state predicates):
 *        TypeInvariant, CountIntegrity, FinalizationCorrect
 *   3. PROPERTIES (temporal/box-action formulas):
 *        NoRollback, TexStatusMonotone, NoFailedToOngoing,
 *        EventuallyTerminates, AllCallsEventuallyTerminal,
 *        TerminalCallsImplyFinalization
 *   4. SPECIFICATION Spec  (includes Fairness for liveness)
 *)

EXTENDS Integers, FiniteSets, TLC

CONSTANTS N_CALLS  \* Total calls that will be created (dataset size)

ASSUME N_CALLS \in Nat /\ N_CALLS > 0

(* =========================================================
   State variables
   ========================================================= *)

VARIABLES
  wf_phase,      \* Current workflow phase
  tex_status,    \* TestExecution.ExecutionStatus in DB
  calls,         \* Set of created call IDs (subset of 1..N_CALLS)
  call_status,   \* Function: call ID -> CallExecution.CallStatus
  total_calls,   \* TestExecution.total_calls counter (set after creation)
  completed,     \* Count of calls that reached COMPLETED
  failed_count   \* Count of calls that reached FAILED or CANCELLED

vars == <<wf_phase, tex_status, calls, call_status, total_calls, completed, failed_count>>

(* =========================================================
   Status sets — mirror the Django TextChoices enums
   ========================================================= *)

WorkflowPhases == {
  "Initializing", "Launching", "Running",
  "Evaluating", "Finalizing", "Done", "WorkflowFailed", "Cancelled"
}

\* TestExecution.ExecutionStatus
TexStatuses == {
  "pending", "running", "completed", "failed", "cancelled", "evaluating"
}

\* CallExecution.CallStatus
CallStatuses == {
  "pending", "ongoing", "analyzing", "completed", "failed", "cancelled"
}

TerminalCallStatuses == {"completed", "failed", "cancelled"}

\* The workflow phases that represent terminal states
TerminalPhases == {"Done", "WorkflowFailed", "Cancelled"}

(* =========================================================
   Type invariant
   ========================================================= *)

TypeInvariant ==
  /\ wf_phase \in WorkflowPhases
  /\ tex_status \in TexStatuses
  /\ calls \subseteq 1..N_CALLS
  /\ \A c \in calls : call_status[c] \in CallStatuses
  /\ total_calls \in 0..N_CALLS
  /\ completed \in 0..N_CALLS
  /\ failed_count \in 0..N_CALLS
  /\ completed + failed_count <= N_CALLS

(* =========================================================
   Initial state
   ========================================================= *)

Init ==
  /\ wf_phase    = "Initializing"
  /\ tex_status  = "pending"
  /\ calls       = {}
  /\ call_status = [c \in 1..N_CALLS |-> "pending"]  \* pre-allocated, not yet "created"
  /\ total_calls = 0
  /\ completed   = 0
  /\ failed_count = 0

(* =========================================================
   Actions — Initializing phase
   ========================================================= *)

\* Setup activity succeeds: transition to creating call records
SetupSucceeds ==
  /\ wf_phase = "Initializing"
  /\ tex_status = "pending"
  /\ wf_phase'   = "Launching"        \* skip straight to Launching (creation is synchronous)
  /\ tex_status' = "running"
  /\ UNCHANGED <<calls, call_status, total_calls, completed, failed_count>>

\* Setup activity fails
SetupFails ==
  /\ wf_phase = "Initializing"
  /\ wf_phase'   = "WorkflowFailed"
  /\ tex_status' = "failed"
  /\ UNCHANGED <<calls, call_status, total_calls, completed, failed_count>>

(* =========================================================
   Actions — Call record creation (part of Initializing → Launching)
   We model creation as happening atomically in SetupSucceeds for
   simplicity, but we need to model the "fail at creation" case.
   We expand: after setup, calls are created one at a time before
   Launching begins.
   ========================================================= *)

\* A new call record is created as PENDING
CreateCall(c) ==
  /\ wf_phase = "Launching"
  /\ c \in 1..N_CALLS
  /\ c \notin calls
  /\ calls'       = calls \cup {c}
  /\ UNCHANGED <<wf_phase, tex_status, call_status, total_calls, completed, failed_count>>

\* A call is marked FAILED at creation (fix #312: unresolved template tokens)
FailCallAtCreation(c) ==
  /\ wf_phase = "Launching"
  /\ c \in calls
  /\ call_status[c] = "pending"
  /\ call_status' = [call_status EXCEPT ![c] = "failed"]
  /\ failed_count' = failed_count + 1
  /\ UNCHANGED <<wf_phase, tex_status, calls, total_calls, completed>>

\* All calls have been created — set total_calls counter
CreationComplete ==
  /\ wf_phase = "Launching"
  /\ Cardinality(calls) = N_CALLS
  /\ total_calls' = N_CALLS
  /\ UNCHANGED <<wf_phase, tex_status, calls, call_status, completed, failed_count>>

(* =========================================================
   Actions — Launching phase
   ========================================================= *)

\* A PENDING call is launched (transitions to ONGOING)
LaunchCall(c) ==
  /\ wf_phase = "Launching"
  /\ c \in calls
  /\ call_status[c] = "pending"
  /\ total_calls = N_CALLS              \* creation must be complete before launching
  /\ call_status' = [call_status EXCEPT ![c] = "ongoing"]
  /\ UNCHANGED <<wf_phase, tex_status, calls, total_calls, completed, failed_count>>

\* All non-failed calls are launched → move to Running
AllLaunched ==
  /\ wf_phase = "Launching"
  /\ total_calls = N_CALLS
  /\ \A c \in calls : call_status[c] /= "pending"
  /\ wf_phase' = "Running"
  /\ UNCHANGED <<tex_status, calls, call_status, total_calls, completed, failed_count>>

(* =========================================================
   Actions — Running phase (calls in progress)
   ========================================================= *)

\* A call transitions from ONGOING to ANALYZING (call audio/text received)
CallAnalyzing(c) ==
  /\ wf_phase \in {"Running", "Evaluating"}
  /\ c \in calls
  /\ call_status[c] = "ongoing"
  /\ call_status' = [call_status EXCEPT ![c] = "analyzing"]
  /\ UNCHANGED <<wf_phase, tex_status, calls, total_calls, completed, failed_count>>

\* All calls are at least analyzing → move to Evaluating
AllAnalyzing ==
  /\ wf_phase = "Running"
  /\ \A c \in calls : call_status[c] \in (CallStatuses \ {"pending", "ongoing"})
  /\ wf_phase'   = "Evaluating"
  /\ tex_status' = "evaluating"
  /\ UNCHANGED <<calls, call_status, total_calls, completed, failed_count>>

\* Cancel a pending or ongoing call
CancelCall(c) ==
  /\ c \in calls
  /\ call_status[c] \in {"pending", "ongoing", "analyzing"}
  /\ call_status'  = [call_status EXCEPT ![c] = "cancelled"]
  /\ failed_count' = failed_count + 1
  /\ UNCHANGED <<wf_phase, tex_status, calls, total_calls, completed>>

(* =========================================================
   Actions — Evaluating phase (eval scoring in progress)
   ========================================================= *)

\* Evaluation of a call completes successfully
EvalCompletes(c) ==
  /\ wf_phase = "Evaluating"
  /\ c \in calls
  /\ call_status[c] = "analyzing"
  /\ call_status' = [call_status EXCEPT ![c] = "completed"]
  /\ completed'   = completed + 1
  /\ UNCHANGED <<wf_phase, tex_status, calls, total_calls, failed_count>>

\* Evaluation of a call fails
EvalFails(c) ==
  /\ wf_phase = "Evaluating"
  /\ c \in calls
  /\ call_status[c] = "analyzing"
  /\ call_status'  = [call_status EXCEPT ![c] = "failed"]
  /\ failed_count' = failed_count + 1
  /\ UNCHANGED <<wf_phase, tex_status, calls, total_calls, completed>>

\* All calls reached terminal state → move to Finalizing
AllCallsTerminal ==
  /\ wf_phase = "Evaluating"
  /\ \A c \in calls : call_status[c] \in TerminalCallStatuses
  /\ wf_phase' = "Finalizing"
  /\ UNCHANGED <<tex_status, calls, call_status, total_calls, completed, failed_count>>

(* =========================================================
   Actions — Finalization phase
   ========================================================= *)

\* Finalize: at least one call completed → COMPLETED
FinalizeCompleted ==
  /\ wf_phase = "Finalizing"
  /\ completed > 0
  /\ wf_phase'   = "Done"
  /\ tex_status' = "completed"
  /\ UNCHANGED <<calls, call_status, total_calls, completed, failed_count>>

\* Finalize: no calls completed (all failed/cancelled) → FAILED
FinalizeFailed ==
  /\ wf_phase = "Finalizing"
  /\ completed = 0
  /\ wf_phase'   = "Done"
  /\ tex_status' = "failed"
  /\ UNCHANGED <<calls, call_status, total_calls, completed, failed_count>>

\* Workflow cancelled externally (Temporal handle.cancel())
WorkflowCancelled ==
  /\ wf_phase \notin TerminalPhases
  /\ wf_phase'   = "Cancelled"
  /\ tex_status' = "cancelled"
  /\ UNCHANGED <<calls, call_status, total_calls, completed, failed_count>>

(* =========================================================
   Complete next-state relation
   ========================================================= *)

Next ==
  \/ SetupSucceeds
  \/ SetupFails
  \/ \E c \in 1..N_CALLS : CreateCall(c)
  \/ \E c \in calls : FailCallAtCreation(c)
  \/ CreationComplete
  \/ \E c \in calls : LaunchCall(c)
  \/ AllLaunched
  \/ \E c \in calls : CallAnalyzing(c)
  \/ AllAnalyzing
  \/ \E c \in calls : CancelCall(c)
  \/ \E c \in calls : EvalCompletes(c)
  \/ \E c \in calls : EvalFails(c)
  \/ AllCallsTerminal
  \/ FinalizeCompleted
  \/ FinalizeFailed
  \/ WorkflowCancelled

(* =========================================================
   Fairness — enables liveness checking
   ========================================================= *)

Fairness ==
  \* Workflow phase transitions are weakly fair (if enabled, eventually fire)
  /\ WF_vars(SetupSucceeds)
  /\ WF_vars(CreationComplete)
  /\ WF_vars(AllLaunched)
  /\ WF_vars(AllAnalyzing)
  /\ WF_vars(AllCallsTerminal)
  /\ WF_vars(FinalizeCompleted)
  /\ WF_vars(FinalizeFailed)
  \* Per-call creation and launch are weakly fair
  /\ \A c \in 1..N_CALLS :
       /\ WF_vars(CreateCall(c))
       /\ WF_vars(LaunchCall(c))
       /\ WF_vars(CallAnalyzing(c))
  \* Eval completion/failure is strongly fair (external agent eventually responds)
  /\ \A c \in 1..N_CALLS :
       /\ SF_vars(EvalCompletes(c))
       /\ SF_vars(EvalFails(c))

Spec == Init /\ [][Next]_vars /\ Fairness

(* =========================================================
   Safety properties
   ========================================================= *)

\* CallExecution status only advances (never regresses)
\* Encodes the ordering: pending < ongoing < analyzing < terminal
CallStatusRank(s) ==
  CASE s = "pending"   -> 0
    [] s = "ongoing"   -> 1
    [] s = "analyzing" -> 2
    [] s = "completed" -> 3
    [] s = "failed"    -> 3
    [] s = "cancelled" -> 3
    [] OTHER           -> -1

NoRollback ==
  [][\A c \in calls :
    CallStatusRank(call_status'[c]) >= CallStatusRank(call_status[c])]_call_status

\* TestExecution status only advances
TexStatusRank(s) ==
  CASE s = "pending"    -> 0
    [] s = "running"    -> 1
    [] s = "evaluating" -> 2
    [] s = "completed"  -> 3
    [] s = "failed"     -> 3
    [] s = "cancelled"  -> 3
    [] OTHER            -> -1

TexStatusMonotone ==
  [][TexStatusRank(tex_status') >= TexStatusRank(tex_status)]_tex_status

\* total_calls matches actual call count once creation is done
CountIntegrity ==
  (total_calls > 0) => (total_calls = Cardinality(calls))

\* A call in a terminal state never transitions to ongoing.
\* Expressed as a box-action formula (goes in PROPERTIES, not INVARIANTS).
\* This is the key correctness property for issue #312: calls failed at
\* creation due to unresolved tokens must never be launched.
NoFailedToOngoing ==
  [][\A c \in calls :
    (call_status[c] \in TerminalCallStatuses) =>
    (call_status'[c] \in TerminalCallStatuses)]_call_status

\* Finalization is correct:
\* COMPLETED iff at least one call completed; FAILED iff all calls failed/cancelled
FinalizationCorrect ==
  (wf_phase = "Done") =>
    ( (tex_status = "completed") <=> (completed > 0) )

\* No call is ever both completed and failed (trivial but validates the model)
NoDoubleTerminal ==
  ~(completed > 0 /\ failed_count > 0 /\
    \E c \in calls : call_status[c] = "completed" /\
    \E c2 \in calls : call_status[c2] = "failed" /\
    completed + failed_count > Cardinality(calls))

(* =========================================================
   Liveness properties (require Fairness in Spec)
   ========================================================= *)

\* Workflow eventually reaches a terminal phase
EventuallyTerminates == <>(wf_phase \in TerminalPhases)

\* Every created call eventually reaches a terminal status.
\* Uses ~> (leads-to) rather than => <> to avoid vacuous satisfaction
\* at the initial state where calls = {} (no call is yet created).
\* ~> means: in every state where the antecedent holds, the consequent
\* holds in some future state — checked only over states where c \in calls.
AllCallsEventuallyTerminal ==
  \A c \in 1..N_CALLS :
    (c \in calls) ~> (call_status[c] \in TerminalCallStatuses)

\* Once all calls are terminal, finalization eventually fires
TerminalCallsImplyFinalization ==
  (\A c \in calls : call_status[c] \in TerminalCallStatuses) ~>
  (wf_phase \in TerminalPhases)

=============================================================================
