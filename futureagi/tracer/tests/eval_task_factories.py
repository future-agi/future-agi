"""Shared object factories for the eval-task test suites.

``test_eval_task_usage_date_range`` and ``test_eval_task_aggregations`` both
build the same EvalTemplate -> CustomEvalConfig -> EvalTask -> EvalLogger
chain. They had grown separate copies that then drifted -- one parameterised
the template's output type, the other backdated ``created_at`` -- so these are
the superset: every caller passes what it needs and ignores the rest. The
eval-task, eval-config and AI-tool scope suites share the project and config
builders here, including the projects a caller cannot use.

A plain module rather than ``conftest.py`` fixtures: these are constructors
taking keyword arguments that vary per call, which a fixture would have to
wrap in a factory callable anyway, and importing from a conftest is a pattern
pytest discourages.
"""

import uuid

from accounts.models import Organization
from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from model_hub.models.evals_metric import EvalTemplate
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.eval_task import EvalTask, EvalTaskStatus, RunType
from tracer.models.observation_span import (
    EvalLogger,
    EvalTargetType,
    ObservationSpan,
)
from tracer.models.project import Project

# Config payload per normalized output type, mirroring what the eval builder
# writes -- the aggregation paths branch on both fields.
_TEMPLATE_OUTPUT = {
    "pass_fail": "Pass/Fail",
    "percentage": "score",
    "deterministic": "choices",
}


def make_template(
    *, organization, workspace, output_type_normalized="pass_fail", name=None
):
    return EvalTemplate.objects.create(
        name=name or f"Template ({output_type_normalized})",
        description="",
        organization=organization,
        workspace=workspace,
        output_type_normalized=output_type_normalized,
        config={"output": _TEMPLATE_OUTPUT[output_type_normalized]},
    )


def make_config(*, project, template, name):
    return CustomEvalConfig.objects.create(
        name=name,
        project=project,
        eval_template=template,
        config={},
        mapping={},
        filters={},
    )


def make_sibling_project(project, name="Sibling Project"):
    """Another project of ``project``'s workspace, so the caller can see it."""
    return Project.objects.create(
        name=name,
        organization=project.organization,
        workspace=project.workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )


OUT_OF_SCOPE_KINDS = ["other_organization", "other_workspace", "deleted_project"]


def make_out_of_scope_project(kind, project, user):
    """A project a caller of ``project``'s workspace cannot use: one of another
    organization in no workspace, so only the organization filter keeps it
    out; one in another workspace of the same organization; or a deleted
    project of the caller's own workspace."""
    if kind == "other_organization":
        return Project.objects.create(
            name="Other Organization Project",
            organization=Organization.objects.create(name="Other Organization"),
            workspace=None,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
        )
    if kind == "other_workspace":
        return Project.objects.create(
            name="Other Workspace Project",
            organization=project.organization,
            workspace=Workspace.objects.create(
                name="Other Workspace",
                organization=project.organization,
                is_default=False,
                is_active=True,
                created_by=user,
            ),
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
        )
    deleted = make_sibling_project(project, "Deleted Sibling Project")
    Project.all_objects.filter(id=deleted.id).update(deleted=True)
    return deleted


def refusal_without_id(response, named_id):
    """A refusal's status and body with the id it names masked, so the answer
    for one id can be compared with the answer for another."""
    body = response.content.decode() if response.content else ""
    return response.status_code, body.replace(str(named_id), "<id>")


def make_task(*, project, name="Eval task"):
    return EvalTask.objects.create(
        project=project,
        name=name,
        filters={},
        sampling_rate=100,
        run_type=RunType.CONTINUOUS,
        status=EvalTaskStatus.PENDING,
        spans_limit=100,
    )


def make_fresh_span(base, *, name="eval span"):
    """A new span sharing base's trace/project — one eval row per span, so
    live rows don't collide on the eval_logger_live_span_uniq
    (task, span, cfg) partial unique constraint."""
    return ObservationSpan.objects.create(
        id=f"span_{uuid.uuid4().hex[:16]}",
        project=base.project,
        trace=base.trace,
        name=name,
        observation_type="llm",
        start_time=base.start_time,
        end_time=base.end_time,
    )


def make_row(*, span, cfg, task, created_at=None, **kwargs):
    """One eval run, optionally backdated.

    ``created_at`` is ``auto_now_add``, so it can only be set after the fact
    via ``.update()``; callers that don't care about the timestamp omit it and
    skip the extra queries.
    """
    row = EvalLogger.objects.create(
        target_type=EvalTargetType.SPAN,
        observation_span=span,
        trace=span.trace,
        custom_eval_config=cfg,
        eval_task_id=str(task.id),
        **kwargs,
    )
    if created_at is not None:
        EvalLogger.objects.filter(id=row.id).update(created_at=created_at)
        row.refresh_from_db()
    return row
