"""A rerun that fails shows a fixed reason for how its calls ended.

A rerun that could not be handed to the workers shows "Rerun dispatch failed.
Re-run it." on its calls (simulate/tests/test_rerun_api.py drives that). A
rerun whose coordinator stops on an error cancels its calls with "The rerun
stopped because of an error. Re-run it." That runs inside a workflow, so these
tests read the source: no end reason the rerun views store is built from an
error's text, and the coordinator cancels its calls with a fixed reason.
"""

import ast
from pathlib import Path

from simulate.temporal.workflows import rerun_coordinator_workflow
from simulate.views import run_test

# The names an error's text is held in where these modules handle a failure.
ERROR_TEXT_NAMES = {
    "e",
    "error",
    "error_message",
    "dispatch_error",
    "dispatch_error_message",
}


def _names_in(node):
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _end_reasons_stored(tree):
    """Every value assigned to an ``ended_reason`` attribute or passed as an
    ``ended_reason`` keyword, with its line."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Attribute) and target.attr == "ended_reason"
            for target in node.targets
        ):
            yield node.lineno, node.value
        elif isinstance(node, ast.keyword) and node.arg == "ended_reason":
            yield node.value.lineno, node.value


def test_no_end_reason_the_rerun_views_store_is_built_from_an_error():
    tree = ast.parse(Path(run_test.__file__).read_text())
    built_from_errors = [
        (line, ast.unparse(value))
        for line, value in _end_reasons_stored(tree)
        if _names_in(value) & ERROR_TEXT_NAMES
    ]
    assert built_from_errors == []


def test_the_rerun_coordinator_cancels_its_calls_with_a_fixed_reason():
    tree = ast.parse(Path(rerun_coordinator_workflow.__file__).read_text())
    reasons = [
        keyword.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", None) == "CancelRerunCallsInput"
        for keyword in node.keywords
        if keyword.arg == "reason"
    ]
    assert reasons
    for reason in reasons:
        assert isinstance(reason, (ast.Name, ast.Constant)), ast.unparse(reason)
        assert not _names_in(reason) & ERROR_TEXT_NAMES
    assert (
        rerun_coordinator_workflow.RERUN_STOPPED
        == "The rerun stopped because of an error. Re-run it."
    )
