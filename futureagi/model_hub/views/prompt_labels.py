import uuid

from django.db import transaction
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from drf_yasg.utils import swagger_auto_schema
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import (
    NotAuthenticated,
    PermissionDenied,
)
from rest_framework.exceptions import (
    ValidationError as DRFValidationError,
)
from rest_framework.permissions import IsAuthenticated

from model_hub.models.prompt_label import LabelTypeChoices, PromptLabel
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from model_hub.serializers.contracts import MODEL_HUB_TEXT_ERROR_RESPONSES
from model_hub.serializers.prompt_label import PromptLabelSerializer
from model_hub.serializers.prompt_template import (
    PromptTemplateSerializer,
)
from model_hub.services.prompt_label import assign_labels_to_version
from tfc.utils.base_viewset import BaseModelViewSetMixin
from tfc.utils.general_methods import GeneralMethods

prompt_label_errors = swagger_auto_schema(responses=MODEL_HUB_TEXT_ERROR_RESPONSES)


def _exception_detail_to_text(detail) -> str:
    if isinstance(detail, dict):
        messages = []
        for field, field_errors in detail.items():
            if isinstance(field_errors, (list, tuple)):
                messages.extend(f"{field}: {error}" for error in field_errors)
            else:
                messages.append(f"{field}: {field_errors}")
        return "; ".join(messages) or "Invalid request"
    if isinstance(detail, (list, tuple)):
        return "; ".join(str(item) for item in detail)
    return str(detail)


@method_decorator(name="list", decorator=prompt_label_errors)
@method_decorator(name="create", decorator=prompt_label_errors)
@method_decorator(name="retrieve", decorator=prompt_label_errors)
@method_decorator(name="update", decorator=prompt_label_errors)
@method_decorator(name="partial_update", decorator=prompt_label_errors)
@method_decorator(name="destroy", decorator=prompt_label_errors)
@method_decorator(name="remove_label_from_version", decorator=prompt_label_errors)
@method_decorator(name="create_system_labels", decorator=prompt_label_errors)
@method_decorator(name="get_by_name", decorator=prompt_label_errors)
@method_decorator(name="assign_label_by_id", decorator=prompt_label_errors)
@method_decorator(name="set_default", decorator=prompt_label_errors)
@method_decorator(name="template_labels", decorator=prompt_label_errors)
@method_decorator(name="assign_multiple_labels", decorator=prompt_label_errors)
class PromptLabelViewSet(BaseModelViewSetMixin, viewsets.ModelViewSet):
    serializer_class = PromptLabelSerializer
    permission_classes = [IsAuthenticated]
    gm = GeneralMethods()

    def get_queryset(self):
        # Get base queryset with automatic filtering from mixin
        queryset = super().get_queryset()

        # Apply special organization filtering for prompt labels
        org = (
            getattr(self.request, "organization", None)
            or self.request.user.organization
        )
        queryset = PromptLabel.no_workspace_objects.filter(
            Q(organization=org, workspace=self.request.workspace)
            | Q(organization__isnull=True, type=LabelTypeChoices.SYSTEM.value)
        )

        return queryset

    def get_serializer_class(self):
        return self.serializer_class

    def handle_exception(self, exc):
        if isinstance(exc, DRFValidationError):
            return self.gm.bad_request(_exception_detail_to_text(exc.detail))
        if isinstance(exc, NotAuthenticated):
            return self.gm.unauthorized_response()
        if isinstance(exc, PermissionDenied):
            return self.gm.forbidden_response(_exception_detail_to_text(exc.detail))
        if isinstance(exc, Http404):
            return self.gm.not_found("Prompt label resource not found")
        return super().handle_exception(exc)

    def perform_create(self, serializer):
        # Create custom label in caller's org
        serializer.save(
            organization=getattr(self.request, "organization", None)
            or self.request.user.organization,
            workspace=getattr(self.request, "workspace", None),
            type=LabelTypeChoices.CUSTOM.value,
        )

    def update(self, request, *args, **kwargs):
        # System labels are immutable
        instance: PromptLabel = self.get_object()
        if instance.type == LabelTypeChoices.SYSTEM.value:
            return self.gm.bad_request("System labels cannot be modified")
        return super().update(request, *args, **kwargs)

    def perform_update(self, serializer):
        serializer.save(
            organization=getattr(self.request, "organization", None)
            or self.request.user.organization,
            workspace=getattr(self.request, "workspace", None),
        )

    def destroy(self, request, *args, **kwargs):
        instance: PromptLabel = self.get_object()
        if instance.type == LabelTypeChoices.SYSTEM.value:
            return self.gm.bad_request("System labels cannot be deleted")
        return super().destroy(request, *args, **kwargs)

    @action(detail=False, methods=["post"], url_path="remove")
    @transaction.atomic
    def remove_label_from_version(self, request):
        """Detach label from a prompt version."""
        label_id = request.data.get("label_id")
        version_id = request.data.get("version_id")
        if not label_id or not version_id:
            return self.gm.bad_request("'label_id' and 'version_id' are required")

        try:
            label = PromptLabel.objects.get(id=label_id)
            version = PromptVersion.objects.get(id=version_id)
        except (PromptLabel.DoesNotExist, PromptVersion.DoesNotExist):
            return self.gm.bad_request("Invalid label or version")

        org = getattr(request, "organization", None) or request.user.organization
        template_org = getattr(version.original_template, "organization", None)
        if template_org != org:
            return self.gm.bad_request(
                "You don't have permission to modify this template"
            )

        version.labels.remove(label)
        return self.gm.success_response({"detail": "Label removed successfully"})

    @action(detail=False, methods=["post"], url_path="create-system-labels")
    def create_system_labels(self, request):
        """Create (idempotently) Production, Staging, Development system labels for the caller's org."""
        created = PromptLabel.create_default_system_labels(
            getattr(request, "organization", None) or request.user.organization
        )
        return self.gm.success_response(
            {
                "created": [lbl.name for lbl in created],
                "count": len(created),
            }
        )

    # ------------------------------------------------------------------
    # SDK- and FE-friendly helper endpoints
    # ------------------------------------------------------------------

    @action(detail=False, methods=["get"], url_path="get-by-name")
    def get_by_name(self, request):
        """Fetch a prompt version by template name and either explicit version or label.

        Query params:
          - name: template name (required)
          - version: version name like v1 (optional)
          - label: label name like Production/Staging/Development or custom (optional)

        Precedence: version over label. No fallback logic.
        """
        name = request.query_params.get("name")
        version = request.query_params.get("version")
        label = request.query_params.get("label")

        if not name:
            return self.gm.bad_request("'name' is required")

        template = get_object_or_404(
            PromptTemplate.objects.filter(
                name__exact=name,
                organization=getattr(request, "organization", None)
                or request.user.organization,
                deleted=False,
            )
        )

        base_qs = PromptVersion.objects.filter(
            original_template=template,
            original_template__organization=getattr(request, "organization", None)
            or request.user.organization,
            deleted=False,
        )

        execution = None
        if version:
            try:
                execution = base_qs.get(template_version=version)
            except PromptVersion.DoesNotExist:
                return self.gm.bad_request(
                    f"No version '{version}' found for this template"
                )
        elif label:
            # Directly filter through the M2M by label name on this template's versions
            execution = base_qs.filter(labels__name__iexact=label).first()
            if not execution:
                return self.gm.bad_request(f"No version found for label '{label}'")
        else:
            return self.gm.bad_request("Provide either 'version' or 'label'")

        tpl_data = PromptTemplateSerializer(template).data
        response_data = {
            **tpl_data,
            "prompt_config": (
                [execution.prompt_config_snapshot]
                if isinstance(execution.prompt_config_snapshot, dict)
                else execution.prompt_config_snapshot
            ),
            "version": execution.template_version,
            "variable_names": execution.variable_names,
            "output": execution.output,
            "is_draft": execution.is_draft,
            "metadata": execution.metadata,
            "labels": [
                {
                    "id": str(lbl.id),
                    "name": lbl.name,
                    "type": lbl.type,
                }
                for lbl in execution.labels.all()
            ],
        }
        return self.gm.success_response(response_data)

    @action(
        detail=False,
        methods=["post"],
        url_path="(?P<template_id>[^/]+)/(?P<label_id>[^/]+)/assign-label-by-id",
    )
    @transaction.atomic
    def assign_label_by_id(self, request, template_id, label_id):
        """Assign a label to a specific version by template name and version name.

        Body: {"template_name": "...", "version": "v1", "label": "production|staging|..."}
        Enforces template-level exclusivity for the label.
        """
        version = request.data.get("version")

        if not template_id or not version or not label_id:
            return self.gm.bad_request(
                "'template_id', 'version' and 'label_id' are required"
            )

        template = get_object_or_404(
            PromptTemplate.objects.filter(
                id=template_id,
                organization=getattr(request, "organization", None)
                or request.user.organization,
                deleted=False,
            )
        )

        try:
            target_version = PromptVersion.objects.get(
                original_template=template,
                original_template__organization=getattr(request, "organization", None)
                or request.user.organization,
                template_version=version,
                deleted=False,
            )
        except PromptVersion.DoesNotExist:
            return self.gm.bad_request(
                f"No version '{version}' found for template '{template_id}'"
            )

        label_obj = get_object_or_404(
            PromptLabel.no_workspace_objects.filter(
                Q(
                    organization=getattr(request, "organization", None)
                    or request.user.organization,
                    workspace=request.workspace,
                )
                | Q(organization__isnull=True, type=LabelTypeChoices.SYSTEM.value),
                id=label_id,
            )
        )
        if label_obj.organization and label_obj.organization != (
            getattr(request, "organization", None) or request.user.organization
        ):
            return self.gm.bad_request("You don't have permission to use this label")

        # Remove from other versions under same template
        other_versions = PromptVersion.objects.filter(
            original_template=template,
            original_template__organization=getattr(request, "organization", None)
            or request.user.organization,
            labels__id=label_obj.id,
            deleted=False,
        ).exclude(id=target_version.id)
        moved_from = []
        for ov in other_versions:
            ov.labels.remove(label_obj)
            moved_from.append(ov.template_version)

        target_version.labels.add(label_obj)

        return self.gm.success_response(
            {
                "template_id": str(template.id),
                "version": target_version.template_version,
                "label_id": label_obj.id,
                "label_name": label_obj.name,
                "moved_from_versions": moved_from,
            }
        )

    @action(detail=False, methods=["post"], url_path="set-default")
    @transaction.atomic
    def set_default(self, request):
        """Set default version for a template by name and version.

        Body: {"template_name": "...", "version": "v1"}
        """
        template_name = request.data.get("template_name")
        version_name = request.data.get("version")

        if not template_name or not version_name:
            return self.gm.bad_request("'template_name' and 'version' are required")

        template = get_object_or_404(
            PromptTemplate.objects.filter(
                name__exact=template_name,
                organization=getattr(request, "organization", None)
                or request.user.organization,
                deleted=False,
            )
        )

        try:
            version_obj = PromptVersion.objects.get(
                original_template=template,
                template_version=version_name,
                deleted=False,
            )
        except PromptVersion.DoesNotExist:
            return self.gm.bad_request(
                f"No version '{version_name}' found for template '{template_name}'"
            )

        version_obj.is_default = True
        version_obj.save(
            update_fields=["is_default", "updated_at"]
        )  # save() will unset others

        return self.gm.success_response(
            {
                "template_id": str(template.id),
                "version": version_obj.template_version,
                "is_default": True,
            }
        )

    @action(detail=False, methods=["get"], url_path="template-labels")
    def template_labels(self, request):
        """List versions with labels for a template by name or id.

        Query: template_name or template_id
        """
        template_name = request.query_params.get("template_name")
        template_id = request.query_params.get("template_id")

        if not template_name and not template_id:
            return self.gm.bad_request("'template_name' or 'template_id' is required")

        tpl_qs = PromptTemplate.objects.filter(
            organization=getattr(request, "organization", None)
            or request.user.organization,
            deleted=False,
        )
        if template_id:
            template = get_object_or_404(tpl_qs, id=template_id)
        else:
            template = get_object_or_404(tpl_qs, name__exact=template_name)

        versions = PromptVersion.objects.filter(
            original_template=template,
            original_template__organization=getattr(request, "organization", None)
            or request.user.organization,
            deleted=False,
        ).order_by("created_at")
        allowed_label_filter = Q(
            organization=getattr(request, "organization", None)
            or request.user.organization,
            workspace=request.workspace,
        ) | Q(organization__isnull=True, type=LabelTypeChoices.SYSTEM.value)
        data = [
            {
                "version": v.template_version,
                "labels": list(
                    PromptLabel.no_workspace_objects.filter(
                        allowed_label_filter,
                        prompt_versions=v,
                        deleted=False,
                    ).values_list("name", flat=True)
                ),
                "is_default": v.is_default,
                "is_draft": v.is_draft,
            }
            for v in versions
        ]
        return self.gm.success_response(data)

    @action(detail=False, methods=["post"], url_path="assign-multiple-labels")
    @transaction.atomic
    def assign_multiple_labels(self, request):

        try:
            template_version_id = request.data.get("template_version_id")
            label_ids = request.data.get("label_ids", [])

            if not isinstance(label_ids, list):
                return self.gm.bad_request("label_ids must be an array")

            # Validate UUIDs
            try:
                label_ids = [str(uuid.UUID(lid)) for lid in label_ids]
            except (ValueError, AttributeError):
                return self.gm.bad_request("Invalid UUID format in label_ids")

            if not template_version_id:
                return self.gm.bad_request("Template version id is required")

            if not label_ids or len(label_ids) == 0:
                return self.gm.bad_request("Label ids are required")

            label_ids = list(set(label_ids))

            assign_labels_to_version(
                template_version_id, label_ids, request.user, request.workspace
            )

            return self.gm.success_response("Labels assigned successfully")

        except ValueError as e:
            return self.gm.bad_request(f"Value Error: {str(e)}")

        except Exception as e:
            return self.gm.bad_request(f"Error assigning multiple labels: {str(e)}")
