"""Tests for gen_cfg.py operator classification."""

import pytest
from gen_cfg import _classify, _strip_comments, _parse, generate


# ── _strip_comments ───────────────────────────────────────────────────────────

def test_strip_line_comment():
    src = r"foo \* this is a comment" + "\n"
    assert r'\*' not in _strip_comments(src)
    assert 'foo' in _strip_comments(src)


def test_strip_block_comment():
    src = "(* block content *) foo"
    assert 'block content' not in _strip_comments(src)
    assert 'foo' in _strip_comments(src)


def test_strip_multiline_block_comment():
    src = "(*\n  multi\n  line\n*) bar"
    assert 'multi' not in _strip_comments(src)
    assert 'bar' in _strip_comments(src)


# ── _classify — must match PROPERTY ──────────────────────────────────────────

def _op(name, body, params=False):
    return {'name': name, 'params': params, 'body': body}


@pytest.mark.parametrize("name,body", [
    # eventually
    ("EventuallyDone",     "<>(done = TRUE)"),
    # leads-to
    ("RowReachesEval",     "(r \\in inserted) ~> (r \\in evaluated)"),
    # weak fairness
    ("Liveness",           "WF_vars(SomeAction)"),
    # strong fairness
    ("RetryLiveness",      "SF_vars(WorkflowSuccess)"),
    # action subscript — the canonical mistake we've been fixing
    ("WatermarkMonotone",  "[][watermark' >= watermark]_watermark"),
    ("MonotonicViolations","[][violations \\subseteq violations']_violations"),
    ("TerminalIsStable",   "[][phase \\notin TerminalPhases \\/ phase' = phase]_phase"),
    ("AttemptMonotone",    "[][attempt' >= attempt]_attempt"),
    # globally ([]) applied to state formula
    ("NoViolationsAfterDone", "done => [](violations = violations)"),
    # FeedbackGrows as in GuardrailReflexion
    ("FeedbackGrows",      "[][Len(feedback') >= Len(feedback)]_feedback"),
    # SuccessOnFirstPass fix (was [](...)  with primes, now [][...]_vars)
    ("SuccessOnFirstPass", "[][(state = \"idle\" /\\ passed' = TRUE) => (state' = \"done\")]_vars"),
    # leads-to liveness
    ("EventuallyPublishable", "\\A t \\in Templates : HasDraft(t) ~> HasActive(t)"),
    # primed comparison
    ("ExecutionVersionStability", "[][exec_pinned \\subseteq exec_pinned']_exec_pinned"),
])
def test_classify_property(name, body):
    assert _classify(_op(name, body)) == 'property', f"{name!r} should be property"


# ── _classify — must match INVARIANT ─────────────────────────────────────────

@pytest.mark.parametrize("name,body", [
    # conjunction of state predicates
    ("TypeInvariant",     "/\\ pending \\subseteq AllRows\n/\\ watermark \\in 0..MaxRows"),
    # at-most-one cardinality
    ("ActiveVersionUnique", "\\A t \\in Templates : Cardinality(ActiveVersionOf(t)) <= 1"),
    # no-draft guard — quantifier, no primes
    ("NoDraftExecution",  "\\A <<e, t, v>> \\in exec_pinned : version_state[t][v] /= \"draft\""),
    # simple boolean
    ("NoDuplicateEval",   "in_flight \\cap evaluated = {}"),
    # guarded implication (no primes)
    ("CoverageInvariant", "done =>\n/\\ pending_sites = {}\n/\\ pending_files = {}"),
    # trivially true
    ("DeduplicationInvariant", "TRUE"),
    # negation
    ("NeverUnsafeExtraction", "~(\\E f \\in extracted : ~SitesSatisfy(f))"),
    # bounded check
    ("AttemptBounded",    "attempt <= MaxAttempts"),
    # from SimulateCLI
    ("TimeoutBounded",    "elapsed_s <= TIMEOUT_S"),
    ("NeverPollBeforeStart", "(phase = \"polling\") => (execution_id /= \"none\")"),
])
def test_classify_invariant(name, body):
    assert _classify(_op(name, body)) == 'invariant', f"{name!r} should be invariant"


# ── _classify — must be skipped ───────────────────────────────────────────────

@pytest.mark.parametrize("name,body,params", [
    # has parameters
    ("SitesSatisfy",    "\\A rec \\in CallSites3 : ...", True),
    ("ActiveVersionOf", "{v \\in Versions : ...}", True),
    # structural operators
    ("Init",            "/\\ x = 0", False),
    ("Next",            "\\/ Action1 \\/ Action2", False),
    ("Fairness",        "WF_vars(A)", False),
    ("Spec",            "Init /\\ [][Next]_vars /\\ Fairness", False),
    ("FullSpec",        "Init /\\ [][Next]_vars /\\ Fairness", False),
    # action (has UNCHANGED)
    ("PublishTemplate", "/\\ done' = TRUE\n/\\ UNCHANGED <<x, y>>", False),
    # set literal
    ("VersionStates",   "{\"draft\", \"active\", \"inactive\"}", False),
    # numeric range
    ("Versions",        "1..MaxVersions", False),
    # Cartesian product (set expression, no bool opener)
    ("ViolationKeys",   "(Modes \\X Sites) \\union (FileModes \\X Files)", False),
])
def test_classify_skip(name, body, params):
    assert _classify(_op(name, body, params)) == 'skip', f"{name!r} should be skip"


# ── Integration: parse + generate ────────────────────────────────────────────

_MINI_SPEC = r"""
---- MODULE Mini ----
EXTENDS Naturals

CONSTANTS
    MaxSteps,   \* max steps
    FailMode    \* TRUE = allow failures

ASSUME MaxSteps \in Nat /\ MaxSteps >= 1

VARIABLES x, done
vars == <<x, done>>

Init == /\ x = 0 /\ done = FALSE

Step == /\ x < MaxSteps /\ x' = x + 1 /\ UNCHANGED done
Finish == /\ x = MaxSteps /\ done' = TRUE /\ UNCHANGED x

Next == Step \/ Finish
Fairness == WF_vars(Step) /\ WF_vars(Finish)
Spec == Init /\ [][Next]_vars /\ Fairness

TypeInvariant == /\ x \in 0..MaxSteps /\ done \in BOOLEAN
BoundedX == x <= MaxSteps
StepMonotone == [][x' >= x]_x
EventuallyDone == <>(done = TRUE)
====
"""


def test_mini_spec_invariants():
    parsed = _parse(_MINI_SPEC)
    cfg = generate(parsed)
    assert 'INVARIANTS' in cfg
    assert '    TypeInvariant' in cfg
    assert '    BoundedX' in cfg
    assert 'StepMonotone' not in cfg.split('INVARIANTS')[1].split('PROPERTIES')[0]


def test_mini_spec_properties():
    parsed = _parse(_MINI_SPEC)
    cfg = generate(parsed)
    assert 'PROPERTIES' in cfg
    assert '    StepMonotone' in cfg
    assert '    EventuallyDone' in cfg


def test_mini_spec_constants():
    parsed = _parse(_MINI_SPEC)
    cfg = generate(parsed)
    assert 'MaxSteps = 3' in cfg    # Nat with >= 1 → base+2
    assert 'FailMode = TRUE' in cfg  # ends with 'Mode'


def test_mini_spec_no_structural_operators():
    parsed = _parse(_MINI_SPEC)
    cfg = generate(parsed)
    # Use exact line match (prefix match would catch 'Step' inside 'StepMonotone')
    lines = cfg.splitlines()
    for name in ('Init', 'Next', 'Fairness', 'Spec', 'Step', 'Finish'):
        assert f'    {name}' not in lines, f"structural operator {name!r} should not appear"
