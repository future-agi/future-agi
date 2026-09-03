import structlog
from django.db import IntegrityError, models
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from tfc.routers import uses_db
from tfc.utils.api_contracts import validated_request
from tfc.utils.base_viewset import BaseModelViewSetMixin
from tfc.utils.general_methods import GeneralMethods
from tracer.db_routing import DATABASE_FOR_SAVED_VIEW_LIST
from tracer.models.project import Project
from tracer.models.saved_view import SavedView
from tracer.serializers.saved_view import (
    SavedViewCreateSerializer,
    SavedViewDetailResponseSerializer,
    SavedViewDetailSerializer,
    SavedViewListResponseSerializer,
    SavedViewListSerializer,
    SavedViewMessageResponseSerializer,
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
    @validated_request(responses={200: SavedViewListResponseSerializer})
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

    @validated_request(responses={200: SavedViewDetailResponseSerializer})
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

    @validated_request(responses={200: SavedViewDetailResponseSerializer})
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

            scope_qs = SavedView.scoped(
                request.user,
                project=project,
                workspace=request.workspace,
                tab_type=data.get("tab_type"),
            )

            # Calculate next position (scoped to the same bucket as the new view)
            max_position = scope_qs.aggregate(max_pos=models.Max("position")).get(
                "max_pos"
            )
            next_position = (max_position or 0) + 1

            # Reject duplicates with a clear error instead of silently upserting.
            if scope_qs.filter(name=data["name"]).exists():
                return self._gm.bad_request(
                    f"A view named '{data['name']}' already exists."
                )

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
                return self._gm.bad_request(
                    f"A view named '{data['name']}' already exists."
                )

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

    @validated_request(responses={200: SavedViewDetailResponseSerializer})
    def update(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            partial = kwargs.get("partial", False)
            serializer = SavedViewUpdateSerializer(data=request.data, partial=partial)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)

            data = serializer.validated_data
            if instance.project_id is None and data.get("visibility") == "project":
                data["visibility"] = "personal"

            # Reject renaming onto a name that already exists in the same scope.
            new_name = data.get("name")
            if new_name and new_name != instance.name:
                duplicate_exists = (
                    SavedView.scoped(
                        instance.created_by,
                        project=instance.project,
                        workspace=instance.workspace,
                        tab_type=instance.tab_type,
                    )
                    .filter(name=new_name)
                    .exclude(id=instance.id)
                    .exists()
                )
                if duplicate_exists:
                    return self._gm.bad_request(
                        f"A view named '{new_name}' already exists."
                    )

            for attr, value in data.items():
                setattr(instance, attr, value)
            instance.updated_by = request.user
            try:
                instance.save()
            except IntegrityError:
                return self._gm.bad_request(
                    f"A view named '{instance.name}' already exists."
                )

            response_serializer = SavedViewDetailSerializer(
                instance, context={"request": request}
            )
            return self._gm.success_response(response_serializer.data)
        except Exception as e:
            logger.error(f"Failed to update saved view: {e}", exc_info=True)
            return self._gm.bad_request("Failed to update saved view.")

    @validated_request(responses={200: SavedViewDetailResponseSerializer})
    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)

    # ------------------------------------------------------------------
    # DESTROY (soft delete)
    # ------------------------------------------------------------------

    @validated_request(responses={200: SavedViewMessageResponseSerializer})
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

    @validated_request(responses={200: SavedViewDetailResponseSerializer})
    @action(detail=True, methods=["post"], url_path="duplicate")
    def duplicate(self, request, *args, **kwargs):
        try:
            original = self.get_object()

            scope_qs = SavedView.scoped(
                request.user,
                project=original.project,
                workspace=request.workspace,
                tab_type=original.tab_type,
            )

            # Calculate next position
            max_position = scope_qs.aggregate(max_pos=models.Max("position")).get(
                "max_pos"
            )
            next_position = (max_position or 0) + 1

            # Resolve a free name: an explicit requested name that collides is
            # rejected (same contract as create/update); the auto-generated
            # copy name is uniquified so repeat duplicates keep working.
            existing_names = set(scope_qs.values_list("name", flat=True))
            max_len = SavedView._meta.get_field("name").max_length
            requested_name = request.data.get("name")
            if requested_name is not None and not isinstance(requested_name, str):
                return self._gm.bad_request("View name must be a string.")
            if isinstance(requested_name, str):
                requested_name = requested_name.strip()
            if requested_name:
                new_name = requested_name
                if new_name in existing_names:
                    return self._gm.bad_request(
                        f"A view named '{new_name}' already exists."
                    )
            elif requested_name == "":
                return self._gm.bad_request("View name cannot be empty.")
            else:
                def copy_name(suffix):
                    label = " (Copy)" if suffix is None else f" (Copy {suffix})"
                    return f"{original.name[: max_len - len(label)]}{label}"

                new_name = copy_name(None)
                suffix = 2
                while new_name in existing_names:
                    new_name = copy_name(suffix)
                    suffix += 1

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
            try:
                new_view.save()
            except IntegrityError:
                return self._gm.bad_request(
                    f"A view named '{new_name}' already exists."
                )

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

    @validated_request(responses={200: SavedViewMessageResponseSerializer})
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

            accessible_ids = {str(v.id) for v in accessible_views}
            requested_ids = {str(vid) for vid in view_ids}
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
