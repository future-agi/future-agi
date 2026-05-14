import structlog
from django.db import IntegrityError, models
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from tfc.routers import uses_db
from tfc.utils.base_viewset import BaseModelViewSetMixin
from tfc.utils.general_methods import GeneralMethods
from tracer.db_routing import DATABASE_FOR_SAVED_VIEW_LIST
from tracer.models.project import Project
from tracer.models.saved_view import SavedView
from tracer.serializers.saved_view import (
    SavedViewCreateSerializer,
    SavedViewDetailSerializer,
    SavedViewListSerializer,
    SavedViewReorderSerializer,
    SavedViewUpdateSerializer,
)

logger = structlog.get_logger(__name__)

DEFAULT_TABS = [
    {"key": "traces", "label": "Traces", "tab_type": "traces"},
    {"key": "spans", "label": "Spans", "tab_type": "spans"},
    {"key": "voice", "label": "Voice", "tab_type": "voice"},
]


class SavedViewViewSet(BaseModelViewSetMixin, ModelViewSet):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]
    serializer_class = SavedViewListSerializer
    lookup_value_regex = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"

    def get_queryset(self):
        queryset = super().get_queryset()
        project_id = self.request.query_params.get("project_id")
        tab_type = self.request.query_params.get("tab_type")
        if project_id:
            queryset = queryset.filter(project_id=project_id)
        else:
            # Workspace-scoped views (project-null). Only personal visibility
            # is meaningful here since there's no project to share against.
            queryset = queryset.filter(
                project__isnull=True,
                workspace=self.request.workspace,
                created_by=self.request.user,
                visibility="personal",
            )
            if tab_type:
                queryset = queryset.filter(tab_type=tab_type)
            return queryset.select_related("created_by", "updated_by")

        # Show personal views for current user + all project-shared views
        queryset = queryset.filter(
            models.Q(created_by=self.request.user, visibility="personal")
            | models.Q(visibility="project")
        )
        return queryset.select_related("created_by", "updated_by")

    def get_serializer_class(self):
        if self.action == "retrieve":
            return SavedViewDetailSerializer
        return SavedViewListSerializer

    # ------------------------------------------------------------------
    # LIST — returns default tabs + custom views
    # ------------------------------------------------------------------

    @uses_db(DATABASE_FOR_SAVED_VIEW_LIST, feature_key="feature:saved_view_list")
    def list(self, request, *args, **kwargs):
        try:
            project_id = request.query_params.get("project_id")
            if project_id:
                # Existence check only — does NOT validate workspace/user
                # access for this project. Workspace/user access is enforced
                # by `BaseModelViewSetMixin.get_queryset()` below (the actual
                # SavedView rows it returns are already workspace/user-scoped).
                # Stays on `default`: Project isn't opted in to replica
                # routing, and a stale 404 here would be confusing right
                # after a project create.
                try:
                    Project.objects.get(id=project_id)
                except Project.DoesNotExist:
                    return self._gm.not_found("Project not found.")

            # Route the saved-view list read to the replica when the
            # feature key is opted in. See tracer/db_routing.py.
            # No-op (stays on "default") until READ_REPLICA_OPT_IN includes
            # "feature:saved_view_list".
            queryset = self.get_queryset().using(DATABASE_FOR_SAVED_VIEW_LIST)
            serializer = SavedViewListSerializer(
                queryset, many=True, context={"request": request}
            )

            return self._gm.success_response(
                {
                    "default_tabs": DEFAULT_TABS,
                    "custom_views": serializer.data,
                }
            )
        except Exception as e:
            logger.error(f"Failed to list saved views: {e}", exc_info=True)
            return self._gm.bad_request("Failed to list saved views.")

    # ------------------------------------------------------------------
    # RETRIEVE
    # ------------------------------------------------------------------

    def retrieve(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            serializer = SavedViewDetailSerializer(
                instance, context={"request": request}
            )
            return self._gm.success_response(serializer.data)
        except SavedView.DoesNotExist:
            return self._gm.not_found("Saved view not found.")
        except Exception as e:
            logger.error(f"Failed to retrieve saved view: {e}", exc_info=True)
            return self._gm.bad_request("Failed to retrieve saved view.")

    # ------------------------------------------------------------------
    # CREATE
    # ------------------------------------------------------------------

    def create(self, request, *args, **kwargs):
        try:
            serializer = SavedViewCreateSerializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)

            data = serializer.validated_data
            project_id = data.pop("project_id", None)

            project = None
            if project_id:
                try:
                    project = Project.objects.get(id=project_id)
                except Project.DoesNotExist:
                    return self._gm.not_found("Project not found.")
            else:
                # Workspace-scoped saved views are personal-only (no project to share against)
                data["visibility"] = "personal"

            # Calculate next position (scoped to the same bucket as the new view)
            position_qs = SavedView.objects.filter(
                created_by=request.user,
                deleted=False,
            )
            if project is not None:
                position_qs = position_qs.filter(project=project)
            else:
                position_qs = position_qs.filter(
                    project__isnull=True,
                    workspace=request.workspace,
                    tab_type=data.get("tab_type"),
                )
            max_position = position_qs.aggregate(max_pos=models.Max("position")).get(
                "max_pos"
            )
            next_position = (max_position or 0) + 1

            saved_view = SavedView(
                project=project,
                workspace=request.workspace,
                created_by=request.user,
                position=next_position,
                **data,
            )
            try:
                saved_view.save()
            except IntegrityError:
                # A view with this name already exists for this user+project — update it instead
                lookup = {"created_by": request.user, "name": data["name"], "deleted": False}
                if project is not None:
                    lookup["project"] = project
                else:
                    lookup["project__isnull"] = True
                    lookup["workspace"] = request.workspace
                existing = SavedView.objects.get(**lookup)
                update_fields = []
                for field in ("config", "visibility", "icon", "tab_type"):
                    if field in data:
                        setattr(existing, field, data[field])
                        update_fields.append(field)
                if update_fields:
                    existing.save(update_fields=update_fields)
                saved_view = existing

            response_serializer = SavedViewDetailSerializer(
                saved_view, context={"request": request}
            )
            return self._gm.success_response(response_serializer.data)
        except Exception as e:
            logger.error(f"Failed to create saved view: {e}", exc_info=True)
            return self._gm.bad_request("Failed to create saved view.")

    # ------------------------------------------------------------------
    # UPDATE / PARTIAL UPDATE
    # ------------------------------------------------------------------

    def update(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            partial = kwargs.get("partial", False)
            serializer = SavedViewUpdateSerializer(data=request.data, partial=partial)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)

            data = serializer.validated_data
            for attr, value in data.items():
                setattr(instance, attr, value)
            instance.updated_by = request.user
            instance.save()

            response_serializer = SavedViewDetailSerializer(
                instance, context={"request": request}
            )
            return self._gm.success_response(response_serializer.data)
        except Exception as e:
            logger.error(f"Failed to update saved view: {e}", exc_info=True)
            return self._gm.bad_request("Failed to update saved view.")

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)

    # ------------------------------------------------------------------
    # DESTROY (soft delete)
    # ------------------------------------------------------------------

    def destroy(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            instance.delete()  # BaseModel soft delete
            return self._gm.success_response({"message": "View deleted."})
        except Exception as e:
            logger.error(f"Failed to delete saved view: {e}", exc_info=True)
            return self._gm.bad_request("Failed to delete saved view.")

    # ------------------------------------------------------------------
    # DUPLICATE
    # ------------------------------------------------------------------

    @action(detail=True, methods=["post"], url_path="duplicate")
    def duplicate(self, request, *args, **kwargs):
        try:
            original = self.get_object()
            new_name = request.data.get("name", f"{original.name} (Copy)")

            # Calculate next position
            max_position = (
                SavedView.objects.filter(
                    project=original.project,
                    created_by=request.user,
                    deleted=False,
                )
                .aggregate(max_pos=models.Max("position"))
                .get("max_pos")
            )
            next_position = (max_position or 0) + 1

            new_view = SavedView(
                project=original.project,
                workspace=request.workspace,
                created_by=request.user,
                name=new_name,
                tab_type=original.tab_type,
                visibility="personal",
                position=next_position,
                icon=original.icon,
                config=original.config,
            )
            new_view.save()

            response_serializer = SavedViewDetailSerializer(
                new_view, context={"request": request}
            )
            return self._gm.success_response(response_serializer.data)
        except Exception as e:
            logger.error(f"Failed to duplicate saved view: {e}", exc_info=True)
            return self._gm.bad_request("Failed to duplicate saved view.")

    # ------------------------------------------------------------------
    # REORDER
    # ------------------------------------------------------------------

    @action(detail=False, methods=["post"], url_path="reorder")
    def reorder(self, request, *args, **kwargs):
        try:
            serializer = SavedViewReorderSerializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)

            data = serializer.validated_data
            project_id = data.get("project_id")
            tab_type = data.get("tab_type")
            order = data["order"]

            # Verify all view IDs belong to views the user can edit
            view_ids = [item["id"] for item in order]
            accessible_views = SavedView.objects.filter(
                id__in=view_ids,
                deleted=False,
            )
            if project_id:
                accessible_views = accessible_views.filter(
                    project_id=project_id,
                ).filter(
                    models.Q(created_by=request.user) | models.Q(visibility="project")
                )
            else:
                accessible_views = accessible_views.filter(
                    project__isnull=True,
                    workspace=request.workspace,
                    created_by=request.user,
                )
                if tab_type:
                    accessible_views = accessible_views.filter(tab_type=tab_type)

            accessible_ids = set(str(v.id) for v in accessible_views)
            requested_ids = set(str(vid) for vid in view_ids)
            if not requested_ids.issubset(accessible_ids):
                return self._gm.bad_request(
                    "Some view IDs are not accessible or do not exist."
                )

            # Bulk update positions
            for item in order:
                SavedView.objects.filter(id=item["id"]).update(
                    position=item["position"]
                )

            return self._gm.success_response({"message": "Views reordered."})
        except Exception as e:
            logger.error(f"Failed to reorder saved views: {e}", exc_info=True)
            return self._gm.bad_request("Failed to reorder saved views.")
