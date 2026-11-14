"""Old refresh commands cannot change protected issue state."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from tracer.services.grouping.control import GroupingConflict, GroupingControlError
from tracer.services.grouping.publish import _refresh_prototypes


@pytest.fixture
def refresh():
    mechanism = {
        "mechanism": "Wrong refund amount",
        "fix_hypothesis": "Use requested amount",
        "falsifier": "Executed amount matches",
    }
    state = SimpleNamespace(
        revision=3,
        retired=False,
        protected=True,
        mechanism=mechanism,
        prototype_occurrence_ids=["b", "a"],
        cluster=SimpleNamespace(
            assignee_id=None,
            external_issue_url="",
            external_issue_id="",
            status="escalating",
        ),
    )
    command = {
        "expected_issue_revision": 3,
        "mechanism": deepcopy(mechanism),
        "prototype_occurrence_ids": ["a", "b"],
    }
    return state, command, ["a", "b", "c"]


@pytest.mark.parametrize("reordered", [False, True])
def test_protected_same_examples_preserve_state(refresh, reordered):
    state, command, members = refresh
    if not reordered:
        command["prototype_occurrence_ids"] = list(state.prototype_occurrence_ids)
    before = deepcopy(state)
    assert (
        _refresh_prototypes(state, command, members) == before.prototype_occurrence_ids
    )
    assert state == before


@pytest.mark.parametrize(
    "guard", ["missing", "retired", "revision", "mechanism", "membership", "selection"]
)
def test_noop_does_not_bypass_authority_checks(refresh, guard):
    state, command, members = refresh
    if guard == "missing":
        state = None
    elif guard == "retired":
        state.retired = True
    elif guard == "revision":
        command["expected_issue_revision"] -= 1
    elif guard == "mechanism":
        command["mechanism"]["mechanism"] = "Different failure"
    elif guard == "membership":
        members = ["a"]
    else:
        command["prototype_occurrence_ids"] = ["a", "c"]
    with pytest.raises(GroupingConflict):
        _refresh_prototypes(state, command, members)


@pytest.mark.parametrize("invalid", [["a", "a"], [], ["x"] * 6])
def test_noop_rejects_invalid_examples(refresh, invalid):
    state, command, members = refresh
    command["prototype_occurrence_ids"] = invalid
    with pytest.raises(GroupingControlError):
        _refresh_prototypes(state, command, members)


def test_unprotected_issue_can_change_examples(refresh):
    state, command, members = refresh
    state.protected = False
    command["prototype_occurrence_ids"] = ["c", "a"]
    assert _refresh_prototypes(state, command, members) == ["c", "a"]


def test_assignment_also_protects_issue(refresh):
    state, command, members = refresh
    state.protected = False
    state.cluster.assignee_id = "owner"
    assert (
        _refresh_prototypes(state, command, members) == state.prototype_occurrence_ids
    )
    command["prototype_occurrence_ids"] = ["c"]
    with pytest.raises(GroupingConflict):
        _refresh_prototypes(state, command, members)
