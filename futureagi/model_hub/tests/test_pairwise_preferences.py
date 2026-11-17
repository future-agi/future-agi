"""Human preferences use the results matrix's stable example identity."""

import uuid

import pytest
from django.apps import apps
from django.db import IntegrityError, transaction

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.models.dataset_versions import DatasetVersion, DatasetVersionItem
from model_hub.models.develop_dataset import Dataset, Row
from model_hub.models.experiments import ExperimentRun, ExperimentsTable
from tfc.constants.levels import Level

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("urlconf_loaded_without_workspace_context"),
]


@pytest.fixture
def pair(organization, workspace, user):
    dataset = Dataset.no_workspace_objects.create(
        name="Review examples",
        organization=organization,
        workspace=workspace,
        user=user,
        column_order=[],
        column_config={},
    )
    experiment = ExperimentsTable.no_workspace_objects.create(
        name="Review outputs",
        dataset=dataset,
        user=user,
    )
    versions = [
        DatasetVersion.no_workspace_objects.create(dataset=dataset) for _ in range(2)
    ]
    examples = [uuid.uuid4() for _ in range(4)]
    for version in versions:
        for example_id in examples:
            DatasetVersionItem.no_workspace_objects.create(
                dataset_version=version,
                example_id=example_id,
            )
    runs = [
        ExperimentRun.no_workspace_objects.create(
            experiment=experiment,
            dataset_version=version,
        )
        for version in versions
    ]
    return experiment, runs, examples


def _url(pair):
    return f"/model-hub/experiments/v2/{pair[0].pk}/pairwise/"


def _data(pair, **changes):
    return {
        "run_a": str(pair[1][0].pk),
        "run_b": str(pair[1][1].pk),
        "row_id": str(pair[2][0]),
        "winner": "a",
        **changes,
    }


def _post(client, pair, **changes):
    return client.post(_url(pair), _data(pair, **changes), format="json")


def _summary(client, pair, **changes):
    return client.get(
        _url(pair) + "summary/",
        {"run_a": str(pair[1][0].pk), "run_b": str(pair[1][1].pk), **changes},
    )


def _preferences():
    return apps.get_model("model_hub", "PairwisePreference").objects


def test_resubmitting_updates_one_preference(auth_client, pair, user):
    response = _post(auth_client, pair, note="A is clearer")
    assert response.status_code == 204
    preference = _preferences().get()
    identity, created_at = preference.pk, preference.created_at
    assert preference.experiment_id == pair[0].pk
    assert (preference.run_a_id, preference.run_b_id) == tuple(
        run.pk for run in pair[1]
    )
    assert preference.row_id == pair[2][0]
    assert preference.created_by_id == user.pk
    assert (preference.winner, preference.source, preference.note) == (
        "a",
        "human",
        "A is clearer",
    )
    assert _post(auth_client, pair, winner="b", note="B is complete").status_code == 204
    preference.refresh_from_db()
    assert _preferences().count() == 1
    assert (preference.pk, preference.created_at) == (identity, created_at)
    assert (preference.winner, preference.note) == ("b", "B is complete")
    assert _post(auth_client, pair, winner="tie").status_code == 204
    preference.refresh_from_db()
    assert (preference.winner, preference.note) == ("tie", "")


def test_summary_counts_preferences_and_distinct_reviewed_rows(auth_client, pair, user):
    empty = _summary(auth_client, pair)
    assert empty.status_code == 200
    assert empty.json() == {
        "a_wins": 0,
        "b_wins": 0,
        "ties": 0,
        "rows_reviewed": 0,
        "rows_total": 4,
    }
    for row_id, winner in zip(pair[2], ["a", "b", "tie"], strict=False):
        assert (
            _post(
                auth_client,
                pair,
                row_id=str(row_id),
                winner=winner,
            ).status_code
            == 204
        )
    other = User.objects.create_user(
        email=f"reviewer-{uuid.uuid4()}@example.test",
        organization=user.organization,
    )
    OrganizationMembership.no_workspace_objects.update_or_create(
        user=other,
        organization=user.organization,
        defaults={"level": Level.MEMBER, "role": "Member", "is_active": True},
    )
    WorkspaceMembership.no_workspace_objects.update_or_create(
        user=other,
        workspace=pair[0].dataset.workspace,
        defaults={"level": Level.WORKSPACE_MEMBER, "role": "workspace_member"},
    )
    auth_client.force_authenticate(user=other)
    assert _post(auth_client, pair, winner="b").status_code == 204
    auth_client.force_authenticate(user=user)
    assert _post(auth_client, pair, winner="tie").status_code == 204
    summary = _summary(auth_client, pair)
    assert summary.status_code == 200
    assert summary.json() == {
        "a_wins": 0,
        "b_wins": 2,
        "ties": 2,
        "rows_reviewed": 3,
        "rows_total": 4,
    }
    assert _preferences().count() == 4
    reverse = _summary(
        auth_client,
        pair,
        run_a=str(pair[1][1].pk),
        run_b=str(pair[1][0].pk),
    )
    assert reverse.status_code == 200
    assert reverse.json() == {
        "a_wins": 0,
        "b_wins": 0,
        "ties": 0,
        "rows_reviewed": 0,
        "rows_total": 4,
    }


@pytest.mark.parametrize("method", ["post", "get"])
def test_same_run_is_refused(auth_client, pair, method):
    call = _post if method == "post" else _summary
    response = call(auth_client, pair, run_b=str(pair[1][0].pk))
    assert response.status_code == 400
    assert response.json()["detail"] == "run_a and run_b must differ."
    assert _preferences().count() == 0


@pytest.mark.parametrize("field", ["run_a", "run_b"])
@pytest.mark.parametrize("method", ["post", "get"])
def test_foreign_run_is_not_found(auth_client, pair, field, method):
    foreign = ExperimentRun.no_workspace_objects.create(
        dataset_version=pair[1][0].dataset_version,
        experiment=ExperimentsTable.no_workspace_objects.create(
            name="Another experiment",
            dataset=pair[0].dataset,
        ),
    )
    call = _post if method == "post" else _summary
    response = call(auth_client, pair, **{field: str(foreign.pk)})
    assert response.status_code == 404
    assert _preferences().count() == 0


@pytest.mark.parametrize("version_index", [0, 1])
def test_row_must_be_a_live_member_of_both_versions(auth_client, pair, version_index):
    DatasetVersionItem.no_workspace_objects.filter(
        dataset_version=pair[1][version_index].dataset_version,
        example_id=pair[2][0],
    ).update(deleted=True)
    response = _post(auth_client, pair)
    assert response.status_code == 400
    assert response.json()["detail"] == (
        "row_id must belong to both runs' dataset versions."
    )
    assert _preferences().count() == 0
    assert _summary(auth_client, pair).json() == {
        "a_wins": 0,
        "b_wins": 0,
        "ties": 0,
        "rows_reviewed": 0,
        "rows_total": 3,
    }


def test_live_dataset_row_is_not_a_version_example(auth_client, pair):
    row = Row.no_workspace_objects.create(dataset=pair[0].dataset, order=0)
    response = _post(auth_client, pair, row_id=str(row.pk))
    assert response.status_code == 400
    assert response.json()["detail"] == (
        "row_id must belong to both runs' dataset versions."
    )
    assert _preferences().count() == 0


@pytest.mark.parametrize("hidden_by", ["organization", "workspace", "deleted"])
def test_out_of_scope_experiment_is_not_found(auth_client, pair, user, hidden_by):
    dataset = Dataset.no_workspace_objects.filter(pk=pair[0].dataset_id)
    if hidden_by == "organization":
        dataset.update(organization=Organization.objects.create(name="Other tenant"))
    elif hidden_by == "workspace":
        other = Workspace.no_workspace_objects.create(
            name="Other workspace",
            organization=user.organization,
            created_by=user,
            is_default=False,
        )
        dataset.update(workspace=other)
    else:
        dataset.update(deleted=True)
    assert _post(auth_client, pair).status_code == 404
    assert _summary(auth_client, pair).status_code == 404
    assert _preferences().count() == 0


def test_viewer_can_read_but_cannot_write(
    auth_client,
    pair,
    organization,
    workspace,
    user,
):
    assert _post(auth_client, pair).status_code == 204
    OrganizationMembership.no_workspace_objects.filter(
        user=user,
        organization=organization,
    ).update(level=Level.VIEWER, role="Viewer")
    WorkspaceMembership.no_workspace_objects.filter(
        user=user,
        workspace=workspace,
    ).update(level=Level.WORKSPACE_VIEWER, role="workspace_viewer")
    response = _post(auth_client, pair, winner="b")
    assert response.status_code == 403
    assert response.json()["detail"] == "Workspace write access required"
    assert _preferences().get().winner == "a"
    summary = _summary(auth_client, pair)
    assert summary.status_code == 200
    assert summary.json() == {
        "a_wins": 1,
        "b_wins": 0,
        "ties": 0,
        "rows_reviewed": 1,
        "rows_total": 4,
    }


@pytest.mark.parametrize("hidden_by", ["run", "version"])
def test_deleted_runs_and_versions_are_not_found(auth_client, pair, hidden_by):
    model = ExperimentRun if hidden_by == "run" else DatasetVersion
    instance = pair[1][1] if hidden_by == "run" else pair[1][1].dataset_version
    model.no_workspace_objects.filter(pk=instance.pk).update(deleted=True)
    assert _post(auth_client, pair).status_code == 404
    assert _summary(auth_client, pair).status_code == 404
    assert _preferences().count() == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"winner": "A"},
        {"winner": "unknown"},
        {"source": "judge"},
        {"created_by": str(uuid.uuid4())},
        {"row_id": "invalid"},
        {"note": "x" * 4001},
    ],
)
def test_invalid_or_server_owned_fields_are_refused(auth_client, pair, changes):
    assert _post(auth_client, pair, **changes).status_code == 400
    assert _preferences().count() == 0


def test_database_enforces_one_preference_per_reviewer(auth_client, pair, user):
    assert _post(auth_client, pair).status_code == 204
    with pytest.raises(IntegrityError), transaction.atomic():
        _preferences().create(
            experiment=pair[0],
            run_a=pair[1][0],
            run_b=pair[1][1],
            row_id=pair[2][0],
            created_by=user,
            winner="b",
            source="human",
        )
    assert _preferences().count() == 1


def test_distinct_runs_on_one_version_can_be_reviewed(auth_client, pair):
    ExperimentRun.no_workspace_objects.filter(pk=pair[1][1].pk).update(
        dataset_version=pair[1][0].dataset_version,
    )
    assert _post(auth_client, pair).status_code == 204
    assert _summary(auth_client, pair).json() == {
        "a_wins": 1,
        "b_wins": 0,
        "ties": 0,
        "rows_reviewed": 1,
        "rows_total": 4,
    }


def test_routes_expose_only_their_assigned_methods(auth_client, pair):
    assert auth_client.get(_url(pair)).status_code == 405
    response = auth_client.post(
        _url(pair) + "summary/",
        _data(pair),
        format="json",
    )
    assert response.status_code == 405


def test_summary_query_requires_two_valid_run_identifiers(auth_client, pair):
    assert auth_client.get(_url(pair) + "summary/").status_code == 400
    assert _summary(auth_client, pair, run_a="invalid").status_code == 400
