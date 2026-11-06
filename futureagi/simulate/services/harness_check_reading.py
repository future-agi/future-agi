"""What a sub-goal's ``def check(world, calls[, reference])`` reads, by Python's
``ast``: whether it reads the world, and which tools it fails without a call to.
Never estimated: a tool whose role cannot be read is reported as such.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class CheckReading:
    """What one sub-goal's check reads.

    ``tools`` are the tools it fails without a call to; ``named`` every tool it
    compares a call's name with, a tool it forbids included. ``clear`` is false when
    a named tool's role could not be read.
    """

    world: bool
    tools: frozenset[str]
    named: frozenset[str] = frozenset()
    clear: bool = True


def read_check(source: str) -> CheckReading | None:
    """What ``def check(world, calls[, reference])`` in ``source`` reads; ``None``
    when the source does not parse or defines no such function."""
    if not source.strip():
        return None
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    check = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name == "check"
            and len(node.args.args) >= 2
        ),
        None,
    )
    if check is None:
        return None
    world = check.args.args[0].arg
    required, named, clear = _tool_roles(check)
    return CheckReading(
        world=any(
            isinstance(node, ast.Name) and node.id == world for node in ast.walk(check)
        ),
        tools=required,
        named=named,
        clear=clear,
    )


def _tool_roles(
    check: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[frozenset[str], frozenset[str], bool]:
    """The tools the check fails without, every tool it names, and whether each
    named tool's role was read.

    A role is read from a guard that returns a failure: ``if not <calls>:`` requires
    the calls, ``if <calls>:`` forbids them, and ``return None if <calls> else ...``
    requires them. ``<calls>`` is a call-name comparison, ``any``, ``len`` or
    ``bool`` of one, or a name assigned from one.
    """
    named = frozenset(_named_tools(check))
    if not named:
        return frozenset(), named, True
    held: dict[str, _Tools] = {}
    for node in ast.walk(check):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            tools = _tools_in(node.value, held)
            known = held.get(node.targets[0].id, _Tools(frozenset(), False))
            held[node.targets[0].id] = _Tools(
                known.names | tools.names, known.alternatives or tools.alternatives
            )
    roles = _Roles()
    for node in ast.walk(check):
        if isinstance(node, ast.If):
            if _fails(node.body):
                _guard(node.test, True, held, roles)
            elif _fails(node.orelse):
                _guard(node.test, False, held, roles)
        elif isinstance(node, ast.Return) and isinstance(node.value, ast.IfExp):
            when = node.value
            if _failure(when.body) and not _failure(when.orelse):
                _guard(when.test, True, held, roles)
            elif _failure(when.orelse) and not _failure(when.body):
                _guard(when.test, False, held, roles)
    clear = named <= roles.placed and not (roles.unclear - roles.placed)
    return frozenset(roles.required), named, clear


@dataclass(frozen=True)
class _Tools:
    """Tools a call-name comparison picks; ``alternatives`` when any of them would do."""

    names: frozenset[str]
    alternatives: bool


@dataclass
class _Roles:
    required: set[str] = field(default_factory=set)
    placed: set[str] = field(default_factory=set)
    unclear: set[str] = field(default_factory=set)


def _guard(
    test: ast.expr, fails_when_true: bool, held: Mapping[str, _Tools], roles: _Roles
) -> None:
    """Record the role of each tool ``test`` reads in a guard returning a failure."""
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        _guard(test.operand, not fails_when_true, held, roles)
        return
    if isinstance(test, ast.BoolOp):
        either = isinstance(test.op, ast.Or)
        if either == fails_when_true:
            # Failing when any part holds, or passing only when every part does.
            for value in test.values:
                _guard(value, fails_when_true, held, roles)
            return
        if fails_when_true:
            # Failing only when every part holds requires no call it picks; a part
            # failing on a missing call leaves open which call is needed.
            for value in test.values:
                names = _tools_in(value, held).names
                if _monotone(value):
                    roles.placed.update(names)
                else:
                    roles.unclear.update(names)
            return
        roles.unclear.update(_tools_in(test, held).names)
        return
    tools = _tools_in(test, held)
    if not tools.names:
        return
    if not _monotone(test):
        roles.unclear.update(tools.names)
    elif fails_when_true:
        roles.placed.update(tools.names)
    elif tools.alternatives:
        roles.unclear.update(tools.names)
    else:
        roles.required.update(tools.names)
        roles.placed.update(tools.names)


def _monotone(test: ast.expr) -> bool:
    """Whether ``test`` holds once a call it picks is made, whatever else is."""
    if isinstance(test, ast.Name | ast.ListComp | ast.SetComp | ast.GeneratorExp):
        return True
    if isinstance(test, ast.Call):
        return (
            isinstance(test.func, ast.Name)
            and test.func.id in ("any", "len", "bool")
            and len(test.args) == 1
        )
    return _name_equals(test) is not None


def _tools_in(node: ast.AST, held: Mapping[str, _Tools]) -> _Tools:
    """The tools ``node`` compares a call's name with, directly or through a name
    assigned earlier; ``alternatives`` when one sits under ``or`` or ``not``."""
    names: set[str] = set()
    alternatives = False
    for one in ast.walk(node):
        if isinstance(one, ast.Name) and one.id in held:
            names |= held[one.id].names
            alternatives = alternatives or held[one.id].alternatives
        tool = _name_equals(one)
        if tool is not None:
            names.add(tool)
        elif isinstance(one, ast.BoolOp | ast.UnaryOp) and _named_tools(one):
            alternatives = alternatives or isinstance(one.op, ast.Or | ast.Not)
    return _Tools(frozenset(names), alternatives)


def _fails(body: list[ast.stmt]) -> bool:
    return any(isinstance(one, ast.Return) and _failure(one.value) for one in body)


def _failure(value: ast.expr | None) -> bool:
    """A check returns ``None`` when it holds and a reason when it does not."""
    return value is not None and not (
        isinstance(value, ast.Constant) and value.value is None
    )


def _name_equals(node: ast.AST) -> str | None:
    """``"<tool>"`` from ``<call>.name == "<tool>"``, either way round."""
    if not (
        isinstance(node, ast.Compare)
        and len(node.ops) == 1
        and isinstance(node.ops[0], ast.Eq)
    ):
        return None
    sides = (node.left, node.comparators[0])
    for one, other in (sides, sides[::-1]):
        if (
            isinstance(one, ast.Attribute)
            and one.attr == "name"
            and isinstance(other, ast.Constant)
            and isinstance(other.value, str)
        ):
            return other.value
    return None


def _named_tools(tree: ast.AST) -> Iterable[str]:
    """Every tool name compared with a call's ``name``, by any comparison."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        sides = [node.left, *node.comparators]
        if not any(
            isinstance(one, ast.Attribute) and one.attr == "name" for one in sides
        ):
            continue
        for side in sides:
            for one in ast.walk(side):
                if isinstance(one, ast.Constant) and isinstance(one.value, str):
                    yield one.value
