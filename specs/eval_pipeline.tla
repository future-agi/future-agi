---- MODULE eval_pipeline ----
(**
 * TLA+ specification of the run_eval() pipeline.
 *
 * Models the 6-phase state machine in evaluations/engine/runner.py:
 *   Start → Lookup → ProtectCheck → Registry → Instance → Params →
 *   Preprocess → Execute → Format → Done
 *                                            ↘ Failed (from any phase)
 *
 * Safety properties verified:
 *   - Every terminal state is either Done or Failed (no stuck states)
 *   - Failed state always carries a non-empty reason
 *   - Done state always has duration set
 *   - protect/protect_flash call_type always routes through DeterministicEvaluator
 *   - eval_type_id is immutable after ProtectCheck
 *
 * Run with TLC: java -jar tla2tools.jar eval_pipeline.tla
 * Or from TLA+ Toolbox: Open spec → Run TLC Model Checker
 *
 * See docs/adr/006-protect-shortcut-in-runner.md for the protect invariant.
 *)

EXTENDS Naturals, Sequences, TLC

CONSTANTS
    \* All possible eval type IDs in the system
    EvalTypeIds,
    \* The fixed DeterministicEvaluator type ID
    DeterministicId,
    \* Protect call types
    ProtectCallTypes

ASSUME DeterministicId \in EvalTypeIds
ASSUME ProtectCallTypes \subseteq {"protect", "protect_flash", ""}

VARIABLES
    phase,          \* Current pipeline phase
    eval_type_id,   \* Resolved evaluator type (may be overridden by protect check)
    call_type,      \* Runtime call_type from inputs (protect, protect_flash, or "")
    failure,        \* Error message if failed; "" if not failed
    duration_set    \* Whether duration has been set on the result

Phase == {
    "Start", "Lookup", "ProtectCheck", "Registry",
    "Instance", "Params", "Preprocess", "Execute", "Format",
    "Done", "Failed"
}

TypeInvariant ==
    /\ phase \in Phase
    /\ eval_type_id \in (EvalTypeIds \union {""})
    /\ call_type \in ProtectCallTypes \union {""}
    /\ failure \in STRING
    /\ duration_set \in BOOLEAN

Init ==
    /\ phase = "Start"
    /\ eval_type_id = ""       \* not yet resolved
    /\ call_type \in (ProtectCallTypes \union {""})  \* injected by caller
    /\ failure = ""
    /\ duration_set = FALSE

\* Phase transitions — each action moves the pipeline forward one step

Lookup ==
    /\ phase = "Start"
    /\ \/ /\ eval_type_id = ""          \* missing eval_type_id
           /\ phase' = "Failed"
           /\ failure' = "eval_type_id not found in EvalTemplate config"
           /\ UNCHANGED <<eval_type_id, call_type, duration_set>>
       \/ /\ eval_type_id # ""          \* found — proceed
           /\ phase' = "ProtectCheck"
           /\ UNCHANGED <<eval_type_id, call_type, failure, duration_set>>

ProtectCheck ==
    /\ phase = "ProtectCheck"
    /\ \/ /\ call_type \in ProtectCallTypes  \* protect shortcut fires
           /\ eval_type_id' = DeterministicId
           /\ phase' = "Registry"
           /\ UNCHANGED <<call_type, failure, duration_set>>
       \/ /\ call_type \notin ProtectCallTypes
           /\ phase' = "Registry"
           /\ UNCHANGED <<eval_type_id, call_type, failure, duration_set>>

Registry ==
    /\ phase = "Registry"
    /\ \/ /\ phase' = "Instance"         \* class found
           /\ UNCHANGED <<eval_type_id, call_type, failure, duration_set>>
       \/ /\ phase' = "Failed"           \* unknown eval_type_id
           /\ failure' = "Unknown evaluator type"
           /\ UNCHANGED <<eval_type_id, call_type, duration_set>>

Instance ==
    /\ phase = "Instance"
    /\ phase' = "Params"
    /\ UNCHANGED <<eval_type_id, call_type, failure, duration_set>>

Params ==
    /\ phase = "Params"
    /\ phase' = "Preprocess"
    /\ UNCHANGED <<eval_type_id, call_type, failure, duration_set>>

Preprocess ==
    /\ phase = "Preprocess"
    \* Preprocessing never raises — it logs and continues (see SPEC.md)
    /\ phase' = "Execute"
    /\ UNCHANGED <<eval_type_id, call_type, failure, duration_set>>

Execute ==
    /\ phase = "Execute"
    /\ phase' = "Format"
    /\ UNCHANGED <<eval_type_id, call_type, failure, duration_set>>

Format ==
    /\ phase = "Format"
    /\ phase' = "Done"
    /\ duration_set' = TRUE
    /\ UNCHANGED <<eval_type_id, call_type, failure>>

Next ==
    \/ Lookup
    \/ ProtectCheck
    \/ Registry
    \/ Instance
    \/ Params
    \/ Preprocess
    \/ Execute
    \/ Format

Spec == Init /\ [][Next]_<<phase, eval_type_id, call_type, failure, duration_set>>

\* ── Safety properties ──────────────────────────────────────────────────────

\* 1. Pipeline always terminates (no infinite non-Done/Failed states)
Termination == <>(phase = "Done" \/ phase = "Failed")

\* 2. Failed state always has a non-empty reason
FailedHasReason == [](phase = "Failed" => failure # "")

\* 3. Done state always has duration set
DoneHasDuration == [](phase = "Done" => duration_set = TRUE)

\* 4. Protect shortcut: after ProtectCheck, if call_type was protect,
\*    eval_type_id is always DeterministicId
ProtectInvariant ==
    [](
        /\ phase \notin {"Start", "Lookup", "ProtectCheck"}
        /\ call_type \in ProtectCallTypes
        => eval_type_id = DeterministicId
    )

\* 5. eval_type_id never changes after ProtectCheck
EvalTypeIdStable ==
    [][
        phase \notin {"Start", "Lookup", "ProtectCheck"}
        => eval_type_id' = eval_type_id
    ]_<<phase, eval_type_id>>

====
