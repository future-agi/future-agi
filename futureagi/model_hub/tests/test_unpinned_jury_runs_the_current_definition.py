"""An unpinned jury runs its current definition.

The jury's saved version (the default) holds other seats, children and pass
threshold than the composite's current definition, which was changed after
it with no version saved. An unpinned binding runs the current definition:
its live seats, children and pass threshold. The default version it resolves
only stamps the run. A binding pinned to that version, by the metric's pin or
by the binding's own jury selection, runs the version. A deleted pin on an
ordinary composite runs its current children.

Runs go through the real CompositeEvaluationRunner and jury execution; only
each seat's judge call is replaced, so the test reads which children run.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import (
    CompositeEvalChild,
    EvalTemplate,
    EvalTemplateVersion,
    UserEvalMetric,
)
from model_hub.models.evaluation import Evaluation
from model_hub.services.composite_jury import validate_definition
from model_hub.tasks.composite_runner import CompositeEvaluationRunner
from model_hub.tests.test_composite_jury import definition, save

pytest_plugins = ["model_hub.tests.test_composite_jury"]
pytestmark = pytest.mark.django_db

JUDGE = "model_hub.views.utils.evals.run_eval_func"
# Every seat scores this; it passes the saved threshold and not the current one.
SEAT_SCORE = 0.7
SAVED_THRESHOLD = 0.5
CURRENT_THRESHOLD = 0.9


def second_child(parent):
    return EvalTemplate.no_workspace_objects.create(
        name="jury-second-child",
        organization=parent.organization,
        workspace=parent.workspace,
        owner=parent.owner,
        config={"output": "score"},
        output_type_normalized="percentage",
        pass_threshold=0.5,
    )


def link(parent, child, order):
    return CompositeEvalChild.objects.create(
        parent=parent, child=child, order=order, weight=1
    )


def seats(*children):
    """One row-level seat per child, in order."""
    body = {"seats": []}
    for letter, child in zip("abcdefgh", children, strict=False):
        seat = definition(child)["seats"][0]
        seat["id"] = f"judge-{letter}"
        body["seats"].append(seat)
    return body


def change_in_place(parent, children):
    """The current definition changes with no version saved: its seats, the
    children they read and its pass threshold."""
    links = CompositeEvalChild.objects.filter(
        parent=parent, deleted=False
    ).select_related("child", "pinned_version")
    jury = validate_definition(
        seats(*children), list(links), parent.organization, parent.workspace
    )
    parent.config = {**(parent.config or {}), "jury": jury}
    parent.pass_threshold = CURRENT_THRESHOLD
    parent.save(update_fields=["config", "pass_threshold"])


def one_row(parent):
    dataset = Dataset.objects.create(
        name="current-jury-rows",
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
    return dataset, {"output": str(output.id)}


@pytest.fixture(params=["child-added", "child-removed"])
def moved(request, jury, user):
    """V1 is the default and runs one set of children at the saved threshold;
    the current definition was changed after it, with no version saved."""
    parent, first, client = jury
    second = second_child(parent)
    if request.param == "child-removed":
        link(parent, second, 1)
        saved, current = [first, second], [first]
    else:
        saved, current = [first], [first, second]
    save(client, parent, seats(*saved))
    version = EvalTemplateVersion.objects.get_default(parent)
    if request.param == "child-removed":
        CompositeEvalChild.objects.filter(parent=parent, child=second).update(
            deleted=True
        )
    else:
        link(parent, second, 1)
    parent.refresh_from_db()
    change_in_place(parent, current)

    # The saved version still names the older seats, children and threshold.
    parent.refresh_from_db()
    assert EvalTemplateVersion.objects.get_default(parent) == version
    snapshot = version.config_snapshot
    assert [seat["child_id"] for seat in snapshot["jury"]["seats"]] == [
        str(child.id) for child in saved
    ]
    assert [entry["child_id"] for entry in snapshot["children"]] == [
        str(child.id) for child in saved
    ]
    assert version.pass_threshold == SAVED_THRESHOLD
    assert [seat["child_id"] for seat in parent.config["jury"]["seats"]] == [
        str(child.id) for child in current
    ]
    assert parent.pass_threshold == CURRENT_THRESHOLD

    dataset, mapping = one_row(parent)
    return SimpleNamespace(
        parent=parent,
        user=user,
        version=version,
        saved=saved,
        current=current,
        dataset=dataset,
        mapping=mapping,
    )


def binding(world, pin=None):
    config = {"mapping": world.mapping}
    if pin == "binding":
        config["config"] = {"jury_version_id": str(world.version.id)}
    return UserEvalMetric.objects.create(
        name=f"current-jury-{pin or 'unpinned'}",
        template=world.parent,
        dataset=world.dataset,
        user=world.user,
        organization=world.parent.organization,
        workspace=world.parent.workspace,
        config=config,
        pinned_version=world.version if pin == "model" else None,
    )


def run(metric):
    """Run the binding; return the runner, the children judged in order and
    the row's stored jury verdict."""
    with patch(
        JUDGE,
        return_value={"output": SEAT_SCORE, "reason": "seat", "output_type": "score"},
    ) as judge:
        runner = CompositeEvaluationRunner(metric.id)
        runner.run_prompt()
    judged = [call.args[2].id for call in judge.call_args_list]
    assert runner.jury_run is not None
    (verdict,) = Evaluation.objects.filter(
        organization=metric.organization,
        eval_config__jury_run_id=str(runner.jury_run.id),
    )
    return runner, judged, verdict


def seat_children(verdict):
    return [seat["child_id"] for seat in verdict.data["jury"]["seats"]]


def test_an_unpinned_jury_runs_its_current_definition(moved):
    runner, judged, verdict = run(binding(moved))

    # The current seats and children run, not the saved version's.
    assert judged == [child.id for child in moved.current]
    assert seat_children(verdict) == [str(child.id) for child in moved.current]
    assert [entry.child_id for entry in runner.children] == [
        child.id for child in moved.current
    ]
    # The current pass threshold decides the verdict.
    assert runner.template.pass_threshold == CURRENT_THRESHOLD
    assert verdict.data["aggregate_score"] == pytest.approx(SEAT_SCORE)
    assert verdict.data["aggregate_pass"] is False
    # The default version only stamps the run.
    assert runner.jury_version == moved.version
    assert runner.jury_run.eval_config["version_id"] == str(moved.version.id)


@pytest.mark.parametrize("pin", ["model", "binding"])
def test_a_pinned_jury_runs_its_version(moved, pin):
    runner, judged, verdict = run(binding(moved, pin))

    assert judged == [child.id for child in moved.saved]
    assert seat_children(verdict) == [str(child.id) for child in moved.saved]
    assert runner.template.pass_threshold == SAVED_THRESHOLD
    assert verdict.data["aggregate_score"] == pytest.approx(SEAT_SCORE)
    assert verdict.data["aggregate_pass"] is True
    assert runner.jury_version == moved.version
    assert runner.jury_run.eval_config["version_id"] == str(moved.version.id)


def test_a_deleted_pin_on_an_ordinary_composite_runs_its_current_children(jury, user):
    parent, first, _ = jury
    saved = EvalTemplateVersion.objects.create_version(
        eval_template=parent,
        organization=parent.organization,
        workspace=parent.workspace,
    )
    assert saved.config_snapshot["jury"] is None
    pinned = EvalTemplateVersion.objects.create(
        eval_template=parent,
        version_number=saved.version_number + 1,
        is_default=False,
        config_snapshot=dict(saved.config_snapshot),
        pass_threshold=parent.pass_threshold,
        organization=parent.organization,
        workspace=parent.workspace,
    )
    second = second_child(parent)
    link(parent, second, 1)
    EvalTemplateVersion.all_objects.filter(pk=pinned.pk).update(deleted=True)
    dataset, mapping = one_row(parent)
    metric = UserEvalMetric.objects.create(
        name="ordinary-deleted-pin",
        template=parent,
        dataset=dataset,
        user=user,
        organization=parent.organization,
        workspace=parent.workspace,
        config={"mapping": mapping},
        pinned_version=pinned,
    )

    with patch(
        JUDGE,
        return_value={"output": SEAT_SCORE, "reason": "child", "output_type": "score"},
    ) as judge:
        runner = CompositeEvaluationRunner(metric.id)
        runner.run_prompt()

    assert [call.args[2].id for call in judge.call_args_list] == [
        first.id,
        second.id,
    ]
    assert not (runner.template.config or {}).get("jury")
    assert runner.jury_version is None
