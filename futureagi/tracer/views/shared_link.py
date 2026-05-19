import structlog
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from accounts.models.user import User
from tfc.utils.base_viewset import BaseModelViewSetMixin
from tfc.utils.general_methods import GeneralMethods
from tracer.models.shared_link import AccessType, SharedLink, SharedLinkAccess
from tracer.serializers.shared_link import (
    AddAccessSerializer,
    SharedLinkAccessSerializer,
    SharedLinkCreateSerializer,
    SharedLinkDetailSerializer,
    SharedLinkListSerializer,
    SharedLinkUpdateSerializer,
)

logger = structlog.get_logger(__name__)


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
        if self.action == "retrieve":
            return SharedLinkDetailSerializer
        return SharedLinkListSerializer

    def create(self, request, *args, **kwargs):
        serializer = SharedLinkCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        link = SharedLink.objects.create(
            resource_type=data["resource_type"],
            resource_id=data["resource_id"],
            access_type=data.get("access_type", AccessType.RESTRICTED),
            expires_at=data.get("expires_at"),
            created_by=request.user,
            organization=request.organization,
            workspace=getattr(request, "workspace", None),
        )

        # Add ACL entries if provided
        emails = data.get("emails", [])
        for email in emails:
            user = User.objects.filter(email=email).first()
            SharedLinkAccess.objects.create(
                shared_link=link,
                email=email,
                user=user,
                granted_by=request.user,
            )

        return self._gm.success_response(
            SharedLinkDetailSerializer(link, context={"request": request}).data,
            status_code=status.HTTP_201_CREATED,
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
            user = User.objects.filter(email=email).first()
            obj, was_created = SharedLinkAccess.objects.get_or_create(
                shared_link=link,
                email=email,
                defaults={"user": user, "granted_by": request.user},
            )
            if was_created:
                created.append(SharedLinkAccessSerializer(obj).data)

        return self._gm.success_response(created, status_code=status.HTTP_201_CREATED)

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
            return self._gm.error_response(
                "Access entry not found", status_code=status.HTTP_404_NOT_FOUND
            )
        return self._gm.success_response({"message": "Access removed"})


# --------------------------------------------------------------------------
# Public token-resolve endpoint (no auth required for public links)
# --------------------------------------------------------------------------


def _get_accessible_link(token):
    """
    Look up a SharedLink by token and verify it is still usable.

    Returns (link, error_response). Exactly one is non-None: on success
    `link` is set, otherwise `error_response` carries the DRF Response.
    """
    try:
        link = SharedLink.objects.get(token=token, deleted=False)
    except SharedLink.DoesNotExist:
        return None, Response(
            {"error": "Shared link not found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    if not link.is_accessible:
        reason = "expired" if link.is_expired else "revoked"
        return None, Response(
            {"error": f"This shared link has been {reason}"},
            status=status.HTTP_410_GONE,
        )

    return link, None


def _check_link_access(link, request):
    """
    Enforce the ACL for restricted links.

    Public links are always accessible. Restricted links require an
    authenticated user whose email is in the ACL (or who created the link).
    Returns a DRF Response to short-circuit with, or None if access is granted.
    """
    if link.access_type != AccessType.RESTRICTED:
        return None

    if not request.user or not request.user.is_authenticated:
        return Response(
            {"error": "Authentication required to access this link"},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    has_access = link.access_list.filter(
        email=request.user.email, deleted=False
    ).exists()
    # Also allow the creator
    if not has_access and request.user != link.created_by:
        return Response(
            {"error": "You don't have access to this shared resource"},
            status=status.HTTP_403_FORBIDDEN,
        )

    return None


@api_view(["GET"])
@permission_classes([AllowAny])
def resolve_shared_link(request, token):
    """
    Resolve a share token to the underlying resource data.
    - Public links: no auth needed
    - Restricted links: user must be authenticated + email in ACL
    """
    link, error = _get_accessible_link(token)
    if error is not None:
        return error

    # Check access for restricted links
    access_error = _check_link_access(link, request)
    if access_error is not None:
        return access_error

    # Resolve the resource
    resource_data = _resolve_resource(link)
    if resource_data is None:
        return Response(
            {"error": "The shared resource no longer exists"},
            status=status.HTTP_404_NOT_FOUND,
        )

    return Response(
        {
            "resource_type": link.resource_type,
            "resource_id": link.resource_id,
            "access_type": link.access_type,
            "data": resource_data,
        }
    )


def _resolve_resource(link):
    """
    Fetch resource data by type and ID.
    Returns a dict or None if not found.
    """
    try:
        if link.resource_type == "trace":
            from tracer.models.observation_span import ObservationSpan
            from tracer.models.trace import Trace
            from tracer.serializers.observation_span import ObservationSpanSerializer

            trace = (
                Trace.objects.filter(
                    id=link.resource_id,
                    project__organization=link.organization,
                )
                .select_related("project")
                .first()
            )
            if not trace:
                return None

            # Get spans for this trace and build a flat list
            spans_qs = ObservationSpan.objects.filter(
                trace_id=str(trace.id),
                project=trace.project,
            ).order_by("start_time")
            spans_data = ObservationSpanSerializer(spans_qs, many=True).data

            # Build tree structure (parent→children)
            span_map = {}
            roots = []
            for s in spans_data:
                entry = {"observation_span": s, "children": []}
                span_map[s["id"]] = entry

            for s in spans_data:
                parent_id = s.get("parent_observation_id")
                entry = span_map[s["id"]]
                if parent_id and parent_id in span_map:
                    span_map[parent_id]["children"].append(entry)
                else:
                    roots.append(entry)

            return {
                "trace": {
                    "id": str(trace.id),
                    "name": trace.name,
                    "project_id": str(trace.project_id),
                    "input": trace.input,
                    "output": trace.output,
                    "metadata": trace.metadata,
                    "tags": trace.tags,
                    "created_at": str(trace.created_at) if trace.created_at else None,
                },
                "observation_spans": roots,
                "summary": {
                    "total_spans": len(spans_data),
                },
            }

        elif link.resource_type == "dashboard":
            from tracer.models.dashboard import Dashboard
            from tracer.serializers.dashboard import DashboardWidgetSerializer

            dashboard = Dashboard.objects.filter(
                id=link.resource_id,
                workspace__organization=link.organization,
            ).first()
            if not dashboard:
                return None

            widgets_qs = dashboard.widgets.filter(deleted=False).order_by(
                "position", "created_at"
            )
            return {
                "id": str(dashboard.id),
                "name": dashboard.name,
                "description": dashboard.description,
                "widgets": DashboardWidgetSerializer(widgets_qs, many=True).data,
            }

        # Extend for other resource types as needed
        return None

    except Exception:
        logger.exception("Failed to resolve shared resource", link_id=str(link.id))
        return None


@api_view(["GET"])
@permission_classes([AllowAny])
def resolve_shared_widget_data(request, token, widget_id):
    """
    Execute one dashboard widget's query for a share token and return results.

    Lets a public viewer of a shared dashboard load live widget data without
    an auth wall: the share token authorizes the read, and the query runs
    scoped to the link's workspace (the viewer has no workspace of their own).

    - Public links: no auth needed
    - Restricted links: same ACL as resolve_shared_link
    """
    link, error = _get_accessible_link(token)
    if error is not None:
        return error

    access_error = _check_link_access(link, request)
    if access_error is not None:
        return access_error

    # Widget data only makes sense for shared dashboards.
    if link.resource_type != "dashboard":
        return Response(
            {"error": "This shared link is not a dashboard"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    from tracer.models.dashboard import DashboardWidget
    from tracer.services.clickhouse.client import is_clickhouse_enabled
    from tracer.views.dashboard import DashboardViewSet

    if not is_clickhouse_enabled():
        return Response(
            {"error": "ClickHouse is not enabled"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # The widget must belong to the dashboard this link shares — never trust
    # the widget_id alone, or any token would unlock any widget.
    widget = (
        DashboardWidget.objects.filter(
            id=widget_id,
            dashboard_id=link.resource_id,
            deleted=False,
        )
        .select_related("dashboard")
        .first()
    )
    if not widget:
        return Response(
            {"error": "Widget not found in this shared dashboard"},
            status=status.HTTP_404_NOT_FOUND,
        )

    query_config = widget.query_config
    if not query_config or not query_config.get("metrics"):
        return Response(
            {"error": "Widget has no query configuration"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # The link's workspace owns the data; fall back to the dashboard's own
    # workspace for older links created before workspace was recorded.
    workspace = link.workspace or widget.dashboard.workspace

    try:
        # execute_ch_query_config is request-independent (see its docstring),
        # so it is safe to reuse here for the public share-token path.
        return DashboardViewSet().execute_ch_query_config(query_config, workspace)
    except Exception as e:
        logger.exception(
            "Failed to execute shared widget query",
            link_id=str(link.id),
            widget_id=str(widget_id),
        )
        return Response(
            {"error": f"Query execution failed: {str(e)}"},
            status=status.HTTP_400_BAD_REQUEST,
        )
