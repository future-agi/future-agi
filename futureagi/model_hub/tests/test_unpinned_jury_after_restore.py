"""After Restore or Set default, a composite runs the version gone back to.

Everything goes through the product routes: the jury save, the composite edit,
Restore and Set default, the composite detail, the dataset binding and the
dataset Run. Only each seat's judge call is replaced, so the test reads which
children are judged.

Restore and Set default put back the version's jury, its child links and its
aggregation, so an unpinned binding (which runs the composite's current
definition) runs that version as it was saved. Each run case prints one line:

    JURY|<case>|<tree>|judges=<...>|children=<...>|rows=<...>

judges: the child judged for each call, in order. children: the composite
links the runner loaded. rows: each row's result (pass, fail, unscored or
error). <tree> names the code the test runs on: "before" when an unpinned
binding resolves through the default version, "after" when it runs the live
composite, "rebuilt" when Restore and Set default also put back the links and
aggregation.
"""

import inspect
import json
from copy import deepcopy
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
from model_hub.services import jury_versions
from model_hub.tasks.composite_runner import CompositeEvaluationRunner
from model_hub.tests.test_composite_jury import definition, save
from model_hub.tests.test_jury_binding_versions import body, run_from_dataset_route
from model_hub.views import separate_evals

pytest_plugins = ["model_hub.tests.test_composite_jury"]
pytestmark = pytest.mark.django_db

JUDGE = "model_hub.views.utils.evals.run_eval_func"
FIRST = "jury-child"
REPLACEMENT = "jury-replacement-child"
# One seat; its score decides each row against the composite's 0.5.
ROW_SCORES = {"row-0": 0.7, "row-1": 0.2}
UNAVAILABLE_CHILD = (
    "This version includes an evaluation that is no longer available, so it "
    "cannot be used. Choose another version."
)


def tree():
    if hasattr(separate_evals, "_apply_composite_version_structure"):
        return "rebuilt"
    source = inspect.getsource(jury_versions.resolve_jury_runtime)
    return "after" if "if not selected:" in source else "before"


def composite_url(parent):
    return f"/model-hub/eval-templates/{parent.id}/composite/"


def version_url(parent, version, action):
    return f"/model-hub/eval-templates/{parent.id}/versions/{version.id}/{action}/"


def edit(client, parent, changes):
    response = client.patch(composite_url(parent), changes, format="json")
    assert response.status_code == 200, response.content


def detail(client, parent):
    """The composite as its page shows it."""
    response = client.get(composite_url(parent))
    assert response.status_code == 200, response.content
    return response.json()["result"]


def ask_to_go_back(client, parent, version, route):
    """Restore the version, or Set default on it."""
    if route == "restore":
        return client.post(version_url(parent, version, "restore"), {}, format="json")
    return client.put(version_url(parent, version, "set-default"), {}, format="json")


def go_back(client, parent, version, route):
    response = ask_to_go_back(client, parent, version, route)
    assert response.status_code == 200, response.content


def linked(parent):
    links = CompositeEvalChild.objects.filter(parent=parent, deleted=False)
    return [link.child.name for link in links.order_by("order")]


def rows_of(parent):
    dataset = Dataset.objects.create(
        name="jury-restore-rows",
        organization=parent.organization,
        workspace=parent.workspace,
    )
    output = Column.objects.create(
        name="output", dataset=dataset, data_type="text", source="OTHERS"
    )
    for order, value in enumerate(ROW_SCORES):
        row = Row.objects.create(dataset=dataset, order=order)
        Cell.objects.create(
            dataset=dataset, row=row, column=output, value=value, status="pass"
        )
    return dataset, {"output": str(output.id)}


def judge(*args, **_kwargs):
    return {
        "output": ROW_SCORES[args[1]["output"]],
        "reason": "seat",
        "output_type": "score",
    }


def row_result(cell):
    if cell.status == "error":
        return "error"
    infos = cell.value_infos
    infos = json.loads(infos) if isinstance(infos, str) else infos or {}
    verdict = infos.get("aggregate_pass")
    if verdict is True:
        return "pass"
    if verdict is False:
        return "fail"
    return "unscored"


def run_unpinned(world):
    """Bind the composite with no version through the dataset route, Run it,
    and read what ran."""
    client, parent, dataset, mapping = (
        world.client,
        world.parent,
        world.dataset,
        world.mapping,
    )
    response = client.post(
        f"/model-hub/develops/{dataset.id}/add_user_eval/",
        body(parent, mapping, None),
        format="json",
    )
    assert response.status_code == 200, response.content
    metric = UserEvalMetric.objects.get(dataset=dataset, name="saved-jury")
    assert metric.pinned_version_id is None
    assert not (metric.config.get("config") or {}).get("jury_version_id")

    runners = []
    real_run = CompositeEvaluationRunner.run_prompt

    def run_and_keep(self, *args, **kwargs):
        runners.append(self)
        return real_run(self, *args, **kwargs)

    with (
        patch(JUDGE, side_effect=judge) as judged,
        patch.object(CompositeEvaluationRunner, "run_prompt", run_and_keep),
    ):
        run_from_dataset_route(client, dataset, metric)

    (runner,) = runners
    column = Column.objects.get(
        dataset=dataset, source_id=str(metric.id), deleted=False
    )
    cells = list(
        Cell.objects.filter(column=column, deleted=False).order_by("row__order")
    )
    return SimpleNamespace(
        judges=[call.args[2].name for call in judged.call_args_list],
        children=[link.child.name for link in runner.children],
        rows=[row_result(cell) for cell in cells],
        cells=cells,
        column=column,
    )


def report(case, seen):
    line = (
        f"JURY|{case}|{tree()}|judges={'+'.join(seen.judges) or 'none'}"
        f"|children={'+'.join(seen.children) or 'none'}"
        f"|rows={','.join(seen.rows) or 'none'}"
    )
    print(line)
    return line


@pytest.fixture
def world(jury):
    parent, first, client = jury
    replacement = EvalTemplate.no_workspace_objects.create(
        name=REPLACEMENT,
        organization=parent.organization,
        workspace=parent.workspace,
        owner=first.owner,
        config={"output": "score"},
        output_type_normalized="percentage",
        pass_threshold=0.5,
    )
    dataset, mapping = rows_of(parent)
    assert first.name == FIRST
    return SimpleNamespace(
        parent=parent,
        first=first,
        replacement=replacement,
        client=client,
        dataset=dataset,
        mapping=mapping,
    )


def saved_on_first(world):
    """The jury saved with one seat on the first child: its version."""
    save(world.client, world.parent, definition(world.first))
    version = EvalTemplateVersion.objects.get_default(world.parent)
    snapshot = version.config_snapshot
    assert [seat["child_id"] for seat in snapshot["jury"]["seats"]] == [
        str(world.first.id)
    ]
    assert [entry["child_id"] for entry in snapshot["children"]] == [
        str(world.first.id)
    ]
    assert snapshot["aggregation_enabled"] is True
    return version


def replaced_judge(world):
    """The first child is replaced by another and the jury is saved on it."""
    edit(
        world.client,
        world.parent,
        {"child_template_ids": [str(world.replacement.id)]},
    )
    save(world.client, world.parent, definition(world.replacement))
    latest = EvalTemplateVersion.objects.get_default(world.parent)
    assert [seat["child_id"] for seat in latest.config_snapshot["jury"]["seats"]] == [
        str(world.replacement.id)
    ]
    assert linked(world.parent) == [REPLACEMENT]


@pytest.mark.parametrize("route", ["restore", "set-default"])
def test_after_going_back_over_a_replaced_judge(world, route):
    first_version = saved_on_first(world)
    replaced_judge(world)
    go_back(world.client, world.parent, first_version, route)

    world.parent.refresh_from_db()
    # The version's jury, and the child it judges, are back on the composite.
    assert [seat["child_id"] for seat in world.parent.config["jury"]["seats"]] == [
        str(world.first.id)
    ]
    assert linked(world.parent) == [FIRST]
    shown = detail(world.client, world.parent)
    assert [child["child_name"] for child in shown["children"]] == [FIRST]

    seen = run_unpinned(world)
    report(f"replaced-judge-{route}", seen)

    # The version runs as it was saved: its seat over its own child.
    assert seen.judges == [FIRST, FIRST]
    assert seen.children == [FIRST]
    assert seen.rows == ["pass", "fail"]


@pytest.mark.parametrize("route", ["restore", "set-default"])
def test_after_going_back_over_an_aggregation_change(world, route):
    first_version = saved_on_first(world)
    edit(world.client, world.parent, {"aggregation_enabled": False})
    world.parent.refresh_from_db()
    assert world.parent.aggregation_enabled is False
    latest = EvalTemplateVersion.objects.get_default(world.parent)
    assert latest.config_snapshot["aggregation_enabled"] is False
    go_back(world.client, world.parent, first_version, route)

    world.parent.refresh_from_db()
    # The version's aggregation is the composite's own again, not only an
    # entry in its configuration.
    assert world.parent.config["aggregation_enabled"] is True
    assert world.parent.aggregation_enabled is True
    assert detail(world.client, world.parent)["aggregation_enabled"] is True

    seen = run_unpinned(world)
    report(f"aggregation-{route}", seen)

    # Aggregation is on, as the version saved it: each row gets a verdict.
    assert seen.judges == [FIRST, FIRST]
    assert seen.children == [FIRST]
    assert seen.rows == ["pass", "fail"]
    assert seen.column.data_type == "float"


def test_a_replaced_judge_with_no_going_back(world):
    saved_on_first(world)
    replaced_judge(world)

    seen = run_unpinned(world)
    report("replaced-judge-control", seen)

    # Never restored: the composite runs its current definition, the
    # replacement.
    assert seen.judges == [REPLACEMENT, REPLACEMENT]
    assert seen.children == [REPLACEMENT]
    assert seen.rows == ["pass", "fail"]


@pytest.mark.parametrize("route", ["restore", "set-default"])
def test_going_back_to_a_version_whose_judge_is_gone_changes_nothing(world, route):
    first_version = saved_on_first(world)
    replaced_judge(world)
    world.first.deleted = True
    world.first.save(update_fields=["deleted"])
    world.parent.refresh_from_db()
    jury_before = deepcopy(world.parent.config["jury"])
    default_before = EvalTemplateVersion.objects.get_default(world.parent)
    versions_before = EvalTemplateVersion.objects.filter(
        eval_template=world.parent
    ).count()

    response = ask_to_go_back(world.client, world.parent, first_version, route)

    # The person is told why, in plain words; nothing is half applied.
    assert response.status_code == 400, response.content
    assert response.json()["message"] == UNAVAILABLE_CHILD
    world.parent.refresh_from_db()
    assert world.parent.config["jury"] == jury_before
    assert linked(world.parent) == [REPLACEMENT]
    assert EvalTemplateVersion.objects.get_default(world.parent) == default_before
    assert (
        EvalTemplateVersion.objects.filter(eval_template=world.parent).count()
        == versions_before
    )


@pytest.mark.parametrize("route", ["restore", "set-default"])
def test_an_ordinary_composite_goes_back_to_its_saved_children(world, route):
    # A composite with no jury: its first saved version links the first child.
    edit(world.client, world.parent, {"description": "first children"})
    first_version = EvalTemplateVersion.objects.get_default(world.parent)
    saved = first_version.config_snapshot["children"]
    assert [entry["child_id"] for entry in saved] == [str(world.first.id)]
    edit(
        world.client,
        world.parent,
        {
            "child_template_ids": [str(world.replacement.id)],
            "aggregation_enabled": False,
            "aggregation_function": "min",
        },
    )
    assert linked(world.parent) == [REPLACEMENT]

    go_back(world.client, world.parent, first_version, route)

    world.parent.refresh_from_db()
    assert not (world.parent.config or {}).get("jury")
    assert linked(world.parent) == [FIRST]
    assert world.parent.aggregation_enabled is True
    assert world.parent.aggregation_function == "avg"
    shown = detail(world.client, world.parent)
    assert [child["child_name"] for child in shown["children"]] == [FIRST]
    assert shown["aggregation_enabled"] is True
    assert shown["aggregation_function"] == "avg"


# The composite's child axis: Restore and Set default check it the way the
# composite edit does.
NOT_A_SCORE = (
    f"Child '{FIRST}' is not a Score eval. This composite only accepts Score evals."
)
UNAVAILABLE_CHILD_VERSION = (
    "This version uses a saved version of one of its evaluations that is no "
    "longer available, so it cannot be used. Choose another version."
)


def on_score_axis(world):
    """The composite locked to Score children: its version."""
    edit(world.client, world.parent, {"composite_child_axis": "percentage"})
    version = EvalTemplateVersion.objects.get_default(world.parent)
    assert version.config_snapshot["composite_child_axis"] == "percentage"
    return version


def first_becomes_pass_fail(world):
    """The first child is edited into a Pass/Fail evaluation."""
    world.first.output_type_normalized = "pass_fail"
    world.first.config = {"output": "Pass/Fail"}
    world.first.save(update_fields=["output_type_normalized", "config"])


def refused_and_unchanged(world, version, route, message):
    """Going back to the version is refused with the message, and nothing
    about the composite or its versions changes."""
    world.parent.refresh_from_db()
    axis_before = world.parent.composite_child_axis
    links_before = linked(world.parent)
    default_before = EvalTemplateVersion.objects.get_default(world.parent)
    versions_before = EvalTemplateVersion.objects.filter(
        eval_template=world.parent
    ).count()

    response = ask_to_go_back(world.client, world.parent, version, route)

    assert response.status_code == 400, response.content
    assert response.json()["message"] == message
    world.parent.refresh_from_db()
    assert world.parent.composite_child_axis == axis_before
    assert linked(world.parent) == links_before
    assert EvalTemplateVersion.objects.get_default(world.parent) == default_before
    assert (
        EvalTemplateVersion.objects.filter(eval_template=world.parent).count()
        == versions_before
    )


@pytest.mark.parametrize("route", ["restore", "set-default"])
def test_going_back_to_children_that_no_longer_fit_the_axis_changes_nothing(
    world, route
):
    on_score = on_score_axis(world)
    edit(
        world.client,
        world.parent,
        {
            "child_template_ids": [str(world.replacement.id)],
            "composite_child_axis": "",
        },
    )
    first_becomes_pass_fail(world)

    # The edit's own sentence, as when a child that does not fit is added.
    refused_and_unchanged(world, on_score, route, NOT_A_SCORE)
    assert linked(world.parent) == [REPLACEMENT]
    assert world.parent.composite_child_axis == ""


@pytest.mark.parametrize("route", ["restore", "set-default"])
def test_going_back_to_an_axis_the_children_no_longer_fit_changes_nothing(world, route):
    on_score = on_score_axis(world)
    edit(world.client, world.parent, {"composite_child_axis": ""})
    first_becomes_pass_fail(world)

    # The edit's own sentence, as when the axis is switched under them.
    refused_and_unchanged(
        world,
        on_score,
        route,
        f"Cannot switch to 'percentage' axis: {NOT_A_SCORE}",
    )
    assert linked(world.parent) == [FIRST]
    assert world.parent.composite_child_axis == ""


@pytest.mark.parametrize("route", ["restore", "set-default"])
def test_going_back_to_an_axis_the_children_fit(world, route):
    on_score = on_score_axis(world)
    edit(world.client, world.parent, {"composite_child_axis": ""})

    go_back(world.client, world.parent, on_score, route)

    world.parent.refresh_from_db()
    assert world.parent.composite_child_axis == "percentage"
    assert linked(world.parent) == [FIRST]
    assert detail(world.client, world.parent)["composite_child_axis"] == "percentage"


@pytest.mark.parametrize("route", ["restore", "set-default"])
def test_going_back_to_a_version_whose_child_version_is_gone_changes_nothing(
    world, route
):
    pin = EvalTemplateVersion.objects.create_version(
        eval_template=world.first,
        criteria="",
        organization=world.first.organization,
        workspace=world.first.workspace,
    )
    edit(
        world.client,
        world.parent,
        {
            "child_template_ids": [str(world.first.id)],
            "child_pinned_versions": {str(world.first.id): str(pin.id)},
        },
    )
    pinned = EvalTemplateVersion.objects.get_default(world.parent)
    saved = pinned.config_snapshot["children"]
    assert [entry["pinned_version_id"] for entry in saved] == [str(pin.id)]
    edit(
        world.client,
        world.parent,
        {"child_template_ids": [str(world.replacement.id)]},
    )
    EvalTemplateVersion.objects.filter(id=pin.id).update(deleted=True)

    refused_and_unchanged(world, pinned, route, UNAVAILABLE_CHILD_VERSION)
    assert linked(world.parent) == [REPLACEMENT]
