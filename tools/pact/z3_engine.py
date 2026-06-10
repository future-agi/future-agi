"""
Z3 Fixedpoint engine for pact.

Program analysis as Datalog: Python extracts facts, Z3 derives violations.

Schema
------
EDB:
  site_creates(site, model)       call site is Model.objects.create()
  site_provides(site, field)      call site passes this kwarg
  model_req(model, field)         model requires this field

IDB (derived by Z3 in two strata):
  site_req(site, field)           = join: site creates a model that requires this field
  violation(site, field)          = site_req but not site_provides

Two rules, no existential variables in negated atoms:
  site_req(S, F) :- site_creates(S, M), model_req(M, F)
  violation(S, F) :- site_req(S, F), not site_provides(S, F)

Python adds facts. Z3 reasons. One query. All violations.

Implementation notes
--------------------
- Z3 datalog engine requires FINITE sorts — use BitVecSort, not IntSort.
- Rules must be in ForAll(vars, Implies(body, head)) form.
- Query with Exists([qs, qf], violation(qs, qf)).
- get_answer() returns a formula in de Bruijn Var notation:
    Var(0) = qs = first arg (site), Var(1) = qf = second arg (field)
  (Z3 binds Exists vars right-to-left: last in list → Var(0))
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import z3 as _z3
from z3 import (
    And, BitVecSort, BitVecVal, BitVecs, BoolSort,
    Exists, Fixedpoint, ForAll, Function, Implies, Not,
    is_and, is_eq, is_false, is_or, sat,
)

from .extractor import extract_from_codebase

_ctx = _z3.main_ctx()


def _is_var(expr: _z3.ExprRef) -> bool:
    return _z3.Z3_get_ast_kind(_ctx.ref(), expr.as_ast()) == _z3.Z3_VAR_AST


def _var_idx(expr: _z3.ExprRef) -> int:
    return _z3.Z3_get_index_value(_ctx.ref(), expr.as_ast())


def _extract_tuples(formula: _z3.BoolRef) -> list[tuple[int, int]]:
    """Parse Or(And(Var(0)==si, Var(1)==fi), ...) → [(si, fi), ...].

    Z3's datalog get_answer() returns a DNF formula where each clause
    is one violation tuple. This walks the formula in O(|violations|).
    """
    if is_false(formula):
        return []
    clauses = formula.children() if is_or(formula) else [formula]
    tuples: list[tuple[int, int]] = []
    for clause in clauses:
        if not is_and(clause):
            continue
        vals: dict[int, int] = {}
        for eq in clause.children():
            if not is_eq(eq):
                continue
            lhs, rhs = eq.children()
            if _is_var(lhs):
                vals[_var_idx(lhs)] = rhs.as_long()
            elif _is_var(rhs):
                vals[_var_idx(rhs)] = lhs.as_long()
        if 0 in vals and 1 in vals:
            tuples.append((vals[0], vals[1]))  # (site, field)
    return tuples

# Domain size: supports up to 2^BITS distinct IDs per intern table.
# 16 bits = 65536 — more than enough for any codebase.
_BITS = 16
_BVS = BitVecSort(_BITS)

def _bv(n: int):
    return BitVecVal(n, _BITS)


# ── EDB ───────────────────────────────────────────────────────────────────────
site_creates  = Function('site_creates',  _BVS, _BVS, BoolSort())
site_provides = Function('site_provides', _BVS, _BVS, BoolSort())
model_req     = Function('model_req',     _BVS, _BVS, BoolSort())

# ── IDB ───────────────────────────────────────────────────────────────────────
site_req   = Function('site_req',   _BVS, _BVS, BoolSort())  # derived join
violation  = Function('violation',  _BVS, _BVS, BoolSort())  # the violations


@dataclass
class Z3Violation:
    file: str
    line: int
    call: str
    missing: list[str] = field(default_factory=list)
    context: str = "model_constraint"

    def __str__(self) -> str:
        return f"{self.file}:{self.line}  {self.call}  missing: {', '.join(self.missing)}"


class PactEngine:
    """
    Loads a codebase as a Datalog database.
    Z3's Fixedpoint derives all violations — Python just reads the results.
    """

    def __init__(self) -> None:
        self._fp = Fixedpoint()
        self._fp.set(engine='datalog')
        for rel in (site_creates, site_provides, model_req, site_req, violation):
            self._fp.register_relation(rel)

        # Stratum 1: materialize the join (no negation, existential _m is fine here)
        # site_req(S, F) :- site_creates(S, M), model_req(M, F)
        _s, _m, _f = BitVecs('_s _m _f', _BITS)
        self._fp.add_rule(ForAll(
            [_s, _m, _f],
            Implies(And(site_creates(_s, _m), model_req(_m, _f)), site_req(_s, _f)),
        ))

        # Stratum 2: negate site_provides — now no existential in the negated atom
        # violation(S, F) :- site_req(S, F), not site_provides(S, F)
        _s2, _f2 = BitVecs('_s2 _f2', _BITS)
        self._fp.add_rule(ForAll(
            [_s2, _f2],
            Implies(And(site_req(_s2, _f2), Not(site_provides(_s2, _f2))), violation(_s2, _f2)),
        ))

        # Intern tables
        self._site_id:  dict[tuple, int] = {}
        self._field_id: dict[str, int]   = {}
        self._model_id: dict[str, int]   = {}
        self._site_meta: dict[int, dict] = {}

    def _sid(self, file: str, line: int, call: str) -> int:
        key = (file, line)
        if key not in self._site_id:
            n = len(self._site_id)
            self._site_id[key] = n
            self._site_meta[n] = {"file": file, "line": line, "call": call}
        return self._site_id[key]

    def _fid(self, name: str) -> int:
        if name not in self._field_id:
            self._field_id[name] = len(self._field_id)
        return self._field_id[name]

    def _mid(self, name: str) -> int:
        if name not in self._model_id:
            self._model_id[name] = len(self._model_id)
        return self._model_id[name]

    def load(self, root: Path) -> None:
        """Extract AST facts → Z3 Fixedpoint database."""
        models, _funcs, calls = extract_from_codebase(root)

        for model in models:
            mi = self._mid(model.name)
            for fc in model.required_fields:
                fi = self._fid(fc.name)
                self._fp.add_rule(model_req(_bv(mi), _bv(fi)))

        for call in calls:
            if not call.is_create_call or not call.model_name:
                continue
            si = self._sid(call.file, call.line, call.callee_name)
            mi = self._mid(call.model_name)
            self._fp.add_rule(site_creates(_bv(si), _bv(mi)))
            for kwarg in call.provided_kwargs:
                fi = self._fid(kwarg)
                self._fp.add_rule(site_provides(_bv(si), _bv(fi)))

    def violations(self) -> list[Z3Violation]:
        """One query. Z3 derives all violations."""
        qs, qf = BitVecs('qs qf', _BITS)
        result = self._fp.query(Exists([qs, qf], violation(qs, qf)))
        if result != sat:
            return []

        # get_answer() returns a DNF formula — one clause per violation tuple.
        # _extract_tuples() reads it in O(|violations|) instead of O(|sites|×|fields|).
        tuples = _extract_tuples(self._fp.get_answer())
        field_names = {v: k for k, v in self._field_id.items()}

        by_site: dict[int, list[str]] = {}
        for si, fi in tuples:
            fname = field_names.get(fi)
            if fname is not None and si in self._site_meta:
                by_site.setdefault(si, []).append(fname)

        return [
            Z3Violation(
                file=self._site_meta[si]["file"],
                line=self._site_meta[si]["line"],
                call=self._site_meta[si]["call"],
                missing=missing,
            )
            for si, missing in by_site.items()
        ]


def run(root: Path) -> list[Z3Violation]:
    engine = PactEngine()
    engine.load(root)
    return engine.violations()
