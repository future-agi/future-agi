"""Dataset grid views: scoped listing, creation, soft deletion and restoration."""

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from model_hub.models.dataset_views import DatasetSavedView
from model_hub.models.develop_dataset import Dataset
from model_hub.serializers.dataset_views import (
    DatasetViewDeletedResponseSerializer,
    DatasetViewListResponseSerializer,
    DatasetViewQuerySerializer,
    DatasetViewResponseSerializer,
    DatasetViewSerializer,
    DatasetViewWriteSerializer,
)
from model_hub.services.dataset_view_state import validate_grid_state
from tfc.constants.levels import Level
from tfc.middleware.workspace_context import (
    get_current_organization,
    get_current_workspace,
)
from tfc.permissions.utils import get_effective_workspace_level
from tfc.utils.api_contracts import validated_request


def _scope(request):
    if hasattr(request, "_dataset_view_scope"):
        return request._dataset_view_scope
    organization = getattr(request, "organization", None) or get_current_organization()
    workspace = getattr(request, "workspace", None) or get_current_workspace()
    if (
        not organization
        or not workspace
        or workspace.organization_id != organization.id
    ):
        raise PermissionDenied("An active organization and workspace are required.")
    level = get_effective_workspace_level(request.user, workspace.id)
    if level is None or level < Level.VIEWER:
        raise PermissionDenied("Workspace access required.")
    request._dataset_view_scope = (organization, workspace, level)
    return request._dataset_view_scope


def _datasets(request):
    organization, workspace, _ = _scope(request)
    workspace_filter = Q(workspace=workspace)
    if workspace.is_default:
        # Match the classic grid's legacy default-workspace dataset scope.
        workspace_filter |= Q(workspace__isnull=True) | Q(
            workspace__is_default=True, workspace__organization=organization
        )
    return Dataset.no_workspace_objects.filter(
        workspace_filter, organization=organization, deleted=False
    )


def _views(request):
    return (
        DatasetSavedView.objects.filter(dataset__in=_datasets(request))
        .filter(Q(created_by=request.user) | Q(shared=True))
        .select_related("created_by")
    )


def _can_delete(request, view):
    return view.created_by_id == request.user.id or (
        view.shared and _scope(request)[2] >= Level.ADMIN
    )


def _result(request, view):
    return DatasetViewSerializer(
        {
            "id": view.id,
            "surface": "grid",
            "scope": view.dataset_id,
            "name": view.name,
            "state": view.state,
            "shared": view.shared,
            "mine": view.created_by_id == request.user.id,
            "can_delete": _can_delete(request, view),
            "created_by": view.created_by.name,
            "created_at": view.created_at,
        }
    ).data


def _success(result):
    return Response({"status": True, "result": result})


class DatasetSavedViewsView(APIView):
    permission_classes = [IsAuthenticated]

    @validated_request(
        query_serializer=DatasetViewQuerySerializer,
        responses={200: DatasetViewListResponseSerializer},
    )
    def get(self, request):
        dataset = get_object_or_404(
            _datasets(request), id=request.validated_query_data["scope"]
        )
        views = _views(request).filter(dataset=dataset, deleted=False)
        query = request.validated_query_data
        if query.get("after"):
            cursor = get_object_or_404(views, id=query["after"])
            views = views.filter(
                Q(created_at__gt=cursor.created_at)
                | Q(created_at=cursor.created_at, id__gt=cursor.id)
            )
        page_size = query["page_size"]
        page = list(views[: page_size + 1])
        return _success(
            {
                "results": [_result(request, view) for view in page[:page_size]],
                "next": str(page[page_size - 1].id) if len(page) > page_size else None,
            }
        )

    @validated_request(
        request_serializer=DatasetViewWriteSerializer,
        responses={200: DatasetViewResponseSerializer},
    )
    def post(self, request):
        data = request.validated_data
        dataset = get_object_or_404(_datasets(request), id=data["scope"])
        try:
            with transaction.atomic():
                if "restore_id" in data:
                    view = get_object_or_404(
                        _views(request).select_for_update(),
                        id=data["restore_id"],
                        dataset=dataset,
                    )
                    if not _can_delete(request, view):
                        raise PermissionDenied(
                            "Only the author or an administrator can restore this view."
                        )
                    if view.shared and _scope(request)[2] < Level.MEMBER:
                        raise PermissionDenied(
                            "Sharing a view requires workspace write access."
                        )
                    validate_grid_state(view.state, dataset)
                    view.deleted = False
                    view.save(update_fields=["deleted"])
                else:
                    if data["shared"] and _scope(request)[2] < Level.MEMBER:
                        raise PermissionDenied(
                            "Sharing a view requires workspace write access."
                        )
                    state = validate_grid_state(data["state"], dataset)
                    view = DatasetSavedView.objects.create(
                        dataset=dataset,
                        created_by=request.user,
                        name=data["name"],
                        state=state,
                        shared=data["shared"],
                    )
        except IntegrityError:
            return Response(
                {"status": False, "message": "A view with that name already exists."},
                status=409,
            )
        return _success(_result(request, view))


class DatasetSavedViewDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @validated_request(responses={200: DatasetViewDeletedResponseSerializer})
    def delete(self, request, view_id):
        with transaction.atomic():
            view = get_object_or_404(
                _views(request).select_for_update(), id=view_id, deleted=False
            )
            if not _can_delete(request, view):
                raise PermissionDenied(
                    "Only the author or an administrator can delete this view."
                )
            view.deleted = True
            view.save(update_fields=["deleted"])
        return _success({"id": str(view.id)})
