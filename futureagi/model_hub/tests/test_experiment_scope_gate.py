"""Run settings are on by default and only sent when set. Turned off, a create
that uses one is refused with a sentence a person can act on, while plain
creates and split selection keep working."""

# Imported fixtures are injected by pytest.
# ruff: noqa: F811

import json
import os
from unittest.mock import patch

import pytest
from django.test import override_settings
from django.urls import include, path
from drf_yasg import openapi
from drf_yasg.codecs import OpenAPICodecJson
from drf_yasg.generators import OpenAPISchemaGenerator

from model_hub.models.choices import CellStatus
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplateVersion
from model_hub.models.experiment_row_budget import ExperimentRowBudget
from model_hub.models.experiments import (
    ExperimentDatasetTable,
    ExperimentPromptConfig,
    ExperimentsTable,
)
from model_hub.models.native_experiment_runs import (
    NativeExampleBinding,
    NativeExperimentSnapshot,
    NativeRunBinding,
)
from model_hub.serializers.experiments import (
    ExperimentCreateV2Serializer,
    ExperimentDetailV2Serializer,
)
from model_hub.services.experiment_row_budget import (
    bind_graph_budget,
    budget_for_prompt,
)
from model_hub.services.experiment_scope_gate import (
    SCOPE_OFF_MESSAGE,
    UNAVAILABLE_ROW_LIMITS_MESSAGE,
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
from tfc.utils.tests.test_column_average_choice_quality import summarize  # noqa: F401

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


def test_setting_defaults_on(settings):
    # The test job leaves EXPERIMENT_SCOPE_ENABLED unset, so this is the
    # value every deployment gets when it does not set it.
    assert "EXPERIMENT_SCOPE_ENABLED" not in os.environ
    assert getattr(settings, "EXPERIMENT_SCOPE_ENABLED", None) is True


def test_default_create_accepts_run_settings(auth_client, scope_world):
    # No settings override here: the create view runs with the default.
    picked = [
        str(row_id)
        for row_id in Row.no_workspace_objects.filter(
            dataset=scope_world[0], order__in=[1, 2, 4, 5, 6]
        ).values_list("id", flat=True)
    ]
    requested = {
        "row_ids": picked,
        "sample": {"size": 2, "seed": 17},
        "metadata": {"purpose": "default on"},
        "error_policy": "exclude",
        "limits": {"timeout_s": 30},
    }
    record = create_run(auth_client, request_body(scope_world, **requested))
    experiment = record.experiment_dataset.experiment
    experiment.refresh_from_db()
    # The snapshot the run reads holds 2 of the 5 picked rows.
    orders = snapshot_orders(experiment)
    assert len(orders) == 2
    assert set(orders) <= {1, 2, 4, 5, 6}
    assert experiment.run_policy == {
        "error_policy": "exclude",
        "limits": {"timeout_s": 30},
    }
    assert experiment.run_metadata["purpose"] == "default on"
    result = detail(auth_client, experiment)
    assert set(result["row_ids"]) == set(picked)
    for key in ("sample", "metadata", "error_policy", "limits"):
        assert result[key] == requested[key]
    assert result["run_scope"]["selected_count"] == 2


def test_plain_wizard_create_stores_no_run_settings(
    auth_client, scope_world, summarize
):
    # The body the New experiment wizard sends when no run setting is changed:
    # no row_ids, sample, metadata, error_policy or limits.
    record = create_run(auth_client, request_body(scope_world))
    experiment = record.experiment_dataset.experiment
    experiment.refresh_from_db()
    assert experiment.run_scope == {}
    assert experiment.run_policy == {}
    assert snapshot_orders(experiment) == list(range(8))
    result = detail(auth_client, experiment)
    assert not {"row_ids", "sample", "error_policy", "limits"}.intersection(result)
    assert result.get("metadata", {}) == {}
    # Turning run settings on moves no score: with no stored policy a failed
    # row gets the error cell the workers write (eval_runner.py and the
    # failed-row activity: the policy's cells, else this), and the column
    # average leaves it out of the mean and of n.
    message = "Upstream native output failed; evaluator was not run"
    failed = failed_row_values(experiment.run_policy.get("error_policy"), message) or {
        "value": message,
        "status": CellStatus.ERROR.value,
        "value_infos": json.dumps({"reason": message}),
    }
    stats = summarize(
        [repr({"score": 1, "choice": "Good"}), failed["value"]],
        overrides={1: failed},
    )
    assert stats["average"] == 100
    assert stats["total_rows"] == stats["valid_rows"] == 1


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
    # A sentence a person can act on, naming no internals, and every field.
    body = response.json()
    assert body["message"] == SCOPE_OFF_MESSAGE
    assert "Unknown field." not in response.content.decode()
    for field in extra:
        assert field in body["details"]
    assert counts() == before
    start.assert_not_called()
    snapshot.assert_not_called()
    resolve.assert_not_called()


@pytest.mark.parametrize(
    "limits",
    [
        {"tokens": 500},
        {"messages": 2},
        {"cost_usd": 0.05},
        {"tokens": 500, "timeout_s": 30},
    ],
)
def test_token_message_and_cost_limits_are_refused(auth_client, scope_world, limits):
    # The same sentence the wizard shows, so the API and SDK tell the same truth.
    before = counts()
    with patch("tfc.temporal.experiments.start_experiment_v2_workflow") as start:
        response = auth_client.post(
            CREATE_URL, request_body(scope_world, limits=limits), format="json"
        )
    assert response.status_code == 400, response.content
    body = response.json()
    assert body["message"] == UNAVAILABLE_ROW_LIMITS_MESSAGE
    assert body["details"]["limits"] == [UNAVAILABLE_ROW_LIMITS_MESSAGE]
    assert counts() == before
    start.assert_not_called()


def test_time_limit_alone_is_accepted(auth_client, scope_world):
    record = create_run(
        auth_client, request_body(scope_world, limits={"timeout_s": 30})
    )
    experiment = record.experiment_dataset.experiment
    experiment.refresh_from_db()
    assert experiment.run_policy == {"limits": {"timeout_s": 30}}
    assert detail(auth_client, experiment)["limits"] == {"timeout_s": 30}


# Turned off, a plain create (what the wizard sends with no run setting set,
# and older bodies with metadata={}) still runs.
@pytest.mark.parametrize("extra", [{}, {"metadata": {}}])
def test_off_basic_create_keeps_legacy_success_and_empty_policy(
    auth_client, scope_world, off, extra
):
    # Reviewed F01/F02 returns 200, not the planning packet's assumed 201.
    # create_run asserts that exact response contract and accepted dispatch.
    record = create_run(auth_client, request_body(scope_world, **extra))
    experiment = record.experiment_dataset.experiment
    experiment.refresh_from_db()
    assert experiment.run_scope == {}
    assert experiment.run_policy == {}
    snapshot = NativeExperimentSnapshot.objects.get(experiment=experiment)
    links = list(
        NativeExampleBinding.objects.filter(snapshot=snapshot).select_related("version_item")
    )
    assert len(links) == 8
    identity = experiment.run_metadata["native_snapshot"]
    assert set(experiment.run_metadata) == {"native_snapshot"}
    assert set(identity) == {"dataset_version_id", "identities"}
    assert identity["dataset_version_id"] == str(snapshot.dataset_version_id)
    expected = [
        {
            "source_row_id": str(link.source_row_id),
            "snapshot_row_id": str(link.snapshot_row_id),
            "example_id": str(link.version_item.example_id),
            "version_item_id": str(link.version_item_id),
        }
        for link in links
    ]
    assert sorted(identity["identities"], key=lambda row: row["source_row_id"]) == sorted(
        expected, key=lambda row: row["source_row_id"]
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
                limits={"timeout_s": 30},
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
        "limits": {"timeout_s": 30},
    }
    record = create_run(auth_client, request_body(scope_world, **requested))
    experiment = record.experiment_dataset.experiment
    result = detail(auth_client, experiment)
    assert len(snapshot_orders(experiment)) == 2
    for key, value in requested.items():
        assert result[key] == value
    assert "native_snapshot" not in result["metadata"]
    experiment.refresh_from_db()
    assert experiment.run_metadata["purpose"] == "ON"
    snapshot = NativeExperimentSnapshot.objects.get(experiment=experiment)
    assert experiment.run_metadata["native_snapshot"]["dataset_version_id"] == str(
        snapshot.dataset_version_id
    )


@pytest.mark.parametrize("enabled", [False, True])
def test_create_pins_fixture_evaluator_version(
    auth_client, scope_world, settings, enabled
):
    settings.EXPERIMENT_SCOPE_ENABLED = enabled
    dataset, _prompt, _version, template = scope_world
    pin = EvalTemplateVersion.objects.get_default(template)
    assert pin is not None
    assert pin.is_default and not pin.deleted
    assert (pin.eval_template_id, pin.organization_id, pin.workspace_id) == (
        template.id,
        dataset.organization_id,
        dataset.workspace_id,
    )
    assert pin.config_snapshot == template.config
    assert pin.criteria == template.criteria
    assert pin.model == template.model

    record = create_run(auth_client, request_body(scope_world))
    metric = record.experiment_dataset.experiment.user_eval_template_ids.get()
    assert EvalTemplateVersion.objects.resolve_for_metric(metric).id == pin.id
    binding = NativeRunBinding.objects.select_related("run").get(
        variant=record.experiment_dataset
    )
    pins = binding.run.evaluator_versions
    assert len(pins) == 1
    assert pins[0]["metric_id"] == str(metric.id)
    assert pins[0]["template_id"] == str(template.id)
    assert pins[0]["evaluator_version_id"] == str(pin.id)
    assert pins[0]["version_number"] == pin.version_number


@pytest.mark.parametrize("enabled", [False, True])
def test_create_without_live_evaluator_version_fails_closed(
    auth_client, scope_world, settings, enabled
):
    settings.EXPERIMENT_SCOPE_ENABLED = enabled
    template = scope_world[3]
    assert EvalTemplateVersion.objects.filter(
        eval_template=template, deleted=False
    ).update(deleted=True, is_default=False) == 1
    assert EvalTemplateVersion.objects.get_default(template) is None
    before = counts()
    with patch("tfc.temporal.experiments.start_experiment_v2_workflow") as start:
        response = auth_client.post(
            CREATE_URL, request_body(scope_world), format="json"
        )
    assert response.status_code == 400, response.content
    assert response.json()["detail"] == (
        "native evaluator version is unavailable in this tenant"
    )
    assert counts() == before
    start.assert_not_called()
