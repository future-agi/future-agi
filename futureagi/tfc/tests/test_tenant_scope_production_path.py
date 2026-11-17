"""Tenant conformance on the production request path.

Every request here runs through ``MIDDLEWARE`` exactly as
``tfc.settings.settings`` defines it, with the URLconf warmed as a production
worker warms it, and is authenticated by a bearer token from
``/accounts/token/`` with the workspace headers the web app sends
(``tfc.utils.tests.production_path``). Nothing patches the view layer, so
authentication alone decides the caller's organization and workspace.

Conformance properties, for tenants A and B (each an organization with its
default workspace), checked on every endpoint the tenant scoping covers:

- a workspace B request returns only B rows;
- a workspace B write naming an id outside B is rejected with the normal
  validation error: the same answer an id that does not exist gets, and
  nothing is written.

A member removed from A who signs in again belongs to no organization: such a
request returns no organization's rows, and such a write is answered as one
naming an unknown id.
"""

import ipaddress
import itertools
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models.organization_membership import OrganizationMembership
from accounts.tests.personal_keys import personal_key_for
from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.models.ai_model import AIModel
from model_hub.models.choices import DatasetSourceChoices, OwnerChoices, StatusType
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.develop_dataset import Dataset, KnowledgeBaseFile
from model_hub.models.develop_optimisation import OptimizationDataset
from model_hub.models.evals_metric import EvalTemplate
from model_hub.models.experiments import ExperimentsTable
from model_hub.models.kb import KnowledgeBase
from model_hub.models.prompt_folders import PromptFolder
from tfc.constants.levels import Level
from tfc.middleware.workspace_context import clear_workspace_context
from tfc.utils.tests.production_path import (
    make_tenant,
    removed_member,
    same_answer,
    sign_in,
    use_production_request_settings,
)
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.eval_task import EvalTask, EvalTaskStatus, RunType
from tracer.models.monitor import UserAlertMonitor, UserAlertMonitorLog
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project
from tracer.models.project_version import ProjectVersion
from tracer.models.replay_session import ReplaySession, ReplayType
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession

EXPERIMENTS_URL = "/model-hub/experiments/data/"
CONFIG_URL = "/tracer/custom-eval-config/"
TASK_URL = "/tracer/eval-task/"
LABELS_URL = "/model-hub/annotations-labels/"
KB_URL = "/model-hub/kb/"
TRACE_URL = "/tracer/trace/"
MONITOR_URL = "/tracer/user-alerts/"
VERSION_URL = "/tracer/project-version/"
SESSION_URL = "/tracer/trace-session/"
SPAN_URL = "/tracer/observation-span/"
CONFIG_LIST_ACTION_URL = f"{CONFIG_URL}list_custom_eval_configs/"
CONFIG_BY_NAME_URL = f"{CONFIG_URL}get_custom_eval_by_name/"
MONITOR_METRIC_OPTIONS_URL = f"{MONITOR_URL}metric-options/"
MONITOR_LOG_URL = "/tracer/user-alert-logs/"
MONITOR_LOG_RESOLVE_URL = f"{MONITOR_LOG_URL}resolve/"
# One endpoint for each shared scoping helper module:
# model_hub.utils.workspace_scope (scoped_optimization_queryset, and
# request_organization directly) and tracer.utils.workspace_scope
# (project_workspace_scope_q, through get_request_organization).
OPTIMISATION_URL = "/model-hub/optimisation/"
PROMPT_FOLDERS_URL = "/model-hub/prompt-folders/"
REPLAY_SESSION_URL = "/tracer/replay-session/"
MONITOR_BODY = {
    "metric_type": "count_of_errors",
    "threshold_operator": "greater_than",
    "threshold_type": "static",
    "critical_threshold_value": 0.15,
    "alert_frequency": 60,
}
# Each API-key client gets its own address, so no request shares a rate bucket.
_ADDRESSES = itertools.count(1)

LABEL_SETTINGS = {
    "options": [{"label": "yes"}, {"label": "no"}],
    "multi_choice": False,
    "rule_prompt": "",
    "auto_annotate": False,
    "strategy": None,
}
TEMPLATE_CONFIG = {"type": "pass_fail", "criteria": "Is the output correct?"}
MAPPING = {"input": "input", "output": "output"}


def _project(tenant, name, workspace="tenant"):
    return Project.no_workspace_objects.create(
        name=name,
        organization=tenant.organization,
        workspace=tenant.workspace if workspace == "tenant" else workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )


def _template(tenant, name):
    return EvalTemplate.no_workspace_objects.create(
        name=name,
        organization=tenant.organization,
        workspace=tenant.workspace,
        owner=OwnerChoices.USER.value,
        config=TEMPLATE_CONFIG,
    )


def _config(project, template, name, kb_file=None):
    return CustomEvalConfig.no_workspace_objects.create(
        name=name,
        project=project,
        eval_template=template,
        kb_id=kb_file,
        config={},
        mapping=MAPPING,
        filters={},
    )


def _label(tenant, project, name, **extra):
    return AnnotationsLabels.no_workspace_objects.create(
        name=name,
        type="categorical",
        settings=LABEL_SETTINGS,
        organization=tenant.organization,
        workspace=tenant.workspace,
        project=project,
        **extra,
    )


def _rows(tenant, tag):
    """One of each row the endpoints under test list or reference."""
    clear_workspace_context()
    dataset = Dataset.no_workspace_objects.create(
        name=f"{tag} data",
        organization=tenant.organization,
        workspace=tenant.workspace,
        source=DatasetSourceChoices.BUILD.value,
    )
    no_workspace_dataset = Dataset.no_workspace_objects.create(
        name=f"{tag} data, no workspace",
        organization=tenant.organization,
        workspace=None,
        source=DatasetSourceChoices.BUILD.value,
    )
    project = _project(tenant, f"{tag} project")
    no_workspace_project = _project(tenant, f"{tag} project, no workspace", None)
    template = _template(tenant, f"{tag.lower()}-template")
    kb_file = KnowledgeBaseFile.no_workspace_objects.create(
        name=f"{tag} kb file",
        organization=tenant.organization,
        workspace=tenant.workspace,
    )
    config = _config(project, template, f"{tag} config", kb_file)
    task = EvalTask.no_workspace_objects.create(
        project=project,
        name=f"{tag} task",
        filters={},
        sampling_rate=50.0,
        run_type=RunType.CONTINUOUS,
        status=EvalTaskStatus.PENDING,
    )
    task.evals.add(config)
    rows = SimpleNamespace(
        dataset=dataset,
        experiment=ExperimentsTable.no_workspace_objects.create(
            name=f"{tag} run", dataset=dataset, status=StatusType.COMPLETED.value
        ),
        no_workspace_experiment=ExperimentsTable.no_workspace_objects.create(
            name=f"{tag} run, no workspace",
            dataset=no_workspace_dataset,
            status=StatusType.COMPLETED.value,
        ),
        project=project,
        no_workspace_project=no_workspace_project,
        template=template,
        kb_file=kb_file,
        knowledge_base=KnowledgeBase.no_workspace_objects.create(
            name=f"{tag} knowledge base",
            chunk_size=256,
            organization=tenant.organization,
            workspace=tenant.workspace,
        ),
        label=_label(tenant, project, f"{tag} label"),
        archived_label=_label(tenant, project, f"{tag} archived label", deleted=True),
        config=config,
        other_config=_config(project, template, f"{tag} other config"),
        no_workspace_config=_config(
            no_workspace_project, template, f"{tag} config, no workspace"
        ),
        task=task,
        optimisation=OptimizationDataset.no_workspace_objects.create(
            name=f"{tag} optimisation",
            optimize_type="PROMPT_TEMPLATE",
            dataset=dataset,
        ),
        prompt_folder=PromptFolder.no_workspace_objects.create(
            name=f"{tag} prompt folder",
            organization=tenant.organization,
            workspace=tenant.workspace,
            created_by=tenant.user,
        ),
        replay_session=ReplaySession.no_workspace_objects.create(
            project=project, replay_type=ReplayType.TRACE
        ),
    )
    rows.monitor = UserAlertMonitor.no_workspace_objects.create(
        organization=tenant.organization,
        workspace=tenant.workspace,
        project=project,
        name=f"{tag} monitor",
        **MONITOR_BODY,
    )
    rows.monitor_log = UserAlertMonitorLog.no_workspace_objects.create(
        alert=rows.monitor,
        type="critical",
        message=f"{tag} alert log",
        resolved=False,
    )
    clear_workspace_context()
    return rows


@pytest.fixture
def tenants(db, settings, monkeypatch):
    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_DISABLED", "true")
    use_production_request_settings(settings)
    tenant_a = make_tenant("A")
    tenant_b = make_tenant("B")
    data = SimpleNamespace(
        a=tenant_a,
        b=tenant_b,
        a_rows=_rows(tenant_a, "A"),
        b_rows=_rows(tenant_b, "B"),
        system_template=EvalTemplate.no_workspace_objects.create(
            name="conformance-system-template",
            owner=OwnerChoices.SYSTEM.value,
            config=TEMPLATE_CONFIG,
        ),
    )
    data.client = sign_in(tenant_b.user.email, workspace=tenant_b.workspace)
    yield data
    clear_workspace_context()


@pytest.fixture
def removed(tenants):
    """A member removed from A, signed in again."""
    _, client = removed_member(tenants.a, "A")
    return client


@pytest.fixture
def team(tenants):
    """A second, non-default workspace of B's organization, with one experiment,
    and B's owner signed in to it through the workspace header."""
    clear_workspace_context()
    workspace = Workspace.no_workspace_objects.create(
        name="Tenant B team workspace",
        organization=tenants.b.organization,
        is_default=False,
        is_active=True,
        created_by=tenants.b.user,
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=tenants.b.user,
        workspace=workspace,
        role="Workspace Owner",
        level=Level.OWNER,
        is_active=True,
        organization_membership=OrganizationMembership.no_workspace_objects.get(
            user=tenants.b.user, organization=tenants.b.organization
        ),
    )
    dataset = Dataset.no_workspace_objects.create(
        name="B team data",
        organization=tenants.b.organization,
        workspace=workspace,
        source=DatasetSourceChoices.BUILD.value,
    )
    experiment = ExperimentsTable.no_workspace_objects.create(
        name="B team run", dataset=dataset, status=StatusType.COMPLETED.value
    )
    clear_workspace_context()
    return SimpleNamespace(
        workspace=workspace,
        experiment=experiment,
        client=sign_in(tenants.b.user.email, workspace=workspace),
    )


def _api_key_client(tenant):
    """A client authenticated with the tenant actor's personal key pair."""
    clear_workspace_context()
    key = personal_key_for(
        tenant.user, tenant.organization, workspace=tenant.workspace
    )
    clear_workspace_context()
    address = str(ipaddress.ip_address("198.19.0.0") + next(_ADDRESSES))
    client = APIClient(enforce_csrf_checks=True, REMOTE_ADDR=address)
    client.credentials(HTTP_X_API_KEY=key.api_key, HTTP_X_SECRET_KEY=key.secret_key)
    return client


def _body(response):
    assert response.status_code == status.HTTP_200_OK, response.content
    return response.content.decode()


def _refused(response):
    """Authentication's answer to a signed-in account with no organization."""
    return (
        response.status_code == status.HTTP_403_FORBIDDEN
        and response.json().get("code") == "no_active_organization"
    )


def _removed_member_body(response):
    """A removed member's read: refused, or answered with an empty scope."""
    if _refused(response):
        return response.content.decode()
    return _body(response)


# ---------------------------------------------------------------- reads

# name -> (url, query, B rows it must list, rows outside B it must not list)
READS = {
    "experiment-list": lambda t: (
        EXPERIMENTS_URL,
        {},
        [t.b_rows.experiment],
        [t.a_rows.experiment, t.a_rows.no_workspace_experiment],
    ),
    "experiment-list-dataset-filter": lambda t: (
        EXPERIMENTS_URL,
        {"dataset_id": str(t.a_rows.dataset.id)},
        [],
        [t.a_rows.experiment],
    ),
    "custom-eval-config-list": lambda t: (
        CONFIG_URL,
        {},
        [t.b_rows.config],
        [t.a_rows.config, t.a_rows.no_workspace_config],
    ),
}

REMOVED_MEMBER_READS = {
    "experiment-list": (EXPERIMENTS_URL, {}),
    "custom-eval-config-list": (CONFIG_URL, {}),
    "custom-eval-config-list-action": (CONFIG_LIST_ACTION_URL, {}),
    "annotation-labels-list": (LABELS_URL, {}),
    "annotation-labels-list-include-archived": (
        LABELS_URL,
        {"include_archived": "true"},
    ),
    "annotation-labels-list-archived": (LABELS_URL, {"archived": "true"}),
    "knowledge-base-list": (KB_URL, {}),
    "optimisation-list": (OPTIMISATION_URL, {}),
    "prompt-folder-list": (PROMPT_FOLDERS_URL, {}),
    "replay-session-list": (REPLAY_SESSION_URL, {}),
    "monitor-log-list": (MONITOR_LOG_URL, {}),
}


def _listed_rows(rows):
    """Every row of a tenant that one of the removed-member reads lists."""
    return (
        rows.experiment,
        rows.no_workspace_experiment,
        rows.label,
        rows.archived_label,
        rows.knowledge_base,
        rows.config,
        rows.other_config,
        rows.no_workspace_config,
        rows.optimisation,
        rows.prompt_folder,
        rows.replay_session,
        rows.monitor_log,
    )


# ---------------------------------------------------------------- writes


@dataclass
class Write:
    send: Callable  # (client, referenced id) -> response
    foreign_id: object
    state: Callable  # () -> what the write would change


def _config_create(field):
    def build(t):
        foreign = {
            "project": t.a_rows.project.id,
            "eval_template": t.a_rows.template.id,
            "kb_id": t.a_rows.kb_file.id,
        }[field]

        def send(client, ref_id):
            body = {
                "name": "conformance config",
                "project": str(t.b_rows.project.id),
                "eval_template": str(t.b_rows.template.id),
                field: str(ref_id),
                "config": {},
                "mapping": MAPPING,
            }
            return client.post(CONFIG_URL, body, format="json")

        return Write(send, foreign, CustomEvalConfig.no_workspace_objects.count)

    return build


def _config_patch(field):
    def build(t):
        config = t.b_rows.config
        foreign = {
            "project": t.a_rows.project.id,
            "eval_template": t.a_rows.template.id,
            "kb_id": t.a_rows.kb_file.id,
        }[field]

        def send(client, ref_id):
            return client.patch(
                f"{CONFIG_URL}{config.id}/", {field: str(ref_id)}, format="json"
            )

        def state():
            config.refresh_from_db()
            return (config.project_id, config.eval_template_id, config.kb_id_id)

        return Write(send, foreign, state)

    return build


def _task_create(t):
    def send(client, ref_id):
        body = {
            "project": str(t.b_rows.project.id),
            "name": "conformance task",
            "run_type": RunType.CONTINUOUS,
            "sampling_rate": 50.0,
            "filters": {},
            "evals": [str(ref_id)],
        }
        return client.post(TASK_URL, body, format="json")

    return Write(send, t.a_rows.config.id, EvalTask.no_workspace_objects.count)


def _task_evals(task):
    return set(
        EvalTask.no_workspace_objects.get(id=task.id).evals.values_list("id", flat=True)
    )


def _task_patch(t):
    task = t.b_rows.task

    def send(client, ref_id):
        return client.patch(
            f"{TASK_URL}{task.id}/", {"evals": [str(ref_id)]}, format="json"
        )

    return Write(send, t.a_rows.config.id, lambda: _task_evals(task))


def _label_body(project_id, name):
    return {
        "name": name,
        "type": "categorical",
        "settings": LABEL_SETTINGS,
        "project": str(project_id),
    }


def _label_create(t):
    def send(client, ref_id):
        return client.post(
            LABELS_URL, _label_body(ref_id, "conformance label"), format="json"
        )

    return Write(
        send,
        t.a_rows.project.id,
        lambda: AnnotationsLabels.all_objects.filter(name="conformance label").count(),
    )


def _label_patch(t):
    label = t.b_rows.label

    def send(client, ref_id):
        return client.patch(
            f"{LABELS_URL}{label.id}/", _label_body(ref_id, label.name), format="json"
        )

    def state():
        label.refresh_from_db()
        return label.project_id

    return Write(send, t.a_rows.project.id, state)


def _config_put(field):
    """PUT runs ModelViewSet.update (get_object, then the serializer)."""

    def build(t):
        config = t.b_rows.config
        foreign = {
            "project": t.a_rows.project.id,
            "eval_template": t.a_rows.template.id,
            "kb_id": t.a_rows.kb_file.id,
        }[field]

        def send(client, ref_id):
            body = {
                "name": config.name,
                "project": str(t.b_rows.project.id),
                "eval_template": str(t.b_rows.template.id),
                "kb_id": str(t.b_rows.kb_file.id),
                field: str(ref_id),
                "config": {},
                "mapping": MAPPING,
            }
            return client.put(f"{CONFIG_URL}{config.id}/", body, format="json")

        def state():
            config.refresh_from_db()
            return (config.project_id, config.eval_template_id, config.kb_id_id)

        return Write(send, foreign, state)

    return build


def _task_body(project_id, config_id, name):
    return {
        "project": str(project_id),
        "name": name,
        "run_type": RunType.CONTINUOUS,
        "sampling_rate": 50.0,
        "filters": {},
        "evals": [str(config_id)],
    }


def _task_put(t):
    """PUT runs EvalTaskView.update, decorated apart from partial_update."""
    task = t.b_rows.task

    def send(client, ref_id):
        return client.put(
            f"{TASK_URL}{task.id}/",
            _task_body(t.b_rows.project.id, ref_id, task.name),
            format="json",
        )

    return Write(send, t.a_rows.config.id, lambda: _task_evals(task))


def _task_create_project(t):
    def send(client, ref_id):
        return client.post(
            TASK_URL,
            _task_body(ref_id, t.b_rows.config.id, "conformance task"),
            format="json",
        )

    return Write(send, t.a_rows.project.id, EvalTask.no_workspace_objects.count)


def _monitor_create(t):
    def send(client, ref_id):
        body = {"project": str(ref_id), "name": "conformance monitor", **MONITOR_BODY}
        return client.post(MONITOR_URL, body, format="json")

    return Write(send, t.a_rows.project.id, UserAlertMonitor.all_objects.count)


WRITES = {
    "custom-eval-config-create-project": _config_create("project"),
    "custom-eval-config-create-eval-template": _config_create("eval_template"),
    "custom-eval-config-create-kb-file": _config_create("kb_id"),
    "custom-eval-config-patch-project": _config_patch("project"),
    "custom-eval-config-patch-eval-template": _config_patch("eval_template"),
    "custom-eval-config-patch-kb-file": _config_patch("kb_id"),
    "custom-eval-config-put-project": _config_put("project"),
    "custom-eval-config-put-eval-template": _config_put("eval_template"),
    "custom-eval-config-put-kb-file": _config_put("kb_id"),
    "eval-task-create-eval-config": _task_create,
    "eval-task-create-project": _task_create_project,
    "eval-task-patch-eval-config": _task_patch,
    "eval-task-put-eval-config": _task_put,
    "annotation-label-create-project": _label_create,
    "annotation-label-patch-project": _label_patch,
}


def _removed_config_create(t):
    def send(client, ref_id):
        body = {
            "name": "conformance config",
            "project": str(ref_id),
            "eval_template": str(t.system_template.id),
            "config": {},
            "mapping": MAPPING,
        }
        return client.post(CONFIG_URL, body, format="json")

    return Write(send, t.a_rows.project.id, CustomEvalConfig.no_workspace_objects.count)


def _removed_task_patch(t):
    task = t.a_rows.task

    def send(client, ref_id):
        return client.patch(
            f"{TASK_URL}{task.id}/", {"evals": [str(ref_id)]}, format="json"
        )

    return Write(send, t.a_rows.other_config.id, lambda: _task_evals(task))


def _removed_trace_create(t):
    def send(client, ref_id):
        return client.post(
            TRACE_URL, {"project": str(ref_id), "name": "trace"}, format="json"
        )

    return Write(
        send,
        t.a_rows.project.id,
        lambda: Trace.no_workspace_objects.filter(project=t.a_rows.project).count(),
    )


def _removed_task_create_project(t):
    def send(client, ref_id):
        return client.post(
            TASK_URL,
            _task_body(ref_id, t.a_rows.config.id, "conformance task"),
            format="json",
        )

    return Write(send, t.a_rows.project.id, EvalTask.no_workspace_objects.count)


def _removed_project_create(url, model, **extra):
    """A create naming a project (and nothing else of the former organization
    unless ``extra`` builds it)."""

    def build(t):
        fields = {name: str(make(t)) for name, make in extra.items()}

        def send(client, ref_id):
            body = {"project": str(ref_id), "name": "conformance row", **fields}
            if model is UserAlertMonitor:
                body.update(MONITOR_BODY)
            return client.post(url, body, format="json")

        return Write(send, t.a_rows.project.id, model.all_objects.count)

    return build


def _a_trace(t):
    clear_workspace_context()
    trace = Trace.no_workspace_objects.create(project=t.a_rows.project, name="A trace")
    clear_workspace_context()
    return trace.id


def _removed_monitor_log_create(t):
    def send(client, ref_id):
        body = {"alert": str(ref_id), "type": "critical", "message": "conformance log"}
        return client.post(MONITOR_LOG_URL, body, format="json")

    return Write(
        send,
        t.a_rows.monitor.id,
        UserAlertMonitorLog.no_workspace_objects.filter(
            message="conformance log"
        ).count,
    )


def _removed_monitor_log_resolve(t):
    log = t.a_rows.monitor_log

    def send(client, ref_id):
        return client.post(
            MONITOR_LOG_RESOLVE_URL, {"log_ids": [str(ref_id)]}, format="json"
        )

    def state():
        log.refresh_from_db()
        return (log.resolved, log.resolved_by_id)

    return Write(send, log.id, state)


def _removed_prompt_folder_create(t):
    """A folder create under a former folder: a caller with no organization
    has nowhere to keep it, and the former organization gets no folder."""

    def send(client, ref_id):
        body = {"name": "conformance folder", "parent_folder": str(ref_id)}
        return client.post(PROMPT_FOLDERS_URL, body, format="json")

    return Write(
        send,
        t.a_rows.prompt_folder.id,
        PromptFolder.no_workspace_objects.filter(name="conformance folder").count,
    )


def _removed_prompt_folder_patch(t):
    folder = t.a_rows.prompt_folder

    def send(client, ref_id):
        return client.patch(
            f"{PROMPT_FOLDERS_URL}{ref_id}/",
            {"name": "conformance renamed folder"},
            format="json",
        )

    def state():
        folder.refresh_from_db()
        return (folder.name, folder.organization_id, folder.workspace_id)

    return Write(send, folder.id, state)


REMOVED_MEMBER_WRITES = {
    "custom-eval-config-create-project": _removed_config_create,
    "eval-task-create-project": _removed_task_create_project,
    "eval-task-patch-eval-config": _removed_task_patch,
    "trace-create-project": _removed_trace_create,
    "monitor-create-project": _removed_project_create(MONITOR_URL, UserAlertMonitor),
    "project-version-create-project": _removed_project_create(
        VERSION_URL, ProjectVersion
    ),
    "trace-session-create-project": _removed_project_create(SESSION_URL, TraceSession),
    "observation-span-create-project": _removed_project_create(
        SPAN_URL,
        ObservationSpan,
        trace=_a_trace,
        observation_type=lambda t: "llm",
    ),
    "monitor-log-create-alert": _removed_monitor_log_create,
    "monitor-log-resolve-log": _removed_monitor_log_resolve,
    "prompt-folder-create-parent-folder": _removed_prompt_folder_create,
    "prompt-folder-patch-folder": _removed_prompt_folder_patch,
}


def _check_rejected(client, write, status_code=None):
    before = write.state()
    unknown_id = uuid.uuid4()

    foreign = write.send(client, write.foreign_id)
    unknown = write.send(client, unknown_id)

    if status_code is None:
        assert foreign.status_code >= 400, foreign.content
    else:
        assert foreign.status_code == status_code, foreign.content
    assert same_answer(foreign, write.foreign_id, unknown, unknown_id), (
        foreign.content,
        unknown.content,
    )
    assert write.state() == before


@pytest.mark.integration
@pytest.mark.api
class TestWorkspaceBRequestReturnsOnlyBRows:
    @pytest.mark.parametrize("read", sorted(READS))
    def test_workspace_b_request_returns_only_b_rows(self, tenants, read):
        url, query, own, outside = READS[read](tenants)

        body = _body(tenants.client.get(url, query))

        for row in own:
            assert str(row.id) in body, (read, row)
        for row in outside:
            assert str(row.id) not in body, (read, row)

    @pytest.mark.parametrize("read", sorted(REMOVED_MEMBER_READS))
    def test_removed_member_request_returns_no_organization_rows(
        self, tenants, removed, read
    ):
        url, query = REMOVED_MEMBER_READS[read]

        body = _removed_member_body(removed.get(url, query))

        for rows in (tenants.a_rows, tenants.b_rows):
            for row in _listed_rows(rows):
                assert str(row.id) not in body, (read, row)

    def test_removed_member_request_finds_no_former_user_template(
        self, tenants, removed
    ):
        response = removed.post(
            CONFIG_BY_NAME_URL,
            {"eval_template_name": tenants.a_rows.template.name},
            format="json",
        )

        body = _removed_member_body(response)
        assert str(tenants.a_rows.template.id) not in body
        if not _refused(response):
            assert response.json()["result"]["is_user_eval_template"] is False, body

    def test_removed_member_request_finds_no_former_monitor_project(
        self, tenants, removed
    ):
        """The monitor metric options for a former project are answered as
        those for an unknown project, and list none of its eval configs."""
        former, unknown_id = tenants.a_rows.project.id, uuid.uuid4()

        response = removed.get(MONITOR_METRIC_OPTIONS_URL, {"project_id": former})
        unknown = removed.get(MONITOR_METRIC_OPTIONS_URL, {"project_id": unknown_id})

        assert response.status_code >= 400, response.content
        assert same_answer(response, former, unknown, unknown_id), (
            response.content,
            unknown.content,
        )
        for row in (tenants.a_rows.config, tenants.a_rows.other_config):
            assert str(row.id) not in response.content.decode(), row

    def test_second_workspace_request_returns_only_its_rows(self, tenants, team):
        """A caller signed in to a non-default workspace of B's organization
        through the workspace header sees only that workspace's experiments."""
        body = _body(team.client.get(EXPERIMENTS_URL))

        assert str(team.experiment.id) in body
        for rows in (tenants.a_rows, tenants.b_rows):
            for row in (rows.experiment, rows.no_workspace_experiment):
                assert str(row.id) not in body, row

    def test_default_workspace_request_does_not_list_the_second_workspace(
        self, tenants, team
    ):
        body = _body(tenants.client.get(EXPERIMENTS_URL))

        assert str(team.experiment.id) not in body

    def test_workspace_b_api_key_request_returns_only_b_rows(self, tenants):
        """The same reads, authenticated with B's organization API key pair."""
        client = _api_key_client(tenants.b)
        a, b = tenants.a_rows, tenants.b_rows

        experiments = _body(client.get(EXPERIMENTS_URL))
        configs = _body(client.get(CONFIG_URL))

        assert str(b.experiment.id) in experiments
        assert str(b.config.id) in configs
        for row in (a.experiment, a.no_workspace_experiment):
            assert str(row.id) not in experiments, row
        for row in (a.config, a.no_workspace_config):
            assert str(row.id) not in configs, row


@pytest.mark.integration
@pytest.mark.api
class TestWorkspaceBWriteNamingAnIdOutsideBIsRejected:
    @pytest.mark.parametrize("write", sorted(WRITES))
    def test_workspace_b_write_naming_an_id_outside_b_is_rejected(self, tenants, write):
        _check_rejected(
            tenants.client, WRITES[write](tenants), status.HTTP_400_BAD_REQUEST
        )

    @pytest.mark.parametrize("write", sorted(REMOVED_MEMBER_WRITES))
    def test_removed_member_write_naming_a_former_id_is_rejected(
        self, tenants, removed, write
    ):
        _check_rejected(removed, REMOVED_MEMBER_WRITES[write](tenants))


@pytest.mark.integration
@pytest.mark.api
class TestProductionPathControls:
    """Controls, not conformance properties: they pass with or without the
    tenant scoping and show that the path above authenticates for real and
    still serves B its own rows."""

    def test_control_unauthenticated_request_is_refused(self, tenants):
        anonymous = APIClient(enforce_csrf_checks=True)

        response = anonymous.get(EXPERIMENTS_URL)

        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ), response.content

    def test_control_workspace_b_reads_and_writes_its_own_rows(self, tenants):
        client, own = tenants.client, tenants.b_rows

        for url, row in [
            (EXPERIMENTS_URL, own.experiment),
            (CONFIG_URL, own.config),
            (LABELS_URL, own.label),
            (KB_URL, own.knowledge_base),
        ]:
            assert str(row.id) in _body(client.get(url)), url

        created = client.post(
            CONFIG_URL,
            {
                "name": "own conformance config",
                "project": str(own.project.id),
                "eval_template": str(own.template.id),
                "kb_id": str(own.kb_file.id),
                "config": {},
                "mapping": MAPPING,
            },
            format="json",
        )
        assert created.status_code == status.HTTP_200_OK, created.content

        patched = client.patch(
            f"{TASK_URL}{own.task.id}/",
            {"evals": [str(own.other_config.id)]},
            format="json",
        )
        assert patched.status_code == status.HTTP_200_OK, patched.content
        assert _task_evals(own.task) == {own.other_config.id}

        label = client.post(
            LABELS_URL, _label_body(own.project.id, "own label"), format="json"
        )
        assert label.status_code == status.HTTP_200_OK, label.content

    def test_control_default_workspace_keeps_its_no_workspace_rows(self, tenants):
        """B's default workspace still lists B's rows that have no workspace:
        an experiment on a dataset with no workspace, and an eval config on a
        project with no workspace."""
        client, own = tenants.client, tenants.b_rows

        experiments = _body(client.get(EXPERIMENTS_URL))
        configs = _body(client.get(CONFIG_URL))

        assert str(own.experiment.id) in experiments
        assert str(own.no_workspace_experiment.id) in experiments
        assert str(own.config.id) in configs
        assert str(own.no_workspace_config.id) in configs

    def test_control_workspace_b_puts_and_monitor_with_its_own_rows(self, tenants):
        client, own = tenants.client, tenants.b_rows

        task_put = client.put(
            f"{TASK_URL}{own.task.id}/",
            _task_body(own.project.id, own.other_config.id, own.task.name),
            format="json",
        )
        assert task_put.status_code == status.HTTP_200_OK, task_put.content
        assert _task_evals(own.task) == {own.other_config.id}

        config_put = client.put(
            f"{CONFIG_URL}{own.config.id}/",
            {
                "name": own.config.name,
                "project": str(own.no_workspace_project.id),
                "eval_template": str(own.template.id),
                "kb_id": str(own.kb_file.id),
                "config": {},
                "mapping": MAPPING,
            },
            format="json",
        )
        assert config_put.status_code == status.HTTP_200_OK, config_put.content

        monitor = client.post(
            MONITOR_URL,
            {"project": str(own.project.id), "name": "own monitor", **MONITOR_BODY},
            format="json",
        )
        assert monitor.status_code == status.HTTP_200_OK, monitor.content

    def test_control_workspace_b_keeps_its_alert_logs_and_prompt_folders(self, tenants):
        """B still lists and resolves its own alert logs, and lists, creates
        and renames its own prompt folders."""
        client, own = tenants.client, tenants.b_rows

        assert str(own.monitor_log.id) in _body(client.get(MONITOR_LOG_URL))
        resolved = client.post(
            MONITOR_LOG_RESOLVE_URL,
            {"log_ids": [str(own.monitor_log.id)]},
            format="json",
        )
        assert resolved.status_code == status.HTTP_200_OK, resolved.content
        own.monitor_log.refresh_from_db()
        assert own.monitor_log.resolved is True

        assert str(own.prompt_folder.id) in _body(client.get(PROMPT_FOLDERS_URL))
        created = client.post(
            PROMPT_FOLDERS_URL,
            {"name": "own folder", "parent_folder": str(own.prompt_folder.id)},
            format="json",
        )
        assert created.status_code in (
            status.HTTP_200_OK,
            status.HTTP_201_CREATED,
        ), created.content
        folder = PromptFolder.no_workspace_objects.get(name="own folder")
        assert folder.organization_id == tenants.b.organization.id
        assert folder.parent_folder_id == own.prompt_folder.id
        renamed = client.patch(
            f"{PROMPT_FOLDERS_URL}{own.prompt_folder.id}/",
            {"name": "own renamed folder"},
            format="json",
        )
        assert renamed.status_code == status.HTTP_200_OK, renamed.content

    def test_control_monitor_create_naming_a_project_outside_b_is_rejected(
        self, tenants
    ):
        """A control, not a conformance test: plain dev and plain main already
        limit the monitor's project to the caller's observe projects there
        (UserAlertMonitorView.get_serializer). It shows the same answer now
        that UserAlertMonitorSerializer does the limiting."""
        _check_rejected(
            tenants.client, _monitor_create(tenants), status.HTTP_400_BAD_REQUEST
        )
