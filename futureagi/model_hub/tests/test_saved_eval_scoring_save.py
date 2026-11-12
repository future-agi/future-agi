"""Save from the Scoring editor on a dataset's saved evaluations.

The editor sends the evaluation as get_evals_list returns it (its stored
config, the version it runs and its error-localizer flag) with the new pass
threshold in its own field (SavedEvalsList.jsx scoringSavePayload). After
that Save the evaluation keeps everything it stored: its run settings, its
mapping and notes, the version it runs (no new version), its reason-column
setting and columns, its model. Only the threshold changes, and the list
reports the new one as the threshold runs apply.

The fixture is a score evaluation saved the way production saves one: the
dataset evaluation editor puts its controls, pass threshold included, in
config.run_config (bindingSavePayload.buildBindingSavePayload, sent by
EvaluationDrawer on add), and add_user_eval stores that config with the
reason column on and the selected version pinned, and creates the eval and
reason columns on add-and-run (AddUserEvalView).
"""

from __future__ import annotations

import uuid
from copy import deepcopy

import pytest

from model_hub.models.choices import (
    DatasetSourceChoices,
    DataTypeChoices,
    OwnerChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Column, Dataset
from model_hub.models.evals_metric import (
    EvalTemplate,
    EvalTemplateVersion,
    UserEvalMetric,
)
from model_hub.tests.network_isolation import no_external_lookups  # noqa: F401

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("no_external_lookups")]

PROMPT = "Judge {{output}}"


def _stored_config(column_id, *, pinned, reason_column):
    config = {
        "mapping": {"output": str(column_id)},
        # The template definition as the editor sends it, with the
        # template's own threshold.
        "config": {
            "rule_prompt": PROMPT,
            "required_keys": ["output"],
            "output": "score",
            "model": "gpt-4o-mini",
            "pass_threshold": 0.5,
            **({"version_pin": "v1"} if pinned else {}),
        },
        # The editor's run settings: the threshold the user saved is here.
        "run_config": {
            "model": "gpt-4o-mini",
            "agent_mode": "agent",
            "check_internet": False,
            "summary": {"type": "concise"},
            "pass_threshold": 0.7,
            "multi_choice": False,
            "error_localizer_enabled": False,
            "data_injection": {"variables_only": True},
        },
        "description": "Flags answers that miss the question",
    }
    if reason_column:
        config["reason_column"] = True
    return config


@pytest.fixture
def make_saved_eval(user, organization, workspace):
    def make(*, pinned=True, reason_column=True):
        template = EvalTemplate.no_workspace_objects.create(
            name=f"scoring-save-{uuid.uuid4().hex[:6]}",
            organization=organization,
            workspace=workspace,
            owner=OwnerChoices.USER.value,
            eval_type="llm",
            config={
                "rule_prompt": PROMPT,
                "required_keys": ["output"],
                "output": "score",
                "model": "gpt-4o-mini",
                "pass_threshold": 0.5,
            },
            criteria=PROMPT,
            model="gpt-4o-mini",
            pass_threshold=0.5,
            output_type_normalized="percentage",
        )
        v1, v2 = (
            EvalTemplateVersion.objects.create_version(
                eval_template=template,
                config_snapshot={**template.config, "rule_prompt": rubric},
                criteria=rubric,
                model=template.model,
                user=user,
                organization=organization,
                workspace=workspace,
            )
            for rubric in (PROMPT, "Judge {{output}} strictly")
        )
        dataset = Dataset.no_workspace_objects.create(
            name=f"scoring-save-{uuid.uuid4().hex[:6]}",
            organization=organization,
            workspace=workspace,
            source=DatasetSourceChoices.BUILD.value,
        )
        source = Column.no_workspace_objects.create(
            name="answer",
            dataset=dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=SourceChoices.OTHERS.value,
        )
        metric = UserEvalMetric.objects.create(
            name="relevance",
            organization=organization,
            workspace=workspace,
            dataset=dataset,
            template=template,
            pinned_version=v1 if pinned else None,
            config=_stored_config(
                source.id, pinned=pinned, reason_column=reason_column
            ),
            status=StatusType.COMPLETED.value,
            error_localizer=False,
            user=user,
            model="gpt-4o-mini",
        )
        result = Column.no_workspace_objects.create(
            name=metric.name,
            dataset=dataset,
            data_type=DataTypeChoices.FLOAT.value,
            source=SourceChoices.EVALUATION.value,
            source_id=str(metric.id),
        )
        order = [str(source.id), str(result.id)]
        if reason_column:
            reason = Column.no_workspace_objects.create(
                name=f"{metric.name}-reason",
                dataset=dataset,
                data_type=DataTypeChoices.TEXT.value,
                source=SourceChoices.EVALUATION_REASON.value,
                source_id=f"{result.id}-sourceid-{metric.id}",
            )
            order.append(str(reason.id))
        dataset.column_order = order
        dataset.save()
        return metric, (v1, v2)

    return make


def _list_row(client, metric):
    response = client.get(
        f"/model-hub/develops/{metric.dataset_id}/get_evals_list/",
        {"eval_type": "user"},
    )
    assert response.status_code == 200, response.content
    return next(
        row for row in response.json()["result"]["evals"] if row["id"] == str(metric.id)
    )


def _editor_request(row, threshold):
    """What the editor's Save sends for a list row (scoringSavePayload)."""
    config = deepcopy(row["config"])
    # A missing reason-column setting is sent as off, which it already is.
    config.setdefault("reason_column", False)
    return {
        "config": config,
        "pinned_version_id": row["pinned_version_id"],
        "error_localizer": row["error_localizer"],
        "save_as_template": False,
        "run": False,
        "pass_threshold": threshold,
    }


def _save(client, metric, body):
    return client.post(
        f"/model-hub/develops/{metric.dataset_id}/edit_and_run_user_eval/{metric.id}/",
        body,
        format="json",
    )


def _state(metric):
    metric.refresh_from_db()
    metric.dataset.refresh_from_db()
    columns = Column.no_workspace_objects.filter(dataset=metric.dataset, deleted=False)
    return {
        "config": deepcopy(metric.config),
        "pinned_version_id": metric.pinned_version_id,
        "model": metric.model,
        "error_localizer": metric.error_localizer,
        "kb_id": metric.kb_id,
        "status": metric.status,
        "versions": EvalTemplateVersion.objects.filter(
            eval_template=metric.template
        ).count(),
        "reason_columns": columns.filter(
            source=SourceChoices.EVALUATION_REASON.value
        ).count(),
        "column_order": list(metric.dataset.column_order),
    }


def _with_threshold(config, threshold):
    expected = deepcopy(config)
    expected["run_config"]["pass_threshold"] = threshold
    # The definition's copy of the threshold says the same thing.
    expected["config"]["pass_threshold"] = threshold
    return expected


def test_save_keeps_a_pinned_evaluation_whole_and_stores_the_threshold(
    auth_client, make_saved_eval
):
    metric, (v1, _v2) = make_saved_eval(pinned=True)
    before = _state(metric)
    row = _list_row(auth_client, metric)
    # Production saved 0.7 and runs v1, though v2 is now the default.
    assert row["pass_threshold"] == 0.7
    assert row["pinned_version_id"] == str(v1.id)

    response = _save(auth_client, metric, _editor_request(row, 0.8))

    assert response.status_code == 200, response.content
    after = _state(metric)
    assert after["config"] == _with_threshold(before["config"], 0.8)
    assert after["pinned_version_id"] == v1.id
    assert {k: v for k, v in after.items() if k != "config"} == {
        k: v for k, v in before.items() if k != "config"
    }
    read_back = _list_row(auth_client, metric)
    assert read_back["pass_threshold"] == 0.8
    assert read_back["config"]["run_config"]["pass_threshold"] == 0.8
    assert read_back["pinned_version_id"] == str(v1.id)
    assert read_back["version"] == "v1"
    # No run reads another direction; the list says so unchanged.
    assert read_back["higher_is_better"] is True


def test_save_keeps_an_evaluation_on_the_default_version(auth_client, make_saved_eval):
    metric, _versions = make_saved_eval(pinned=False)
    before = _state(metric)
    row = _list_row(auth_client, metric)
    assert row["pinned_version_id"] is None
    assert row["version"] == "v2"

    response = _save(auth_client, metric, _editor_request(row, 0.8))

    assert response.status_code == 200, response.content
    after = _state(metric)
    assert after["config"] == _with_threshold(before["config"], 0.8)
    # Still unpinned, still following the default; no version was saved.
    assert after["pinned_version_id"] is None
    assert after["versions"] == before["versions"] == 2
    read_back = _list_row(auth_client, metric)
    assert read_back["pass_threshold"] == 0.8
    assert read_back["version"] == "v2"


def test_save_leaves_a_missing_reason_setting_off(auth_client, make_saved_eval):
    metric, _versions = make_saved_eval(pinned=True, reason_column=False)
    before = _state(metric)
    assert before["reason_columns"] == 0
    row = _list_row(auth_client, metric)

    response = _save(auth_client, metric, _editor_request(row, 0.8))

    assert response.status_code == 200, response.content
    after = _state(metric)
    # Every reader treats a missing setting as off; it is still off, and no
    # reason column was added to the dataset.
    assert after["config"].get("reason_column") is False
    assert after["reason_columns"] == 0
    assert after["column_order"] == before["column_order"]
    stored = {k: v for k, v in after["config"].items() if k != "reason_column"}
    assert stored == _with_threshold(before["config"], 0.8)
    assert _list_row(auth_client, metric)["pass_threshold"] == 0.8


@pytest.mark.parametrize("threshold", [1.5, -0.1, "nan"])
def test_an_out_of_range_threshold_is_refused_and_changes_nothing(
    auth_client, make_saved_eval, threshold
):
    metric, _versions = make_saved_eval(pinned=True)
    before = _state(metric)
    row = _list_row(auth_client, metric)

    response = _save(auth_client, metric, _editor_request(row, threshold))

    assert response.status_code == 400, response.content
    assert _state(metric) == before
    assert _list_row(auth_client, metric)["pass_threshold"] == 0.7
