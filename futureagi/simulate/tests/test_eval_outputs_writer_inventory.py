"""The record of every writer of a call's ``eval_outputs``, decided one by one.

A call's ``eval_outputs`` slot is the run's own score. A path that replaces a
verdict there keeps it first, through one function (``replaced_verdict_rows``
and ``keep_replaced_verdicts``, or ``ClearedVerdicts`` around them). This file
keeps that true as the code grows: every function that writes the field is
filed below under the claim it makes, with a reason, and the claim is held to
the code where the code can show it. A writer nobody filed fails the first
case, and so does an entry whose function no longer writes, so a new writer
cannot ship until someone has decided.

A writer is the innermost function (or method) whose own code assigns to
``.eval_outputs`` or one of its keys, deletes a key, calls a mutating method on
it, passes ``eval_outputs=`` to a manager, or saves the field by name
(``update_fields`` or ``bulk_update``). The last form also catches a function
that edits a copy of the slots and saves it. Test files and migrations are not
scanned. The scan only reads source files.
"""

from __future__ import annotations

import ast
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
        "_cleared_verdicts",
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
    "simulate/services/run_regrade.py::regrade_run_evals": Filed(
        KEEPS, "run-new-evals: sets each chosen eval's slot pending to grade it again"
    ),
    f"{_VIEWS}::UpdateEvalConfigView.post": Filed(
        KEEPS, "editing an eval with run set: its slot goes pending on every call"
    ),
    f"{_VIEWS}::_rerun_call_executions": Filed(
        KEEPS,
        "eval-only rerun called from code: empties each call's slots. No "
        "production caller today, only tests; kept so a new caller keeps too",
    ),
    f"{_VIEWS}::CallExecutionRerunView.post": Filed(
        KEEPS, "eval-only rerun of chosen calls: empties each call's slots"
    ),
    f"{_VIEWS}::TestExecutionRerunView._prepare_call_executions_for_rerun_bulk": Filed(
        KEEPS, "eval-only rerun of whole runs: empties each call's slots"
    ),
    # --- putting back ---------------------------------------------------------
    f"{_VIEWS}::_put_back_eval_only_rerun_state": Filed(
        PUTS_BACK,
        "a failed eval-only dispatch: the snapshot's slots go back, and "
        "_restore_eval_only_rerun_state forgets the request's rows with them",
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
    "simulate/temporal/activities/small.py::persist_processing_skip_state": Filed(
        GRADES,
        "the call workflow's failure step: overwrites only what the current "
        "attempt wrote, and the verdicts the rerun replaced were kept by its "
        "route. Known loss: verdicts that attempt wrote before failing. A "
        "failure step does as little as it can, so it gets no write; the later "
        "fix is to mark only slots that hold no verdict",
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


def _names_field_in_a_list(node) -> bool:
    return isinstance(node, (ast.List, ast.Tuple, ast.Set)) and any(
        isinstance(item, ast.Constant) and item.value == FIELD for item in node.elts
    )


def writes(node) -> bool:
    """Whether this one statement or call writes the field."""
    if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            for part in ast.walk(target):
                if _is_field(part) or (
                    isinstance(part, ast.Subscript) and _is_field(part.value)
                ):
                    return True
    if isinstance(node, ast.Delete):
        return any(
            isinstance(target, ast.Subscript) and _is_field(target.value)
            for target in node.targets
        )
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        method = node.func.attr
        if method in MUTATORS and _is_field(node.func.value):
            return True
        if method in MANAGER_WRITES and any(k.arg == FIELD for k in node.keywords):
            return True
        if method in MANAGER_WRITES and any(
            k.arg == "defaults"
            and isinstance(k.value, ast.Dict)
            and any(
                isinstance(key, ast.Constant) and key.value == FIELD
                for key in k.value.keys
            )
            for k in node.keywords
        ):
            return True
        if method in FIELD_SAVES and (
            any(_names_field_in_a_list(arg) for arg in node.args)
            or any(_names_field_in_a_list(k.value) for k in node.keywords)
        ):
            return True
    return False


def writers_in(source: str, relative: str) -> dict[str, int]:
    """Each writer in one file, by ``path::qualname``, with its first write's line."""
    found: dict[str, int] = {}

    def walk(node, stack):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                walk(child, [*stack, child.name])
                continue
            if writes(child):
                key = f"{relative}::{'.'.join(stack) or '<module>'}"
                found.setdefault(key, child.lineno)
            walk(child, stack)

    walk(ast.parse(source), [])
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
    # The bulk route reaches ClearedVerdicts through its own accessor.
    accessor = _function(_VIEWS, "TestExecutionRerunView._cleared_verdicts")
    assert "ClearedVerdicts" in _names_used(accessor)


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
    callers = _callers_of("_put_back_eval_only_rerun_state")
    assert callers == {f"{_VIEWS}::_restore_eval_only_rerun_state"}
    restore = _function(_VIEWS, "_restore_eval_only_rerun_state")
    assert {"forget_replacement", "atomic"} <= _names_used(restore)


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
