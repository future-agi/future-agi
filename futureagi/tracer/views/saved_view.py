import structlog
from django.db import IntegrityError, models, transaction
from django.http import Http404
from django.shortcuts import get_object_or_404
from drf_yasg import openapi
from rest_framework import serializers
from tfc.constants.roles import RolePermissions
from tfc.utils.api_serializers import ManagementAPIErrorResponseSerializer
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from tfc.routers import uses_db
from tfc.utils.api_contracts import validated_request
from tfc.utils.base_viewset import BaseModelViewSetMixin
from tfc.utils.general_methods import GeneralMethods
from tracer.db_routing import DATABASE_FOR_SAVED_VIEW_LIST
from tracer.models.project import Project
from tracer.models.saved_view import SavedView, SavedViewTabOrder
from tracer.serializers.saved_view import (
    SavedViewCreateSerializer,
    SavedViewDeleteQuerySerializer,
    SavedViewListQuerySerializer,
    SavedViewDuplicateSerializer,
    SavedViewConflictResponseSerializer,
    SavedViewOrderConflictResponseSerializer,
    SavedViewPreconditionResponseSerializer,
    SavedViewForbiddenResponseSerializer,
    SavedViewNotFoundResponseSerializer,
    SavedViewReorderResponseSerializer,
    SavedViewDetailResponseSerializer,
    SavedViewDetailSerializer,
    SavedViewListResponseSerializer,
    SavedViewListSerializer,
    SavedViewMessageResponseSerializer,
    SavedViewReorderSerializer,
    SavedViewUpdateSerializer,
)

logger = structlog.get_logger(__name__)

READ_ERRORS = {400: ManagementAPIErrorResponseSerializer, 404: SavedViewNotFoundResponseSerializer}
WRITE_ERRORS = {**READ_ERRORS, 403: SavedViewForbiddenResponseSerializer,
                409: SavedViewConflictResponseSerializer, 428: SavedViewPreconditionResponseSerializer}


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
            return queryset.select_related("created_by", "updated_by", "workspace__organization")

        # Show personal views for current user + all project-shared views
        queryset = queryset.filter(
            models.Q(created_by=self.request.user, visibility="personal")
            | models.Q(visibility="project")
        )
        return queryset.select_related("created_by", "updated_by", "workspace__organization")

    def get_serializer_class(self):
        if self.action == "retrieve":
            return SavedViewDetailSerializer
        return SavedViewListSerializer

    def _mutation_policy(self, instance):
        # Default-workspace expansion can return records from several workspaces.
        # Memoize role lookups per record workspace, never per serializer row.
        if not hasattr(self, "_workspace_roles"):
            self._workspace_roles = {}
        if instance.workspace_id not in self._workspace_roles:
            self._workspace_roles[instance.workspace_id] = self.request.user.get_workspace_role(instance.workspace)
        owner = instance.created_by_id is not None and instance.created_by_id == self.request.user.id
        return {
            "is_owner": owner,
            "can_edit": owner,
            "can_delete": owner or (instance.visibility == "project" and
                self._workspace_roles[instance.workspace_id] in RolePermissions.ADMIN_ROLES),
        }

    def get_serializer_context(self):
        return {**super().get_serializer_context(), "policy": self._mutation_policy}

    def _detail(self, instance):
        return SavedViewDetailSerializer(instance, context=self.get_serializer_context()).data

    def _revision_required(self):
        return self._gm.custom_error_response(428,
            "This view was loaded without a revision. Refresh the page and try again.",
            code="revision_required")

    def _conflict(self, current):
        return self._gm.custom_error_response(409, {
            "message": "This view changed since you loaded it.", "current": current,
        }, code="revision_conflict")

    def _order_bucket(self, project_id, tab_type):
        return {"user": self.request.user, "workspace": self.request.workspace,
                "project_id": project_id or None, "tab_type": None if project_id else tab_type}

    @staticmethod
    def _order_data(record):
        return {"revision": record.revision if record else 0, "order": record.order if record else []}

    # ------------------------------------------------------------------
    # LIST — returns default tabs + custom views
    # ------------------------------------------------------------------

    @uses_db(DATABASE_FOR_SAVED_VIEW_LIST, feature_key="feature:saved_view_list")
    @validated_request(query_serializer=SavedViewListQuerySerializer, responses={200: SavedViewListResponseSerializer, **READ_ERRORS})
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

            bucket = self._order_bucket(project_id, request.query_params.get("tab_type"))
            if request.query_params.get("consistency") == "primary":
                queryset = self.get_queryset().using("default")
                order = SavedViewTabOrder.objects.using("default").filter(**bucket).first()
            else:
                queryset = self.get_queryset().using(DATABASE_FOR_SAVED_VIEW_LIST)
                order = SavedViewTabOrder.objects.using(DATABASE_FOR_SAVED_VIEW_LIST).filter(**bucket).first()
            views = list(queryset.order_by("position", "created_at"))
            by_id = {str(view.id): view for view in views}
            stored = order.order if order else []
            effective = list(dict.fromkeys(pk for pk in stored if pk in by_id))
            seen = set(effective)
            effective.extend(str(view.id) for view in views if str(view.id) not in seen)
            serializer = SavedViewListSerializer(
                [by_id[pk] for pk in effective], many=True, context=self.get_serializer_context()
            )
            return self._gm.success_response({
                "default_tabs": DEFAULT_TABS, "custom_views": serializer.data,
                "tab_order": {"revision": order.revision if order else 0, "order": effective},
            })
        except Exception as e:
            logger.error(f"Failed to list saved views: {e}", exc_info=True)
            return self._gm.bad_request("Failed to list saved views.")

    # ------------------------------------------------------------------
    # RETRIEVE
    # ------------------------------------------------------------------

    @validated_request(query_serializer=SavedViewListQuerySerializer, responses={200: SavedViewDetailResponseSerializer, **READ_ERRORS})
    def retrieve(self, request, *args, **kwargs):
        try:
            instance = (get_object_or_404(self.get_queryset().using("default"), pk=kwargs[self.lookup_field])
                        if request.query_params.get("consistency") == "primary" else self.get_object())
            serializer = SavedViewDetailSerializer(
                instance, context=self.get_serializer_context()
            )
            return self._gm.success_response(serializer.data)
        except Http404:
            return self._gm.not_found("Saved view not found.")
        except Exception as e:
            logger.error(f"Failed to retrieve saved view: {e}", exc_info=True)
            return self._gm.bad_request("Failed to retrieve saved view.")

    # ------------------------------------------------------------------
    # CREATE
    # ------------------------------------------------------------------

    @validated_request(SavedViewCreateSerializer, responses={200: SavedViewDetailResponseSerializer, **READ_ERRORS})
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
                saved_view, context=self.get_serializer_context()
            )
            return self._gm.success_response(response_serializer.data)
        except Exception as e:
            logger.error(f"Failed to create saved view: {e}", exc_info=True)
            return self._gm.bad_request("Failed to create saved view.")

    # ------------------------------------------------------------------
    # UPDATE / PARTIAL UPDATE
    # ------------------------------------------------------------------

    @validated_request(SavedViewUpdateSerializer, strict_request_validation=False,
                       responses={200: SavedViewDetailResponseSerializer, **WRITE_ERRORS})
    def update(self, request, *args, **kwargs):
        try:
            with transaction.atomic():
                # Lock only the saved view: nullable creator joins cannot be locked.
                instance = self.get_queryset().select_for_update(of=("self",)).get(pk=kwargs[self.lookup_field])
                if not self._mutation_policy(instance)["can_edit"]:
                    return self._gm.forbidden_response("Only the owner can change this view.")
                if "expected_revision" not in request.data:
                    return self._revision_required()
                try:
                    expected = SavedViewUpdateSerializer().fields["expected_revision"].run_validation(request.data["expected_revision"])
                except serializers.ValidationError as exc:
                    return self._gm.bad_request(exc.detail)
                if instance.revision != expected:
                    return self._conflict(self._detail(instance))
                # Validate only after access, permission, and revision checks.
                serializer = SavedViewUpdateSerializer(data=request.data)
                if not serializer.is_valid():
                    return self._gm.bad_request(serializer.errors)
                data = dict(serializer.validated_data)
                data.pop("expected_revision")
                if instance.project_id is None and data.get("visibility") == "project":
                    data["visibility"] = "personal"
                new_name = data.get("name")
                if new_name and new_name != instance.name and SavedView.scoped(
                    instance.created_by, project=instance.project, workspace=instance.workspace,
                    tab_type=instance.tab_type,
                ).filter(name=new_name).exclude(id=instance.id).exists():
                    return self._gm.bad_request(f"A view named '{new_name}' already exists.")
                for attr, value in data.items():
                    setattr(instance, attr, value)
                instance.updated_by = request.user
                instance.revision += 1
                instance.save()
                return self._gm.success_response(self._detail(instance))
        except Http404:
            return self._gm.not_found("Saved view not found.")
        except SavedView.DoesNotExist:
            return self._gm.not_found("Saved view not found.")
        except IntegrityError:
            return self._gm.bad_request("A view with this name already exists.")
        except Exception:
            logger.error("Failed to update saved view", exc_info=True)
            return self._gm.bad_request("Failed to update saved view.")

    @validated_request(SavedViewUpdateSerializer, strict_request_validation=False,
                       responses={200: SavedViewDetailResponseSerializer, **WRITE_ERRORS})
    def partial_update(self, request, *args, **kwargs):
        return self.update(request, *args, **kwargs)

    @validated_request(responses={200: SavedViewMessageResponseSerializer, **WRITE_ERRORS},
        manual_parameters=[
            openapi.Parameter("expected_revision", openapi.IN_QUERY, type=openapi.TYPE_INTEGER, required=True, minimum=1),
            openapi.Parameter("project_id", openapi.IN_QUERY, type=openapi.TYPE_STRING, format="uuid"),
        ])
    def destroy(self, request, *args, **kwargs):
        try:
            with transaction.atomic():
                instance = self.get_queryset().select_for_update(of=("self",)).get(pk=kwargs[self.lookup_field])
                if not self._mutation_policy(instance)["can_delete"]:
                    return self._gm.forbidden_response("Only the owner can change this view.")
                if "expected_revision" not in request.query_params:
                    return self._revision_required()
                serializer = SavedViewDeleteQuerySerializer(data=request.query_params)
                if not serializer.is_valid():
                    return self._gm.bad_request(serializer.errors)
                if instance.revision != serializer.validated_data["expected_revision"]:
                    return self._conflict(self._detail(instance))
                instance.delete()
                return self._gm.success_response({"message": "View deleted."})
        except Http404:
            return self._gm.not_found("Saved view not found.")
        except SavedView.DoesNotExist:
            return self._gm.not_found("Saved view not found.")
        except Exception:
            logger.error("Failed to delete saved view", exc_info=True)
            return self._gm.bad_request("Failed to delete saved view.")

    # ------------------------------------------------------------------
    # DUPLICATE
    # ------------------------------------------------------------------

    @validated_request(SavedViewDuplicateSerializer, strict_request_validation=False, query_serializer=SavedViewListQuerySerializer, responses={200: SavedViewDetailResponseSerializer, **READ_ERRORS})
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
                if len(requested_name) > max_len:
                    return self._gm.bad_request("View name cannot exceed 255 characters.")
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
                new_view, context=self.get_serializer_context()
            )
            return self._gm.success_response(response_serializer.data)
        except Http404:
            return self._gm.not_found("Saved view not found.")
        except Exception as e:
            logger.error(f"Failed to duplicate saved view: {e}", exc_info=True)
            return self._gm.bad_request("Failed to duplicate saved view.")

    # ------------------------------------------------------------------
    # REORDER
    # ------------------------------------------------------------------

    @validated_request(SavedViewReorderSerializer, strict_request_validation=False,
        responses={200: SavedViewReorderResponseSerializer, **WRITE_ERRORS, 409: SavedViewOrderConflictResponseSerializer})
    @action(detail=False, methods=["post"], url_path="reorder")
    def reorder(self, request, *args, **kwargs):
        try:
            if "expected_revision" not in request.data:
                return self._revision_required()
            serializer = SavedViewReorderSerializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)
            data = serializer.validated_data
            project_id = data.get("project_id")
            tab_type = data.get("tab_type")
            bucket = self._order_bucket(project_id, tab_type)
            requested = [str(item["id"]) for item in sorted(data["order"], key=lambda item: item["position"])]
            # Start with the same workspace conventions as list, but scope by the
            # submitted bucket rather than the list query parameters.
            accessible = super().get_queryset()
            if project_id:
                if not Project.objects.filter(pk=project_id).exists():
                    return self._gm.custom_error_response(400, "The requested order is invalid.", code="invalid_order")
                accessible = accessible.filter(project_id=project_id).filter(
                    models.Q(created_by=request.user, visibility="personal") | models.Q(visibility="project"))
            else:
                accessible = accessible.filter(project__isnull=True, workspace=request.workspace,
                    tab_type=tab_type, created_by=request.user, visibility="personal")
            accessible_ids = {str(pk) for pk in accessible.values_list("id", flat=True)}
            if not set(requested).issubset(accessible_ids):
                return self._gm.custom_error_response(400, "The requested order is invalid.", code="invalid_order")
            with transaction.atomic():
                record = SavedViewTabOrder.objects.select_for_update().filter(**bucket).first()
                if (record.revision if record else 0) != data["expected_revision"]:
                    return self._conflict(self._order_data(record))
                if record:
                    record.order = requested
                    record.revision += 1
                    record.save()
                else:
                    try:
                        # Savepoint keeps the outer transaction usable after a
                        # concurrent first write wins the unique constraint.
                        with transaction.atomic():
                            record = SavedViewTabOrder.objects.create(**bucket, order=requested)
                    except IntegrityError:
                        current = SavedViewTabOrder.objects.select_for_update().get(**bucket)
                        return self._conflict(self._order_data(current))
                return self._gm.success_response({"message": "Views reordered.", "tab_order": self._order_data(record)})
        except Exception:
            logger.error("Failed to reorder saved views", exc_info=True)
            return self._gm.bad_request("Failed to reorder saved views.")
