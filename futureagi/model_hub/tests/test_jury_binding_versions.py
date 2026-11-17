"""The public dataset binding routes retain immutable jury selections."""

import uuid
from unittest.mock import patch

import pytest
from rest_framework.exceptions import ValidationError

from model_hub.models.choices import StatusType
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import (
    CompositeEvalChild,
    EvalTemplateVersion,
    UserEvalMetric,
)
from model_hub.services.composite_jury import validate_definition
from model_hub.services.jury_versions import resolve_jury_runtime
from model_hub.tasks.composite_runner import CompositeEvaluationRunner
from model_hub.tests import test_jury_dataset as dataset_fixtures
from model_hub.tests.test_composite_jury import (
    config_url,
    definition,
    report_url,
    save,
)

pytest_plugins = ["model_hub.tests.test_composite_jury"]
bound = dataset_fixtures.bound
JUDGE = "model_hub.views.utils.evals.run_eval_func"


def composite_v1(child):
    """The V1 snapshot composite creation saved before juries were versioned."""
    return {
        "aggregation_enabled": True,
        "aggregation_function": "avg",
        "composite_child_axis": "",
        "children": [
            {
                "child_id": str(child.id),
                "child_name": child.name,
                "order": 0,
                "weight": 1.0,
                "config": {},
                "pinned_version_id": None,
            }
        ],
    }


def save_live_jury(parent, child):
    """The jury save before juries were versioned: live configuration only."""
    links = CompositeEvalChild.objects.filter(
        parent=parent, deleted=False
    ).select_related("child", "pinned_version")
    jury = validate_definition(
        definition(child), list(links), parent.organization, parent.workspace
    )
    parent.config = {**(parent.config or {}), "jury": jury}
    parent.save(update_fields=["config"])


def body(parent, mapping, version):
    return {
        "name": "saved-jury",
        "template_id": str(parent.id),
        "run": False,
        "config": {"mapping": mapping, "config": {"jury_version_id": version}},
    }


def run_from_dataset_route(client, dataset, metric):
    """Run a binding the way the dataset Run action does.

    The public route queues the binding; the scheduled dispatcher picks it up
    and hands it to its task. Both activities run inline here, since their
    Temporal wrappers start workflows and close the test's connection.
    """
    from model_hub.tasks import user_evaluation

    response = client.post(
        f"/model-hub/develops/{dataset.id}/start_evals_process/",
        {"user_eval_ids": [str(metric.id)]},
        format="json",
    )
    assert response.status_code == 200, response.content
    task = user_evaluation.process_evaluation_single_task

    def dispatch_inline(args, **_options):
        return task._original_func(*args)

    with (
        patch.object(user_evaluation, "close_old_connections"),
        patch.object(task, "apply_async", side_effect=dispatch_inline) as dispatched,
    ):
        user_evaluation.execute_evaluation._original_func()
    dispatched.assert_called_once_with(
        args=({"type": "single", "eval_id": str(metric.id)},)
    )


@pytest.mark.django_db
def test_create_reopen_edit_and_clear_jury_version(bound):
    parent, child, client, dataset, original, mapping = bound
    first = EvalTemplateVersion.objects.get_default(parent)
    save(client, parent, definition(child))
    count = EvalTemplateVersion.objects.filter(eval_template=parent).count()
    response = client.post(
        f"/model-hub/develops/{dataset.id}/add_user_eval/",
        body(parent, mapping, str(first.id)),
        format="json",
    )
    assert response.status_code == 200, response.content
    metric = UserEvalMetric.objects.get(dataset=dataset, name="saved-jury")
    assert metric.config["config"]["jury_version_id"] == str(first.id)
    edit = f"/model-hub/develops/{dataset.id}/edit_and_run_user_eval/{metric.id}/"
    response = client.post(edit, body(parent, mapping, str(first.id)), format="json")
    assert response.status_code == 200, response.content
    metric.refresh_from_db()
    assert metric.pinned_version_id == first.id
    response = client.post(edit, body(parent, mapping, None), format="json")
    assert response.status_code == 200, response.content
    metric.refresh_from_db()
    assert metric.pinned_version_id is None
    assert "jury_version_id" not in metric.config["config"]
    assert EvalTemplateVersion.objects.filter(eval_template=parent).count() == count


@pytest.mark.django_db
@pytest.mark.parametrize("version", ["not-a-uuid", str(uuid.uuid4())])
def test_bad_version_does_not_create_binding(bound, version):
    parent, child, client, dataset, original, mapping = bound
    response = client.post(
        f"/model-hub/develops/{dataset.id}/add_user_eval/",
        body(parent, mapping, version),
        format="json",
    )
    assert response.status_code == 400, response.content
    assert not UserEvalMetric.objects.filter(
        dataset=dataset, name="saved-jury"
    ).exists()


@pytest.mark.django_db
def test_saved_definition_query_and_latest(bound):
    parent, child, client, dataset, original, mapping = bound
    first = EvalTemplateVersion.objects.get_default(parent)
    save(client, parent, definition(child))
    saved = client.get(config_url(parent), {"version_id": str(first.id)})
    assert saved.status_code == 200
    assert len(saved.json()["result"]["seats"]) == 2
    latest = client.get(config_url(parent), {"version_id": "latest"})
    assert latest.status_code == 200
    assert len(latest.json()["result"]["seats"]) == 1


@pytest.mark.django_db
@pytest.mark.parametrize("config", [[], ["invalid"], {"config": []}, {"mapping": []}])
def test_invalid_binding_shapes_return_validation_errors(bound, config):
    parent, child, client, dataset, original, mapping = bound
    data = body(parent, mapping, None)
    data["config"] = config
    response = client.post(
        f"/model-hub/develops/{dataset.id}/add_user_eval/", data, format="json"
    )
    assert response.status_code == 400, response.content
    assert not UserEvalMetric.objects.filter(
        dataset=dataset, name="saved-jury"
    ).exists()


@pytest.mark.django_db
def test_legacy_jury_authoring_has_an_explicit_refusal(bound):
    parent, child, client, dataset, original, mapping = bound
    parent.workspace = None
    parent.save(update_fields=["workspace"])
    response = client.get(config_url(parent))
    assert response.status_code == 400, response.content
    assert "legacy unscoped composites are not supported" in str(response.json())


@pytest.mark.django_db
def test_soft_deleted_jury_pin_is_still_refused(bound):
    parent, child, client, dataset, metric, mapping = bound
    version = EvalTemplateVersion.objects.get_default(parent)
    metric.pinned_version = version
    metric.save(update_fields=["pinned_version"])
    version.deleted = True
    version.save(update_fields=["deleted"])
    # A jury pin stays strict; only an ordinary composite takes the default.
    with pytest.raises(ValidationError, match="jury version is not available"):
        CompositeEvaluationRunner(metric.id)


@pytest.mark.django_db
@pytest.mark.parametrize("saved_by", ["composite-create", "binding-edit"])
def test_base_era_jury_binding_still_runs_its_live_jury(jury, user, saved_by):
    parent, child, client = jury
    save_live_jury(parent, child)
    # composite-create: V1 holds children and aggregation but no jury key.
    # binding-edit: the version a binding edit pinned holds the live
    # configuration (with the jury) but no children.
    snapshot = (
        composite_v1(child)
        if saved_by == "composite-create"
        else {**parent.config, "model": "turing_large"}
    )
    version = EvalTemplateVersion.objects.create(
        eval_template=parent,
        version_number=1,
        is_default=True,
        config_snapshot=snapshot,
        pass_threshold=parent.pass_threshold,
        organization=parent.organization,
        workspace=parent.workspace,
    )
    dataset = Dataset.objects.create(
        name="base-era-jury-rows",
        organization=parent.organization,
        workspace=parent.workspace,
    )
    output = Column.objects.create(
        name="output", dataset=dataset, data_type="text", source="OTHERS"
    )
    row = Row.objects.create(dataset=dataset, order=0)
    Cell.objects.create(
        dataset=dataset, row=row, column=output, value="row-0", status="pass"
    )
    metric = UserEvalMetric.objects.create(
        name="base-era-jury",
        template=parent,
        dataset=dataset,
        user=user,
        organization=parent.organization,
        workspace=parent.workspace,
        config={"mapping": {"output": str(output.id)}},
        pinned_version=version if saved_by == "binding-edit" else None,
    )
    with patch(
        JUDGE, return_value={"output": 1, "reason": "seat", "output_type": "score"}
    ) as judge:
        runner = CompositeEvaluationRunner(metric.id)
        runner.run_prompt()
    # The unchanged binding keeps running its saved seat, model override and all.
    parent.refresh_from_db()
    assert runner.jury_version == version
    assert runner.template.config["jury"] == parent.config["jury"]
    assert [link.child_id for link in runner.children] == [child.id]
    assert [call.kwargs["model"] for call in judge.call_args_list] == [
        "fixture-model-a"
    ]
    saved = client.get(config_url(parent), {"version_id": str(version.id)})
    assert saved.status_code == 200, saved.content
    assert saved.json()["result"]["seats"][0]["model"] == "fixture-model-a"


@pytest.mark.django_db
def test_restoring_a_version_saved_before_juries_keeps_it_without_a_jury(jury):
    parent, child, client = jury
    legacy = EvalTemplateVersion.objects.create(
        eval_template=parent,
        version_number=1,
        is_default=True,
        config_snapshot=composite_v1(child),
        organization=parent.organization,
        workspace=parent.workspace,
    )
    save(client, parent, definition(child))
    assert EvalTemplateVersion.objects.get_default(parent).config_snapshot["jury"]
    response = client.post(
        f"/model-hub/eval-templates/{parent.id}/versions/{legacy.id}/restore/",
        {},
        format="json",
    )
    assert response.status_code == 200, response.content
    mirror = EvalTemplateVersion.objects.get(id=response.json()["result"]["id"])
    parent.refresh_from_db()
    # The mirror is the restored history, not the current jury.
    assert mirror.config_snapshot == legacy.config_snapshot
    assert "jury" not in parent.config
    assert EvalTemplateVersion.objects.get_default(parent) == mirror
    assert client.get(config_url(parent)).json()["result"] == {"seats": []}
    restored = client.get(config_url(parent), {"version_id": str(mirror.id)})
    assert restored.json()["result"] == {"seats": []}
    binding = UserEvalMetric(template=parent, config={})
    template, links, version = resolve_jury_runtime(
        binding, parent.organization, parent.workspace
    )
    assert (template, links, version) == (parent, None, None)
    assert not (template.config or {}).get("jury")


@pytest.mark.django_db
@pytest.mark.parametrize("pin", ["model", "binding"])
def test_deleted_jury_pin_stays_refused_after_an_ordinary_version_is_activated(
    bound, pin
):
    parent, child, client, dataset, metric, mapping = bound
    ordinary = EvalTemplateVersion.objects.create_version(
        eval_template=parent,
        config_snapshot={"jury": None},
        organization=parent.organization,
        workspace=parent.workspace,
    )
    save(client, parent, definition(child))
    pinned = EvalTemplateVersion.objects.get_default(parent)
    assert pinned.config_snapshot["jury"]
    if pin == "model":
        metric.pinned_version = pinned
    else:
        metric.config = {
            "mapping": mapping,
            "config": {"jury_version_id": str(pinned.id)},
        }
    metric.save(update_fields=["pinned_version", "config"])
    response = client.put(
        f"/model-hub/eval-templates/{parent.id}/versions/{ordinary.id}/set-default/",
        {},
        format="json",
    )
    assert response.status_code == 200, response.content
    parent.refresh_from_db()
    assert not parent.config.get("jury")
    assert EvalTemplateVersion.objects.get_default(parent) == ordinary
    pinned.deleted = True
    pinned.save(update_fields=["deleted"])
    # Neither the live configuration nor the default defines a jury now, but
    # the pinned selection did: it is refused, never run as an ordinary composite.
    with pytest.raises(ValidationError, match="jury version is not available"):
        CompositeEvaluationRunner(metric.id)


@pytest.mark.django_db
@pytest.mark.parametrize("pin", ["binding", "model"])
def test_metadata_edit_version_keeps_its_jury_after_a_later_jury_save(jury, pin):
    parent, child, client = jury
    legacy = EvalTemplateVersion.objects.create(
        eval_template=parent,
        version_number=1,
        is_default=True,
        config_snapshot=composite_v1(child),
        organization=parent.organization,
        workspace=parent.workspace,
    )
    jury_a = save(client, parent, definition(child))
    # A metadata-only edit rebuilds the structural snapshot legacy V1 holds.
    response = client.patch(
        f"/model-hub/eval-templates/{parent.id}/composite/",
        {"description": "metadata only"},
        format="json",
    )
    assert response.status_code == 200, response.content
    edited = EvalTemplateVersion.objects.get(
        eval_template=parent,
        version_number=response.json()["result"]["version_number"],
    )
    assert EvalTemplateVersion.objects.get_default(parent) == edited
    dataset = Dataset.objects.create(
        name="edited-version-rows",
        organization=parent.organization,
        workspace=parent.workspace,
    )
    output = Column.objects.create(
        name="output", dataset=dataset, data_type="text", source="OTHERS"
    )
    row = Row.objects.create(dataset=dataset, order=0)
    Cell.objects.create(
        dataset=dataset, row=row, column=output, value="row-0", status="pass"
    )
    mapping = {"output": str(output.id)}
    # binding: the binding's own jury selection. model: the generic pin the
    # dataset editor's version picker saves through the binding edit route.
    response = client.post(
        f"/model-hub/develops/{dataset.id}/add_user_eval/",
        body(parent, mapping, str(edited.id) if pin == "binding" else None),
        format="json",
    )
    assert response.status_code == 200, response.content
    metric = UserEvalMetric.objects.get(dataset=dataset, name="saved-jury")
    if pin == "model":
        response = client.post(
            f"/model-hub/develops/{dataset.id}/edit_and_run_user_eval/{metric.id}/",
            {
                **body(parent, mapping, str(edited.id)),
                "pinned_version_id": str(edited.id),
            },
            format="json",
        )
        assert response.status_code == 200, response.content
        metric.refresh_from_db()
        assert metric.pinned_version_id == edited.id
    assert metric.config["config"]["jury_version_id"] == str(edited.id)
    jury_b = save(client, parent, definition(child, model="fixture-model-b"))
    assert client.get(config_url(parent)).json()["result"] == jury_b
    # The pin keeps the jury its version was saved with, not the live jury B.
    pinned = client.get(config_url(parent), {"version_id": str(edited.id)})
    assert pinned.status_code == 200, pinned.content
    assert pinned.json()["result"] == jury_a
    with patch(
        JUDGE, return_value={"output": 1, "reason": "seat", "output_type": "score"}
    ) as judge:
        run_from_dataset_route(client, dataset, metric)
    # The dataset run executes the pinned version's seat with model A.
    assert [call.kwargs["model"] for call in judge.call_args_list] == [
        "fixture-model-a"
    ]
    report = client.get(report_url(parent), {"dataset_id": str(dataset.id)})
    assert report.status_code == 200, report.content
    result = report.json()["result"]
    assert result["version_id"] == str(edited.id)
    assert [seat["judge_model"] for seat in result["children"]] == ["fixture-model-a"]
    metric.refresh_from_db()
    assert metric.status == StatusType.COMPLETED.value
    assert metric.config["config"]["jury_version_id"] == str(edited.id)
    assert metric.pinned_version_id == (edited.id if pin == "model" else None)
    # The edit keeps its own snapshot and records jury A; V1 stays legacy.
    edited.refresh_from_db()
    assert edited.config_snapshot == {**composite_v1(child), "jury": jury_a}
    legacy.refresh_from_db()
    assert "jury" not in legacy.config_snapshot


@pytest.mark.django_db
@pytest.mark.parametrize("pinned", ["by-the-edit", "base-era"])
def test_repeating_an_ordinary_binding_edit_keeps_its_version(jury, pinned):
    parent, child, client = jury
    dataset = Dataset.objects.create(
        name="ordinary-binding-rows",
        organization=parent.organization,
        workspace=parent.workspace,
    )
    output = Column.objects.create(
        name="output", dataset=dataset, data_type="text", source="OTHERS"
    )
    mapping = {"output": str(output.id)}
    response = client.post(
        f"/model-hub/develops/{dataset.id}/add_user_eval/",
        {
            "name": "ordinary-binding",
            "template_id": str(parent.id),
            "run": False,
            "config": {"mapping": mapping},
        },
        format="json",
    )
    assert response.status_code == 200, response.content
    metric = UserEvalMetric.objects.get(dataset=dataset, name="ordinary-binding")
    edit = f"/model-hub/develops/{dataset.id}/edit_and_run_user_eval/{metric.id}/"
    overrides = {str(child.id): 2.0}
    change = {
        "config": {"mapping": mapping},
        "composite_weight_overrides": overrides,
        "run": False,
    }
    if pinned == "base-era":
        # A binding edit saved before juries stored the snapshot it built.
        version = EvalTemplateVersion.objects.create(
            eval_template=parent,
            version_number=1,
            is_default=True,
            config_snapshot={
                "model": metric.model or parent.model or "",
                "composite_weight_overrides": overrides,
            },
            organization=parent.organization,
            workspace=parent.workspace,
        )
        metric.pinned_version = version
        metric.save(update_fields=["pinned_version"])
    else:
        response = client.post(edit, change, format="json")
        assert response.status_code == 200, response.content
        metric.refresh_from_db()
        version = metric.pinned_version
        # The version records the composite's empty jury and its children.
        assert version.config_snapshot["jury"] is None
        children = version.config_snapshot["children"]
        assert [link["child_id"] for link in children] == [str(child.id)]
    versions = EvalTemplateVersion.objects.filter(eval_template=parent)
    count = versions.count()
    for _ in range(2):
        response = client.post(edit, change, format="json")
        assert response.status_code == 200, response.content
        metric.refresh_from_db()
        # The identical edit creates, pins and promotes no other version.
        assert metric.pinned_version_id == version.id
        assert versions.count() == count
        assert EvalTemplateVersion.objects.get_default(parent) == version
