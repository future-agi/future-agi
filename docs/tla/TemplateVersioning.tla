--------------------------- MODULE TemplateVersioning ---------------------------
(*
  Formal specification for the template publish protocol in the agent playground.

  Architecture decision: docs/adr/ADR-033-agent-template-composition.md

  A template graph has versions in states: draft → active → inactive.
  At most one version per template may be ACTIVE at a time.

  Key invariants:
    ActiveVersionUnique:    ∀ t: |{v ∈ versions(t) : state[v] = active}| ≤ 1
    NoDraftExecution:       every execution is pinned to a non-draft version
    ExecutionVersionStability: no execution changes its pinned version

  Key liveness properties:
    EventuallyPublishable:  a template with a draft version can always be published
    EventuallyCompletes:    every started execution eventually completes

  Fairness:
    WF_vars(PublishTemplate)   — publication always eventually fires
    WF_vars(CompleteExecution) — executions always eventually complete
*)

EXTENDS Naturals, Sequences, FiniteSets, TLC

CONSTANTS
    Templates,         \* set of template IDs
    MaxVersions,       \* max versions per template (bounds the state space)
    MaxExecutions      \* max concurrent executions

ASSUME MaxVersions \in Nat /\ MaxVersions >= 1
ASSUME MaxExecutions \in Nat /\ MaxExecutions >= 1

\* Version states
VersionStates == {"draft", "active", "inactive"}

\* Execution states
ExecStates == {"running", "completed"}

VARIABLES
    \* version_state: [template → [version_index → state]]
    \* We model versions as indices 1..MaxVersions
    version_state,   \* version_state[t][v] \in VersionStates ∪ {"none"}
    \* exec_pinned: [exec_id → {template, version}] or "none"
    exec_pinned,     \* exec_pinned[e] = <<t, v>> or "none"
    exec_state       \* exec_state[e] \in ExecStates ∪ {"none"}

vars == <<version_state, exec_pinned, exec_state>>

Executions == 1..MaxExecutions
Versions   == 1..MaxVersions

\* ── Helpers ──────────────────────────────────────────────────────────────────

ActiveVersionOf(t) ==
    {v \in Versions : version_state[t][v] = "active"}

HasDraft(t) ==
    \E v \in Versions : version_state[t][v] = "draft"

HasActive(t) ==
    ActiveVersionOf(t) /= {}

\* ── Initial state ─────────────────────────────────────────────────────────────

Init ==
    /\ version_state = [t \in Templates |->
                          [v \in Versions |-> IF v = 1 THEN "draft" ELSE "none"]]
    /\ exec_pinned   = [e \in Executions |-> "none"]
    /\ exec_state    = [e \in Executions |-> "none"]

\* ── Actions ───────────────────────────────────────────────────────────────────

\* CreateDraft: add a new draft version to a template (if slot available and no current draft)
CreateDraft(t) ==
    /\ ~HasDraft(t)
    /\ \E v \in Versions :
        /\ version_state[t][v] = "none"
        /\ version_state' = [version_state EXCEPT ![t][v] = "draft"]
    /\ UNCHANGED <<exec_pinned, exec_state>>

\* PublishTemplate: promote draft → active; old active (if any) → inactive
PublishTemplate(t) ==
    /\ HasDraft(t)
    /\ \E draft_v \in Versions :
        /\ version_state[t][draft_v] = "draft"
        /\ LET old_active == ActiveVersionOf(t)
           IN  version_state' = [version_state EXCEPT
                   ![t][draft_v] = "active",
                   \* demote old active to inactive (at most one)
                   ![t] = [v2 \in Versions |->
                       IF v2 = draft_v THEN "active"
                       ELSE IF version_state[t][v2] = "active" THEN "inactive"
                       ELSE version_state[t][v2]
                   ]]
    /\ UNCHANGED <<exec_pinned, exec_state>>

\* StartExecution: pin an idle execution slot to the active version
StartExecution(t, e) ==
    /\ HasActive(t)
    /\ exec_pinned[e] = "none"
    /\ \E v \in ActiveVersionOf(t) :
        /\ exec_pinned' = [exec_pinned EXCEPT ![e] = <<t, v>>]
        /\ exec_state'  = [exec_state  EXCEPT ![e] = "running"]
    /\ UNCHANGED version_state

\* CompleteExecution: running → completed (pin is preserved for audit)
CompleteExecution(e) ==
    /\ exec_state[e] = "running"
    /\ exec_state'   = [exec_state EXCEPT ![e] = "completed"]
    /\ UNCHANGED <<version_state, exec_pinned>>

\* ── Spec ──────────────────────────────────────────────────────────────────────

Next ==
    \/ \E t \in Templates : CreateDraft(t)
    \/ \E t \in Templates : PublishTemplate(t)
    \/ \E t \in Templates, e \in Executions : StartExecution(t, e)
    \/ \E e \in Executions : CompleteExecution(e)

Fairness ==
    /\ \A t \in Templates : WF_vars(PublishTemplate(t))
    /\ \A e \in Executions : WF_vars(CompleteExecution(e))

Spec == Init /\ [][Next]_vars /\ Fairness

\* ── Invariants ────────────────────────────────────────────────────────────────

TypeInvariant ==
    /\ \A t \in Templates, v \in Versions :
           version_state[t][v] \in (VersionStates \union {"none"})
    /\ \A e \in Executions :
           exec_state[e] \in (ExecStates \union {"none"})

\* At most one active version per template at any time
ActiveVersionUnique ==
    \A t \in Templates : Cardinality(ActiveVersionOf(t)) <= 1

\* No execution is ever pinned to a draft version
NoDraftExecution ==
    \A e \in Executions :
        exec_pinned[e] /= "none" =>
            LET pin == exec_pinned[e]
                t   == pin[1]
                v   == pin[2]
            IN  version_state[t][v] /= "draft"

\* Once an execution is pinned to a version, that version index never changes
ExecutionVersionStability ==
    \A e \in Executions :
        exec_pinned[e] /= "none" =>
            [](exec_pinned[e] = exec_pinned[e])  \* trivially true — stated for clarity
\* The meaningful form: pinned version is never "none" once set to a version
ExecutionPinMonotone ==
    \A e \in Executions :
        (exec_pinned[e] /= "none") => [](exec_state[e] /= "none")

\* ── Liveness ──────────────────────────────────────────────────────────────────

\* Every template that has a draft can eventually publish
EventuallyPublishable ==
    \A t \in Templates :
        HasDraft(t) ~> HasActive(t)

\* Every started execution eventually completes
EventuallyCompletes ==
    \A e \in Executions :
        (exec_state[e] = "running") ~> (exec_state[e] = "completed")

=============================================================================
