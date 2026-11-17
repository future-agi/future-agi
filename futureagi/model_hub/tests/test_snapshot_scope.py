"""Same workspace scope: a row with no workspace belongs to its organization's
default workspace, and to nothing else.

The helper is imported inside each test so this module still collects on a
build that does not have it yet. Prepared from source, not run by the author.
"""

from uuid import uuid4

import pytest

from accounts.models.workspace import Workspace
from model_hub.tests import test_experiment_v2_results_tenancy as tenancy
from tfc.middleware.workspace_context import clear_workspace_context

pytestmark = pytest.mark.django_db


def same_workspace_scope(*args):
    from model_hub.utils.snapshot_scope import same_workspace_scope as helper

    return helper(*args)


@pytest.fixture
def orgs(db):
    a = tenancy._member_client("Scope Org A", "scope-a")
    b = tenancy._member_client("Scope Org B", "scope-b")
    a["sibling"] = Workspace.no_workspace_objects.create(
        name="Scope Org A sibling",
        organization=a["org"],
        is_default=False,
        is_active=True,
        created_by=a["user"],
    )
    clear_workspace_context()
    yield {"a": a, "b": b}
    for side in (a, b):
        side["client"].stop_workspace_injection()
    clear_workspace_context()


def test_equal_workspaces_match_without_a_query(orgs, django_assert_num_queries):
    a = orgs["a"]
    with django_assert_num_queries(0):
        assert same_workspace_scope(a["workspace"].pk, a["workspace"].pk, a["org"].pk)
        assert same_workspace_scope(a["sibling"].pk, a["sibling"].pk, a["org"].pk)
        assert same_workspace_scope(None, None, a["org"].pk)


@pytest.mark.parametrize("no_workspace_first", [True, False])
def test_no_workspace_matches_the_organizations_default_workspace(
    orgs, no_workspace_first
):
    a = orgs["a"]
    assert a["workspace"].is_default
    pair = (None, a["workspace"].pk)
    if not no_workspace_first:
        pair = pair[::-1]
    assert same_workspace_scope(*pair, a["org"].pk)


@pytest.mark.parametrize("no_workspace_first", [True, False])
@pytest.mark.parametrize(
    "case",
    [
        "non_default_workspace",
        "other_organizations_default",
        "default_judged_for_other_organization",
        "unknown_workspace",
        "no_organization",
    ],
)
def test_no_workspace_matches_nothing_else(orgs, case, no_workspace_first):
    a, b = orgs["a"], orgs["b"]
    workspace_id, organization_id = {
        "non_default_workspace": (a["sibling"].pk, a["org"].pk),
        "other_organizations_default": (b["workspace"].pk, a["org"].pk),
        "default_judged_for_other_organization": (a["workspace"].pk, b["org"].pk),
        "unknown_workspace": (uuid4(), a["org"].pk),
        "no_organization": (a["workspace"].pk, None),
    }[case]
    pair = (None, workspace_id) if no_workspace_first else (workspace_id, None)
    assert not same_workspace_scope(*pair, organization_id)


@pytest.mark.parametrize("change", ["inactive", "deleted", "no_longer_default"])
def test_default_workspace_must_still_be_the_live_default(orgs, change):
    a = orgs["a"]
    Workspace._base_manager.filter(pk=a["workspace"].pk).update(
        **{
            "inactive": {"is_active": False},
            "deleted": {"deleted": True},
            "no_longer_default": {"is_default": False},
        }[change]
    )
    assert not same_workspace_scope(None, a["workspace"].pk, a["org"].pk)
    assert not same_workspace_scope(a["workspace"].pk, None, a["org"].pk)


def test_two_different_workspaces_never_match_without_a_query(
    orgs, django_assert_num_queries
):
    a, b = orgs["a"], orgs["b"]
    with django_assert_num_queries(0):
        assert not same_workspace_scope(a["workspace"].pk, a["sibling"].pk, a["org"].pk)
        assert not same_workspace_scope(a["sibling"].pk, a["workspace"].pk, a["org"].pk)
        assert not same_workspace_scope(
            a["workspace"].pk, b["workspace"].pk, a["org"].pk
        )
