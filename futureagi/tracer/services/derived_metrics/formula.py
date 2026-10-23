"""A bounded expression language compiled to ClickHouse, never executed as Python.

Aggregates operate on complete cases at the definition's selection unit. Probability
weights apply once per unit, after each input has independently been reduced.
"""

from __future__ import annotations

import ast
import math
import re
from dataclasses import dataclass

ALIAS = re.compile(r"[a-z][a-z0-9_]{0,39}\Z")
AGGREGATES = {"ratio": 2, "mean": 1, "sum": 1, "count_if": 1}
BINARY = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/"}
COMPARE = {
    ast.Eq: "=",
    ast.NotEq: "!=",
    ast.Lt: "<",
    ast.LtE: "<=",
    ast.Gt: ">",
    ast.GtE: ">=",
}


class FormulaError(ValueError):
    """A formula cannot be compiled safely or has an unsupported shape."""


@dataclass(frozen=True)
class Formula:
    kind: str
    numerator: str
    denominator: str
    required: tuple[str, ...]
    parameters: dict[str, float]
    # Proven from the immutable formula, never inferred from observed input values.
    rate_eligible: bool = False

    @property
    def complete_sql(self) -> str:
        return " AND ".join(
            f"isNotNull(v_{name}) AND isFinite(v_{name})" for name in self.required
        )


def _indicator(node: ast.AST) -> bool:
    """Prove a validated expression is 0/1 on every complete unit.

    Bindings are arbitrary numeric evaluations/attributes, even when all observed
    values are zero or one. Only formula operations can establish a population
    bound. Unsupported identities conservatively use generic numeric inference.
    """
    if isinstance(node, ast.Constant):
        return node.value in (0, 1)
    if isinstance(node, ast.Compare | ast.BoolOp):
        # SQL AND/OR produce booleans (not Python's operand-valued result).
        return True
    if isinstance(node, ast.UnaryOp):
        return isinstance(node.op, ast.Not) or (
            isinstance(node.op, ast.UAdd) and _indicator(node.operand)
        )
    if isinstance(node, ast.BinOp):
        if isinstance(node.op, ast.Mult):
            return _indicator(node.left) and _indicator(node.right)
        if isinstance(node.op, ast.Sub):
            return (
                isinstance(node.left, ast.Constant)
                and node.left.value == 1
                and _indicator(node.right)
            )
    return False


def _indicator_factors(node: ast.AST) -> set[str]:
    """Conjuncts of a proven indicator, for a conservative subset proof."""
    if isinstance(node, ast.Constant) and node.value == 1:
        return set()
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.UAdd):
        return _indicator_factors(node.operand)
    if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.And):
        return set().union(*(_indicator_factors(value) for value in node.values))
    if (
        isinstance(node, ast.BinOp)
        and isinstance(node.op, ast.Mult)
        and _indicator(node)
    ):
        return _indicator_factors(node.left) | _indicator_factors(node.right)
    return {ast.dump(node)}


def compile_formula(text: str, aliases: list[str]) -> Formula:
    """Compile only named inputs, finite constants, arithmetic and predicates."""
    if len(text) > 2048 or not text.strip():
        raise FormulaError("Formula must contain 1–2048 characters.")
    if not aliases or len(aliases) > 16 or len(set(aliases)) != len(aliases):
        raise FormulaError("Use 1–16 uniquely named inputs.")
    if any(not ALIAS.fullmatch(name) or name in AGGREGATES for name in aliases):
        raise FormulaError(
            "Input aliases must start with a lowercase letter and contain only letters, digits or underscores."
        )
    try:
        tree = ast.parse(text, mode="eval")
    except (SyntaxError, RecursionError) as exc:
        raise FormulaError("Formula syntax is invalid.") from exc
    if sum(1 for _ in ast.walk(tree)) > 128:
        raise FormulaError("Formula exceeds the 128-node complexity limit.")
    call = tree.body
    if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name):
        raise FormulaError("Start with ratio, mean, sum or count_if.")
    kind = call.func.id
    if kind not in AGGREGATES or len(call.args) != AGGREGATES[kind] or call.keywords:
        raise FormulaError(
            "Use ratio(numerator, denominator), mean(value), sum(value) or count_if(condition)."
        )
    required: set[str] = set()
    parameters: dict[str, float] = {}

    def expression(node: ast.AST, depth: int = 0) -> str:
        if depth > 16:
            raise FormulaError("Formula is too deeply nested.")
        if isinstance(node, ast.Name) and node.id in aliases:
            required.add(node.id)
            return f"v_{node.id}"
        if isinstance(node, ast.Constant) and type(node.value) in (int, float, bool):
            try:
                value = float(node.value)
            except OverflowError as exc:
                raise FormulaError("Constants must be finite numbers.") from exc
            if not math.isfinite(value) or abs(value) > 1e15:
                raise FormulaError(
                    "Constants must be finite numbers no larger than 1e15."
                )
            name = f"constant_{len(parameters)}"
            parameters[name] = value
            return f"%({name})s"
        if isinstance(node, ast.BinOp) and type(node.op) in BINARY:
            left, right = (
                expression(node.left, depth + 1),
                expression(node.right, depth + 1),
            )
            if isinstance(node.op, ast.Div):
                right = f"nullIf({right}, 0)"
            return f"({left} {BINARY[type(node.op)]} {right})"
        if (
            isinstance(node, ast.Compare)
            and len(node.ops) == 1
            and type(node.ops[0]) in COMPARE
        ):
            return f"({expression(node.left, depth + 1)} {COMPARE[type(node.ops[0])]} {expression(node.comparators[0], depth + 1)})"
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.And | ast.Or):
            operator = " AND " if isinstance(node.op, ast.And) else " OR "
            return (
                "("
                + operator.join(expression(value, depth + 1) for value in node.values)
                + ")"
            )
        if isinstance(node, ast.UnaryOp) and isinstance(
            node.op, ast.Not | ast.USub | ast.UAdd
        ):
            operator = {ast.Not: "NOT ", ast.USub: "-", ast.UAdd: "+"}[type(node.op)]
            return f"({operator}{expression(node.operand, depth + 1)})"
        raise FormulaError(
            "Only input aliases, numbers, + - * /, comparisons and and/or/not are allowed."
        )

    numerator = expression(call.args[0])
    denominator = expression(call.args[1]) if kind == "ratio" else "1.0"
    if kind == "count_if":
        numerator = f"toFloat64(({numerator}) != 0)"
    if not required:
        raise FormulaError("Reference at least one input.")
    # Ratios require more than two bounded operands: the numerator must be a
    # subset of the denominator on EVERY possible unit, not just this sample.
    rate_eligible = kind in {"mean", "ratio"} and _indicator(call.args[0])
    if kind == "ratio":
        rate_eligible = (
            rate_eligible
            and _indicator(call.args[1])
            and _indicator_factors(call.args[1]) <= _indicator_factors(call.args[0])
        )
    return Formula(
        kind, numerator, denominator, tuple(sorted(required)), parameters, rate_eligible
    )
