"""Eval consumers and experiment decisions against a frozen PostgreSQL replica.

Only external workflow dispatch is replaced. ORM queries, transaction boundaries,
request validation and serialization run normally with the real replica router.
"""

import uuid
from types import SimpleNamespace

import pytest

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.eval_groups import EvalGroup
from model_hub.services.eval_group import apply_eval_group_to_observation_span
from tfc.temporal.eval_tasks import client as workflow_client
from tfc.temporal.eval_tasks.client import start_eval_task_workflow_sync
from tfc.tests.lagging_replica import lagging_replica  # noqa: F401
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.eval_task import EvalTask
from tracer.models.monitor import UserAlertMonitor
from tracer.models.observation_span import EvalLogger, ObservationSpan
from tracer.models.project import Project
from tracer.models.project_version import ProjectVersion, ProjectVersionWinner
from tracer.models.trace import Trace
from tracer.serializers.eval_task import EditEvalTaskSerializer, EvalTaskSerializer
from tracer.views.eval_task import EvalTaskView

pytestmark = pytest.mark.django_db(transaction=True)
TASK_URL = "/tracer/eval-task/"


@pytest.fixture(autouse=True)
def disable_external_mirrors(monkeypatch):
    monkeypatch.setenv("CH25_TRACE_DUAL_WRITE", "false")


@pytest.fixture
def group(project, user, eval_template):
    eval_template.config = {"required_keys": ["input"], "optional_keys": ["output"]}
    eval_template.save(update_fields=["config"])
    group = EvalGroup.objects.create(
        name="Replica group",
        organization=project.organization,
        workspace=project.workspace,
        created_by=user,
    )
    group.eval_templates.add(eval_template)
    return group


def _apply(group, project, user, mapping, **options):
    return apply_eval_group_to_observation_span(
        group,
        {"project_id": str(project.pk), **options},
        mapping,
        user,
        [],
        project.workspace,
        {},
    )


def test_c11_apply_identical_group_reuses_acknowledged_config(
    lagging_replica,  # noqa: F811
    group,
    project,
    user,
):
    lagging_replica.freeze()
    first = _apply(group, project, user, {"input": "input", "output": "output"})
    second = _apply(group, project, user, {"output": "output", "input": "input"})
    assert len(first) == len(second) == 1
    assert first[0]["id"] == second[0]["id"]
    assert (
        CustomEvalConfig.objects.using("default").filter(eval_group=group).count() == 1
    )
    assert not CustomEvalConfig.objects.filter(eval_group=group).exists()


def test_c12_new_mapping_allocates_next_name_even_with_dedupe_fixed(
    lagging_replica,  # noqa: F811
    group,
    project,
    user,
    eval_template,
):
    lagging_replica.freeze()
    first = _apply(group, project, user, {"input": "input"})
    second = _apply(group, project, user, {"input": "output"})
    assert first[0]["id"] != second[0]["id"]
    base = f"{eval_template.name}_{group.name}"
    assert [first[0]["name"], second[0]["name"]] == [base, base + "_v2"]
    assert second[0]["mapping"] == {"input": "output"}
    assert (
        CustomEvalConfig.objects.using("default").filter(eval_group=group).count() == 2
    )


def test_group_dedupe_preserves_options_and_project(
    lagging_replica,  # noqa: F811
    group,
    project,
    observe_project,
    user,
):
    lagging_replica.freeze()
    first = _apply(group, project, user, {"input": "input"})
    option = _apply(group, project, user, {"input": "input"}, model="turing_small")
    other = _apply(group, observe_project, user, {"input": "input"})
    assert len({first[0]["id"], option[0]["id"], other[0]["id"]}) == 3
    assert option[0]["model"] == "turing_small"
    assert other[0]["project"] == str(observe_project.pk)


def _create_config(client, project, template, *, instant=False):
    response = client.post(
        "/tracer/custom-eval-config/",
        {
            "project": str(project.pk),
            "eval_template": str(template.pk),
            "name": "fresh-" + uuid.uuid4().hex,
            "mapping": {"input": "input"},
            "config": {"instant_eval": instant},
        },
        format="json",
    )
    assert response.status_code == 200, response.data
    result = response.data["result"]
    return str(result.get("id") or result["custom_eval_config_id"])


def _monitor(client, project, config_id):
    return client.post(
        "/tracer/user-alerts/",
        {
            "project": str(project.pk),
            "name": "fresh eval monitor",
            "metric_type": "evaluation_metrics",
            "metric": config_id,
            "threshold_operator": "greater_than",
            "threshold_type": "static",
            "critical_threshold_value": 0.15,
            "alert_frequency": 60,
        },
        format="json",
    )


def test_c36_monitor_accepts_just_created_config_and_keeps_project_check(
    lagging_replica,  # noqa: F811
    auth_client,
    observe_project,
    project,
    eval_template,
):
    lagging_replica.freeze()
    config_id = _create_config(auth_client, observe_project, eval_template)
    assert not CustomEvalConfig.objects.filter(pk=config_id).exists()
    response = _monitor(auth_client, observe_project, config_id)
    assert response.status_code == 200, response.data
    assert UserAlertMonitor.objects.get(name="fresh eval monitor").metric == config_id
    foreign_id = _create_config(auth_client, project, eval_template)
    denied = _monitor(auth_client, observe_project, foreign_id)
    assert denied.status_code == 400, denied.data
    assert UserAlertMonitor.objects.filter(name="fresh eval monitor").count() == 1


def _version(project, name, latency):
    version = ProjectVersion.objects.create(project=project, name=name, version=name)
    trace = Trace.objects.create(project=project, project_version=version)
    span = ObservationSpan.objects.create(
        id="span_" + uuid.uuid4().hex,
        project=project,
        project_version=version,
        trace=trace,
        latency_ms=latency,
    )
    return version, trace, span


def _winner(client, project, config):
    response = client.post(
        "/tracer/project-version/project_version_winner/",
        {"project_id": str(project.pk), "config": config},
        format="json",
    )
    assert response.status_code == 200, response.data
    return ProjectVersionWinner.objects.get(project=project, eval_config=config)


def test_c70_winner_includes_new_version_and_complete_mapping(
    lagging_replica,  # noqa: F811
    auth_client,
    project,
):
    a, _, _ = _version(project, "A", 100)
    lagging_replica.freeze()
    b, _, _ = _version(project, "B", 10)
    winner = _winner(auth_client, project, {"avg_latency_ms": 1})
    assert winner.winner_version_id == b.pk
    assert winner.version_mapper == {
        str(a.pk): {"score": -100.0, "rank": 2},
        str(b.pk): {"score": -10.0, "rank": 1},
    }


@pytest.mark.parametrize("output", ["float", "bool", "choices"])
def test_c70_winner_reads_fresh_weighted_configs_and_eval_subqueries(
    lagging_replica,  # noqa: F811
    auth_client,
    project,
    eval_template,
    output,
):
    eval_template.choices = ["good", "bad"] if output == "choices" else []
    eval_template.save(update_fields=["choices"])
    a, ta, sa = _version(project, "A", 10)
    b, tb, sb = _version(project, "B", 100)
    lagging_replica.freeze()
    config = CustomEvalConfig.objects.create(
        project=project, eval_template=eval_template, name="new weighted eval"
    )
    fields = {
        "float": ({"output_float": 0.1}, {"output_float": 0.9}, 10.0, 90.0),
        "bool": ({"output_bool": False}, {"output_bool": True}, 0.0, 100.0),
        "choices": (
            {"output_str_list": ["bad"]},
            {"output_str_list": ["good"]},
            0.0,
            100.0,
        ),
    }
    av, bv, score_a, score_b = fields[output]
    # Seed results in bulk to isolate the winner from the separate single-row
    # EvalLogger.full_clean FK lookup; PostgreSQL still checks the target shape.
    EvalLogger.objects.bulk_create(
        [
            EvalLogger(
                trace=trace, observation_span=span, custom_eval_config=config, **values
            )
            for trace, span, values in [(ta, sa, av), (tb, sb, bv)]
        ]
    )
    key = str(config.pk) + ("**good" if output == "choices" else "")
    winner = _winner(auth_client, project, {"avg_latency_ms": 1, key: 2})
    assert winner.winner_version_id == b.pk
    assert winner.version_mapper == {
        str(a.pk): {"score": -10.0 + score_a * 2, "rank": 2},
        str(b.pk): {"score": -100.0 + score_b * 2, "rank": 1},
    }
    assert not CustomEvalConfig.objects.filter(pk=config.pk).exists()
    assert not EvalLogger.objects.filter(custom_eval_config=config).exists()


@pytest.fixture
def workflow_inputs(monkeypatch):
    """Exercise real workflow selection/sizing; replace only Temporal transport."""
    calls = []
    monkeypatch.setattr(
        "tracer.views.eval_task.start_eval_task_workflow_sync",
        start_eval_task_workflow_sync,
    )
    monkeypatch.setattr(
        workflow_client, "_describe_says_nothing_is_draining", lambda _: True
    )

    def start(**kwargs):
        calls.append(kwargs["workflow_input"])
        return SimpleNamespace(id=kwargs["workflow_id"])

    monkeypatch.setattr(workflow_client, "start_workflow_sync", start)
    return calls


def _task_payload(project, config_id):
    return {
        "project": str(project.pk),
        "name": "instant task",
        "run_type": "historical",
        "spans_limit": 100,
        "sampling_rate": 100.0,
        "evals": [str(config_id)],
    }


def test_c34_config_to_task_api_reads_new_relation_for_workflow_sizing(
    lagging_replica,  # noqa: F811
    auth_client,
    project,
    eval_template,
    workflow_inputs,
):
    lagging_replica.freeze()
    config_id = _create_config(auth_client, project, eval_template, instant=True)
    response = auth_client.post(
        TASK_URL, _task_payload(project, config_id), format="json"
    )
    assert response.status_code == 200, response.data
    task = EvalTask.objects.get(pk=response.data["result"]["id"])
    assert list(task.evals.using("default").values_list("id", flat=True)) == [
        uuid.UUID(config_id)
    ]
    assert len(workflow_inputs) == 1
    assert workflow_inputs[0].max_concurrent == 32
    assert workflow_inputs[0].batch_size == 100
    assert CustomEvalConfig.objects.all().db == "replica"


def test_c34_edit_rerun_accepts_fresh_configs_and_sizes_current_relation(
    lagging_replica,  # noqa: F811
    auth_client,
    project,
    eval_template,
    eval_task,
    workflow_inputs,
):
    eval_task.run_type = "historical"
    eval_task.save(update_fields=["run_type"])
    lagging_replica.freeze()
    config_id = _create_config(auth_client, project, eval_template, instant=True)
    response = auth_client.patch(
        TASK_URL + "update_eval_task/",
        {
            "eval_task_id": str(eval_task.pk),
            "evals": [config_id],
            "edit_type": "edit_rerun",
        },
        format="json",
    )
    assert response.status_code == 200, response.data
    assert list(eval_task.evals.using("default").values_list("id", flat=True)) == [
        uuid.UUID(config_id)
    ]
    assert len(workflow_inputs) == 1
    assert workflow_inputs[0].max_concurrent == 32
    assert CustomEvalConfig.objects.all().db == "replica"


@pytest.mark.parametrize("method", ["patch", "put"])
def test_c34_generic_edit_returns_new_config_not_frozen_m2m(
    lagging_replica,  # noqa: F811
    auth_client,
    project,
    eval_template,
    eval_task,
    custom_eval_config,
    method,
):
    # Both configs exist on the replica; only membership changes after freeze.
    config_id = _create_config(auth_client, project, eval_template)
    lagging_replica.freeze()
    payload = {"evals": [config_id]}
    if method == "put":
        payload = _task_payload(project, config_id)
    response = getattr(auth_client, method)(
        TASK_URL + f"{eval_task.pk}/", payload, format="json"
    )
    assert response.status_code == 200, response.data
    body = response.data.get("result", response.data)
    assert [str(pk) for pk in body["evals"]] == [config_id]
    assert list(eval_task.evals.using("default").values_list("id", flat=True)) == [
        uuid.UUID(config_id)
    ]
    assert list(eval_task.evals.values_list("id", flat=True)) == [custom_eval_config.pk]
    assert CustomEvalConfig.objects.all().db == "replica"


@pytest.mark.parametrize(
    "action", ["create", "update", "partial_update", "update_eval_task"]
)
@pytest.mark.parametrize("reject", [False, True])
def test_task_primary_context_covers_contract_validation_and_restores(
    lagging_replica,  # noqa: F811
    auth_client,
    project,
    eval_task,
    custom_eval_config,
    monkeypatch,
    action,
    reject,
):
    seen = []
    cls = EditEvalTaskSerializer if action == "update_eval_task" else EvalTaskSerializer
    original = cls.validate_evals

    def validate(self, value):
        seen.append(CustomEvalConfig.objects.all().db)
        return original(self, value)

    monkeypatch.setattr(cls, "validate_evals", validate)
    lagging_replica.freeze()
    evals = [] if reject else [str(custom_eval_config.pk)]
    if action == "create":
        response = auth_client.post(
            TASK_URL,
            {**_task_payload(project, custom_eval_config.pk), "evals": evals},
            format="json",
        )
    elif action == "update_eval_task":
        response = auth_client.patch(
            TASK_URL + "update_eval_task/",
            {
                "eval_task_id": str(eval_task.pk),
                "evals": evals,
                "edit_type": "edit_rerun",
            },
            format="json",
        )
    else:
        method = auth_client.put if action == "update" else auth_client.patch
        response = method(
            TASK_URL + f"{eval_task.pk}/",
            {**_task_payload(project, custom_eval_config.pk), "evals": evals},
            format="json",
        )
    assert response.status_code == (400 if reject else 200), response.data
    assert seen and set(seen) == {"default"}
    assert CustomEvalConfig.objects.all().db == "replica"


@pytest.mark.parametrize("boundary", ["organization", "workspace"])
def test_task_many_related_field_rejects_foreign_configs(
    lagging_replica,  # noqa: F811
    auth_client,
    project,
    user,
    eval_template,
    eval_task,
    custom_eval_config,
    boundary,
):
    org = (
        Organization.objects.create(name="Foreign")
        if boundary == "organization"
        else project.organization
    )
    workspace = Workspace.objects.create(
        name="Foreign",
        organization=org,
        created_by=user,
        is_active=True,
        is_default=False,
    )
    foreign_project = Project.objects.create(
        name="Foreign",
        organization=org,
        workspace=workspace,
        model_type=project.model_type,
        trace_type="observe",
    )
    lagging_replica.freeze()
    config = CustomEvalConfig.objects.create(
        project=foreign_project, eval_template=eval_template, name="private"
    )
    # Exercise the view's scoping independently of middleware manager filtering.
    view = EvalTaskView()
    view.request = SimpleNamespace(
        user=user, organization=project.organization, workspace=project.workspace
    )
    view.format_kwarg = None
    view.kwargs = {}
    serializer = view.get_serializer(data=_task_payload(project, config.pk))
    assert (
        not serializer.fields["evals"]
        .child_relation.queryset.filter(pk=config.pk)
        .exists()
    )
    assert not serializer.is_valid()
    assert "evals" in serializer.errors
    for method, url, payload in [
        ("post", TASK_URL, _task_payload(project, config.pk)),
        ("patch", TASK_URL + f"{eval_task.pk}/", {"evals": [str(config.pk)]}),
        ("put", TASK_URL + f"{eval_task.pk}/", _task_payload(project, config.pk)),
        (
            "patch",
            TASK_URL + "update_eval_task/",
            {
                "eval_task_id": str(eval_task.pk),
                "evals": [str(config.pk)],
                "edit_type": "edit_rerun",
            },
        ),
    ]:
        response = getattr(auth_client, method)(url, payload, format="json")
        assert response.status_code == 400, response.data
        assert CustomEvalConfig.objects.all().db == "replica"
    assert list(eval_task.evals.using("default").values_list("id", flat=True)) == [
        custom_eval_config.pk
    ]


def test_task_project_guard_rejects_config_from_another_visible_project(
    lagging_replica,  # noqa: F811
    auth_client,
    project,
    observe_project,
    eval_template,
    eval_task,
):
    lagging_replica.freeze()
    config_id = _create_config(auth_client, observe_project, eval_template)
    create = auth_client.post(
        TASK_URL, _task_payload(project, config_id), format="json"
    )
    edit = auth_client.patch(
        TASK_URL + "update_eval_task/",
        {
            "eval_task_id": str(eval_task.pk),
            "evals": [config_id],
            "edit_type": "edit_rerun",
        },
        format="json",
    )
    assert create.status_code == edit.status_code == 400
    assert not eval_task.evals.using("default").filter(pk=config_id).exists()


@pytest.mark.parametrize("action", ["list", "retrieve"])
def test_task_browsing_keeps_replica_eligible_related_reads(
    lagging_replica,  # noqa: F811
    auth_client,
    project,
    user,
    eval_task,
    custom_eval_config,
    action,
):
    lagging_replica.freeze()
    CustomEvalConfig.objects.using("default").filter(pk=custom_eval_config.pk).update(
        name="new name"
    )
    view = EvalTaskView()
    view.action = action
    view.request = SimpleNamespace(
        user=user,
        organization=project.organization,
        workspace=project.workspace,
        query_params={},
    )
    view.kwargs = {"pk": eval_task.pk} if action == "retrieve" else {}
    with lagging_replica.capture() as queries:
        task = view.get_queryset().get(pk=eval_task.pk)
        assert task.evals.all()[0].name == custom_eval_config.name
    assert queries.captured_queries
    assert CustomEvalConfig.objects.all().db == "replica"


# -- R4: a config or task the user acts on right after creating it ------------

CONFIG_URL = "/tracer/custom-eval-config/"


def _primary_config(config_id):
    return CustomEvalConfig.objects.using("default").get(pk=config_id)


def test_r4_1_config_edited_right_after_create_is_accepted_and_saved(
    lagging_replica,  # noqa: F811
    auth_client,
    project,
    eval_template,
):
    """The task panel edits an eval it added a moment ago: 200, not
    'Custom eval config with id ... does not exist.'"""
    lagging_replica.freeze()
    config_id = _create_config(auth_client, project, eval_template)
    assert not CustomEvalConfig.objects.filter(pk=config_id).exists()

    response = auth_client.patch(
        f"{CONFIG_URL}{config_id}/",
        {"name": "renamed", "mapping": {"input": "output"}},
        format="json",
    )
    assert response.status_code == 200, response.data
    config = _primary_config(config_id)
    assert (config.name, config.mapping) == ("renamed", {"input": "output"})


def test_r4_1_two_quick_edits_keep_both_changes(
    lagging_replica,  # noqa: F811
    auth_client,
    project,
    eval_template,
):
    """The second edit must not save back a copy of the row from before the
    first one."""
    config_id = _create_config(auth_client, project, eval_template)
    lagging_replica.freeze()

    first = auth_client.patch(
        f"{CONFIG_URL}{config_id}/", {"mapping": {"input": "output"}}, format="json"
    )
    assert first.status_code == 200, first.data
    second = auth_client.patch(
        f"{CONFIG_URL}{config_id}/", {"name": "renamed"}, format="json"
    )
    assert second.status_code == 200, second.data
    config = _primary_config(config_id)
    assert (config.name, config.mapping) == ("renamed", {"input": "output"})
    assert CustomEvalConfig.objects.get(pk=config_id).mapping == {"input": "input"}


def test_r4_1_config_opened_right_after_create_is_found(
    lagging_replica,  # noqa: F811
    auth_client,
    project,
    eval_template,
):
    lagging_replica.freeze()
    config_id = _create_config(auth_client, project, eval_template)
    response = auth_client.get(f"{CONFIG_URL}{config_id}/")
    assert response.status_code == 200, response.content
    body = response.json()
    assert body.get("result", body)["id"] == config_id


class _Spans:
    """The ClickHouse span reader, returning the spans it is given."""

    def __init__(self, spans):
        self.spans = spans
        self.asked = []

    def __call__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def list_by_project(self, project_id, **filters):
        self.asked.append((project_id, filters))
        return self.spans


def test_r4_1_rerun_right_after_create_saves_the_result(
    lagging_replica,  # noqa: F811
    settings,
    auth_client,
    project,
    eval_template,
    stub_run_eval,
    stub_cost_log,
    monkeypatch,
):
    """Re-running a config created a moment ago: the request finds it, the
    evaluation it dispatches finds it, and the result is saved. Only the
    model call, the cost log and the ClickHouse span listing are stubbed."""
    import tracer.services.clickhouse.v2 as ch_v2
    import tracer.utils.eval as eval_module

    settings.EVAL_SPAN_READ_SOURCE = "postgres"
    version = ProjectVersion.objects.create(project=project, name="A", version="A")
    trace = Trace.objects.create(project=project, project_version=version)
    span = ObservationSpan.objects.create(
        id="span_" + uuid.uuid4().hex,
        project=project,
        project_version=version,
        trace=trace,
        observation_type="llm",
        input={"question": "What is the capital of France?"},
        output={"answer": "Paris"},
    )
    lagging_replica.freeze()
    created = auth_client.post(
        CONFIG_URL,
        {
            "project": str(project.pk),
            "eval_template": str(eval_template.pk),
            "name": "rerun-" + uuid.uuid4().hex,
            "mapping": {"input": "input", "output": "output"},
            "config": {},
        },
        format="json",
    )
    assert created.status_code == 200, created.data
    config_id = created.data["result"]["id"]
    version.eval_tags = [{"custom_eval_config_id": config_id, "value": "LLM"}]
    version.save(update_fields=["eval_tags"])
    assert not CustomEvalConfig.objects.filter(pk=config_id).exists()

    reader = _Spans([SimpleNamespace(id=span.id)])
    monkeypatch.setattr(ch_v2, "get_reader", reader)
    # The worker runs the dispatched evaluation in place.
    monkeypatch.setattr(
        eval_module.evaluate_observation_span,
        "delay",
        eval_module.evaluate_observation_span._original_func,
    )

    response = auth_client.post(
        f"{CONFIG_URL}run_evaluation/",
        {"custom_eval_config_id": config_id, "project_version_id": str(version.pk)},
        format="json",
    )
    assert response.status_code == 200, response.data
    assert reader.asked == [
        (
            str(project.pk),
            {"project_version_id": str(version.pk), "observation_type": "llm"},
        )
    ]
    assert len(stub_run_eval.requests) == 1
    results = list(
        EvalLogger.objects.using("default").filter(
            observation_span_id=span.id, deleted=False
        )
    )
    assert [(str(r.custom_eval_config_id), r.error) for r in results] == [
        (config_id, False)
    ]


def _eval_ids_in_details(client, task_id):
    response = client.get(f"{TASK_URL}get_eval_details/", {"eval_id": str(task_id)})
    assert response.status_code == 200, response.data
    return sorted(e["id"] for e in response.data["result"]["evals_applied"])


def test_r4_2_task_details_list_the_eval_of_a_task_just_created(
    lagging_replica,  # noqa: F811
    auth_client,
    project,
    eval_template,
    workflow_inputs,
):
    """The task page right after Create Task shows the task's eval, not
    'No evaluations added yet'."""
    lagging_replica.freeze()
    config_id = _create_config(auth_client, project, eval_template)
    created = auth_client.post(
        TASK_URL, _task_payload(project, config_id), format="json"
    )
    assert created.status_code == 200, created.data
    task_id = created.data["result"]["id"]
    assert not CustomEvalConfig.objects.filter(eval_tasks__id=task_id).exists()
    assert _eval_ids_in_details(auth_client, task_id) == [config_id]


def test_r4_2_task_details_list_every_eval_right_after_an_edit(
    lagging_replica,  # noqa: F811
    auth_client,
    project,
    eval_template,
    eval_task,
    custom_eval_config,
    workflow_inputs,
):
    """Saved with a just-added second eval: the page right after Save shows
    both, so a second Save from it does not drop the new one."""
    eval_task.run_type = "historical"
    eval_task.save(update_fields=["run_type"])
    lagging_replica.freeze()
    added = _create_config(auth_client, project, eval_template)
    both = sorted([str(custom_eval_config.pk), added])
    edited = auth_client.patch(
        TASK_URL + "update_eval_task/",
        {"eval_task_id": str(eval_task.pk), "evals": both, "edit_type": "edit_rerun"},
        format="json",
    )
    assert edited.status_code == 200, edited.data
    assert list(
        CustomEvalConfig.objects.filter(eval_tasks=eval_task).values_list(
            "id", flat=True
        )
    ) == [custom_eval_config.pk]
    assert _eval_ids_in_details(auth_client, eval_task.pk) == both


def _check_exists(client, project, name, template, mapping):
    response = client.post(
        f"{CONFIG_URL}check_exists/",
        {
            "project_name": project.name,
            "project_type": project.trace_type,
            "eval_tags": [
                {
                    "custom_eval_name": name,
                    "eval_name": template.name,
                    "mapping": mapping,
                }
            ],
        },
        format="json",
    )
    assert response.status_code == 200, response.data
    return response.data["result"]["exists"]


def test_r4_4_sdk_check_sees_a_config_created_a_moment_ago(
    lagging_replica,  # noqa: F811
    auth_client,
    project,
    eval_template,
):
    """The SDK's pre-flight check reports a same-name config with another
    mapping, created a moment ago, as a conflict; the same config is not."""
    lagging_replica.freeze()
    response = auth_client.post(
        CONFIG_URL,
        {
            "project": str(project.pk),
            "eval_template": str(eval_template.pk),
            "name": "sdk-eval",
            "mapping": {"input": "input"},
            "config": {},
        },
        format="json",
    )
    assert response.status_code == 200, response.data
    assert not CustomEvalConfig.objects.filter(name="sdk-eval").exists()

    assert _check_exists(
        auth_client, project, "sdk-eval", eval_template, {"input": "output"}
    )
    assert not _check_exists(
        auth_client, project, "sdk-eval", eval_template, {"input": "input"}
    )


# -- Follow-up: the Ask page's config list right after it saves a question ----

LIST_URL = "/tracer/custom-eval-config/list_custom_eval_configs/"


def test_fresh_config_list_includes_a_config_created_after_freeze(
    lagging_replica,  # noqa: F811
    auth_client,
    project,
    eval_template,
    monkeypatch,
):
    """With feature:custom_eval_config_list opted in the list reads the
    replica; the Ask page asks for `fresh` and sees the question it saved a
    moment ago (to reuse it, and to pick a name that is not taken). Other
    callers keep the replica."""
    from tracer.views import custom_eval_config as config_views

    monkeypatch.setattr(
        config_views, "DATABASE_FOR_CUSTOM_EVAL_CONFIG_LIST", lagging_replica.alias
    )
    lagging_replica.freeze()
    config_id = _create_config(auth_client, project, eval_template)

    def listed(**params):
        response = auth_client.get(LIST_URL, {"project_id": str(project.pk), **params})
        assert response.status_code == 200, response.content
        return [str(row["id"]) for row in response.json()["result"]]

    assert config_id in listed(fresh="true")
    assert config_id not in listed()
