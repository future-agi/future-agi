"""
Created By filter and sort on the eval list (W10-EVALS-L06, W10-EVALS-U07).

The list shows one creator per eval: "System" for system evals, then the
evaluator's user, then the author of the first version, then the
organization name. Filtering and sorting by Created By must use that same
creator, so every returned row shows a creator the user picked.
"""

import pytest

from accounts.models import User
from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate, EvalTemplateVersion, Evaluator

URL = "/model-hub/eval-templates/list/"

AUTHOR_A = "Ada Author"
AUTHOR_B = "Ben Builder"


def _template(organization, workspace, name):
    return EvalTemplate.no_workspace_objects.create(
        name=name,
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={"output": "score"},
        eval_tags=["llm"],
        visible_ui=True,
    )


def _version(template, number, *, is_default, created_by, organization, workspace):
    return EvalTemplateVersion.objects.create(
        eval_template=template,
        version_number=number,
        is_default=is_default,
        organization=organization,
        workspace=workspace,
        created_by=created_by,
    )


@pytest.fixture
def authored_evals(organization, workspace, user):
    author_a = User.objects.create_user(
        email="w10-l06-ada@example.com", password="test-password", name=AUTHOR_A
    )
    author_b = User.objects.create_user(
        email="w10-l06-ben@example.com", password="test-password", name=AUTHOR_B
    )

    # Shown as A through the evaluator's user; it has no versions.
    by_evaluator = _template(organization, workspace, "w10_l06_evaluator_author")
    Evaluator.objects.create(
        eval_template=by_evaluator, user=author_a, name="w10_l06_evaluator_author"
    )

    # Shown as A, the first version's author, while B authored the default.
    by_first_version = _template(organization, workspace, "w10_l06_first_version")
    _version(
        by_first_version,
        1,
        is_default=False,
        created_by=author_a,
        organization=organization,
        workspace=workspace,
    )
    _version(
        by_first_version,
        2,
        is_default=True,
        created_by=author_b,
        organization=organization,
        workspace=workspace,
    )

    # Shown as B.
    by_b = _template(organization, workspace, "w10_l06_single_version")
    _version(
        by_b,
        1,
        is_default=True,
        created_by=author_b,
        organization=organization,
        workspace=workspace,
    )

    # Shown as the organization: no evaluator user and no version author.
    by_org = _template(organization, workspace, "w10_l06_org_fallback")

    return {
        "by_evaluator": by_evaluator.name,
        "by_first_version": by_first_version.name,
        "by_b": by_b.name,
        "by_org": by_org.name,
        "org_name": organization.display_name,
    }


def _list(auth_client, **body):
    payload = {"owner_filter": "user", "page_size": 100, **body}
    response = auth_client.post(URL, payload, format="json")
    assert response.status_code == 200, response.content
    return response.data["result"]["items"]


def _shown(items):
    return {item["name"]: item["created_by_name"] for item in items}


@pytest.mark.e2e
@pytest.mark.django_db
class TestEvalListCreatedByFilter:
    def test_list_shows_the_expected_creators(self, auth_client, authored_evals):
        shown = _shown(_list(auth_client))
        assert shown[authored_evals["by_evaluator"]] == AUTHOR_A
        assert shown[authored_evals["by_first_version"]] == AUTHOR_A
        assert shown[authored_evals["by_b"]] == AUTHOR_B
        assert shown[authored_evals["by_org"]] == authored_evals["org_name"]

    def test_filter_returns_every_eval_shown_with_the_author(
        self, auth_client, authored_evals
    ):
        shown = _shown(_list(auth_client, filters={"created_by": [AUTHOR_A]}))
        assert authored_evals["by_evaluator"] in shown
        assert authored_evals["by_first_version"] in shown
        assert set(shown.values()) == {AUTHOR_A}

    def test_filter_skips_evals_shown_with_another_author(
        self, auth_client, authored_evals
    ):
        shown = _shown(_list(auth_client, filters={"created_by": [AUTHOR_B]}))
        assert authored_evals["by_b"] in shown
        assert authored_evals["by_first_version"] not in shown
        assert set(shown.values()) == {AUTHOR_B}

    def test_filter_by_organization_returns_only_evals_shown_with_it(
        self, auth_client, authored_evals
    ):
        org_name = authored_evals["org_name"]
        shown = _shown(_list(auth_client, filters={"created_by": [org_name]}))
        assert authored_evals["by_org"] in shown
        assert set(shown.values()) == {org_name}

    def test_exclude_author_removes_every_eval_shown_with_them(
        self, auth_client, authored_evals
    ):
        shown = _shown(_list(auth_client, filters={"created_by_not": [AUTHOR_A]}))
        assert AUTHOR_A not in shown.values()
        assert authored_evals["by_b"] in shown
        assert authored_evals["by_org"] in shown

    def test_exclude_organization_keeps_evals_shown_with_a_person(
        self, auth_client, authored_evals
    ):
        org_name = authored_evals["org_name"]
        shown = _shown(_list(auth_client, filters={"created_by_not": [org_name]}))
        assert org_name not in shown.values()
        assert authored_evals["by_evaluator"] in shown
        assert authored_evals["by_first_version"] in shown
        assert authored_evals["by_b"] in shown


@pytest.mark.e2e
@pytest.mark.django_db
class TestEvalListCreatedBySort:
    @pytest.mark.parametrize("sort_order", ["asc", "desc"])
    def test_sort_orders_by_the_creator_shown(
        self, auth_client, authored_evals, sort_order
    ):
        items = _list(auth_client, sort_by="created_by_name", sort_order=sort_order)
        creators = [item["created_by_name"] for item in items]
        assert creators == sorted(creators, reverse=sort_order == "desc")
        assert {AUTHOR_A, AUTHOR_B, authored_evals["org_name"]} <= set(creators)

    def test_sort_breaks_ties_in_a_stable_order(self, auth_client, authored_evals):
        first = [
            item["id"]
            for item in _list(auth_client, sort_by="created_by_name", sort_order="asc")
        ]
        pages = [
            item["id"]
            for page in range(len(first))
            for item in _list(
                auth_client,
                sort_by="created_by_name",
                sort_order="asc",
                page=page,
                page_size=1,
            )
        ]
        assert pages == first
