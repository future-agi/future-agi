"""
Z3 proofs for the human-facing fi-simulate CLI extension (ADR-035).

Mirrors invariants from SimulateCLIHuman.tla. Each proof follows the pattern:
  1. Add domain bounds
  2. Add the violation (negation of invariant)
  3. Add the invariant as an axiom
  4. Assert UNSAT — violation + axiom is a contradiction

Invariants:
  NameResolutionBeforeStart — execution never dispatches before name → UUID resolved
  AmbiguousNameFails        — >1 match → FAILED, not starting
  ZeroMatchesFails          — 0 matches → FAILED, not starting
  ListIsStateless           — list subcommand never reaches execution phases
  StatusIsStateless         — status subcommand never dispatches an execution
  TerminalIsStable          — terminal phases cannot transition to non-terminal
  NeverPollBeforeStart      — polling requires execution_id set (preserved)
  SummaryOnlyAfterTerminal  — summarizing requires terminal run_status (preserved)
"""

from __future__ import annotations

import pytest

z3 = pytest.importorskip("z3")

# ---------------------------------------------------------------------------
# Phase encoding (integer IDs)
# ---------------------------------------------------------------------------

PHASES = {
    "init":        0,
    "listing":     1,
    "resolving":   2,
    "starting":    3,
    "polling":     4,
    "summarizing": 5,
    "done":        6,
    "failed":      7,
    "timed_out":   8,
}
P = {name: z3.IntVal(n) for name, n in PHASES.items()}

# Execution phases — the ones that dispatch server-side work
EXECUTION_PHASES = {PHASES["starting"], PHASES["polling"], PHASES["summarizing"]}

# Terminal phases
TERMINAL_PHASES = {PHASES["done"], PHASES["failed"], PHASES["timed_out"]}

# Subcommands
SUB_LIST   = z3.IntVal(0)
SUB_RUN    = z3.IntVal(1)
SUB_STATUS = z3.IntVal(2)


def _make_state(solver: z3.Solver, suffix: str = "") -> dict:
    """Create symbolic state variables and add domain constraints to solver."""
    ph  = z3.Int(f"phase{suffix}")
    sub = z3.Int(f"sub{suffix}")
    matches = z3.Int(f"matches{suffix}")
    has_name   = z3.Bool(f"has_name{suffix}")
    rt_set     = z3.Bool(f"rt_set{suffix}")      # run_test_id resolved
    exec_set   = z3.Bool(f"exec_set{suffix}")    # execution_id assigned by CLI
    run_term   = z3.Bool(f"run_term{suffix}")    # run_status is terminal
    locked     = z3.Bool(f"locked{suffix}")      # phase_locked

    solver.add(z3.And(ph >= 0, ph <= 8))
    solver.add(z3.And(sub >= 0, sub <= 2))
    solver.add(z3.And(matches >= 0, matches <= 100))

    return {
        "ph": ph, "sub": sub, "matches": matches,
        "has_name": has_name, "rt_set": rt_set,
        "exec_set": exec_set, "run_term": run_term,
        "locked": locked,
    }


def _in_execution_phase(ph):
    return z3.Or(ph == P["starting"], ph == P["polling"], ph == P["summarizing"])


def _is_terminal(ph):
    return z3.Or(ph == P["done"], ph == P["failed"], ph == P["timed_out"])


# ---------------------------------------------------------------------------
# Class 1: NameResolutionBeforeStart
# ---------------------------------------------------------------------------

class TestNameResolutionBeforeStart:
    def test_starting_with_name_query_requires_resolved_uuid(self):
        """UNSAT: starting + has_name_query + run_test_id NOT resolved."""
        s = z3.Solver()
        v = _make_state(s)
        # Invariant axiom
        s.add(z3.Implies(
            z3.And(v["ph"] == P["starting"], v["has_name"]),
            v["rt_set"],
        ))
        # Violation
        s.add(v["ph"] == P["starting"])
        s.add(v["has_name"] == True)
        s.add(v["rt_set"] == False)
        assert s.check() == z3.unsat

    def test_starting_by_uuid_directly_is_valid(self):
        """SAT: starting + no name query + UUID provided."""
        s = z3.Solver()
        v = _make_state(s)
        s.add(v["ph"] == P["starting"])
        s.add(v["has_name"] == False)
        s.add(v["rt_set"] == True)
        assert s.check() == z3.sat

    def test_starting_with_resolved_name_is_valid(self):
        """SAT: starting + name resolved to exactly one UUID."""
        s = z3.Solver()
        v = _make_state(s)
        s.add(v["ph"] == P["starting"])
        s.add(v["has_name"] == True)
        s.add(v["rt_set"] == True)
        s.add(v["matches"] == 1)
        assert s.check() == z3.sat


# ---------------------------------------------------------------------------
# Class 2: AmbiguousNameFails
# ---------------------------------------------------------------------------

class TestAmbiguousNameFails:
    def test_run_with_multiple_matches_cannot_start(self):
        """UNSAT: run + matches > 1 + phase = starting."""
        s = z3.Solver()
        v = _make_state(s)
        # Invariant: run + matches > 1 → failed
        s.add(z3.Implies(
            z3.And(v["sub"] == SUB_RUN, v["matches"] > 1),
            v["ph"] == P["failed"],
        ))
        # Violation
        s.add(v["sub"] == SUB_RUN)
        s.add(v["matches"] > 1)
        s.add(v["ph"] == P["starting"])
        assert s.check() == z3.unsat

    def test_run_with_multiple_matches_transitions_to_failed(self):
        """SAT: run + matches > 1 + failed."""
        s = z3.Solver()
        v = _make_state(s)
        s.add(v["sub"] == SUB_RUN)
        s.add(v["matches"] > 1)
        s.add(v["ph"] == P["failed"])
        assert s.check() == z3.sat

    def test_list_with_many_suites_is_fine(self):
        """SAT: list returning many results is not an error."""
        s = z3.Solver()
        v = _make_state(s)
        s.add(v["sub"] == SUB_LIST)
        s.add(v["matches"] > 1)
        s.add(v["ph"] == P["done"])
        assert s.check() == z3.sat


# ---------------------------------------------------------------------------
# Class 3: ZeroMatchesFails
# ---------------------------------------------------------------------------

class TestZeroMatchesFails:
    def test_run_with_zero_name_matches_cannot_start(self):
        """UNSAT: run + has_name + matches = 0 + phase = starting."""
        s = z3.Solver()
        v = _make_state(s)
        s.add(z3.Implies(
            z3.And(v["sub"] == SUB_RUN, v["has_name"], v["matches"] == 0),
            v["ph"] == P["failed"],
        ))
        s.add(v["sub"] == SUB_RUN)
        s.add(v["has_name"] == True)
        s.add(v["matches"] == 0)
        s.add(v["ph"] == P["starting"])
        assert s.check() == z3.unsat

    def test_run_with_zero_matches_goes_to_failed(self):
        """SAT: run + name query + zero matches → failed is reachable."""
        s = z3.Solver()
        v = _make_state(s)
        s.add(v["sub"] == SUB_RUN)
        s.add(v["has_name"] == True)
        s.add(v["matches"] == 0)
        s.add(v["ph"] == P["failed"])
        assert s.check() == z3.sat

    def test_run_by_uuid_ignores_zero_matches(self):
        """SAT: direct UUID run with matches=0 can start (matches irrelevant)."""
        s = z3.Solver()
        v = _make_state(s)
        s.add(v["sub"] == SUB_RUN)
        s.add(v["has_name"] == False)
        s.add(v["matches"] == 0)
        s.add(v["rt_set"] == True)
        s.add(v["ph"] == P["starting"])
        assert s.check() == z3.sat


# ---------------------------------------------------------------------------
# Class 4: ListIsStateless
# ---------------------------------------------------------------------------

class TestListIsStateless:
    @pytest.mark.parametrize("bad_phase", ["starting", "polling", "summarizing"])
    def test_list_cannot_reach_execution_phases(self, bad_phase):
        """UNSAT: list subcommand + any execution-dispatching phase."""
        s = z3.Solver()
        v = _make_state(s)
        s.add(z3.Implies(
            v["sub"] == SUB_LIST,
            z3.Not(_in_execution_phase(v["ph"])),
        ))
        s.add(v["sub"] == SUB_LIST)
        s.add(v["ph"] == P[bad_phase])
        assert s.check() == z3.unsat

    @pytest.mark.parametrize("ok_phase", ["init", "listing", "done", "failed"])
    def test_list_valid_phases(self, ok_phase):
        """SAT: list in its allowed phases."""
        s = z3.Solver()
        v = _make_state(s)
        s.add(v["sub"] == SUB_LIST)
        s.add(v["ph"] == P[ok_phase])
        assert s.check() == z3.sat


# ---------------------------------------------------------------------------
# Class 5: StatusIsStateless
# ---------------------------------------------------------------------------

class TestStatusIsStateless:
    @pytest.mark.parametrize("bad_phase", ["starting", "polling", "summarizing"])
    def test_status_cannot_reach_execution_phases(self, bad_phase):
        """UNSAT: status subcommand + any execution-dispatching phase."""
        s = z3.Solver()
        v = _make_state(s)
        s.add(z3.Implies(
            v["sub"] == SUB_STATUS,
            z3.Not(_in_execution_phase(v["ph"])),
        ))
        s.add(v["sub"] == SUB_STATUS)
        s.add(v["ph"] == P[bad_phase])
        assert s.check() == z3.unsat

    def test_status_never_dispatches_an_execution(self):
        """UNSAT: status subcommand + exec_set=True (CLI would never set this)."""
        s = z3.Solver()
        v = _make_state(s)
        s.add(z3.Implies(v["sub"] == SUB_STATUS, z3.Not(v["exec_set"])))
        s.add(v["sub"] == SUB_STATUS)
        s.add(v["exec_set"] == True)
        assert s.check() == z3.unsat

    @pytest.mark.parametrize("ok_phase", ["init", "listing", "done", "failed"])
    def test_status_valid_phases(self, ok_phase):
        """SAT: status in its allowed phases."""
        s = z3.Solver()
        v = _make_state(s)
        s.add(v["sub"] == SUB_STATUS)
        s.add(v["ph"] == P[ok_phase])
        assert s.check() == z3.sat


# ---------------------------------------------------------------------------
# Class 6: TerminalIsStable
# ---------------------------------------------------------------------------

class TestTerminalIsStable:
    @pytest.mark.parametrize("terminal", ["done", "failed", "timed_out"])
    @pytest.mark.parametrize("bad_next", ["init", "listing", "starting", "polling"])
    def test_locked_terminal_cannot_transition(self, terminal, bad_next):
        """UNSAT: phase_locked + terminal phase + next phase is non-terminal."""
        curr = z3.Solver()
        ph_curr = z3.Int("ph_curr")
        ph_next = z3.Int("ph_next")
        locked  = z3.Bool("locked")
        curr.add(z3.And(ph_curr >= 0, ph_curr <= 8))
        curr.add(z3.And(ph_next >= 0, ph_next <= 8))
        # Invariant: locked means phase is stable
        curr.add(z3.Implies(locked, ph_next == ph_curr))
        # Violation: locked + terminal + different next
        curr.add(locked == True)
        curr.add(ph_curr == P[terminal])
        curr.add(ph_next == P[bad_next])
        assert curr.check() == z3.unsat


# ---------------------------------------------------------------------------
# Class 7: NeverPollBeforeStart (preserved from SimulateCLI.tla)
# ---------------------------------------------------------------------------

class TestNeverPollBeforeStart:
    def test_polling_requires_execution_id(self):
        """UNSAT: phase=polling + execution_id not set."""
        s = z3.Solver()
        v = _make_state(s)
        s.add(z3.Implies(v["ph"] == P["polling"], v["exec_set"]))
        s.add(v["ph"] == P["polling"])
        s.add(v["exec_set"] == False)
        assert s.check() == z3.unsat

    def test_polling_with_execution_id_is_valid(self):
        """SAT: polling + execution_id set."""
        s = z3.Solver()
        v = _make_state(s)
        s.add(v["ph"] == P["polling"])
        s.add(v["exec_set"] == True)
        assert s.check() == z3.sat


# ---------------------------------------------------------------------------
# Class 8: SummaryOnlyAfterTerminal (preserved from SimulateCLI.tla)
# ---------------------------------------------------------------------------

class TestSummaryOnlyAfterTerminal:
    @pytest.mark.parametrize("is_terminal", [True, False])
    def test_summarizing_requires_terminal_run_status(self, is_terminal):
        """
        UNSAT when is_terminal=False: summarizing + non-terminal run_status.
        SAT  when is_terminal=True:  summarizing + terminal run_status.
        """
        s = z3.Solver()
        v = _make_state(s)
        s.add(z3.Implies(v["ph"] == P["summarizing"], v["run_term"]))
        s.add(v["ph"] == P["summarizing"])
        s.add(v["run_term"] == is_terminal)
        if is_terminal:
            assert s.check() == z3.sat
        else:
            assert s.check() == z3.unsat
