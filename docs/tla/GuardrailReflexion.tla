----------------------------- MODULE GuardrailReflexion -----------------------------
(*
 * TLA+ specification for the guardrail reflexion loop in agentcc-gateway.
 *
 * Models the bounded retry loop that fires when a post-stage guardrail blocks
 * the model's response.  The loop injects the failure reason and re-calls the
 * model up to MaxAttempts times.
 *
 * Key properties verified by TLC:
 *
 *   INVARIANTS (pure state predicates):
 *     TypeInvariant     — all variables have the expected types
 *     AttemptBounded    — attempt counter never exceeds MaxAttempts
 *
 *   PROPERTIES (temporal / box-action formulas):
 *     EventuallyTerminates   — the loop always reaches Done or Failed
 *     SuccessOnFirstPass     — if attempt 0 passes, state goes directly to Done
 *     AttemptMonotone        — attempts only increase
 *     FeedbackGrows          — injected feedback only accumulates ([][...] box-action)
 *
 *   SPECIFICATION Spec (with fairness for liveness)
 *
 * To check with TLC (see GuardrailReflexion.cfg):
 *   1. Set MAX_ATTEMPTS to a small value (e.g., 3)
 *   2. INVARIANTS: TypeInvariant, AttemptBounded, FeedbackGrows
 *   3. PROPERTIES: EventuallyTerminates, SuccessOnFirstPass, AttemptMonotone
 *   4. SPECIFICATION Spec
 *)

EXTENDS Naturals, Sequences, TLC

CONSTANTS
    MaxAttempts,   \* upper bound on reflexion attempts (e.g., 3)
    PassProbability \* symbolic: whether each model call can pass (non-deterministic)

ASSUME MaxAttempts \in Nat /\ MaxAttempts >= 1

(*
 * States:
 *   "idle"     — waiting to process the first model response
 *   "blocked"  — post-stage guardrail blocked the response; reflexion eligible
 *   "retrying" — injecting feedback and re-calling model
 *   "done"     — response accepted; request complete
 *   "failed"   — max attempts exhausted; return 403 to client
 *)
VARIABLES
    state,       \* one of {"idle","blocked","retrying","done","failed"}
    attempt,     \* current attempt index (0 = initial call, 1..MaxAttempts = reflexion)
    feedback,    \* sequence of injected feedback messages
    passed       \* whether the most recent model call passed the guardrail

vars == <<state, attempt, feedback, passed>>

TypeInvariant ==
    /\ state   \in {"idle","blocked","retrying","done","failed"}
    /\ attempt \in 0..MaxAttempts
    /\ passed  \in {TRUE, FALSE}
    /\ \A i \in DOMAIN feedback : feedback[i] \in STRING

AttemptBounded ==
    attempt <= MaxAttempts

FeedbackGrows ==
    \* Once a feedback message is injected it is never removed.
    \* This is a temporal action property (box-action formula): in every step
    \* the feedback sequence length is non-decreasing.  A state invariant
    \* Len(feedback) <= attempt would only bound the length from above and would
    \* not catch a transition that shrinks or resets feedback.
    [][Len(feedback') >= Len(feedback)]_feedback

Init ==
    /\ state   = "idle"
    /\ attempt = 0
    /\ feedback = <<>>
    /\ passed  = FALSE

(* First model call — no feedback injected yet *)
InitialCall ==
    /\ state = "idle"
    /\ \/ /\ passed' = TRUE    \* model response passes guardrail
          /\ state'  = "done"
       \/ /\ passed' = FALSE   \* model response blocked
          /\ state'  = "blocked"
    /\ UNCHANGED <<attempt, feedback>>

(* Guardrail blocked — decide whether to retry *)
EvaluateBlock ==
    /\ state = "blocked"
    /\ IF attempt < MaxAttempts
       THEN /\ state'    = "retrying"
            /\ feedback' = Append(feedback, "blocked: policy violation")
            /\ attempt'  = attempt + 1
            /\ UNCHANGED passed
       ELSE /\ state'   = "failed"
            /\ UNCHANGED <<attempt, feedback, passed>>

(* Reflexion retry — re-call model with feedback *)
ReflexionCall ==
    /\ state = "retrying"
    /\ \/ /\ passed' = TRUE    \* revised response passes
          /\ state'  = "done"
       \/ /\ passed' = FALSE   \* still blocked
          /\ state'  = "blocked"
    /\ UNCHANGED <<attempt, feedback>>

Next ==
    \/ InitialCall
    \/ EvaluateBlock
    \/ ReflexionCall

Spec == Init /\ [][Next]_vars /\ WF_vars(Next)

-----------------------------------------------------------------------------
(* Temporal properties *)

EventuallyTerminates ==
    \* Every execution eventually reaches done or failed.
    <>(state = "done" \/ state = "failed")

SuccessOnFirstPass ==
    \* If the initial call passes the guardrail, we go directly to done
    \* without incrementing the attempt counter.
    [](  (state = "idle" /\ passed' = TRUE)
       => (state' = "done" /\ attempt' = attempt))

AttemptMonotone ==
    \* attempt only increases.
    [][attempt' >= attempt]_attempt

=============================================================================
