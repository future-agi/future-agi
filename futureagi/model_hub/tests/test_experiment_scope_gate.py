"""F05 admission stays OFF independently of basic experiments and F50 splits."""

# Imported fixtures are injected by pytest.
# ruff: noqa: F811

import json
from unittest.mock import patch

import pytest
from django.test import override_settings
from django.urls import include, path
from drf_yasg import openapi
from drf_yasg.codecs import OpenAPICodecJson
from drf_yasg.generators import OpenAPISchemaGenerator

from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.experiment_row_budget import ExperimentRowBudget
from model_hub.models.experiments import (
    ExperimentDatasetTable,
    ExperimentPromptConfig,
    ExperimentsTable,
)
from model_hub.serializers.experiments import (
    ExperimentCreateV2Serializer,
    ExperimentDetailV2Serializer,
)
from model_hub.services.experiment_row_budget import (
    bind_graph_budget,
    budget_for_prompt,
)
from model_hub.tests.test_experiment_run_scope import (
    CREATE_URL,
    request_body,
    scope_world,  # noqa: F401
    snapshot_orders,
)
from model_hub.tests.test_experiment_split_scope import make_split
from model_hub.tests.test_experiment_version_params import (
    create_run,
    setup_run,
    version_world,  # noqa: F401
)
from tfc.temporal.experiments.run_policy import failed_row_values

pytestmark = pytest.mark.django_db(transaction=True)
FIELDS = {"row_ids", "sample", "metadata", "error_policy", "limits"}


@pytest.fixture
def off(settings):
    settings.EXPERIMENT_SCOPE_ENABLED = False


def counts():
    return tuple(
        model.no_workspace_objects.count()
        for model in (
            ExperimentsTable,
            ExperimentDatasetTable,
            ExperimentPromptConfig,
            Dataset,
            Column,
            Row,
            Cell,
        )
    ) + (ExperimentRowBudget.objects.count(),)


def detail(client, experiment):
    response = client.get(f"{CREATE_URL}{experiment.id}/")
    assert response.status_code == 200, response.content
    return response.json()["result"]


def schema():
    from model_hub import urls

    names = {"experiments-v2-create", "experiments-v2-detail"}
    patterns = [p for p in urls.urlpatterns if getattr(p, "name", None) in names]
    assert {p.name for p in patterns} == names
    generator = OpenAPISchemaGenerator(
        openapi.Info(title="Scope boundary", default_version="v1"),
        patterns=[path("model-hub/", include(patterns))],
    )
    return json.loads(
        OpenAPICodecJson(validators=[]).encode(
            generator.get_schema(request=None, public=True)
        )
    )


def test_setting_defaults_off(settings):
    # The qualification job must leave EXPERIMENT_SCOPE_ENABLED unset.
    assert getattr(settings, "EXPERIMENT_SCOPE_ENABLED", None) is False


@pytest.mark.parametrize(
    "extra",
    [
        {"row_ids": ["11111111-1111-4111-8111-111111111111"]},
        {"row_ids": []},
        {"sample": {"size": 1, "seed": 17}},
        {"sample": {}},
        {"metadata": {"purpose": "not qualified"}},
        {"metadata": None},
        {"error_policy": "zero"},
        {"error_policy": "exclude"},
        {"limits": {"tokens": 5}},
        {"limits": {}},
        {"metadata": {"purpose": "mixed"}, "error_policy": "zero", "limits": {}},
    ],
)
def test_off_refuses_scope_before_any_write(auth_client, scope_world, off, extra):
    before = counts()
    with (
        patch("tfc.temporal.experiments.start_experiment_v2_workflow") as start,
        patch("model_hub.views.experiments.create_dataset_snapshot") as snapshot,
        patch("model_hub.services.experiment_run_scope.prepare_run_scope") as resolve,
    ):
        response = auth_client.post(
            CREATE_URL, request_body(scope_world, **extra), format="json"
        )
    assert response.status_code == 400, response.content
    assert "Unknown field." in response.content.decode()
    for field in extra:
        assert field in response.content.decode()
    assert counts() == before
    start.assert_not_called()
    snapshot.assert_not_called()
    resolve.assert_not_called()


@pytest.mark.parametrize("extra", [{}, {"metadata": {}}])
def test_off_basic_create_keeps_legacy_success_and_empty_policy(
    auth_client, scope_world, off, extra
):
    # Reviewed F01/F02 returns 200, not the planning packet's assumed 201.
    # create_run asserts that exact response contract and accepted dispatch.
    record = create_run(auth_client, request_body(scope_world, **extra))
    experiment = record.experiment_dataset.experiment
    experiment.refresh_from_db()
    assert (experiment.run_scope, experiment.run_policy, experiment.run_metadata) == (
        {},
        {},
        {},
    )
    assert snapshot_orders(experiment) == list(range(8))
    assert len(setup_run(record)["row_ids"]) == 8
    before_budgets = ExperimentRowBudget.objects.count()
    # Empty policy must stop before row/config lookups or reservation writes.
    assert budget_for_prompt(experiment, None, None) is None
    assert bind_graph_budget(experiment.id, None, None, None) is None
    assert ExperimentRowBudget.objects.count() == before_budgets
    assert (
        failed_row_values(experiment.run_policy.get("error_policy"), "failure") is None
    )
    result = detail(auth_client, experiment)
    assert not FIELDS.intersection(result)
    assert "run_scope" not in result


def test_off_preserves_f50_split_snapshot_and_readback(auth_client, scope_world, off):
    split = make_split(scope_world[0], [1, 3, 6])
    ids = [str(split.id)]
    record = create_run(auth_client, request_body(scope_world, split_ids=ids))
    experiment = record.experiment_dataset.experiment
    assert snapshot_orders(experiment) == [1, 3, 6]
    assert len(setup_run(record)["row_ids"]) == 3
    assert experiment.run_policy == {}
    result = detail(auth_client, experiment)
    assert not FIELDS.intersection(result)
    assert result["run_scope"]["split_ids"] == ids
    assert result["run_scope"]["selected_count"] == 3
    assert not FIELDS.intersection(result["run_scope"])


def test_off_hides_preexisting_scope_without_mutating_stored_policy(
    auth_client, scope_world, off
):
    with override_settings(EXPERIMENT_SCOPE_ENABLED=True):
        record = create_run(
            auth_client,
            request_body(
                scope_world,
                sample={"size": 2, "seed": 17},
                metadata={"purpose": "qualified"},
                error_policy="exclude",
                limits={"tokens": 5},
            ),
        )
    experiment = record.experiment_dataset.experiment
    original = (experiment.run_scope, experiment.run_policy, experiment.run_metadata)
    result = detail(auth_client, experiment)
    assert not FIELDS.intersection(result)
    assert "run_scope" not in result
    experiment.refresh_from_db()
    assert (
        experiment.run_scope,
        experiment.run_policy,
        experiment.run_metadata,
    ) == original


@pytest.mark.parametrize("enabled", [False, True])
def test_schema_tracks_setting_without_hiding_split_ids(settings, enabled):
    settings.EXPERIMENT_SCOPE_ENABLED = enabled
    request_fields = set(ExperimentCreateV2Serializer().fields)
    detail_fields = set(ExperimentDetailV2Serializer().fields)
    assert "split_ids" in request_fields
    assert FIELDS.intersection(request_fields) == (FIELDS if enabled else set())
    assert FIELDS.intersection(detail_fields) == (FIELDS if enabled else set())
    definitions = schema()["definitions"]
    for name in ("ExperimentCreateV2", "ExperimentDetailV2"):
        assert FIELDS.intersection(definitions[name]["properties"]) == (
            FIELDS if enabled else set()
        )
    assert "split_ids" in definitions["ExperimentCreateV2"]["properties"]
    if not enabled:
        assert "ExperimentSample" not in definitions
        assert "ExperimentLimits" not in definitions
        assert not FIELDS.intersection(
            definitions["ExperimentSplitRunScope"]["properties"]
        )


def test_on_scope_round_trips_independently(auth_client, scope_world, settings):
    settings.EXPERIMENT_SCOPE_ENABLED = True
    requested = {
        "sample": {"size": 2, "seed": 17},
        "metadata": {"purpose": "ON"},
        "error_policy": "exclude",
        "limits": {"tokens": 5},
    }
    record = create_run(auth_client, request_body(scope_world, **requested))
    experiment = record.experiment_dataset.experiment
    result = detail(auth_client, experiment)
    assert len(snapshot_orders(experiment)) == 2
    for key, value in requested.items():
        assert result[key] == value
