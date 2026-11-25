import uuid

import structlog
from django.core.exceptions import ValidationError
from django.db import DatabaseError, models
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet

from accounts.models.user import User
from accounts.utils import get_request_organization
from tfc.middleware.workspace_context import (
    get_current_organization,
    get_current_user,
    get_current_workspace,
    set_workspace_context,
)
from tfc.utils.api_contracts import validated_request
from tfc.utils.base_viewset import BaseModelViewSetMixin
from tfc.utils.general_methods import GeneralMethods
from tracer.models.shared_link import (
    AccessType,
    ResourceType,
    SharedLink,
    SharedLinkAccess,
)
from tracer.serializers.feed import (
    FeedDetailCoreSerializer,
    FeedSidebarApiResponseSerializer,
    FeedSidebarQuerySerializer,
    FeedSidebarSerializer,
    OverviewResponseSerializer,
    TracesTabResponseSerializer,
    TrendsTabResponseSerializer,
)
from tracer.serializers.shared_link import (
    AddAccessSerializer,
    SharedLinkAccessSerializer,
    SharedLinkCreateSerializer,
    SharedLinkDetailSerializer,
    SharedLinkListSerializer,
    SharedLinkResolveErrorSerializer,
    SharedLinkResolveResponseSerializer,
    SharedLinkUpdateSerializer,
)
from tracer.serializers.trace import TraceDetailResponseSerializer

logger = structlog.get_logger(__name__)
_gm = GeneralMethods()

SUPPORTED_SHARED_RESOURCE_TYPES = {
    ResourceType.TRACE.value,
    ResourceType.DASHBOARD.value,
    ResourceType.PROJECT.value,
    ResourceType.CALL_EXECUTION.value,
    ResourceType.ERROR_FEED_ISSUE.value,
}


class SharedLinkViewSet(BaseModelViewSetMixin, ModelViewSet):
    """CRUD for shared links. Requires authentication."""

    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]
    serializer_class = SharedLinkListSerializer
    queryset = SharedLink.objects.all()

    def get_queryset(self):
        qs = super().get_queryset()
        # Filter by resource if query params provided
        resource_type = self.request.query_params.get("resource_type")
        resource_id = self.request.query_params.get("resource_id")
        if resource_type:
            qs = qs.filter(resource_type=resource_type)
        if resource_id:
            qs = qs.filter(resource_id=resource_id)
        return qs

    def list(self, request, *args, **kwargs):
        qs = self.filter_queryset(self.get_queryset())
        serializer = self.get_serializer(qs, many=True)
        return self._gm.success_response(serializer.data)

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = SharedLinkDetailSerializer(instance, context={"request": request})
        return self._gm.success_response(serializer.data)

    def get_serializer_class(self):
        if self.action == "create":
            return SharedLinkCreateSerializer
        if self.action in {"update", "partial_update"}:
            return SharedLinkUpdateSerializer
        if self.action == "add_access":
            return AddAccessSerializer
        if self.action == "retrieve":
            return SharedLinkDetailSerializer
        return SharedLinkListSerializer

    def create(self, request, *args, **kwargs):
        serializer = SharedLinkCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if data["resource_type"] not in SUPPORTED_SHARED_RESOURCE_TYPES:
            return self._gm.bad_request("This resource type cannot be shared yet")

        organization = get_request_organization(request)
        workspace = getattr(request, "workspace", None)

        if not _shared_resource_exists(
            data["resource_type"],
            data["resource_id"],
            organization,
            workspace,
        ):
            return self._gm.not_found("Shared resource not found")

        link = SharedLink.objects.create(
            resource_type=data["resource_type"],
            resource_id=data["resource_id"],
            access_type=data.get("access_type", AccessType.RESTRICTED),
            expires_at=data.get("expires_at"),
            created_by=request.user,
            organization=organization,
            workspace=workspace,
        )

        # Add ACL entries if provided
        emails = data.get("emails", [])
        for email in emails:
            _grant_access(link, email, request.user)

        return self._gm.success_response(
            SharedLinkDetailSerializer(link, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )

    def partial_update(self, request, *args, **kwargs):
        link = self.get_object()
        serializer = SharedLinkUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        for field, value in serializer.validated_data.items():
            setattr(link, field, value)
        link.save()

        return self._gm.success_response(
            SharedLinkDetailSerializer(link, context={"request": request}).data
        )

    def update(self, request, *args, **kwargs):
        return self.partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        link = self.get_object()
        link.is_active = False
        link.save(update_fields=["is_active"])
        return self._gm.success_response({"message": "Link revoked"})

    @action(detail=True, methods=["post"], url_path="access")
    def add_access(self, request, pk=None):
        """Add email(s) to the ACL of a shared link."""
        link = self.get_object()
        serializer = AddAccessSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        created = []
        for email in serializer.validated_data["emails"]:
            obj, was_created = _grant_access(link, email, request.user)
            if was_created:
                created.append(SharedLinkAccessSerializer(obj).data)

        return self._gm.success_response(created, status=status.HTTP_201_CREATED)

    @action(
        detail=True,
        methods=["delete"],
        url_path="access/(?P<access_id>[^/.]+)",
    )
    def remove_access(self, request, pk=None, access_id=None):
        """Remove an email from the ACL."""
        link = self.get_object()
        try:
            entry = link.access_list.get(id=access_id, deleted=False)
            entry.delete()
        except SharedLinkAccess.DoesNotExist:
            return self._gm.not_found("Access entry not found")
        return self._gm.success_response({"message": "Access removed"})


# --------------------------------------------------------------------------
# Public token-resolve endpoint (no auth required for public links)
# --------------------------------------------------------------------------


def _check_shared_link_access(request, token):
    """Load a share token's link and apply its access rules.

    Returns ``(link, None)`` when the caller may read it, else
    ``(None, error_response)``.
    """
    try:
        link = _get_shared_link_by_token(token)
    except SharedLink.DoesNotExist:
        return None, _gm.not_found("Shared link not found")
    except DatabaseError:
        logger.exception("shared_link_resolve_database_unavailable")
        return None, _gm.custom_error_response(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Shared link resolver is temporarily unavailable.",
            code="service_unavailable",
        )

    if not link.is_accessible:
        reason = "expired" if link.is_expired else "revoked"
        return None, _gm.custom_error_response(
            status.HTTP_410_GONE,
            f"This shared link has been {reason}",
            code="gone",
        )

    # Check access for restricted links
    if link.access_type == AccessType.RESTRICTED:
        if not request.user or not request.user.is_authenticated:
            return None, _gm.custom_error_response(
                status.HTTP_401_UNAUTHORIZED,
                "Authentication required to access this link",
                code="not_authenticated",
            )
        has_access = link.access_list.filter(
            email__iexact=request.user.email, deleted=False
        ).exists()
        # Also allow the creator
        if not has_access and request.user != link.created_by:
            return None, _gm.forbidden_response(
                "You don't have access to this shared resource"
            )

    return link, None


@swagger_auto_schema(
    method="get",
    responses={
        200: SharedLinkResolveResponseSerializer,
        401: SharedLinkResolveErrorSerializer,
        403: SharedLinkResolveErrorSerializer,
        404: SharedLinkResolveErrorSerializer,
        410: SharedLinkResolveErrorSerializer,
    },
)
@api_view(["GET"])
@permission_classes([AllowAny])
def resolve_shared_link(request, token):
    """
    Resolve a share token to the underlying resource data.
    - Public links: no auth needed
    - Restricted links: user must be authenticated + email in ACL
    """
    link, error = _check_shared_link_access(request, token)
    if error is not None:
        return error

    # Resolve the resource
    resource_data = _resolve_resource(link)
    if resource_data is None:
        return _gm.not_found("The shared resource no longer exists")

    return Response(
        {
            "resource_type": link.resource_type,
            "resource_id": link.resource_id,
            "access_type": link.access_type,
            "data": resource_data,
        }
    )


@swagger_auto_schema(
    method="get",
    responses={
        200: TraceDetailResponseSerializer,
        401: SharedLinkResolveErrorSerializer,
        403: SharedLinkResolveErrorSerializer,
        404: SharedLinkResolveErrorSerializer,
        410: SharedLinkResolveErrorSerializer,
    },
)
@api_view(["GET"])
@permission_classes([AllowAny])
def resolve_shared_issue_trace(request, token, trace_id):
    """One trace of a shared Error Feed issue, under the issue link's rules.

    Only traces linked to the shared issue are readable through its token.
    """
    link, error = _check_shared_link_access(request, token)
    if error is not None:
        return error
    if link.resource_type != ResourceType.ERROR_FEED_ISSUE.value:
        return _gm.not_found("Shared trace not found")

    try:
        detail = _in_link_workspace(
            link, _resolve_shared_issue_trace, request, link, trace_id
        )
    except Exception:
        logger.exception(
            "shared_issue_trace_failed", link_id=str(link.id), trace_id=trace_id
        )
        detail = None
    if detail is None:
        return _gm.not_found("Shared trace not found")
    return _gm.success_response(detail)


class SharedIssueSidebarView(APIView):
    """A shared Error Feed issue's sidebar, scoped to one of its traces.

    Mirrors the issue page: AI metadata and evaluations follow the trace
    picked in Traces affected (``?trace_id=``).
    """

    permission_classes = [AllowAny]

    @validated_request(
        query_serializer=FeedSidebarQuerySerializer,
        responses={
            200: FeedSidebarApiResponseSerializer,
            401: SharedLinkResolveErrorSerializer,
            403: SharedLinkResolveErrorSerializer,
            404: SharedLinkResolveErrorSerializer,
            410: SharedLinkResolveErrorSerializer,
        },
    )
    def get(self, request, token):
        from tracer.utils import feed as feed_service

        link, error = _check_shared_link_access(request, token)
        if error is not None:
            return error
        if link.resource_type != ResourceType.ERROR_FEED_ISSUE.value:
            return _gm.not_found("Shared sidebar not found")

        trace_id = request.validated_query_data.get("trace_id") or None

        def _build():
            scope = _shared_issue_scope(link, trace_id)
            if scope is None:
                return None
            cluster_id, project_ids, _ = scope
            result = feed_service.get_sidebar(
                cluster_id, project_ids, trace_id=trace_id
            )
            return FeedSidebarSerializer(result).data if result is not None else None

        try:
            data = _in_link_workspace(link, _build)
        except Exception:
            logger.exception("shared_issue_sidebar_failed", link_id=str(link.id))
            data = None
        if data is None:
            return _gm.not_found("Shared sidebar not found")
        return _gm.success_response(data)


def _shared_issue_scope(link, trace_id=None):
    """The shared issue behind a link, as ``(cluster_id, project_ids, group)``.

    ``None`` when the sharer lost the licence, the issue is gone, or
    ``trace_id`` is given and is neither one of the issue's traces nor its
    working (passing) trace.
    """
    from tfc.ee_gating import EEFeature, FeatureUnavailable, check_ee_feature
    from tracer.models.trace_error_analysis import TraceErrorGroup
    from tracer.queries.feed import _cluster_has_trace
    from tracer.queries.grouping_redirect import resolve_issue_redirect

    try:
        check_ee_feature(EEFeature.ERROR_FEED, org_id=str(link.organization_id))
    except FeatureUnavailable:
        return None

    project_ids = _shared_feed_project_ids(link.organization, link.workspace)
    if not project_ids:
        return None
    redirect = resolve_issue_redirect(link.resource_id, project_ids)
    if redirect is None:
        return None
    cluster_id = redirect["resolved_cluster_id"]
    group = TraceErrorGroup.objects.filter(
        cluster_id=cluster_id,
        project_id__in=project_ids,
        deleted=False,
        target_type="error_feed",
    ).first()
    if group is None:
        return None
    if (
        trace_id
        and not _cluster_has_trace(cluster_id, trace_id, project_ids)
        and str(trace_id) != _shared_issue_working_trace_id(group, project_ids)
    ):
        return None
    return cluster_id, project_ids, group


def _shared_issue_working_trace_id(group, project_ids):
    """The passing trace the issue page compares against, or ``None``.

    It is not one of the issue's traces by construction, so the shared trace
    read admits this one id besides the members.
    """
    from tracer.utils import feed as feed_service

    if group.success_trace_id:
        return str(group.success_trace_id)
    detail = feed_service.get_feed_detail(group.cluster_id, project_ids)
    if detail is None or detail.success_trace is None:
        return None
    return str(detail.success_trace.trace_id)


def _resolve_shared_issue_trace(request, link, trace_id):
    from types import SimpleNamespace

    from tracer.services.clickhouse.v2.query_builders import trace_detail
    from tracer.services.clickhouse.v2.query_service import V2AnalyticsQueryService
    from tracer.views.trace import TraceView

    scope = _shared_issue_scope(link, trace_id)
    if scope is None:
        return None
    _, _, group = scope

    # Read as the link sees it: its organization and workspace, the
    # issue's project, never the viewer's own scope.
    scoped_request = SimpleNamespace(
        organization=link.organization,
        workspace=link.workspace,
        user=link.created_by,
    )
    view = TraceView()
    view.request = request
    view.format_kwarg = None
    view.kwargs = {"pk": trace_id}
    return trace_detail.retrieve_trace_detail_ch(
        view,
        scoped_request,
        trace_id,
        V2AnalyticsQueryService(),
        project_id=str(group.project_id),
    )


def _resolve_resource(link):
    """
    Fetch resource data by type and ID.
    Returns a dict or None if not found.
    """
    try:
        if link.resource_type == "trace":
            from tracer.services.clickhouse.v2 import get_reader
            from tracer.services.clickhouse.v2.span_reader import CHSpanReader

            trace_id = str(uuid.UUID(str(link.resource_id)))
            project_id = _get_shared_trace_project_id(
                trace_id,
                link.organization,
                link.workspace,
            )
            if not project_id:
                return None

            # Spans read from CH 25.3. The reader returns CHSpan dataclasses;
            # `to_django_dict()` shapes them like ObservationSpanSerializer
            # output so the tree-building below stays unchanged.
            with get_reader() as reader:
                ch_spans = reader.list_by_trace(trace_id, project_id=project_id)
            spans_data = [CHSpanReader.to_django_dict(s) for s in ch_spans]

            # Build tree structure (parent→children)
            span_map = {}
            roots = []
            for s in spans_data:
                entry = {"observation_span": s, "children": []}
                span_map[s["id"]] = entry

            for s in spans_data:
                parent_id = s.get("parent_span_id")
                entry = span_map[s["id"]]
                if parent_id and parent_id in span_map:
                    span_map[parent_id]["children"].append(entry)
                else:
                    roots.append(entry)

            return {
                "trace": _shared_trace_header(trace_id, project_id, roots),
                "observation_spans": roots,
                "summary": {
                    "total_spans": len(spans_data),
                },
            }

        elif link.resource_type == "dashboard":
            from tracer.serializers.dashboard import DashboardDetailSerializer

            dashboard = _get_shared_dashboard(
                link.resource_id,
                link.organization,
                link.workspace,
            )
            if not dashboard:
                return None
            data = DashboardDetailSerializer(dashboard).data
            data["widget_count"] = len(data.get("widgets", []))
            return data

        elif link.resource_type == "project":
            project = _get_shared_project(
                link.resource_id,
                link.organization,
                link.workspace,
            )
            if not project:
                return None
            return _serialize_shared_project(project)

        elif link.resource_type == ResourceType.CALL_EXECUTION.value:
            return _in_link_workspace(link, _resolve_shared_call_execution, link)

        elif link.resource_type == ResourceType.ERROR_FEED_ISSUE.value:
            return _in_link_workspace(link, _resolve_shared_error_issue, link)

        # Extend for other resource types as needed
        return None

    except Exception:
        logger.exception("Failed to resolve shared resource", link_id=str(link.id))
        return None


def _resolve_shared_call_execution(link):
    from simulate.views.run_results_v3 import build_call_execution_detail

    call = _get_shared_call_execution(
        link.resource_id,
        link.organization,
        link.workspace,
    )
    if not call:
        return None
    return build_call_execution_detail(call, workspace=link.workspace)


def _in_link_workspace(link, build, *args):
    """Run ``build`` scoped to the link's workspace, not the viewer's."""
    previous = (
        get_current_workspace(),
        get_current_organization(),
        get_current_user(),
    )
    set_workspace_context(workspace=link.workspace, organization=link.organization)
    try:
        return build(*args)
    finally:
        set_workspace_context(
            workspace=previous[0], organization=previous[1], user=previous[2]
        )


def _grant_access(link, email, granted_by):
    """Add ``email`` to the link's ACL; returns ``(entry, newly_granted)``.

    A removed entry is soft-deleted, and the (link, email) unique constraint
    still counts it, so re-inviting restores that row instead of inserting.
    """
    user = User.objects.filter(email=email).first()
    entry = SharedLinkAccess.all_objects.filter(
        shared_link=link, email__iexact=email
    ).first()
    if entry is None:
        entry = SharedLinkAccess.objects.create(
            shared_link=link, email=email, user=user, granted_by=granted_by
        )
        return entry, True
    if not entry.deleted:
        return entry, False
    entry.deleted = False
    entry.deleted_at = None
    entry.user = user
    entry.granted_by = granted_by
    entry.save(update_fields=["deleted", "deleted_at", "user", "granted_by"])
    return entry, True


def _get_shared_link_by_token(token):
    # The token alone identifies the link; the viewer's own workspace must not
    # hide it (access is checked after lookup).
    return SharedLink.no_workspace_objects.get(token=token, deleted=False)


def _shared_feed_project_ids(organization, workspace):
    from tracer.models.project import Project

    projects = Project.no_workspace_objects.filter(organization=organization).filter(
        _workspace_scope_q(workspace, "workspace")
    )
    return [str(pid) for pid in projects.values_list("id", flat=True)]


def _get_shared_error_issue(cluster_id, organization, workspace):
    from tracer.utils import feed as feed_service

    project_ids = _shared_feed_project_ids(organization, workspace)
    if not project_ids:
        return None
    return feed_service.get_feed_detail(cluster_id, project_ids)


def _resolve_shared_error_issue(link):
    from tracer.utils import feed as feed_service

    # Same rules as the shared trace and sidebar reads: the sharer's licence,
    # the link's projects, and a merged issue followed to its survivor.
    scope = _shared_issue_scope(link)
    if scope is None:
        return None
    cluster_id, project_ids, _ = scope

    detail = feed_service.get_feed_detail(cluster_id, project_ids)
    if detail is None:
        return None

    # Each tab builds on its own: one failing read (overview's vector store,
    # say) leaves that tab empty instead of taking the whole link down.
    def _tab(name, serializer_class, build, **kwargs):
        try:
            value = build(cluster_id, project_ids, **kwargs)
        except Exception:
            logger.exception(
                "shared_error_issue_tab_failed", link_id=str(link.id), tab=name
            )
            return None
        return serializer_class(value).data if value is not None else None

    return {
        "issue": FeedDetailCoreSerializer(detail).data,
        "overview": _tab(
            "overview",
            OverviewResponseSerializer,
            feed_service.get_overview_tab,
            rep_limit=20,
        ),
        "traces": _tab(
            "traces",
            TracesTabResponseSerializer,
            feed_service.get_traces_tab,
            # Same page size the issue page's Traces tab asks for.
            limit=200,
            offset=0,
        ),
        "trends": _tab(
            "trends", TrendsTabResponseSerializer, feed_service.get_trends_tab, days=14
        ),
        "sidebar": _tab("sidebar", FeedSidebarSerializer, feed_service.get_sidebar),
    }


def _shared_resource_exists(resource_type, resource_id, organization, workspace):
    try:
        if resource_type == ResourceType.TRACE.value:
            return (
                _get_shared_trace_project_id(resource_id, organization, workspace)
                is not None
            )
        if resource_type == ResourceType.DASHBOARD.value:
            return (
                _get_shared_dashboard(resource_id, organization, workspace) is not None
            )
        if resource_type == ResourceType.PROJECT.value:
            return _get_shared_project(resource_id, organization, workspace) is not None
        if resource_type == ResourceType.CALL_EXECUTION.value:
            return (
                _get_shared_call_execution(resource_id, organization, workspace)
                is not None
            )
        if resource_type == ResourceType.ERROR_FEED_ISSUE.value:
            return (
                _get_shared_error_issue(resource_id, organization, workspace)
                is not None
            )
    except (TypeError, ValueError, ValidationError):
        return False
    return False


def _workspace_scope_q(workspace, lookup):
    if not workspace:
        return models.Q()
    if getattr(workspace, "is_default", False):
        return (
            models.Q(**{lookup: workspace})
            | models.Q(
                **{
                    f"{lookup}__is_default": True,
                    f"{lookup}__organization": workspace.organization,
                }
            )
            | models.Q(**{f"{lookup}__isnull": True})
        )
    return models.Q(**{lookup: workspace})


def _get_shared_trace_project_id(resource_id, organization, workspace):
    """The trace's project id when its spans live in the org and workspace.

    Collector traces only reach ClickHouse, so the spans decide whether the
    trace exists, as in the trace drawer.
    """
    from tracer.models.project import Project
    from tracer.services.clickhouse.v2.query_service import V2AnalyticsQueryService
    from tracer.services.clickhouse.v2.trace_detail_reads import (
        TraceDetailReadBuilder,
    )

    trace_id = str(uuid.UUID(str(resource_id)))
    project_ids = [
        str(project_id)
        for project_id in Project.no_workspace_objects.filter(organization=organization)
        .filter(_workspace_scope_q(workspace, "workspace"))
        .values_list("id", flat=True)
    ]
    if not project_ids:
        return None
    query, params = TraceDetailReadBuilder(
        project_ids=project_ids, trace_id=trace_id
    ).build_identity_query()
    rows = V2AnalyticsQueryService().execute_ch_query(query, params).data or []
    live_projects = {
        str(row["project_id"]) for row in rows if not row.get("latest_is_deleted")
    }
    return live_projects.pop() if len(live_projects) == 1 else None


def _shared_trace_header(trace_id, project_id, roots):
    """The Postgres trace row when there is one, else the root span."""
    from django.db.utils import ProgrammingError

    from tracer.models.trace import Trace

    try:
        trace = Trace.no_workspace_objects.filter(
            id=trace_id, project_id=project_id
        ).first()
    except ProgrammingError:
        trace = None
    if trace is not None:
        return {
            "id": str(trace.id),
            "name": trace.name,
            "project_id": str(trace.project_id),
            "input": trace.input,
            "output": trace.output,
            "metadata": trace.metadata,
            "tags": trace.tags,
            "created_at": str(trace.created_at) if trace.created_at else None,
        }
    root = roots[0]["observation_span"] if roots else {}
    start_time = root.get("start_time")
    return {
        "id": trace_id,
        "name": root.get("name"),
        "project_id": project_id,
        "input": root.get("input"),
        "output": root.get("output"),
        "metadata": root.get("metadata") or {},
        "tags": root.get("tags") or [],
        "created_at": str(start_time) if start_time else None,
    }


def _get_shared_dashboard(resource_id, organization, workspace):
    from tracer.models.dashboard import Dashboard

    return (
        Dashboard.no_workspace_objects.filter(
            id=resource_id,
            workspace__organization=organization,
        )
        .filter(_workspace_scope_q(workspace, "workspace"))
        .first()
    )


def _get_shared_project(resource_id, organization, workspace):
    from tracer.models.project import Project

    return (
        Project.no_workspace_objects.filter(
            id=resource_id,
            organization=organization,
        )
        .filter(_workspace_scope_q(workspace, "workspace"))
        .first()
    )


def _get_shared_call_execution(resource_id, organization, workspace):
    from simulate.models import CallExecution

    return (
        CallExecution.no_workspace_objects.filter(
            id=resource_id,
            test_execution__deleted=False,
            test_execution__run_test__organization=organization,
            test_execution__run_test__deleted=False,
        )
        .filter(_workspace_scope_q(workspace, "test_execution__run_test__workspace"))
        .select_related(
            "scenario",
            "test_execution__run_test",
            "test_execution__agent_definition",
            "test_execution__simulator_agent",
        )
        .prefetch_related("transcripts", "chat_messages", "snapshots")
        .first()
    )


def _serialize_shared_project(project):
    return {
        "id": str(project.id),
        "name": project.name,
        "trace_type": project.trace_type,
        "model_type": project.model_type,
        "metadata": project.metadata,
        "config": project.config,
        "session_config": project.session_config,
        "tags": project.tags,
        "organization": str(project.organization_id),
        "workspace": str(project.workspace_id) if project.workspace_id else None,
        "created_at": str(project.created_at) if project.created_at else None,
        "updated_at": str(project.updated_at) if project.updated_at else None,
        "url_path": _shared_project_url_path(project),
    }


def _shared_project_url_path(project):
    if project.trace_type == "observe":
        return f"/dashboard/observe/{project.id}/llm-tracing"
    return f"/dashboard/prototype/{project.id}"
