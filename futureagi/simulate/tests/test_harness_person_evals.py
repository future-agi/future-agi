"""An eval a person adds to a harness environment through the simulation
page's own add endpoint: listed, labelled, and gradeable from a run by name."""

from __future__ import annotations

import pytest

from simulate.models import SimulateEvalConfig
from simulate.services.harness_evals import MOST_SELECTED_EVALS, offerable_eval_names

from . import test_harness_environment_evals as _env_evals

# Re-bound rather than imported by name: pytest registers a fixture it finds as
# a module attribute either way, and an assignment is not an import the linter
# can call unused or shadowed.
ENVIRONMENTS = _env_evals.ENVIRONMENTS
_call = _env_evals._call
_graded = _env_evals._graded
_template = _env_evals._template
dispatch = _env_evals.dispatch
env_client = _env_evals.env_client
environment = _env_evals.environment
finished_run = _env_evals.finished_run

UNOFFERED = "legacy_person_only_check"


def _unoffered(name=UNOFFERED, required_keys=("input", "output")):
    assert name not in offerable_eval_names()
    return _template(name, list(required_keys), tags=())


def _old_add(client, environment, workspace, template, *, name, mapping):
    return client.post(
        f"/simulate/run-tests/{environment.run_test_id}/eval-configs/",
        {
            "evaluations_config": [
                {
                    "template_id": str(template.id),
                    "name": name,
                    "mapping": mapping,
                    "config": {},
                    "error_localizer": False,
                    "filters": [],
                }
            ]
        },
        format="json",
        HTTP_X_WORKSPACE_ID=str(workspace.id),
    )


def _detail_selected(client, environment, workspace):
    response = client.get(
        f"{ENVIRONMENTS}/{environment.id}/", HTTP_X_WORKSPACE_ID=str(workspace.id)
    )
    assert response.status_code == 200, response.content
    return response.json()["evaluations"]["selected"]


def _scenario_column(environment, name):
    from model_hub.models.develop_dataset import Column

    scenario = environment.run_test.scenarios.first()
    return Column.objects.get(dataset_id=scenario.dataset_id, name=name, deleted=False)


@pytest.mark.django_db
def test_a_person_added_eval_is_listed_with_readable_labels(
    env_client, environment, workspace
):
    template = _unoffered(required_keys=("input", "output", "context"))
    situation = _scenario_column(environment, "situation")
    added = _old_add(
        env_client,
        environment,
        workspace,
        template,
        name="my situation check",
        mapping={
            "input": str(situation.id),
            "output": "call.transcript",
            "context": "agent_prompt",
        },
    )
    assert added.status_code == 201, added.content
    (row,) = _detail_selected(env_client, environment, workspace)
    assert row["name"] == "my situation check"
    labels = {item["key"]: (item["source"], item["label"]) for item in row["inputs"]}
    assert labels == {
        "context": ("agent_prompt", "Agent instructions"),
        "input": (str(situation.id), "situation"),
        "output": ("call.transcript", "call.transcript"),
    }


@pytest.mark.django_db
def test_a_column_id_from_another_dataset_is_not_given_its_name(
    env_client, environment, workspace, user
):
    from model_hub.models.choices import SourceChoices, StatusType
    from model_hub.models.develop_dataset import Column, Dataset

    foreign = Column.objects.create(
        name="secret",
        data_type="text",
        source=SourceChoices.OTHERS.value,
        status=StatusType.COMPLETED.value,
        dataset=Dataset.objects.create(name="other", organization=user.organization),
    )
    template = _unoffered(required_keys=("output",))
    added = _old_add(
        env_client,
        environment,
        workspace,
        template,
        name="foreign check",
        mapping={"output": str(foreign.id)},
    )
    assert added.status_code == 201, added.content
    (item,) = _detail_selected(env_client, environment, workspace)[0]["inputs"]
    assert item["label"] == str(foreign.id)


@pytest.mark.django_db
def test_grade_this_run_by_name_with_a_person_added_eval(
    env_client,
    environment,
    finished_run,
    workspace,
    dispatch,
    django_capture_on_commit_callbacks,
):
    template = _unoffered()
    added = _old_add(
        env_client,
        environment,
        workspace,
        template,
        name="my check",
        mapping={"input": "persona.name", "output": "call.transcript"},
    )
    assert added.status_code == 201, added.content
    eligible = _call(finished_run, metadata=_graded())
    with django_capture_on_commit_callbacks(execute=True):
        response = env_client.post(
            f"{ENVIRONMENTS}/{environment.id}/runs/{finished_run.id}/evaluations/",
            {"name": "my check"},
            format="json",
            HTTP_X_WORKSPACE_ID=str(workspace.id),
        )
    assert response.status_code == 202, response.content
    assert response.json()["queued"] == 1
    assert dispatch.call_args.kwargs["args"] == (str(eligible.id),)
    assert (
        SimulateEvalConfig.objects.filter(
            run_test=environment.run_test, deleted=False
        ).count()
        == 1
    ), "grading by name binds nothing new"


@pytest.mark.django_db
def test_a_non_string_mapping_value_does_not_break_the_environment_detail(
    env_client, environment, workspace
):
    template = _unoffered(required_keys=("output",))
    added = _old_add(
        env_client,
        environment,
        workspace,
        template,
        name="non string mapping",
        mapping={"output": ["call.transcript"]},
    )
    assert added.status_code == 201, added.content
    (item,) = _detail_selected(env_client, environment, workspace)[0]["inputs"]
    assert item["source"] == '["call.transcript"]'
    assert item["label"] == '["call.transcript"]'


@pytest.mark.django_db
def test_env_add_at_the_cap_is_idempotent_for_a_bound_name(
    env_client, environment, workspace
):
    from unittest.mock import patch

    from simulate.services.harness_evals import add_selected_eval

    names = [f"cap_fill_{i}" for i in range(MOST_SELECTED_EVALS)]
    for i, name in enumerate(names):
        _template(name, ["conversation"], tags=("Conversation",), eval_id=i + 1)
    with patch(
        "simulate.services.harness_evals.offerable_eval_names",
        return_value=frozenset(names),
    ):
        first = [add_selected_eval(environment.run_test, n, "voice") for n in names]
        again = add_selected_eval(environment.run_test, names[0], "voice")
    assert again.id == first[0].id


@pytest.mark.django_db
def test_grade_by_the_template_name_of_a_renamed_person_eval(
    env_client,
    environment,
    finished_run,
    workspace,
    dispatch,
    django_capture_on_commit_callbacks,
):
    template = _unoffered()
    _old_add(
        env_client,
        environment,
        workspace,
        template,
        name="renamed",
        mapping={"input": "persona.name", "output": "call.transcript"},
    )
    _call(finished_run, metadata=_graded())
    with django_capture_on_commit_callbacks(execute=True):
        r = env_client.post(
            f"{ENVIRONMENTS}/{environment.id}/runs/{finished_run.id}/evaluations/",
            {"name": template.name},
            format="json",
            HTTP_X_WORKSPACE_ID=str(workspace.id),
        )
    assert r.status_code == 202, r.content
    assert r.json()["queued"] == 1


@pytest.mark.django_db
def test_grade_by_a_configs_own_name_beats_an_earlier_configs_template_name(
    env_client,
    environment,
    finished_run,
    workspace,
    dispatch,
    django_capture_on_commit_callbacks,
):
    """A later config's own name must win the bind: an earlier config bound
    from a template that merely happens to share that name must not shadow
    it."""
    from unittest.mock import patch

    import simulate.services.harness_run_evals as harness_run_evals

    shadow_name = UNOFFERED
    first_template = _unoffered(name=shadow_name, required_keys=("output",))
    second_template = _unoffered(name="person_custom_tpl", required_keys=("output",))
    _old_add(
        env_client,
        environment,
        workspace,
        first_template,
        name="renamed first",
        mapping={"output": "call.transcript"},
    )
    _old_add(
        env_client,
        environment,
        workspace,
        second_template,
        name=shadow_name,
        mapping={"output": "call.transcript"},
    )
    _call(finished_run, metadata=_graded())
    original = harness_run_evals.queue_eval_for_finished_calls
    with patch.object(
        harness_run_evals, "queue_eval_for_finished_calls", side_effect=original
    ) as spy:
        with django_capture_on_commit_callbacks(execute=True):
            response = env_client.post(
                f"{ENVIRONMENTS}/{environment.id}/runs/{finished_run.id}/evaluations/",
                {"name": shadow_name},
                format="json",
                HTTP_X_WORKSPACE_ID=str(workspace.id),
            )
    assert response.status_code == 202, response.content
    graded_config = spy.call_args.args[1]
    assert graded_config.name == shadow_name


def test_serializer_accepts_a_scenario_column_id_as_a_source():
    from simulate.serializers.harness_environment import (
        HarnessEnvironmentEvalInputSerializer,
    )

    serializer = HarnessEnvironmentEvalInputSerializer(
        data={
            "key": "input",
            "source": "6cdd4513-0000-4000-8000-000000000000",
            "label": "situation",
        }
    )
    assert serializer.is_valid()


@pytest.mark.django_db
def test_an_upper_case_column_id_still_gets_its_name(
    env_client, environment, workspace
):
    template = _unoffered(required_keys=("output",))
    situation = _scenario_column(environment, "situation")
    added = _old_add(
        env_client,
        environment,
        workspace,
        template,
        name="upper case id",
        mapping={"output": str(situation.id).upper()},
    )
    assert added.status_code == 201, added.content
    (item,) = _detail_selected(env_client, environment, workspace)[0]["inputs"]
    assert item["label"] == "situation"


@pytest.mark.django_db
def test_people_are_not_capped(env_client, environment, workspace):
    for index in range(MOST_SELECTED_EVALS + 2):
        template = _unoffered(name=f"person_cap_{index}", required_keys=("output",))
        response = _old_add(
            env_client,
            environment,
            workspace,
            template,
            name=template.name,
            mapping={"output": "call.transcript"},
        )
        assert response.status_code == 201, (index, response.content)
    assert len(_detail_selected(env_client, environment, workspace)) == (
        MOST_SELECTED_EVALS + 2
    )


@pytest.mark.django_db
def test_a_dict_mapping_value_does_not_break_the_environment_detail(
    env_client, environment, workspace
):
    template = _unoffered(required_keys=("output",))
    added = _old_add(
        env_client,
        environment,
        workspace,
        template,
        name="dict mapping",
        mapping={"output": {"path": "call"}},
    )
    assert added.status_code == 201, added.content
    (item,) = _detail_selected(env_client, environment, workspace)[0]["inputs"]
    assert item["source"] == '{"path": "call"}'


@pytest.mark.django_db
def test_every_spelling_of_one_column_id_gets_its_name(
    env_client, environment, workspace
):
    situation = _scenario_column(environment, "situation")
    lower = _unoffered(required_keys=("output",))
    upper = _unoffered(name="second_spelling", required_keys=("output",))
    _old_add(
        env_client,
        environment,
        workspace,
        lower,
        name="lower",
        mapping={"output": str(situation.id)},
    )
    _old_add(
        env_client,
        environment,
        workspace,
        upper,
        name="upper",
        mapping={"output": str(situation.id).upper()},
    )
    labels = {
        row["name"]: row["inputs"][0]["label"]
        for row in _detail_selected(env_client, environment, workspace)
    }
    assert labels == {"lower": "situation", "upper": "situation"}
