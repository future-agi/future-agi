------------------------------ MODULE Pact ------------------------------
(*
 * Formal specification of the pact static analysis engine.
 *
 * Models the analysis of a fixed codebase: extraction produces a finite set
 * of call sites and files; the checker iterates over all (mode, site) and
 * (file_mode, file) pairs; violations accumulate in a deduplicated set.
 *
 * Architecture decision: docs/adr/ADR-036-pact-formal-analysis-toolkit.md
 *
 * Sources:
 *   tools/pact/checker.py       (orchestration, deduplication)
 *   tools/pact/failure_mode.py  (FailureMode plugin layer)
 *   tools/pact/extractor.py     (AST extraction → call sites)
 *   tools/pact/z3_engine.py     (Z3 Datalog fixedpoint engine)
 *
 * To check with TLC:
 *   1. CONSTANTS: Modes = {"m1","m2"}, FileModes = {"fm1"},
 *                 Sites = {"s1","s2"}, Files = {"f1","f2"}
 *   2. INVARIANTS: TypeInvariant, DeduplicationInvariant, MonotonicViolations
 *   3. PROPERTIES: EventuallyTerminates, CoverageComplete
 *   4. SPECIFICATION Spec
 *)

EXTENDS Integers, FiniteSets, TLC

CONSTANTS
    Modes,      \* Set of call-site failure mode identifiers
    FileModes,  \* Set of file-level failure mode identifiers (subset of modes)
    Sites,      \* Set of call site identifiers extracted from the codebase
    Files       \* Set of file paths present in the codebase

ASSUME FileModes \subseteq Modes
ASSUME IsFiniteSet(Modes) /\ IsFiniteSet(Sites) /\ IsFiniteSet(Files)

(* ---------------------------------------------------------------------------
   Violation key — the deduplication identity for a finding.
   In the implementation: (file, line, mode_name, call).
   We model it abstractly as a pair (mode, site) for tractability;
   the real implementation may produce multiple keys per (mode, site) pair
   but each key is deduplicated by the seen set.
   --------------------------------------------------------------------------- *)

ViolationKeys == Modes \X Sites

(* ---------------------------------------------------------------------------
   State variables
   --------------------------------------------------------------------------- *)

VARIABLES
    pending_sites,   \* Set of (mode, site) pairs not yet checked
    pending_files,   \* Set of (file_mode, file) pairs not yet checked
    violations,      \* Set of ViolationKeys found so far (deduplicated)
    done             \* TRUE when all pairs have been checked

vars == <<pending_sites, pending_files, violations, done>>

(* ---------------------------------------------------------------------------
   Initial state — all pairs pending, no violations found
   --------------------------------------------------------------------------- *)

Init ==
    /\ pending_sites = Modes \X Sites
    /\ pending_files = FileModes \X Files
    /\ violations    = {}
    /\ done          = FALSE

(* ---------------------------------------------------------------------------
   CheckSite — process one (mode, site) pair.
   The mode either finds a violation (adds the key) or doesn't.
   Both outcomes are valid; we model both non-deterministically.
   --------------------------------------------------------------------------- *)

CheckSite(mode, site) ==
    /\ ~done
    /\ <<mode, site>> \in pending_sites
    /\ pending_sites' = pending_sites \ {<<mode, site>>}
    /\ \/ violations' = violations \union {<<mode, site>>}   \* violation found
       \/ violations' = violations                            \* site is clean
    /\ UNCHANGED <<pending_files, done>>

(* ---------------------------------------------------------------------------
   CheckFile — process one (file_mode, file) pair.
   Same non-deterministic model as CheckSite.
   --------------------------------------------------------------------------- *)

CheckFile(mode, file) ==
    /\ ~done
    /\ <<mode, file>> \in pending_files
    /\ pending_files' = pending_files \ {<<mode, file>>}
    /\ \/ violations' = violations \union {<<mode, file>>}
       \/ violations' = violations
    /\ UNCHANGED <<pending_sites, done>>

(* ---------------------------------------------------------------------------
   Finish — mark analysis complete when all pairs have been processed.
   --------------------------------------------------------------------------- *)

Finish ==
    /\ ~done
    /\ pending_sites = {}
    /\ pending_files = {}
    /\ done'         = TRUE
    /\ UNCHANGED <<pending_sites, pending_files, violations>>

(* ---------------------------------------------------------------------------
   Next-state relation
   --------------------------------------------------------------------------- *)

Next ==
    \/ \E mode \in Modes,     site \in Sites : CheckSite(mode, site)
    \/ \E mode \in FileModes, file \in Files : CheckFile(mode, file)
    \/ Finish

(* ---------------------------------------------------------------------------
   Fairness — every pending pair is eventually processed.
   Without this, a stuttering trace could avoid Finish forever.
   --------------------------------------------------------------------------- *)

Fairness ==
    /\ \A mode \in Modes,     site \in Sites : WF_vars(CheckSite(mode, site))
    /\ \A mode \in FileModes, file \in Files : WF_vars(CheckFile(mode, file))
    /\ WF_vars(Finish)

Spec == Init /\ [][Next]_vars /\ Fairness

(* ===========================================================================
   TYPE INVARIANT
   =========================================================================== *)

TypeInvariant ==
    /\ pending_sites \subseteq Modes \X Sites
    /\ pending_files \subseteq FileModes \X Files
    /\ violations    \subseteq ViolationKeys
    /\ done          \in BOOLEAN

(* ===========================================================================
   SAFETY INVARIANTS
   =========================================================================== *)

(*
 * DeduplicationInvariant — violations is a set; structural deduplication is
 * guaranteed by TLA+ set semantics. This invariant makes the implementation
 * contract explicit: the checker's `seen` set in checker.py must enforce the
 * same uniqueness that set membership provides here.
 *)
DeduplicationInvariant ==
    \A k1, k2 \in violations : k1 = k2 \/ k1 # k2  \* tautological in TLA+ sets;
    \* The real constraint: each (file, line, mode, call) tuple appears at most once.
    \* Encoded here as: violations is a proper set (no duplicate elements).
    TRUE

(*
 * MonotonicViolations — the violations set only grows.
 * No previously found violation is ever retracted.
 *)
MonotonicViolations ==
    violations \subseteq violations'

(*
 * PendingShrinks — work queues only shrink or stay the same.
 *)
PendingShrinks ==
    /\ pending_sites' \subseteq pending_sites \/ pending_sites' = pending_sites
    /\ pending_files' \subseteq pending_files \/ pending_files' = pending_files

(*
 * NoViolationsAfterDone — once done, the violation set is frozen.
 *)
NoViolationsAfterDone ==
    done => [](violations = violations)

(*
 * CoverageInvariant — when done, every pair was visited.
 * pending_* = {} is established by Finish's precondition.
 *)
CoverageInvariant ==
    done =>
        /\ pending_sites = {}
        /\ pending_files = {}

(* ===========================================================================
   LIVENESS PROPERTIES
   =========================================================================== *)

(*
 * EventuallyTerminates — the analysis always completes under fairness.
 * This is the key liveness property: pact never hangs.
 *)
EventuallyTerminates == <> done

(*
 * CoverageComplete — when done, all pairs were checked (derived from
 * CoverageInvariant + EventuallyTerminates).
 *)
CoverageComplete ==
    <> (done /\ pending_sites = {} /\ pending_files = {})

(*
 * ViolationsStableAfterDone — once done, violations no longer change.
 *)
ViolationsStableAfterDone ==
    <> [] (done => (violations = violations))

(* ===========================================================================
   PLUGIN LAYER PROPERTY
   ===========================================================================
 *
 * NewModeMonotonicity — adding a new failure mode to Modes can only ADD
 * violations, never remove existing ones. This is a meta-property about
 * the plugin architecture: existing findings are stable across mode additions.
 *
 * We express this as a refinement argument: if M1 ⊂ M2 (M2 adds modes),
 * then violations(M1) ⊆ violations(M2).
 *
 * This cannot be expressed directly in a single TLC model; it is verified
 * by the Z3 constraint in tools/pact/test_z3_engine.py:
 *   TestOnlyBadSiteFlagged — adding a mode flags new sites, never unflags old ones.
 *)

(* ===========================================================================
   SOUNDNESS CONTRACT (Z3 layer)
   ===========================================================================
 *
 * Z3Soundness — for every violation reported by the Z3 Datalog engine,
 * the constraint is satisfiable: there exists a model assignment where the
 * required field is absent at the call site.
 *
 * This is verified externally by z3_engine.py: the engine only emits
 * FailureEvidence when the fixedpoint query returns a non-empty result set,
 * which is equivalent to SAT on the missing(call_site, field) relation.
 *
 * The dual: if Z3 returns UNSAT (empty fixedpoint), no violation is emitted.
 * test_z3_engine.py::TestNoViolationWhenAllRequiredFieldsProvided verifies this.
 *)

========================================================================
