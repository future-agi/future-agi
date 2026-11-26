"""The record of every writer of a call's ``eval_outputs``, decided one by one.

A call's ``eval_outputs`` slot is the run's own score. A path that replaces a
verdict there keeps it first, through one function (``replaced_verdict_rows``
and ``keep_replaced_verdicts``, or ``ClearedVerdicts`` around them). This file
keeps that true as the code grows: every function that writes the field is
filed below under the claim it makes, with a reason, and the claim is held to
the code where the code can show it. A writer nobody filed fails the first
case, and so does an entry whose function no longer writes, so a new writer
cannot ship until someone has decided.

A writer is the innermost function (or method) whose own code does one of:

- assigns to ``.eval_outputs`` or one of its keys, deletes a key, or calls a
  mutating method on it, directly or through a name bound to it
  (``outputs = call.eval_outputs or {}``), including from a closure;
- passes ``eval_outputs=`` to a manager, or ``**{"eval_outputs": ...}``;
- saves the field by name: a list naming it given to ``update_fields`` or
  ``bulk_update``, written out or bound to a name in the function or module,
  or grown with ``.append("eval_outputs")``;
- calls ``setattr(..., "eval_outputs", ...)``;
- holds SQL that sets the column (``SET ... eval_outputs =``).

Saving the field by name also catches a function that edits a copy of the
slots and saves it. These are the forms the code uses; a write the scan
cannot see is one whose field name or SQL is built at run time, or one made
through a helper that is passed the dict. Test files and migrations are not
scanned. The scan only reads source files.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import NamedTuple

FUTUREAGI = Path(__file__).resolve().parents[2]
FIELD = "eval_outputs"

_NEVER_WALKED = {
    "tests",
    "migrations",
    "node_modules",
    "__pycache__",
    "site-packages",
    ".venv",
    "venv",
}
MUTATORS = {"pop", "update", "clear", "setdefault", "popitem", "__setitem__"}
MANAGER_WRITES = {"update", "update_or_create", "create", "get_or_create"}
FIELD_SAVES = {"save", "bulk_update", "asave"}
LIST_GROWERS = {"append", "add", "extend", "insert"}

# ---------------------------------------------------------------------------
# The categories
# ---------------------------------------------------------------------------

KEEPS = "replaces verdicts, and keeps each one first through the one function"
PUTS_BACK = "puts back slots a failed request emptied"
GRADES = "the grader's own result, into a slot emptied or set pending for it"
SETTLES = "settles or removes a placeholder no grader will fill"
RESETS_THE_CALL = "resets the whole call for a call rerun; its snapshot keeps it"
CATEGORIES = (KEEPS, PUTS_BACK, GRADES, SETTLES, RESETS_THE_CALL)

# A function filed under KEEPS names one of these in its own code.
KEEPING_NAMES = frozenset(
    {
        "replaced_verdict_rows",
        "keep_replaced_verdicts",
        "ClearedVerdicts",
    }
)


class Filed(NamedTuple):
    category: str
    reason: str


_VIEWS = "simulate/views/run_test.py"
_EXECUTOR = "simulate/services/test_executor.py"
_XL = "simulate/temporal/activities/xl.py"

WRITERS = {
    # --- the paths that replace what a slot held ------------------------------
    "simulate/services/harness_run_evals.py::queue_evals_for_finished_calls": Filed(
        KEEPS,
        "harness run-evals: never replaces an answer (a stored verdict is "
        "never queued), but sets a failed, denied, skipped or timed-out slot "
        "pending; each caller states whether that is kept (QUEUE_CALLERS)",
    ),
    "simulate/services/run_regrade.py::pend_evals": Filed(
        KEEPS,
        "sets each chosen eval's slot pending to grade it again, under the "
        "calls' lock: run-new-evals and editing an eval with run set",
    ),
    f"{_VIEWS}::CallExecutionRerunView.post": Filed(
        KEEPS, "eval-only rerun of chosen calls: empties each call's slots"
    ),
    f"{_VIEWS}::TestExecutionRerunView._prepare_call_executions_for_rerun_bulk": Filed(
        KEEPS, "eval-only rerun of whole runs: empties each call's slots"
    ),
    # --- putting back ---------------------------------------------------------
    "simulate/services/call_rerun.py::restore_eval_only_rerun_state": Filed(
        PUTS_BACK,
        "a failed eval-only dispatch: the snapshot's slots go back, and the "
        "request's rows for them are forgotten in the same transaction",
    ),
    "simulate/services/run_regrade.py::regrade_run_evals": Filed(
        PUTS_BACK,
        "a failed run-new-evals dispatch: puts back what pend_evals replaced, "
        "and forgets the request's rows in the same transaction",
    ),
    "simulate/services/harness_run_evals.py::_clear_queued_stamps": Filed(
        PUTS_BACK,
        "a failed harness dispatch: puts back what its own placeholder replaced, "
        "and forgets the rows of the slots it puts back",
    ),
    # --- graders ----------------------------------------------------------------
    f"{_EXECUTOR}::TestExecutor._run_single_simulate_evaluation": Filed(
        GRADES,
        "dispatched behind the first-grading latch or after one of the KEEPS "
        "routes; with skip_existing a stored verdict is left alone",
    ),
    f"{_XL}::_run_single_evaluation": Filed(
        GRADES, "the Temporal grader; dispatched as the one above"
    ),
    f"{_XL}::_run_evaluations_standalone": Filed(
        GRADES, "marks evals skipped when the call has no transcript, as above"
    ),
    f"{_EXECUTOR}::TestExecutor._mark_processing_skipped_for_eval_rerun": Filed(
        GRADES, "the grader's skip outcome; skip_existing keeps a stored verdict"
    ),
    f"{_EXECUTOR}::TestExecutor._mark_eval_left_to_the_harness": Filed(
        GRADES, "the grader's outcome for a harness column it cannot grade"
    ),
    "simulate/services/evaluation_denials.py::record_evaluation_denial": Filed(
        GRADES, "the grader's outcome when grading is refused for usage or credits"
    ),
    "simulate/services/alk_simulate_ingestion.py::_apply_harness_evaluation_outputs": Filed(
        GRADES, "the harness's own verdicts, arriving with the call's result"
    ),
    "simulate/services/hosted_harness_ingestion.py::_apply_receipt_to_call": Filed(
        GRADES,
        "saves the harness's own verdicts that _apply_harness_evaluation_outputs "
        "wrote into the slots, with the rest of the hosted call's receipt",
    ),
    "simulate/temporal/activities/small.py::persist_processing_skip_state": Filed(
        GRADES,
        "the call workflow's failure step: overwrites only what the current "
        "attempt wrote, and the verdicts the rerun replaced were kept by its "
        "route. Known loss: verdicts that attempt wrote before failing. A "
        "failure step does as little as it can, so it gets no write; the later "
        "fix is to mark only slots that hold no verdict (TH-10785)",
    ),
    # --- placeholders -------------------------------------------------------------
    "simulate/services/scoring_status.py::close_due_scoring": Filed(
        SETTLES, "closes a placeholder past its scoring window as timed out"
    ),
    f"{_EXECUTOR}::TestExecutor._settle_grading_that_never_ran": Filed(
        SETTLES, "closes a placeholder whose grader was never started"
    ),
    f"{_EXECUTOR}::TestExecutor._drop_placeholders_of_removed_evals": Filed(
        SETTLES, "drops the placeholder of an eval removed before it ran"
    ),
    # --- the call itself --------------------------------------------------------------
    "simulate/models/test_execution.py::CallExecution.reset_to_default": Filed(
        RESETS_THE_CALL,
        "a call rerun replaces the evidence itself; its snapshot keeps the call",
    ),
}

# ---------------------------------------------------------------------------
# The scan
# ---------------------------------------------------------------------------


def _is_field(node) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == FIELD


def _is_field_name(node) -> bool:
    return isinstance(node, ast.Constant) and node.value == FIELD


def _names_field_in_a_list(node) -> bool:
    return isinstance(node, (ast.List, ast.Tuple, ast.Set)) and any(
        _is_field_name(item) for item in node.elts
    )


def _own_nodes(scope):
    """Every node in this scope's own code, not inside a nested def or class."""
    for child in ast.iter_child_nodes(scope):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        yield child
        yield from _own_nodes(child)


class Names(NamedTuple):
    """Names a scope binds to the field's own dict, or to a list naming it."""

    aliases: frozenset = frozenset()
    field_lists: frozenset = frozenset()


def _names_in(scope, outer: Names = Names()) -> Names:
    aliases, field_lists = set(outer.aliases), set(outer.field_lists)
    for node in _own_nodes(scope):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names = {t.id for t in targets if isinstance(t, ast.Name)}
        value = node.value
        # ``x = call.eval_outputs`` and ``x = call.eval_outputs or {}`` are the
        # call's own dict: editing ``x`` edits the slots.
        if _is_field(value) or (
            isinstance(value, ast.BoolOp) and any(_is_field(v) for v in value.values)
        ):
            aliases |= names
        if _names_field_in_a_list(value):
            field_lists |= names
    return Names(frozenset(aliases), frozenset(field_lists))


def _is_slots(node, names: Names) -> bool:
    return _is_field(node) or (isinstance(node, ast.Name) and node.id in names.aliases)


def _names_the_field(node, names: Names) -> bool:
    if _names_field_in_a_list(node):
        return True
    if isinstance(node, ast.Name) and node.id in names.field_lists:
        return True
    return isinstance(node, (ast.List, ast.Tuple, ast.Set)) and any(
        isinstance(item, ast.Starred) and _names_the_field(item.value, names)
        for item in node.elts
    )


_SQL_SET = re.compile(r"\bset\b[^;]*?\b" + FIELD + r"\b\"?\s*=", re.IGNORECASE)


def _sql_sets_the_field(node) -> bool:
    """A string of SQL that sets the column: ``UPDATE ... SET eval_outputs = ...``."""
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and bool(_SQL_SET.search(node.value))
    )


def writes(node, names: Names = Names()) -> bool:
    """Whether this one statement or call writes the field."""
    if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            for part in ast.walk(target):
                if _is_field(part) or (
                    isinstance(part, ast.Subscript) and _is_slots(part.value, names)
                ):
                    return True
    if isinstance(node, ast.Delete):
        return any(
            isinstance(target, ast.Subscript) and _is_slots(target.value, names)
            for target in node.targets
        )
    if _sql_sets_the_field(node):
        return True
    if not isinstance(node, ast.Call):
        return False
    if (
        isinstance(node.func, ast.Name)
        and node.func.id == "setattr"
        and len(node.args) > 1
        and _is_field_name(node.args[1])
    ):
        return True
    if not isinstance(node.func, ast.Attribute):
        return False
    method = node.func.attr
    if method in MUTATORS and _is_slots(node.func.value, names):
        return True
    if method in MANAGER_WRITES and any(k.arg == FIELD for k in node.keywords):
        return True
    if method in MANAGER_WRITES and any(
        k.arg in ("defaults", None)
        and isinstance(k.value, ast.Dict)
        and any(_is_field_name(key) for key in k.value.keys)
        for k in node.keywords
    ):
        return True
    if method in FIELD_SAVES and (
        any(_names_the_field(arg, names) for arg in node.args)
        or any(_names_the_field(k.value, names) for k in node.keywords)
    ):
        return True
    # A field list built up and then saved: ``fields.append("eval_outputs")``.
    return method in LIST_GROWERS and any(
        _is_field_name(arg) or _names_field_in_a_list(arg) for arg in node.args
    )


def writers_in(source: str, relative: str) -> dict[str, int]:
    """Each writer in one file, by ``path::qualname``, with its first write's line."""
    found: dict[str, int] = {}
    tree = ast.parse(source)
    module = _names_in(tree)
    # Only a field list is shared with the functions below; an alias at module
    # level is not a call's slots.
    shared = Names(field_lists=module.field_lists)

    def walk(node, stack, names):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                # A closure sees its enclosing function's names; a top-level
                # function or class sees only the module's field lists.
                outer = names if stack else shared
                walk(child, [*stack, child.name], _names_in(child, outer))
                continue
            if stack and writes(child, names) or not stack and writes(child, shared):
                key = f"{relative}::{'.'.join(stack) or '<module>'}"
                found.setdefault(key, child.lineno)
            walk(child, stack, names)

    walk(tree, [], shared)
    return found


def source_files():
    for path in sorted(FUTUREAGI.rglob("*.py")):
        relative = path.relative_to(FUTUREAGI).as_posix()
        if _NEVER_WALKED & set(relative.split("/")[:-1]):
            continue
        yield relative, path


def scan_writers() -> dict[str, int]:
    found: dict[str, int] = {}
    for relative, path in source_files():
        source = path.read_text(errors="ignore")
        if FIELD in source:
            found.update(writers_in(source, relative))
    return found


def _function(relative: str, qualname: str):
    tree = ast.parse((FUTUREAGI / relative).read_text())
    node = tree
    for name in qualname.split("."):
        node = next(
            child
            for child in ast.iter_child_nodes(node)
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and child.name == name
        )
    return node


def _names_used(node) -> set[str]:
    names = set()
    for part in ast.walk(node):
        if isinstance(part, ast.Name):
            names.add(part.id)
        elif isinstance(part, ast.Attribute):
            names.add(part.attr)
    return names


def _callers_of(name: str) -> set[str]:
    """Every function, in every scanned file, whose own code calls ``name``."""
    callers = set()
    for relative, path in source_files():
        source = path.read_text(errors="ignore")
        if name not in source:
            continue

        def walk(node, stack, relative=relative):
            for child in ast.iter_child_nodes(node):
                if isinstance(
                    child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                ):
                    walk(child, [*stack, child.name])
                    continue
                if isinstance(child, ast.Call) and (
                    (isinstance(child.func, ast.Name) and child.func.id == name)
                    or (
                        isinstance(child.func, ast.Attribute)
                        and child.func.attr == name
                    )
                ):
                    callers.add(f"{relative}::{'.'.join(stack) or '<module>'}")
                walk(child, stack)

        walk(ast.parse(source), [])
    return callers


# ---------------------------------------------------------------------------
# The cases
# ---------------------------------------------------------------------------

SAMPLE = """
def assigns(call):
    call.eval_outputs = {}

def sets_a_key(call):
    call.eval_outputs["a"] = 1

def pops(call):
    call.eval_outputs.pop("a")

def deletes(call):
    del call.eval_outputs["a"]

def manager(qs):
    qs.update(eval_outputs={})

def saves_a_copy(call):
    outputs = dict(call.eval_outputs)
    outputs["a"] = 1
    call.save(update_fields=["eval_outputs"])

def bulk(rows):
    Model.objects.bulk_update(rows, ["eval_outputs", "call_metadata"])

class Holder:
    def method(self, call):
        def inner():
            call.eval_outputs["b"] = 2
        return inner

def only_reads(call):
    return call.eval_outputs.get("a")

FIELDS = ["eval_outputs", "call_metadata"]

def saves_a_named_list(call):
    call.save(update_fields=FIELDS)

def edits_an_alias(call):
    outputs = call.eval_outputs or {}
    outputs["a"] = 1
    call.save()

def removes_through_an_alias(call):
    outputs = call.eval_outputs
    outputs.pop("a", None)

def unpacks_the_field(qs):
    qs.update(**{"eval_outputs": {}})

def raw_sql(cursor):
    cursor.execute("UPDATE simulate_call_execution SET eval_outputs = %s WHERE id = %s")

def sets_it_by_name(call):
    setattr(call, "eval_outputs", {})

def builds_the_field_list(call):
    fields = ["status"]
    fields.append("eval_outputs")
    call.save(update_fields=fields)

def closure(call):
    outputs = call.eval_outputs
    def inner():
        outputs["b"] = 2
    return inner

def copies_and_reads(call):
    outputs = dict(call.eval_outputs)
    outputs["a"] = 1
    return outputs

def names_the_column_in_prose():
    '''Set eval_outputs in prose; the column is read here, not written.'''
    return Model.objects.values("id", "eval_outputs")
"""


def test_the_scan_finds_every_form_of_write():
    found = set(writers_in(SAMPLE, "sample.py"))
    assert found == {
        "sample.py::assigns",
        "sample.py::sets_a_key",
        "sample.py::pops",
        "sample.py::deletes",
        "sample.py::manager",
        "sample.py::saves_a_copy",
        "sample.py::bulk",
        "sample.py::Holder.method.inner",
        "sample.py::saves_a_named_list",
        "sample.py::edits_an_alias",
        "sample.py::removes_through_an_alias",
        "sample.py::unpacks_the_field",
        "sample.py::raw_sql",
        "sample.py::sets_it_by_name",
        "sample.py::builds_the_field_list",
        "sample.py::closure.inner",
    }


def test_every_writer_of_eval_outputs_is_filed():
    found = scan_writers()
    unfiled = sorted(set(found) - set(WRITERS))
    stale = sorted(set(WRITERS) - set(found))
    assert not unfiled, (
        "These functions write a call's eval_outputs and nobody has decided "
        "whether they replace a verdict. File each in WRITERS; one that "
        "replaces a verdict keeps it first through keep_replaced_verdicts: "
        + ", ".join(f"{key}:{found[key]}" for key in unfiled)
    )
    assert not stale, (
        "These entries no longer write eval_outputs; remove them: " + ", ".join(stale)
    )
    assert {filed.category for filed in WRITERS.values()} <= set(CATEGORIES)
    assert all(filed.reason for filed in WRITERS.values())


def test_every_path_that_replaces_a_verdict_keeps_it_through_the_one_function():
    missing = []
    for key, filed in WRITERS.items():
        if filed.category != KEEPS:
            continue
        relative, qualname = key.split("::")
        if not _names_used(_function(relative, qualname)) & KEEPING_NAMES:
            missing.append(key)
    assert not missing, f"Filed as keeping, but names no keeping function: {missing}"


def test_both_routes_that_grade_again_go_through_the_one_pending_step():
    assert _callers_of("pend_evals") == {
        "simulate/services/run_regrade.py::regrade_run_evals",
        f"{_VIEWS}::UpdateEvalConfigView.post",
    }


def test_only_the_store_makes_a_replaced_verdict_row():
    store = "simulate/services/eval_result_store.py"
    model = "simulate/models/eval_result_store.py"
    callers = _callers_of("keep_replaced_verdicts")
    assert {caller.split("::")[0] for caller in callers} <= {
        store,
        "simulate/services/run_regrade.py",
        "simulate/services/harness_run_evals.py",
        _VIEWS,
    }
    # The replaced origin is named only where the model defines it and the
    # store builds its rows.
    naming = {
        relative
        for relative, path in source_files()
        if "REPLACED_OWN_SCORE" in path.read_text(errors="ignore")
    }
    assert naming <= {store, model}, naming


def test_every_put_back_forgets_the_rows_of_what_it_puts_back():
    clear = _function("simulate/services/harness_run_evals.py", "_clear_queued_stamps")
    assert {"forget_replacement", "atomic"} <= _names_used(clear)


def test_the_eval_only_put_back_forgets_the_rows_in_the_same_place():
    restore = _function(
        "simulate/services/call_rerun.py", "restore_eval_only_rerun_state"
    )
    assert {"forget_replacement", "atomic"} <= _names_used(restore)
    regrade = _function("simulate/services/run_regrade.py", "regrade_run_evals")
    assert {"forget_replacement", "atomic"} <= _names_used(regrade)


# Whether each caller of the harness queue keeps the slots it sets pending.
# The queue never replaces an answer, only a failed, denied, skipped or
# timed-out slot; that is what makes "do not keep" safe. If it ever replaces
# an answer, every False here must be decided again.
QUEUE_CALLERS = {
    "simulate/views/harness_environment.py::HarnessEnvironmentViewSet.add_run_evaluation": Filed(
        "keeps", "a person presses run evals: what it replaces is their history"
    ),
    "simulate/services/agent_fix_engine.py::_grade_winner": Filed(
        "does not keep",
        "a fix's own check runs, regraded by a machine loop on each report: "
        "never a person's history",
    ),
    "simulate/services/harness_run_evals.py::queue_eval_for_finished_calls": Filed(
        "passes it on", "the one-config form: its own caller states the choice"
    ),
}
QUEUE_NAMES = {"queue_eval_for_finished_calls", "queue_evals_for_finished_calls"}


def _queue_choices() -> dict[str, str]:
    """Each source caller of the queue, by ``path::qualname``, with its choice."""
    found = {}
    for relative, path in source_files():
        source = path.read_text(errors="ignore")
        if "_for_finished_calls" not in source:
            continue

        def walk(node, stack, relative=relative):
            for child in ast.iter_child_nodes(node):
                if isinstance(
                    child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                ):
                    walk(child, [*stack, child.name])
                    continue
                if isinstance(child, ast.Call):
                    func = child.func
                    name = getattr(func, "id", None) or getattr(func, "attr", None)
                    if name in QUEUE_NAMES:
                        keyword = next(
                            (k for k in child.keywords if k.arg == "keep_replaced"),
                            None,
                        )
                        if keyword is None:
                            choice = "states nothing"
                        elif isinstance(keyword.value, ast.Constant):
                            choice = "keeps" if keyword.value.value else "does not keep"
                        else:
                            choice = "passes it on"
                        key = f"{relative}::{'.'.join(stack) or '<module>'}"
                        found[key] = choice
                walk(child, stack)

        walk(ast.parse(source), [])
    return found


def test_every_caller_of_the_harness_queue_states_whether_it_keeps():
    found = _queue_choices()
    filed = {key: entry.category for key, entry in QUEUE_CALLERS.items()}
    assert found == filed, (
        "A caller of the harness queue is new, gone or changed its choice. "
        "Decide whether it keeps what it sets pending and record it in "
        f"QUEUE_CALLERS. Found: {found}"
    )


# ---------------------------------------------------------------------------
# Who starts grading
# ---------------------------------------------------------------------------

# The grading tasks and the call workflow's grading activity. Grading writes
# its result into the call's slot; unless ``skip_existing`` is true it writes
# over whatever the slot holds, keeping nothing. So every place that starts
# grading is filed here with the choice it makes and why the slots it grades
# hold no verdict that would be lost.
GRADING_TASKS = {
    "_run_simulate_evaluations_task",
    "run_new_evals_on_call_executions_task",
}
GRADING_ACTIVITY = "run_simulate_evaluations"
# What the task and the activity run underneath, called directly.
GRADING_BODIES = {"_run_simulate_evaluations", "_run_evaluations_standalone"}
_TASK_STARTS = {
    "apply_async",
    "delay",
    "s",
    "si",
    "signature",
    "apply",
    "run",
    "map",
    "starmap",
    "chunks",
}
_ACTIVITY_STARTS = {"execute_activity", "start_activity"}


def _name_of(node) -> str | None:
    return getattr(node, "id", None) or getattr(node, "attr", None)


def _skip_existing(keywords) -> str:
    """The ``skip_existing`` a dispatch passes, read from its own code."""
    for keyword in keywords:
        if keyword.arg == "skip_existing":
            if isinstance(keyword.value, ast.Constant):
                return f"skip_existing={keyword.value.value}"
            return "passes skip_existing on"
        if keyword.arg == "kwargs" and isinstance(keyword.value, ast.Dict):
            for key, value in zip(
                keyword.value.keys, keyword.value.values, strict=True
            ):
                if isinstance(key, ast.Constant) and key.value == "skip_existing":
                    if isinstance(value, ast.Constant):
                        return f"skip_existing={value.value}"
                    return "passes skip_existing on"
    return "skip_existing default (False)"


def _grading_start(call: ast.Call) -> str | None:
    """How ``call`` starts grading, or None when it does not. Not seen: a task or
    an activity reached through another name (an alias, a variable)."""
    func = call.func
    name = _name_of(func)
    if name in GRADING_BODIES:
        return _skip_existing(call.keywords)
    if name == "send_task" and call.args:
        first = call.args[0]
        sent = first.value if isinstance(first, ast.Constant) else None
        if not isinstance(sent, str):
            return None
        task = sent.rsplit(".", 1)[-1]
        if task == "run_new_evals_on_call_executions_task":
            return "grades again"
        return _skip_existing(call.keywords) if task in GRADING_TASKS else None
    if name in GRADING_TASKS:
        # Called in process.
        return (
            "grades again"
            if name == "run_new_evals_on_call_executions_task"
            else _skip_existing(call.keywords)
        )
    if (
        isinstance(func, ast.Attribute)
        and name in _TASK_STARTS
        and _name_of(func.value) in GRADING_TASKS
    ):
        if _name_of(func.value) == "run_new_evals_on_call_executions_task":
            return "grades again"
        return _skip_existing(call.keywords)
    if name in _ACTIVITY_STARTS and call.args:
        first = call.args[0]
        named = first.value if isinstance(first, ast.Constant) else _name_of(first)
        if named != GRADING_ACTIVITY:
            return None
        given = call.args[1] if len(call.args) > 1 else None
        keywords = given.keywords if isinstance(given, ast.Call) else []
        return _skip_existing(keywords)
    return None


def grading_starts_in(source: str, relative: str) -> dict[str, str]:
    """Each function that starts grading, by ``path::qualname``, with its choices."""
    found: dict[str, list[str]] = {}

    def walk(node, stack):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                walk(child, [*stack, child.name])
                continue
            if isinstance(child, ast.Call):
                choice = _grading_start(child)
                if choice is not None:
                    key = f"{relative}::{'.'.join(stack) or '<module>'}"
                    found.setdefault(key, []).append(choice)
            walk(child, stack)

    walk(ast.parse(source), [])
    return {key: "; ".join(sorted(choices)) for key, choices in found.items()}


def scan_grading_starts() -> dict[str, str]:
    found: dict[str, str] = {}
    for relative, path in source_files():
        source = path.read_text(errors="ignore")
        if any(
            name in source
            for name in (GRADING_ACTIVITY, *GRADING_TASKS, *GRADING_BODIES)
        ):
            found.update(grading_starts_in(source, relative))
    return found


_WORKFLOW = "ee/voice/temporal/workflows/call_execution_workflow.py"

DISPATCHERS = {
    f"{_WORKFLOW}::CallExecutionWorkflow.run": Filed(
        "skip_existing default (False)",
        "the first grading, once the call itself has run: the call is new, or "
        "a call-and-eval rerun reset it and its snapshot keeps what it held",
    ),
    f"{_WORKFLOW}::CallExecutionWorkflow._run_eval_only_mode": Filed(
        "skip_existing=False",
        "the eval-only rerun's child: only the two rerun routes start it "
        "(rerun_call_executions with eval_only), after they emptied the slots "
        "and kept each verdict (WRITERS, KEEPS)",
    ),
    "simulate/services/alk_simulate_ingestion.py::_dispatch_evaluations_once": Filed(
        "skip_existing default (False)",
        "the first grading of a finished call, behind the eval_started latch, "
        "taken in one locked write with the dispatch stamps",
    ),
    "simulate/services/chat_sim.py::finalize_chat_execution": Filed(
        "skip_existing default (False)",
        "the first grading of a finished chat, behind the eval_started latch",
    ),
    "simulate/tasks/chat_sim.py::store_chat_messages": Filed(
        "skip_existing default (False)",
        "the first grading of a finished chat, behind the eval_started latch",
    ),
    "simulate/services/harness_run_evals.py::_dispatch_one": Filed(
        "skip_existing=True",
        "the harness queue: a config whose slot holds a verdict is skipped",
    ),
    "simulate/services/run_regrade.py::regrade_run_evals": Filed(
        "grades again",
        "run-new-evals: after pend_evals kept each verdict it set pending",
    ),
    f"{_VIEWS}::UpdateEvalConfigView.post": Filed(
        "grades again",
        "editing an eval with run set: after pend_evals kept each verdict",
    ),
    f"{_EXECUTOR}::run_new_evals_on_call_executions_task": Filed(
        "skip_existing=False",
        "the job of the two filed above; it grades only the slots they set pending",
    ),
    f"{_EXECUTOR}::_run_simulate_evaluations_task": Filed(
        "passes skip_existing on",
        "the grading task's own body: whoever starts the task chooses "
        "skip_existing, and each of those is filed here",
    ),
    "simulate/temporal/activities/xl.py::run_simulate_evaluations.run_evaluations_sync": Filed(
        "passes skip_existing on",
        "the grading activity's own body: the workflow that starts the activity "
        "chooses skip_existing, and each start is filed here",
    ),
    f"{_EXECUTOR}::TestExecutor.monitor_test_execution": Filed(
        "skip_existing default (False); skip_existing default (False)",
        "nothing runs it: its only caller is commented out "
        "(ee/voice/tasks/test_monitor.py)",
    ),
}


GRADING_SAMPLE = """
def by_name(call):
    _run_simulate_evaluations_task.apply_async(args=(call,))

def keeps_what_is_there(call):
    tasks._run_simulate_evaluations_task.delay(call, skip_existing=True)

def in_kwargs(call, keep):
    _run_simulate_evaluations_task.apply_async(
        args=(call,), kwargs={"skip_existing": keep}
    )

def in_process(call):
    _run_simulate_evaluations_task(call, skip_existing=False)

def again(calls, configs):
    run_new_evals_on_call_executions_task.apply_async(args=(calls, configs))

class Workflow:
    async def run(self, call):
        await workflow.execute_activity(
            "run_simulate_evaluations",
            RunSimulateEvaluationsInput(call_execution_id=call, skip_existing=True),
        )
        workflow.start_activity("run_simulate_evaluations", Input(call))

def twice(call):
    _run_simulate_evaluations_task.apply_async(args=(call,))
    _run_simulate_evaluations_task.apply_async(args=(call,))

def other_task_starts(call, calls):
    _run_simulate_evaluations_task.apply(args=(call,))
    _run_simulate_evaluations_task.run(call, skip_existing=True)
    _run_simulate_evaluations_task.map(calls)
    _run_simulate_evaluations_task.starmap([(call,)])
    _run_simulate_evaluations_task.chunks(calls, 10)

def sent_by_name(call):
    app.send_task(
        "simulate.services.test_executor._run_simulate_evaluations_task",
        args=(call,),
        kwargs={"skip_existing": False},
    )

def underneath(executor, call):
    executor._run_simulate_evaluations(call, skip_existing=True)
    _run_evaluations_standalone(call)

def not_grading(call):
    other_task.apply_async(args=(call,))
    workflow.execute_activity("another_activity", call)
    names = ["run_simulate_evaluations"]
"""


def test_the_grading_scan_finds_every_way_to_start_grading():
    found = grading_starts_in(GRADING_SAMPLE, "sample.py")
    assert found == {
        "sample.py::by_name": "skip_existing default (False)",
        "sample.py::keeps_what_is_there": "skip_existing=True",
        "sample.py::in_kwargs": "passes skip_existing on",
        "sample.py::in_process": "skip_existing=False",
        "sample.py::again": "grades again",
        "sample.py::Workflow.run": "skip_existing default (False); skip_existing=True",
        "sample.py::twice": "skip_existing default (False); skip_existing default (False)",
        "sample.py::other_task_starts": "; ".join(
            ["skip_existing default (False)"] * 4 + ["skip_existing=True"]
        ),
        "sample.py::sent_by_name": "skip_existing=False",
        "sample.py::underneath": "skip_existing default (False); skip_existing=True",
    }


def test_every_place_that_starts_grading_is_filed_with_its_choice():
    found = scan_grading_starts()
    # A build without ee/ has none of its dispatchers.
    filed = {
        key: entry.category
        for key, entry in DISPATCHERS.items()
        if (FUTUREAGI / key.split("::")[0]).exists()
    }
    assert found == filed, (
        "A place that starts grading is new, gone or changed its choice. "
        "Grading writes over what a slot holds unless skip_existing is true: "
        "decide why the slots it grades hold no verdict to lose, or keep them "
        f"first, and record it in DISPATCHERS. Found: {found}"
    )
    assert all(entry.reason for entry in DISPATCHERS.values())
