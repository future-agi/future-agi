--------------------------- MODULE SimulateCLI ---------------------------
(*
 * Formal specification of the fi-simulate CLI / MCP polling state machine.
 *
 * Models the end-to-end lifecycle of a headless simulation run:
 *   authenticate → start execution → poll until terminal → fetch summary → exit
 *
 * Architecture decision: docs/adr/ADR-034-fi-simulate-cli-mcp.md
 *
 * Sources:
 *   futureagi/sdk/cli/main.py       (CLI entry point)
 *   futureagi/sdk/cli/poll.py       (polling state machine)
 *   futureagi/simulate/views/run_test.py (backend endpoints)
 *
 * To check with TLC:
 *   1. CONSTANTS: MAX_POLLS = 5, TIMEOUT_S = 30, POLL_INTERVAL_S = 5
 *   2. INVARIANTS: TypeInvariant, NeverPollBeforeStart, SummaryOnlyAfterTerminal
 *   3. PROPERTIES:  EventuallyTerminates, IfCompletedThenSummary, TimeoutSafe
 *   4. SPECIFICATION Spec
 *)

EXTENDS Integers, TLC

CONSTANTS
    MAX_POLLS,      \* Maximum poll attempts before timeout
    TIMEOUT_S,      \* Total timeout in seconds
    POLL_INTERVAL_S \* Seconds between polls

ASSUME MAX_POLLS \in Nat /\ MAX_POLLS > 0
ASSUME TIMEOUT_S \in Nat /\ TIMEOUT_S > 0
ASSUME POLL_INTERVAL_S \in Nat /\ POLL_INTERVAL_S > 0
ASSUME MAX_POLLS * POLL_INTERVAL_S <= TIMEOUT_S

(* =========================================================
   State variables
   ========================================================= *)

VARIABLES
    phase,          \* CLI lifecycle phase
    execution_id,   \* Assigned by backend on StartExecution (None until then)
    run_status,     \* Last known execution status from backend
    polls_done,     \* Number of poll attempts made
    elapsed_s,      \* Simulated elapsed seconds
    pass_rate,      \* Final pass rate (0..100 integer for TLC tractability)
    exit_code       \* 0 = success, 1 = failure/error, -1 = not yet set

vars == <<phase, execution_id, run_status, polls_done, elapsed_s, pass_rate, exit_code>>

(* =========================================================
   Type sets
   ========================================================= *)

Phases == {
    "init", "authenticating", "starting", "polling",
    "summarizing", "done", "failed", "timed_out"
}

TerminalPhases == {"done", "failed", "timed_out"}

RunStatuses == {"none", "pending", "running", "completed", "failed", "cancelled"}

TerminalRunStatuses == {"completed", "failed", "cancelled"}

(* =========================================================
   Type invariant
   ========================================================= *)

TypeInvariant ==
    /\ phase \in Phases
    /\ execution_id \in {"none"} \cup {"exec_1"}  \* TLC: one possible ID
    /\ run_status \in RunStatuses
    /\ polls_done \in 0..MAX_POLLS
    /\ elapsed_s \in 0..TIMEOUT_S
    /\ pass_rate \in 0..100
    /\ exit_code \in {-1, 0, 1}

(* =========================================================
   Initial state
   ========================================================= *)

Init ==
    /\ phase        = "init"
    /\ execution_id = "none"
    /\ run_status   = "none"
    /\ polls_done   = 0
    /\ elapsed_s    = 0
    /\ pass_rate    = 0
    /\ exit_code    = -1

(* =========================================================
   Actions
   ========================================================= *)

Authenticate ==
    /\ phase = "init"
    /\ phase' = "authenticating"
    /\ UNCHANGED <<execution_id, run_status, polls_done, elapsed_s, pass_rate, exit_code>>

AuthSucceeds ==
    /\ phase = "authenticating"
    /\ phase' = "starting"
    /\ UNCHANGED <<execution_id, run_status, polls_done, elapsed_s, pass_rate, exit_code>>

AuthFails ==
    /\ phase = "authenticating"
    /\ phase'     = "failed"
    /\ exit_code' = 1
    /\ UNCHANGED <<execution_id, run_status, polls_done, elapsed_s, pass_rate>>

StartExecution ==
    /\ phase = "starting"
    /\ execution_id = "none"
    /\ elapsed_s < TIMEOUT_S
    /\ phase'        = "polling"
    /\ execution_id' = "exec_1"
    /\ run_status'   = "pending"
    /\ elapsed_s'    = elapsed_s + POLL_INTERVAL_S
    /\ UNCHANGED <<polls_done, pass_rate, exit_code>>

StartFails ==
    /\ phase = "starting"
    /\ phase'     = "failed"
    /\ exit_code' = 1
    /\ UNCHANGED <<execution_id, run_status, polls_done, elapsed_s, pass_rate>>

\* Poll: execution still in progress
PollContinues ==
    /\ phase = "polling"
    /\ execution_id /= "none"
    /\ run_status \notin TerminalRunStatuses
    /\ polls_done < MAX_POLLS
    /\ elapsed_s + POLL_INTERVAL_S <= TIMEOUT_S
    /\ polls_done' = polls_done + 1
    /\ elapsed_s'  = elapsed_s + POLL_INTERVAL_S
    /\ run_status' \in {"pending", "running"}  \* non-deterministic: still in progress
    /\ UNCHANGED <<phase, execution_id, pass_rate, exit_code>>

\* Poll: execution reached terminal status
PollTerminates ==
    /\ phase = "polling"
    /\ execution_id /= "none"
    /\ run_status \notin TerminalRunStatuses
    /\ polls_done < MAX_POLLS
    /\ elapsed_s + POLL_INTERVAL_S <= TIMEOUT_S
    /\ polls_done' = polls_done + 1
    /\ elapsed_s'  = elapsed_s + POLL_INTERVAL_S
    /\ run_status' \in TerminalRunStatuses
    /\ phase'      = "summarizing"
    /\ UNCHANGED <<execution_id, pass_rate, exit_code>>

\* Timeout during polling
PollTimeout ==
    /\ phase = "polling"
    /\ elapsed_s + POLL_INTERVAL_S > TIMEOUT_S
    /\ phase'     = "timed_out"
    /\ exit_code' = 1
    /\ UNCHANGED <<execution_id, run_status, polls_done, elapsed_s, pass_rate>>

\* Summary fetched — pass rate above threshold (exit 0)
SummaryPass ==
    /\ phase = "summarizing"
    /\ run_status = "completed"
    /\ pass_rate' \in 80..100  \* above threshold
    /\ phase'     = "done"
    /\ exit_code' = 0
    /\ UNCHANGED <<execution_id, run_status, polls_done, elapsed_s>>

\* Summary fetched — pass rate below threshold (exit 1)
SummaryFail ==
    /\ phase = "summarizing"
    /\ run_status = "completed"
    /\ pass_rate' \in 0..79  \* below threshold
    /\ phase'     = "done"
    /\ exit_code' = 1
    /\ UNCHANGED <<execution_id, run_status, polls_done, elapsed_s>>

\* Execution failed/cancelled — no meaningful summary
SummarySkipped ==
    /\ phase = "summarizing"
    /\ run_status \in {"failed", "cancelled"}
    /\ phase'     = "failed"
    /\ exit_code' = 1
    /\ UNCHANGED <<execution_id, run_status, polls_done, elapsed_s, pass_rate>>

(* =========================================================
   Complete next-state relation
   ========================================================= *)

Next ==
    \/ Authenticate
    \/ AuthSucceeds
    \/ AuthFails
    \/ StartExecution
    \/ StartFails
    \/ PollContinues
    \/ PollTerminates
    \/ PollTimeout
    \/ SummaryPass
    \/ SummaryFail
    \/ SummarySkipped

(* =========================================================
   Fairness
   ========================================================= *)

Fairness ==
    /\ WF_vars(AuthSucceeds)
    /\ WF_vars(StartExecution)
    /\ WF_vars(PollTerminates)
    /\ WF_vars(SummaryPass \/ SummaryFail \/ SummarySkipped)

Spec == Init /\ [][Next]_vars /\ Fairness

(* =========================================================
   Safety invariants
   ========================================================= *)

\* Polling only begins after execution_id is assigned
NeverPollBeforeStart ==
    (phase = "polling") => (execution_id /= "none")

\* Summary only fetched after backend reaches a terminal status
SummaryOnlyAfterTerminal ==
    (phase = "summarizing") => (run_status \in TerminalRunStatuses)

\* Elapsed time never exceeds timeout
TimeoutBounded ==
    elapsed_s <= TIMEOUT_S

\* Exit code only set in terminal phases
ExitCodeOnlyWhenTerminal ==
    (exit_code /= -1) => (phase \in TerminalPhases)

\* Once a terminal phase is reached, it is stable (no transitions out)
TerminalIsStable ==
    [][phase \in TerminalPhases => phase' = phase]_phase

(* =========================================================
   Liveness properties
   ========================================================= *)

\* The CLI always eventually terminates
EventuallyTerminates ==
    <>(phase \in TerminalPhases)

\* If execution completes, summary is eventually fetched
IfCompletedThenSummary ==
    (run_status = "completed") ~> (phase \in {"done", "failed"})

\* Timeout safety: if MAX_POLLS is exhausted, we reach timed_out
TimeoutSafe ==
    (polls_done = MAX_POLLS /\ run_status \notin TerminalRunStatuses)
    ~> (phase = "timed_out")

=============================================================================
