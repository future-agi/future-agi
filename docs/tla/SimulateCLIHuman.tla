---- MODULE SimulateCLIHuman ----
(*
  TLA+ spec for the human-facing fi-simulate CLI extension.

  Adds three new subcommands on top of the existing SimulateCLI run-and-poll
  state machine:

    list   — fetch and display all simulation suites; read-only
    run    — resolve a suite by name (or UUID), then delegate to the existing
             run-and-poll machine
    status — fetch and display current status of a specific execution

  Architecture decision: ADR-035

  Key new invariants (in addition to those from SimulateCLI.tla):

    NameResolutionBeforeStart
      A run subcommand that starts with a name query never dispatches
      an execution before the name has been resolved to exactly one UUID.

    AmbiguousNameFails
      If name resolution returns more than one matching suite, the phase
      transitions to FAILED immediately — no auto-selection.

    ZeroMatchesFails
      If name resolution returns zero suites, the phase transitions to
      FAILED immediately — no fallback to UUID.

    ListIsStateless
      The list subcommand never transitions to starting, polling, or any
      phase that would dispatch server-side work.

    StatusIsStateless
      The status subcommand never dispatches an execution — it is read-only.
*)

EXTENDS Naturals, Sequences, TLC

CONSTANTS
    MaxPolls,       \* upper bound on poll attempts
    MaxSuites,      \* maximum suites returned by list
    SuiteNames      \* set of possible suite name strings

VARIABLES
    phase,          \* current phase of the state machine
    subcommand,     \* which subcommand is active: "list" | "run" | "status"
    name_query,     \* the name/pattern the user typed (or "" for UUID-direct)
    run_test_id,    \* resolved UUID (or user-supplied UUID)
    matches,        \* number of suites matching name_query
    execution_id,   \* set after execution is dispatched
    polls_done,     \* number of poll attempts
    run_status,     \* server-reported run status
    phase_locked    \* TRUE once a terminal phase is reached

vars == <<phase, subcommand, name_query, run_test_id, matches,
          execution_id, polls_done, run_status, phase_locked>>

\* ---- Phase definitions ----

Phase == {
    "init",
    "listing",          \* fetching suite list (list subcommand or name resolution)
    "resolving",        \* applying name → UUID resolution after listing
    "starting",         \* dispatching execution (run subcommand only)
    "polling",          \* polling for terminal status
    "summarizing",      \* fetching final results
    "done",             \* terminal: success
    "failed",           \* terminal: error / ambiguous / not-found
    "timed_out"         \* terminal: timeout exceeded
}

Terminal == {"done", "failed", "timed_out"}

RunStatus == {
    "none",             \* no status known yet
    "pending",
    "running",
    "completed",
    "failed",
    "cancelled"
}

TerminalRunStatus == {"completed", "failed", "cancelled"}

\* ---- Type invariant ----

TypeOK ==
    /\ phase \in Phase
    /\ subcommand \in {"list", "run", "status"}
    /\ name_query \in STRING \cup {""}
    /\ run_test_id \in STRING \cup {""}
    /\ matches \in 0..MaxSuites
    /\ execution_id \in STRING \cup {""}
    /\ polls_done \in 0..MaxPolls
    /\ run_status \in RunStatus
    /\ phase_locked \in BOOLEAN

\* ---- Safety invariants ----

\* A run subcommand that started with a name query never begins execution
\* before the name has been resolved to a single UUID.
NameResolutionBeforeStart ==
    (phase = "starting" /\ name_query /= "") =>
        (run_test_id /= "" /\ matches = 1)

\* Ambiguous name resolution always leads to FAILED, never to starting.
AmbiguousNameFails ==
    (subcommand = "run" /\ matches > 1) =>
        (phase = "failed" \/ phase = "resolving")

\* Zero matches from a name query always leads to FAILED.
ZeroMatchesFails ==
    (subcommand = "run" /\ name_query /= "" /\ matches = 0 /\ phase \notin {"init", "listing"}) =>
        phase = "failed"

\* The list subcommand never dispatches server-side work.
ListIsStateless ==
    subcommand = "list" =>
        phase \notin {"starting", "polling", "summarizing"}

\* The status subcommand never dispatches an execution.
StatusIsStateless ==
    subcommand = "status" =>
        (phase \notin {"starting", "polling", "summarizing"} /\
         execution_id = "")  \* status reads an existing execution_id from input

\* Terminal phases are stable once reached.
TerminalIsStable ==
    phase_locked => phase \in Terminal

\* Polling only happens after an execution_id is known.
NeverPollBeforeStart ==
    phase = "polling" => execution_id /= ""

\* Summary only fetched after a terminal run status.
SummaryOnlyAfterTerminal ==
    phase = "summarizing" => run_status \in TerminalRunStatus

\* ---- Liveness ----

\* Every run eventually reaches a terminal phase.
EventualTermination ==
    <>[](phase \in Terminal)

\* ---- Initial state ----

Init ==
    /\ phase = "init"
    /\ subcommand \in {"list", "run", "status"}  \* nondeterministically chosen
    /\ name_query = ""
    /\ run_test_id = ""
    /\ matches = 0
    /\ execution_id = ""
    /\ polls_done = 0
    /\ run_status = "none"
    /\ phase_locked = FALSE

\* ---- Transitions ----

\* list: fetch suites and display
ListFetch ==
    /\ subcommand = "list"
    /\ phase = "init"
    /\ phase' = "listing"
    /\ UNCHANGED <<subcommand, name_query, run_test_id, matches,
                   execution_id, polls_done, run_status, phase_locked>>

ListDone ==
    /\ subcommand = "list"
    /\ phase = "listing"
    /\ phase' = "done"
    /\ phase_locked' = TRUE
    /\ UNCHANGED <<subcommand, name_query, run_test_id, matches,
                   execution_id, polls_done, run_status>>

ListFailed ==
    /\ subcommand = "list"
    /\ phase = "listing"
    /\ phase' = "failed"
    /\ phase_locked' = TRUE
    /\ UNCHANGED <<subcommand, name_query, run_test_id, matches,
                   execution_id, polls_done, run_status>>

\* run: start by listing suites for name resolution (or skip if UUID given)
RunStartByName ==
    /\ subcommand = "run"
    /\ phase = "init"
    /\ name_query /= ""
    /\ phase' = "listing"
    /\ UNCHANGED <<subcommand, name_query, run_test_id, matches,
                   execution_id, polls_done, run_status, phase_locked>>

RunStartByUUID ==
    /\ subcommand = "run"
    /\ phase = "init"
    /\ name_query = ""
    /\ run_test_id /= ""
    /\ phase' = "starting"
    /\ UNCHANGED <<subcommand, name_query, run_test_id, matches,
                   execution_id, polls_done, run_status, phase_locked>>

\* After listing, resolve name to zero, one, or many UUIDs
ResolveAmbiguous ==
    /\ subcommand = "run"
    /\ phase = "listing"
    /\ matches > 1
    /\ phase' = "failed"
    /\ phase_locked' = TRUE
    /\ UNCHANGED <<subcommand, name_query, run_test_id,
                   execution_id, polls_done, run_status, matches>>

ResolveNotFound ==
    /\ subcommand = "run"
    /\ phase = "listing"
    /\ matches = 0
    /\ phase' = "failed"
    /\ phase_locked' = TRUE
    /\ UNCHANGED <<subcommand, name_query, run_test_id,
                   execution_id, polls_done, run_status, matches>>

ResolveSuccess ==
    /\ subcommand = "run"
    /\ phase = "listing"
    /\ matches = 1
    /\ run_test_id /= ""         \* resolved to exactly one UUID
    /\ phase' = "starting"
    /\ phase_locked' = FALSE
    /\ UNCHANGED <<subcommand, name_query, matches,
                   execution_id, polls_done, run_status, run_test_id>>

\* run: dispatch execution
StartSuccess ==
    /\ subcommand = "run"
    /\ phase = "starting"
    /\ execution_id' /= ""       \* server returns an execution_id
    /\ phase' = "polling"
    /\ UNCHANGED <<subcommand, name_query, run_test_id, matches,
                   polls_done, run_status, phase_locked>>

StartFailed ==
    /\ subcommand = "run"
    /\ phase = "starting"
    /\ phase' = "failed"
    /\ phase_locked' = TRUE
    /\ UNCHANGED <<subcommand, name_query, run_test_id, matches,
                   execution_id, polls_done, run_status>>

\* run: poll until terminal
PollNonTerminal ==
    /\ subcommand = "run"
    /\ phase = "polling"
    /\ polls_done < MaxPolls
    /\ run_status \notin TerminalRunStatus
    /\ polls_done' = polls_done + 1
    /\ UNCHANGED <<subcommand, name_query, run_test_id, matches,
                   execution_id, run_status, phase, phase_locked>>

PollTerminal ==
    /\ subcommand = "run"
    /\ phase = "polling"
    /\ run_status \in TerminalRunStatus
    /\ phase' = "summarizing"
    /\ UNCHANGED <<subcommand, name_query, run_test_id, matches,
                   execution_id, polls_done, run_status, phase_locked>>

PollTimeout ==
    /\ subcommand = "run"
    /\ phase = "polling"
    /\ polls_done = MaxPolls
    /\ phase' = "timed_out"
    /\ phase_locked' = TRUE
    /\ UNCHANGED <<subcommand, name_query, run_test_id, matches,
                   execution_id, polls_done, run_status>>

SummarizeDone ==
    /\ subcommand = "run"
    /\ phase = "summarizing"
    /\ phase' = "done"
    /\ phase_locked' = TRUE
    /\ UNCHANGED <<subcommand, name_query, run_test_id, matches,
                   execution_id, polls_done, run_status>>

SummarizeFailed ==
    /\ subcommand = "run"
    /\ phase = "summarizing"
    /\ phase' = "failed"
    /\ phase_locked' = TRUE
    /\ UNCHANGED <<subcommand, name_query, run_test_id, matches,
                   execution_id, polls_done, run_status>>

\* status: fetch a single execution's status (read-only)
StatusFetch ==
    /\ subcommand = "status"
    /\ phase = "init"
    /\ phase' = "listing"   \* reuse "listing" as "fetching" for status
    /\ UNCHANGED <<subcommand, name_query, run_test_id, matches,
                   execution_id, polls_done, run_status, phase_locked>>

StatusDone ==
    /\ subcommand = "status"
    /\ phase = "listing"
    /\ phase' = "done"
    /\ phase_locked' = TRUE
    /\ UNCHANGED <<subcommand, name_query, run_test_id, matches,
                   execution_id, polls_done, run_status>>

StatusFailed ==
    /\ subcommand = "status"
    /\ phase = "listing"
    /\ phase' = "failed"
    /\ phase_locked' = TRUE
    /\ UNCHANGED <<subcommand, name_query, run_test_id, matches,
                   execution_id, polls_done, run_status>>

\* ---- Next-state relation ----

Next ==
    \/ ListFetch \/ ListDone \/ ListFailed
    \/ RunStartByName \/ RunStartByUUID
    \/ ResolveAmbiguous \/ ResolveNotFound \/ ResolveSuccess
    \/ StartSuccess \/ StartFailed
    \/ PollNonTerminal \/ PollTerminal \/ PollTimeout
    \/ SummarizeDone \/ SummarizeFailed
    \/ StatusFetch \/ StatusDone \/ StatusFailed

\* ---- Spec ----

Spec == Init /\ [][Next]_vars /\ WF_vars(Next)

\* ---- Invariant bundle (for TLC) ----

Invariants ==
    /\ TypeOK
    /\ NameResolutionBeforeStart
    /\ AmbiguousNameFails
    /\ ZeroMatchesFails
    /\ ListIsStateless
    /\ StatusIsStateless
    /\ TerminalIsStable
    /\ NeverPollBeforeStart
    /\ SummaryOnlyAfterTerminal

====
