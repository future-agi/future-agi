import math

import structlog
from django.db.models import OuterRef, Q, Subquery
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

logger = structlog.get_logger(__name__)
from model_hub.db_routing import DATABASE_FOR_EVAL_GROUP_LIST
from model_hub.models.eval_groups import EvalGroup, History
from model_hub.models.evals_metric import EvalTemplate
from model_hub.serializers.eval_group import EvalGroupSerializer
from model_hub.services.eval_group import (
    apply_eval_group,
    create_eval_group,
    edit_eval_list_manager,
)
from model_hub.utils.function_eval_params import get_function_params_schema
from model_hub.views.utils.utils import fetch_required_keys_for_eval_template
from tfc.routers import uses_db
from tfc.utils.base_viewset import BaseModelViewSetMixin
from tfc.utils.general_methods import GeneralMethods


class EvalGroupView(BaseModelViewSetMixin, ModelViewSet):
    """
    ViewSet for managing EvalGroup operations.
    Provides CRUD operations with organization-level isolation.
    """

    serializer_class = EvalGroupSerializer
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    def create(self, request, *args, **kwargs):
        try:
            name = request.data.get("name")
            description = request.data.get("description")
            eval_template_ids = request.data.get("eval_template_ids")

            if not name or not eval_template_ids or len(eval_template_ids) < 1:
                raise Exception("Invalid request data")

            data = create_eval_group(
                name,
                description,
                eval_template_ids,
                self.request.user,
                self.request.workspace,
            )

            return self._gm.success_response(data)

        except Exception as e:
            if "unique_eval_group_name_workspace_not_deleted" in str(e):
                return self._gm.bad_request(
                    "A eval group with this name already exists in your workspace."
                )
            return self._gm.bad_request(f"Failed to create eval group: {str(e)}")

    @uses_db(DATABASE_FOR_EVAL_GROUP_LIST, feature_key="feature:eval_group_list")
    def list(self, request, *args, **kwargs):
        """List all eval groups for the user's organization.

        Pure routing: three independent bulk reads in this method (main
        EvalGroup queryset, EvalGroup.eval_templates through-table, and
        EvalTemplate) all route to DATABASE_FOR_EVAL_GROUP_LIST when
        "feature:eval_group_list" is opted in. No query semantics change.
        """
        try:
            name = request.query_params.get("name")
            page_size = int(request.query_params.get("page_size", 10))
            page_number = int(request.query_params.get("page_number", 0))
            start = page_number * page_size
            end = start + page_size

            eval_groups = EvalGroup.no_workspace_objects.db_manager(
                DATABASE_FOR_EVAL_GROUP_LIST
            ).filter(
                Q(
                    workspace=request.workspace,
                    deleted=False,
                    organization=getattr(request, "organization", None)
                    or request.user.organization,
                )
                | Q(is_sample=True, deleted=False)
            ).select_related("organization", "created_by")

            if name:
                eval_groups = eval_groups.filter(name__icontains=name)

            # Get total count before pagination
            total_count = eval_groups.count()
            total_pages = math.ceil(total_count / page_size)

            eval_groups = eval_groups.order_by("-created_at")[start:end]

            response = []

            # Collect all template IDs from all eval_groups using through table to avoid N+1 queries
            all_template_ids = set()
            eval_group_template_map = {}

            # Get all eval_group IDs for efficient through table query
            eval_group_ids = [str(eval_group.id) for eval_group in eval_groups]

            # Single query to get all template relationships from through table
            all_relationships = (
                EvalGroup.eval_templates.through.objects
                .using(DATABASE_FOR_EVAL_GROUP_LIST)
                .filter(evalgroup_id__in=eval_group_ids)
                .values_list("evalgroup_id", "evaltemplate_id")
            )

            # Build mapping of eval_group_id -> template_ids
            for eval_group_id, template_id in all_relationships:
                eval_group_id_str = str(eval_group_id)
                template_id_str = str(template_id)

                if eval_group_id_str not in eval_group_template_map:
                    eval_group_template_map[eval_group_id_str] = []
                eval_group_template_map[eval_group_id_str].append(template_id_str)
                all_template_ids.add(template_id_str)

            # Single query to get all eval_templates
            all_eval_templates = EvalTemplate.no_workspace_objects.db_manager(
                DATABASE_FOR_EVAL_GROUP_LIST
            ).filter(id__in=list(all_template_ids))
            template_id_to_template = {
                str(template.id): template for template in all_eval_templates
            }

            for eval_group in eval_groups:
                # Get templates for this eval_group from our pre-fetched data
                template_ids = eval_group_template_map.get(str(eval_group.id), [])
                eval_templates = [
                    template_id_to_template[tid]
                    for tid in template_ids
                    if tid in template_id_to_template
                ]

                required_keys = fetch_required_keys_for_eval_template(eval_templates)
                response.append(
                    {
                        "id": str(eval_group.id),
                        "name": eval_group.name,
                        "description": eval_group.description,
                        "created_at": eval_group.created_at,
                        "required_keys": required_keys,
                        "evals_count": len(eval_templates),
                        "is_sample": eval_group.is_sample,
                    }
                )

            return self._gm.success_response(
                {
                    "data": response,
                    "total_count": total_count,
                    "total_pages": total_pages,
                }
            )
        except Exception as e:
            return self._gm.internal_server_error_response(
                f"Failed to list eval groups: {str(e)}"
            )

    def retrieve(self, request, *args, **kwargs):
        """Retrieve a specific eval group"""
        try:
            eval_group_id = kwargs.get("pk")
            name = request.query_params.get("name")

            try:
                eval_group = EvalGroup.no_workspace_objects.get(
                    Q(
                        id=eval_group_id,
                        organization=getattr(request, "organization", None)
                        or request.user.organization,
                        workspace=request.workspace,
                    )
                    | Q(id=eval_group_id, is_sample=True)
                )
            except EvalGroup.DoesNotExist:
                return self._gm.bad_request("Eval group does not exist for this user.")
            eval_group_data = self.get_serializer(eval_group).data
            # Get template IDs first, then query with no_workspace_objects
            # This works because:
            template_ids = list(
                eval_group.eval_templates.through.objects.filter(
                    evalgroup_id=eval_group.id
                ).values_list("evaltemplate_id", flat=True)
            )
            eval_templates = EvalTemplate.no_workspace_objects.filter(
                id__in=template_ids
            )

            if name:
                eval_templates = eval_templates.filter(name__icontains=name)

            # Get all template IDs for efficient querying
            template_ids = [str(template.id) for template in eval_templates]

            # Subquery to get the latest created_at for each template
            latest_history_subquery = (
                History.no_workspace_objects.filter(
                    source_id=str(eval_group_id),
                    source_type="EVAL_GROUP",
                    action="ADD",
                    reference_id=OuterRef("reference_id"),
                )
                .order_by("-created_at")
                .values("created_at")[:1]
            )

            # Main query to get only the latest history record for each template
            history_records = History.no_workspace_objects.filter(
                source_id=str(eval_group_id),
                source_type="EVAL_GROUP",
                action="ADD",
                reference_id__in=template_ids,
                created_at=Subquery(latest_history_subquery),
            ).select_related("action_by")

            # Create a dictionary mapping template_id to the latest history record
            latest_history_map = {
                str(record.reference_id): record for record in history_records
            }

            members_data = []
            models_lists = []  # Collect models from each template

            for eval_template in eval_templates:
                template_id_str = str(eval_template.id)
                latest_add_history = latest_history_map.get(template_id_str)
                models = eval_template.config.get("models", [])
                if models and len(models) > 0:
                    models_lists.append(set(models))  # Convert to set for intersection

                if latest_add_history:
                    # Use information from the latest ADD history record

                    added_by = (
                        latest_add_history.action_by.name
                        if not eval_group_data.get("is_sample", False)
                        and latest_add_history.action_by
                        else "Default Future Agi"
                    )
                    members_data.append(
                        {
                            "eval_template_id": template_id_str,
                            "name": str(eval_template.name),
                            "description": eval_template.description,  # Keep template description
                            "added_on": latest_add_history.created_at,
                            "added_by": added_by,
                            "tags": eval_template.eval_tags,
                            "required_keys": eval_template.config.get(
                                "required_keys", []
                            ),
                            "optional_keys": eval_template.config.get(
                                "optional_keys", []
                            ),
                            "models": models,
                        }
                    )

            required_keys = fetch_required_keys_for_eval_template(eval_templates)

            function_params_requirements = {}
            for eval_template in eval_templates:
                schema = get_function_params_schema(eval_template.config)
                if not schema:
                    continue

                for param_name, definition in schema.items():
                    entry = function_params_requirements.setdefault(
                        param_name,
                        {
                            "schema": definition,
                            "supported_by": [],
                            "required_for": [],
                        },
                    )
                    entry["supported_by"].append(
                        {
                            "eval_template_id": str(eval_template.id),
                            "name": eval_template.name,
                        }
                    )
                    if isinstance(definition, dict) and definition.get("required"):
                        entry["required_for"].append(
                            {
                                "eval_template_id": str(eval_template.id),
                                "name": eval_template.name,
                            }
                        )

            # Calculate intersection of models across all templates
            if models_lists:
                models_intersection = (
                    set.intersection(*models_lists)
                    if len(models_lists) > 1
                    else models_lists[0]
                )
            else:
                models_intersection = set()

            return self._gm.success_response(
                {
                    "eval_group": eval_group_data,
                    "members": members_data,
                    "required_keys": required_keys,
                    "models": list(models_intersection),
                    "function_params_requirements": function_params_requirements,
                }
            )
        except Exception as e:
            return self._gm.internal_server_error_response(
                f"Eval Group not found: {str(e)}"
            )

    def update(self, request, *args, **kwargs):
        """Update a eval group"""
        try:
            partial = kwargs.pop("partial", False)
            eval_group_id = kwargs.get("pk")
            try:
                eval_group = EvalGroup.objects.get(
                    id=eval_group_id,
                    organization=getattr(request, "organization", None)
                    or request.user.organization,
                )
            except EvalGroup.DoesNotExist:
                return self._gm.bad_request("Eval group does not exist for this user.")

            serializer = self.get_serializer(
                eval_group, data=request.data, partial=partial
            )

            name = request.data.get("name")

            if (
                name
                and EvalGroup.objects.filter(
                    name=name, deleted=False, workspace=request.workspace
                )
                .exclude(id=eval_group_id)
                .exists()
            ):
                return self._gm.bad_request("Eval group with this name already exists")
            serializer.is_valid(raise_exception=True)
            self.perform_update(serializer)
            return self._gm.success_response(serializer.data)
        except Exception as e:
            return self._gm.bad_request(f"Failed to update eval group: {str(e)}")

    def perform_update(self, serializer):
        """Ensure organization is maintained when updating a eval group"""
        serializer.save()

    def partial_update(self, request, *args, **kwargs):
        """Partially update a prompt base template"""
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        """Delete a eval group (soft delete)"""
        try:
            eval_group_id = kwargs.get("pk")
            try:
                eval_group = EvalGroup.objects.get(
                    id=eval_group_id,
                    organization=getattr(request, "organization", None)
                    or request.user.organization,
                )
            except EvalGroup.DoesNotExist:
                return self._gm.bad_request("Eval group does not exist for this user.")
            self.perform_destroy(eval_group)
            # Clear all eval templates from the group - single delete query
            eval_group.eval_templates.through.objects.filter(
                evalgroup_id=eval_group.id
            ).delete()

            return self._gm.success_response("Eval group deleted successfully")
        except Exception as e:
            return self._gm.bad_request(f"Failed to delete eval group: {str(e)}")

    def perform_destroy(self, instance):
        """Override destroy to implement soft delete"""
        instance.deleted = True
        instance.save()

    @action(detail=False, methods=["post"], url_path="edit-eval-list")
    def edit_eval_list(self, request, *args, **kwargs):
        try:
            added_template_ids = request.data.get("added_template_ids")
            deleted_template_ids = request.data.get("deleted_template_ids")
            eval_group_id = request.data.get("eval_group_id")

            if not added_template_ids and not deleted_template_ids:
                return self._gm.bad_request(
                    "Atleast one of added or deleted template ids are required"
                )

            if not eval_group_id:
                return self._gm.bad_request("Eval group id is required")

            try:
                EvalGroup.objects.get(
                    id=eval_group_id,
                    organization=getattr(request, "organization", None)
                    or request.user.organization,
                )
            except EvalGroup.DoesNotExist:
                return self._gm.bad_request("Eval group does not exist for this user.")

            edit_eval_list_manager(
                eval_group_id=eval_group_id,
                added_template_ids=added_template_ids,
                deleted_template_ids=deleted_template_ids,
                user=request.user,
            )

            return self._gm.success_response("Eval group has been updated succesfully")

        except Exception as e:
            logger.exception(f"Error in editing eval list: {str(e)}")
            return self._gm.internal_server_error_response(
                f"Error in editing evals list from eval group: {str(e)}"
            )

    @action(detail=False, methods=["post"], url_path="apply-eval-group")
    def apply_eval_group(self, request, *args, **kwargs):
        try:
            eval_group_id = request.data.get("eval_group_id")
            filters = request.data.get("filters")
            page_id = request.data.get("page_id")
            mapping = request.data.get("mapping")
            deselected_evals = request.data.get("deselected_evals")
            params = request.data.get("params", {})

            if params is not None and not isinstance(params, dict):
                return self._gm.bad_request(
                    "Invalid function parameter input. Please check the value and try again."
                )

            try:
                eval_group = EvalGroup.no_workspace_objects.get(
                    Q(
                        id=eval_group_id,
                        organization=getattr(request, "organization", None)
                        or request.user.organization,
                        workspace=request.workspace,
                    )
                    | Q(id=eval_group_id, is_sample=True)
                )
            except EvalGroup.DoesNotExist:
                return self._gm.bad_request("Eval group does not exist for this user.")

            response = apply_eval_group(
                eval_group=eval_group,
                filters=filters,
                page_id=page_id,
                user=request.user,
                mapping=mapping,
                workspace=request.workspace,
                deselected_evals=deselected_evals,
                params=params,
            )
            return self._gm.success_response(response)

        except Exception as e:
            logger.exception(f"Error in applying eval group: {str(e)}")
            return self._gm.internal_server_error_response(
                f"Error in applying eval group: {str(e)}"
            )
