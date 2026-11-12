"""History finds a run by any question it asked, and keeps a run whose task
was renamed on the Tasks page."""

import pytest

from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.eval_task import EvalTask, EvalTaskStatus, RowType, RunType
from tracer.models.instant_eval import InstantEvalRun
from tracer.services.instant_eval_rows import RUN_MARKER
from tracer.tests.test_instant_eval_scale import (
    instant_eval_enabled as instant_eval_enabled,
)
from tracer.views.instant_eval import INSTANT_TASK_PREFIX

RUNS = "/tracer/instant-eval/runs/"
pytestmark = pytest.mark.django_db


@pytest.fixture
def ask(observe_project, eval_template):
    """A saved Instant eval question."""

    def make(text):
        return CustomEvalConfig.objects.create(
            name=text[:255],
            project=observe_project,
            eval_template=eval_template,
            config={"instant_eval": {"question": text, "unit": "traces"}},
            mapping={},
            filters={},
        )

    return make


@pytest.fixture
def make_run(observe_project):
    """A task asking these questions in this order; ``recorded`` gives it the
    run record the Ask page writes when it starts a run."""

    def make(name, questions, *, run_type=RunType.HISTORICAL, recorded=True):
        filters = {"project_id": str(observe_project.id)}
        if recorded:
            filters[RUN_MARKER] = True
            filters["instant_config_ids"] = [str(q.id) for q in questions]
        task = EvalTask.objects.create(
            project=observe_project,
            name=name,
            filters=filters,
            sampling_rate=100,
            spans_limit=0,
            run_type=run_type,
            status=(
                EvalTaskStatus.RUNNING
                if run_type == RunType.CONTINUOUS
                else EvalTaskStatus.COMPLETED
            ),
            row_type=RowType.TRACES,
        )
        task.evals.set(questions)
        if recorded:
            InstantEvalRun.objects.create(
                eval_task=task,
                project=observe_project,
                organization=observe_project.organization,
                unit="traces",
                row_type=RowType.TRACES,
            )
        return task

    return make


def _page(client, project, **params):
    response = client.get(RUNS, {"project_id": str(project.id), **params})
    assert response.status_code == 200, response.content
    return response.json()["result"]


def _ids(page):
    return [run["task_id"] for run in page["runs"]]


def test_history_search_finds_a_run_by_any_question_it_asked(
    auth_client, observe_project, ask, make_run
):
    satisfied = ask("Was the user satisfied?")
    refund = ask("Did the agent mention a refund?")
    two = make_run(
        f"{INSTANT_TASK_PREFIX} · Was the user satisfied?", [satisfied, refund]
    )
    polite = ask("Was the answer polite?")
    named = make_run(f"{INSTANT_TASK_PREFIX} · Was the answer polite?", [polite])
    # A first question longer than the name keeps: its end is not in the name.
    long = ask("Did the agent " + "really " * 40 + "mention the warranty?")
    cut = make_run(
        f"{INSTANT_TASK_PREFIX} · {long.config['instant_eval']['question']}"[:255],
        [long],
    )
    assert "warranty" not in cut.name

    # The second question, in any case.
    page = _page(auth_client, observe_project, search="REFUND")
    assert _ids(page) == [str(two.id)]
    assert page["runs"][0]["questions"] == [
        "Was the user satisfied?",
        "Did the agent mention a refund?",
    ]
    assert page["has_more"] is False
    assert _ids(_page(auth_client, observe_project, search="warranty")) == [str(cut.id)]
    # The name still finds a run, and a word no question has finds none.
    assert _ids(_page(auth_client, observe_project, search="polite")) == [str(named.id)]
    assert _page(auth_client, observe_project, search="invoice")["runs"] == []


def test_history_keeps_a_run_renamed_on_the_tasks_page(
    auth_client, observe_project, ask, make_run
):
    question = ask("Did the agent mention a refund?")
    renamed = make_run("Refund check", [question])
    # A renamed "Keep answering" task is still continuous, so still not a run.
    live = make_run(
        "Refund check live", [question], run_type=RunType.CONTINUOUS, recorded=False
    )
    # A task with no run record is History's only by its name (runs from
    # before run records were kept).
    make_run("Refund check nightly", [question], recorded=False)

    assert _ids(_page(auth_client, observe_project)) == [str(renamed.id)]
    assert _ids(_page(auth_client, observe_project, task_id=str(renamed.id))) == [
        str(renamed.id)
    ]
    assert _ids(_page(auth_client, observe_project, search="refund check")) == [
        str(renamed.id)
    ]
    assert _page(auth_client, observe_project, task_id=str(live.id))["runs"] == []
