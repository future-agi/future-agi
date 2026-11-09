"""Admit experiment agents only through an authorized GraphVersion binding.

GraphVersion is the existing experiment target contract, independent of the
Console feature flag. No Console-version mapping exists here. An unresolved
version (including an inaccessible one) is refused identically, without an
unscoped existence probe. Neither UUID shape nor a caller's kind flag is proof.
"""

from types import SimpleNamespace
from uuid import UUID

from rest_framework import status
from rest_framework.exceptions import PermissionDenied

_NO_ROW = object()
_NO_DATASET = object()
ERROR_CODE = "tx1b_not_qualified"
MESSAGE = "Experiment target or execution context not found or unavailable."


class AgentTargetNotQualified(PermissionDenied):
    """Typed admission refusal; workers return FAILED rather than retrying it."""

    default_detail = MESSAGE
    default_code = ERROR_CODE


def refusal_response(error=None):
    from tfc.utils.general_methods import GeneralMethods

    return GeneralMethods().custom_error_response(
        status.HTTP_403_FORBIDDEN,
        str(error.detail) if error is not None else MESSAGE,
        code=ERROR_CODE,
    )


def worker_error(error):
    """Keep the typed reason in the existing activity error-string contract."""
    if isinstance(error, AgentTargetNotQualified):
        return f"{ERROR_CODE}: {error.detail}"
    return str(error)


def _uuid(value):
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError) as error:
        raise AgentTargetNotQualified() from error


def _is_agent_entry(entry):
    return bool(entry.get("agent_id")) or bool(entry.get("agent_version"))


def resolve_graph_version(graph_versions, graph_id, version_id):
    """Resolve only inside a caller-authorized queryset, never probe outside it."""
    version = graph_versions.filter(
        pk=_uuid(version_id), deleted=False, graph__deleted=False
    ).first()
    if version is None:
        raise AgentTargetNotQualified()
    if version.graph_id != _uuid(graph_id):
        raise AgentTargetNotQualified(
            "Selected agent version does not belong to the selected agent."
        )
    # Historical inactive versions are executable experiment pins in v1.56.1;
    # only draft/invalid states are refused, not an older published version.
    if version.status not in ("active", "inactive"):
        raise AgentTargetNotQualified(
            "Selected agent version is in draft state and cannot be used in experiments."
            if version.status == "draft"
            else "Selected agent version is not executable."
        )
    return version


def create_entries_admitted(entries, *, graph_versions, dataset=None, user=None):
    agents = [entry for entry in entries or [] if _is_agent_entry(entry)]
    execution_versions = (
        _graph_versions(_execution_context(dataset, user))
        if agents and dataset is not None
        else None
    )
    for entry in agents:
        resolve_graph_version(
            graph_versions, entry.get("agent_id"), entry.get("agent_version")
        )
        if execution_versions is not None:
            resolve_graph_version(
                execution_versions, entry.get("agent_id"), entry.get("agent_version")
            )
    return True


def _live_agent_configs(experiment_ids):
    from model_hub.models.experiments import ExperimentAgentConfig

    return ExperimentAgentConfig.no_workspace_objects.filter(
        experiment_dataset__experiment_id__in=[_uuid(x) for x in experiment_ids],
        experiment_dataset__deleted=False,
    )


def update_entries_admitted(experiment, entries, *, graph_versions):
    """Validate before any update write; removals require no agent execution."""
    stored_ids = {
        str(x)
        for x in _live_agent_configs([experiment.id]).values_list("id", flat=True)
    }
    for entry in entries or []:
        if _is_agent_entry(entry) and entry.get("id") is not None:
            if str(entry["id"]) not in stored_ids:
                raise AgentTargetNotQualified()
    return create_entries_admitted(
        entries,
        graph_versions=graph_versions,
        dataset=experiment.dataset,
        user=experiment.user,
    )


def _execution_context(source, user):
    """Reconstruct execution scope without ambient scope or workspace creation.

    Only an explicit NULL source workspace uses the organization's live default.
    A non-NULL workspace that has been revoked/deleted must never fall back.
    Membership and default-workspace NULL-row semantics remain canonical.
    """
    from accounts.models.workspace import Workspace
    from model_hub.models.develop_dataset import Dataset
    from tfc.utils.tenant_scope import scope_to_caller

    if not getattr(user, "is_active", False) or getattr(user, "deleted", False):
        raise AgentTargetNotQualified()
    workspace = source.workspace
    if source.workspace_id is None:
        workspace = (
            Workspace.no_workspace_objects.filter(
                organization_id=source.organization_id, is_default=True, is_active=True
            )
            .order_by("pk")
            .first()
        )
    context = SimpleNamespace(
        user=user, organization=source.organization, workspace=workspace
    )
    if not scope_to_caller(
        Dataset.no_workspace_objects.filter(pk=source.pk), context
    ).exists():
        raise AgentTargetNotQualified()
    return context


def _experiment_context(experiment_id, *, request=None, dataset_id=_NO_DATASET):
    """Workers use the persisted execution owner, not a client-supplied actor.

    API callers additionally supply their request. Dataset and snapshot must
    remain live and bound to the same tenant, and current workspace access is
    rechecked by the canonical scope helper.
    """
    from model_hub.models.experiments import ExperimentsTable
    from tfc.utils.tenant_scope import scope_to_caller

    query = ExperimentsTable.no_workspace_objects.filter(
        pk=_uuid(experiment_id),
        dataset__deleted=False,
        snapshot_dataset__deleted=False,
    ).select_related(
        "dataset__workspace", "dataset__organization", "snapshot_dataset", "user"
    )
    if request is not None:
        query = scope_to_caller(
            query,
            request,
            organization_field="dataset__organization",
            workspace_field="dataset__workspace",
        )
    experiment = query.first()
    if experiment is None:
        raise AgentTargetNotQualified()
    snapshot, source = experiment.snapshot_dataset, experiment.dataset
    if (
        snapshot.organization_id != source.organization_id
        or snapshot.workspace_id != source.workspace_id
        or (dataset_id is not _NO_DATASET and snapshot.pk != _uuid(dataset_id))
    ):
        raise AgentTargetNotQualified()
    if request is not None and (
        not getattr(request.user, "is_active", False)
        or getattr(request.user, "deleted", False)
    ):
        raise AgentTargetNotQualified()
    # API preflight must enforce the same current stored owner as dispatch,
    # in addition to (never instead of) the requesting operator's scope.
    return experiment, _execution_context(source, experiment.user)


def _graph_versions(context):
    from agent_playground.models import GraphVersion
    from tfc.utils.tenant_scope import scope_to_caller

    return scope_to_caller(
        GraphVersion.no_workspace_objects.select_related("graph"),
        context,
        organization_field="graph__organization",
        workspace_field="graph__workspace",
    )


def _validated_configs(experiment, context, config_ids):
    wanted = {_uuid(x) for x in config_ids}
    configs = list(
        _live_agent_configs([experiment.pk])
        .filter(pk__in=wanted)
        .select_related(
            "experiment_dataset",
            "experiment_dataset__experiment",
        )
    )
    if {config.pk for config in configs} != wanted:
        raise AgentTargetNotQualified()
    versions = _graph_versions(context)
    for config in configs:
        # Cache only the version returned by the scoped resolution. Never join
        # an unvalidated graph relation merely to classify its target kind.
        config.graph_version = resolve_graph_version(
            versions, config.graph_id, config.graph_version_id
        )
    return configs


def _validate_rows(experiment, row_ids):
    from model_hub.models.develop_dataset import Row

    if row_ids:
        wanted = {_uuid(x) for x in row_ids}
        found = set(
            Row.no_workspace_objects.filter(
                pk__in=wanted,
                dataset_id=experiment.snapshot_dataset_id,
            ).values_list("id", flat=True)
        )
        if found != wanted:
            raise AgentTargetNotQualified()


def ensure_agent_configs_admitted(
    agent_config_ids,
    *,
    experiment_id,
    dataset_id=_NO_DATASET,
    row_ids=None,
    request=None,
    eval_only=False,
):
    """Eval-only and prompt-only work execute no agent and need no agent gate."""
    if eval_only or not agent_config_ids:
        return
    experiment, context = _experiment_context(
        experiment_id,
        request=request,
        dataset_id=dataset_id,
    )
    if request is not None:
        _validated_configs(experiment, request, agent_config_ids)
    _validated_configs(experiment, context, agent_config_ids)
    _validate_rows(experiment, row_ids)


def agent_configs_admitted(agent_config_ids, **context):
    ensure_agent_configs_admitted(agent_config_ids, **context)
    return True


def experiments_admitted(experiment_ids, *, request):
    for experiment_id in experiment_ids:
        ensure_v2_run_admitted(experiment_id, request=request)
    return True


def v2_run_agent_config_ids(
    experiment_id,
    *,
    rerun_prompt_config_ids=None,
    rerun_agent_config_ids=None,
    rerun_eval_template_ids=None,
    column_changed=False,
):
    """Exactly the agent selection; unknown explicit IDs must not vanish."""
    selective = bool(
        rerun_prompt_config_ids or rerun_agent_config_ids or rerun_eval_template_ids
    )
    if (column_changed and not selective) or (selective and not rerun_agent_config_ids):
        return []
    if selective:
        return list(rerun_agent_config_ids)
    return list(_live_agent_configs([experiment_id]).values_list("id", flat=True))


def ensure_v2_run_admitted(experiment_id, *, request=None, **selectors):
    ensure_agent_configs_admitted(
        v2_run_agent_config_ids(experiment_id, **selectors),
        experiment_id=experiment_id,
        request=request,
    )


def ensure_runs_resumable(runs, *, experiment_id, request):
    """Revalidate selected stored targets and their exact run-control binding."""
    configs = list(
        _live_agent_configs([experiment_id]).filter(
            experiment_dataset_id__in=[_uuid(run["run_id"]) for run in runs],
        )
    )
    by_run = {str(config.experiment_dataset_id): config for config in configs}
    for run in runs:
        config = by_run.get(str(run["run_id"]))
        binding = run["binding"]
        if binding is not None and (binding.kind == "agent" or config is not None):
            if (
                config is None
                or binding.kind != "agent"
                or binding.config_id != config.pk
                or str(binding.experiment_dataset_id) != str(run["run_id"])
            ):
                raise AgentTargetNotQualified()
    ensure_agent_configs_admitted(
        [config.pk for config in configs],
        experiment_id=experiment_id,
        request=request,
    )


def ensure_agent_worker_admitted(
    *,
    experiment_id,
    dataset_id,
    experiment_agent_config_id,
    graph_version_id=None,
    row_id=_NO_ROW,
    row_ids=None,
):
    """Return a validated persisted EAC before setup or row preparation writes.

    Old prepare payloads omit the EAC ID. Resolve a unique stored binding in
    the owning experiment instead; an empty ID alone grants nothing. Ambiguous
    bindings cannot safely select a per-variant budget and are refused.
    """
    experiment, context = _experiment_context(experiment_id, dataset_id=dataset_id)
    if experiment_agent_config_id:
        ids = [_uuid(experiment_agent_config_id)]
    elif graph_version_id:
        ids = list(
            _live_agent_configs([experiment.pk])
            .filter(
                graph_version_id=_uuid(graph_version_id),
            )
            .values_list("id", flat=True)[:2]
        )
        if len(ids) != 1:
            raise AgentTargetNotQualified()
    else:
        raise AgentTargetNotQualified()
    config = _validated_configs(experiment, context, ids)[0]
    if graph_version_id is not None or row_id is not _NO_ROW:
        if config.graph_version_id != _uuid(graph_version_id):
            raise AgentTargetNotQualified()
    _validate_rows(experiment, [row_id] if row_id is not _NO_ROW else row_ids)
    return config
