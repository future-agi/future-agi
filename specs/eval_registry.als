/**
 * Alloy specification of the evaluator registry.
 *
 * Models the invariants from evaluations/engine/registry.py and
 * docs/adr/005-registry-lazy-singleton.md:
 *
 *   - The registry is a total function from registered names to classes
 *   - get_eval_class raises iff the name is not registered (no silent None)
 *   - is_registered iff get_eval_class succeeds (no split-brain)
 *   - list_registered covers exactly the set of registered names
 *
 * Run with: java -jar alloy6.jar (open this file in the Alloy Analyzer)
 * Or headless: java -cp alloy6.jar edu.mit.csail.sdg.alloy4whole.ExampleUsingTheAPI
 *
 * All assertions should hold (Alloy Analyzer reports "No counterexample found").
 */

module eval_registry

-- A registered evaluator type ID (string name)
sig EvalTypeId {}

-- An evaluator class (Python class object)
sig EvaluatorClass {}

-- The global registry singleton
one sig Registry {
    -- Partial function: each registered name maps to exactly one class
    entries: EvalTypeId -> lone EvaluatorClass
}

-- ── Predicates mirroring the Python API ────────────────────────────────────

pred isRegistered[id: EvalTypeId] {
    some Registry.entries[id]
}

pred getClass[id: EvalTypeId, cls: EvaluatorClass] {
    Registry.entries[id] = cls
}

fun listRegistered: set EvalTypeId {
    Registry.entries.EvaluatorClass
}

-- ── Invariants ──────────────────────────────────────────────────────────────

-- 1. get_eval_class never returns None for registered names:
--    if isRegistered, there is exactly one class
assert GetClassNeverNone {
    all id: EvalTypeId |
        isRegistered[id] => (one cls: EvaluatorClass | getClass[id, cls])
}

-- 2. is_registered iff get_eval_class succeeds (no split-brain between the two):
assert IsRegisteredConsistentWithGetClass {
    all id: EvalTypeId |
        isRegistered[id] <=> (some cls: EvaluatorClass | getClass[id, cls])
}

-- 3. list_registered covers exactly the registered entries (no hidden names):
assert ListRegisteredIsComplete {
    all id: EvalTypeId |
        id in listRegistered <=> isRegistered[id]
}

-- 4. Each class is reachable from at most one name (no aliasing):
--    (this is desirable but NOT currently enforced by the code — this assertion
--    may find counterexamples if two names are registered to the same class)
assert NoClassAliasing {
    all cls: EvaluatorClass |
        lone id: EvalTypeId | Registry.entries[id] = cls
}

-- 5. Registry is deterministic: same name always yields same class:
assert RegistryIsDeterministic {
    all id: EvalTypeId, c1, c2: EvaluatorClass |
        (getClass[id, c1] and getClass[id, c2]) => c1 = c2
}

-- ── Run checks ──────────────────────────────────────────────────────────────

check GetClassNeverNone for 10
check IsRegisteredConsistentWithGetClass for 10
check ListRegisteredIsComplete for 10
check RegistryIsDeterministic for 10

-- NoClassAliasing is checked separately — it may not hold if multiple
-- names alias to the same class (allowed by the current implementation)
check NoClassAliasing for 5

-- Show a valid registry state exists
run {} for 3
