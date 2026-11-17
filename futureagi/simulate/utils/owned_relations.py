"""Ownership predicates for simulation relations, applied before hydration.

A nullable run relation is not authority for its target. Graphs have their own
organization and inherit workspace from their scenario. Callers supply an
already-authorized organization/workspace, never a tenant inferred from a graph.
"""

from copy import copy

from django.db.models import F, FilteredRelation, OuterRef, Q, Subquery

from simulate.models import CallExecution, Scenarios
from simulate.models.scenario_graph import ScenarioGraph

_RUN_RELATIONS = frozenset({"agent_definition", "simulator_agent"})


def with_owned_run_relation(queryset, relation, *, fields=None):
    """LEFT JOIN an owned nullable target in the same query as its run.

    Runs with a workspace require that exact workspace; legacy workspace-less
    runs still require organization equality. A foreign target becomes NULL,
    not an absent run. Historical soft-deleted local targets retain their old
    read semantics. ``fields`` projects list-card scalars without hydrating the
    target or selecting its large/private fields (and permits root ``only``).
    """
    if relation not in _RUN_RELATIONS:
        raise ValueError("Unsupported run relation")
    alias = f"_owned_{relation}"
    queryset = queryset.annotate(
        **{
            alias: FilteredRelation(
                relation,
                condition=(
                    Q(**{f"{relation}__organization_id": F("organization_id")})
                    & (
                        Q(workspace_id__isnull=True)
                        | Q(**{f"{relation}__workspace_id": F("workspace_id")})
                    )
                ),
            )
        }
    )
    if fields is not None:
        return queryset.annotate(
            **{f"{alias}_{field}": F(f"{alias}__{field}") for field in fields}
        )
    return queryset.select_related(alias)


def owned_run_for_serialization(run, *relations):
    """Use safe joined targets on a copy, including their raw PK fields.

    Never mutate the persisted model's FK/cache during a read. Public query
    builders install the joins above, so serialization issues no relation query.
    Standalone/internal serializers without those joins use one scoped lookup,
    never an unguarded FK traversal, and preserve same-owner historical targets.
    """
    result = copy(run)
    result._state = copy(run._state)
    result._state.fields_cache = run._state.fields_cache.copy()
    for relation in relations:
        if relation not in _RUN_RELATIONS:
            raise ValueError("Unsupported run relation")
        alias = f"_owned_{relation}"
        if hasattr(run, alias):
            related = getattr(run, alias)
        else:
            field = run._meta.get_field(relation)
            related_id = getattr(run, field.attname)
            related = None
            if related_id is not None and run.organization_id is not None:
                query = field.related_model._base_manager.filter(
                    pk=related_id, organization_id=run.organization_id
                )
                if run.workspace_id is not None:
                    query = query.filter(workspace_id=run.workspace_id)
                related = query.first()
        setattr(result, relation, related)
    return result


def owned_scenarios(*, organization_id, workspace_id):
    """Exact caller-tenant scenarios without relying on ambient manager scope.

    The unfiltered base manager retains soft-deleted historical scenarios;
    live-only readers add deleted=False explicitly. Missing organization fails
    closed; a NULL workspace is exact NULL, never a wildcard.
    """
    if organization_id is None:
        return Scenarios._base_manager.none()
    return Scenarios._base_manager.filter(
        organization_id=organization_id, workspace_id=workspace_id
    )


def owned_call_scenario_map(call_ids, *, caller_tenant=None):
    """Project scenario id/name for a batch without hydrating scenario objects.

    The call's own run organization is always the ownership floor. Public readers
    must authorize roots first, then supply (organization_id, workspace_id). Their
    default-workspace root guard admits legacy NULL-workspace runs; those runs
    still require exact caller scenario scope here. Without caller scope, a NULL
    run workspace requires a NULL scenario workspace, never a wildcard. Missing
    entries are authoritative NULL projections, not permission to follow a raw
    scenario FK. Historical same-tenant scenarios remain readable; no FK changes.
    """
    if not call_ids:
        return {}
    calls = CallExecution._base_manager.filter(
        pk__in=call_ids,
        scenario__organization_id=F("test_execution__run_test__organization_id"),
    )
    legacy_workspace = Q(test_execution__run_test__workspace_id__isnull=True)
    if caller_tenant is not None:
        organization_id, workspace_id = caller_tenant
        if organization_id is None:
            return {}
        calls = calls.filter(
            scenario__organization_id=organization_id,
            scenario__workspace_id=workspace_id,
        )
    else:
        legacy_workspace &= Q(scenario__workspace_id__isnull=True)
    calls = calls.filter(
        Q(scenario__workspace_id=F("test_execution__run_test__workspace_id"))
        | legacy_workspace
    )
    return {
        str(call_id): {"id": str(scenario_id), "name": name}
        for call_id, scenario_id, name in calls.values_list(
            "pk", "scenario_id", "scenario__name"
        )
    }


def owned_scenario_graphs(*, organization_id, workspace_id):
    """Unhydrated graphs owned by both their scenario and the caller's tenant.

    ``workspace_id=None`` means exact NULL, never all workspaces. A missing
    organization fails closed. This deliberately leaves active/latest selection
    to the reader: the scenario task's existing ``get`` semantics are retained.
    """
    if organization_id is None:
        return ScenarioGraph.objects.none()
    return ScenarioGraph.objects.filter(
        organization_id=F("scenario__organization_id"),
        scenario__organization_id=organization_id,
        scenario__workspace_id=workspace_id,
        deleted=False,
    )


def owned_scenarios_with_active_graph(*, organization_id, workspace_id):
    """Project graph scalars in the scoped scenario query, not a graph query.

    The detail reader already loads scenarios. Correlated scalar subqueries
    preserve its graph payload without evaluating a ScenarioGraph queryset or
    hydrating graph objects. Apply ownership before newest-active selection,
    exactly as the other readers do; historical local scenarios remain readable.
    """
    graphs = (
        owned_scenario_graphs(
            organization_id=organization_id, workspace_id=workspace_id
        )
        .filter(scenario_id=OuterRef("pk"), is_active=True)
        # Keep the two scalar projections on the same graph when times tie.
        .order_by("-created_at", "-pk")
    )
    return owned_scenarios(
        organization_id=organization_id, workspace_id=workspace_id
    ).annotate(
        _owned_graph_id=Subquery(graphs.values("pk")[:1]),
        _owned_graph_config=Subquery(graphs.values("graph_config")[:1]),
    )


def owned_active_graph_map(scenario_ids, *, organization_id, workspace_id):
    """One filtered query per page, with the newest owned graph per scenario.

    Filter ownership before selecting latest: a newer foreign graph must not
    displace an older owned one. Empty entries are represented by a missing key.
    """
    return {
        str(graph.scenario_id): graph
        for graph in owned_scenario_graphs(
            organization_id=organization_id, workspace_id=workspace_id
        )
        .filter(scenario_id__in=scenario_ids, is_active=True)
        .order_by("scenario_id", "-created_at")
        .distinct("scenario_id")
    }
