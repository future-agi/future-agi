import structlog
from django.core.exceptions import ValidationError
from django.db import DatabaseError, models
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from accounts.models.user import User
from tfc.utils.base_viewset import BaseModelViewSetMixin
from tfc.utils.general_methods import GeneralMethods
from tracer.models.shared_link import (
    AccessType,
    ResourceType,
    SharedLink,
    SharedLinkAccess,
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

logger = structlog.get_logger(__name__)
_gm = GeneralMethods()

SUPPORTED_SHARED_RESOURCE_TYPES = {
    ResourceType.TRACE.value,
    ResourceType.DASHBOARD.value,
    ResourceType.PROJECT.value,
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
        if not _shared_resource_exists(
            data["resource_type"],
            data["resource_id"],
            request.organization,
            getattr(request, "workspace", None),
        ):
            return self._gm.not_found("Shared resource not found")

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
            user = User.objects.filter(email=email).first()
            obj, was_created = SharedLinkAccess.objects.get_or_create(
                shared_link=link,
                email=email,
                defaults={"user": user, "granted_by": request.user},
            )
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
    try:
        link = _get_shared_link_by_token(token)
    except SharedLink.DoesNotExist:
        return _gm.not_found("Shared link not found")
    except DatabaseError:
        logger.exception("shared_link_resolve_database_unavailable")
        return _gm.custom_error_response(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Shared link resolver is temporarily unavailable.",
            code="service_unavailable",
        )

    if not link.is_accessible:
        reason = "expired" if link.is_expired else "revoked"
        return _gm.custom_error_response(
            status.HTTP_410_GONE,
            f"This shared link has been {reason}",
            code="gone",
        )

    # Check access for restricted links
    if link.access_type == AccessType.RESTRICTED:
        if not request.user or not request.user.is_authenticated:
            return _gm.custom_error_response(
                status.HTTP_401_UNAUTHORIZED,
                "Authentication required to access this link",
                code="not_authenticated",
            )
        has_access = link.access_list.filter(
            email=request.user.email, deleted=False
        ).exists()
        # Also allow the creator
        if not has_access and request.user != link.created_by:
            return _gm.forbidden_response(
                "You don't have access to this shared resource"
            )

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


def _resolve_resource(link):
    """
    Fetch resource data by type and ID.
    Returns a dict or None if not found.
    """
    try:
        if link.resource_type == "trace":
            from tracer.services.clickhouse.v2 import get_reader
            from tracer.services.clickhouse.v2.span_reader import CHSpanReader

            trace = _get_shared_trace(
                link.resource_id,
                link.organization,
                link.workspace,
            )
            if not trace:
                return None

            # Spans read from CH 25.3. The reader returns CHSpan dataclasses;
            # `to_django_dict()` shapes them like ObservationSpanSerializer
            # output so the tree-building below stays unchanged.
            with get_reader() as reader:
                ch_spans = reader.list_by_trace(str(trace.id))
            spans_data = [CHSpanReader.to_django_dict(s) for s in ch_spans]

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

        # Extend for other resource types as needed
        return None

    except Exception:
        logger.exception("Failed to resolve shared resource", link_id=str(link.id))
        return None


def _get_shared_link_by_token(token):
    return SharedLink.objects.get(token=token, deleted=False)


def _shared_resource_exists(resource_type, resource_id, organization, workspace):
    try:
        if resource_type == ResourceType.TRACE.value:
            return _get_shared_trace(resource_id, organization, workspace) is not None
        if resource_type == ResourceType.DASHBOARD.value:
            return (
                _get_shared_dashboard(resource_id, organization, workspace) is not None
            )
        if resource_type == ResourceType.PROJECT.value:
            return _get_shared_project(resource_id, organization, workspace) is not None
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


def _get_shared_trace(resource_id, organization, workspace):
    from tracer.models.trace import Trace

    return (
        Trace.no_workspace_objects.filter(
            id=resource_id,
            project__organization=organization,
        )
        .filter(_workspace_scope_q(workspace, "project__workspace"))
        .select_related("project")
        .first()
    )


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
