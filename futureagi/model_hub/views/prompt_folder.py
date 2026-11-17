import traceback

from django.db import IntegrityError, models, transaction
from django.http import Http404
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from model_hub.models.prompt_folders import PromptFolder
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from model_hub.serializers.prompt_folder import PromptFolderSerializer
from model_hub.services.prompt_actor import acting_as, request_actor
from model_hub.services.prompt_label import release_prompts
from model_hub.utils.workspace_scope import (
    request_organization,
    request_workspace_filter,
)
from tfc.utils.base_viewset import BaseModelViewSetMixin
from tfc.utils.general_methods import GeneralMethods


class PromptFolderViewSet(BaseModelViewSetMixin, viewsets.ModelViewSet):
    """
    ViewSet for managing PromptFolder operations.
    Provides CRUD operations with organization-level isolation.
    """

    serializer_class = PromptFolderSerializer
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    def _duplicate_name_response(self):
        return self._gm.bad_request(
            "A prompt folder with this name already exists in your organization."
        )

    def get_queryset(self):
        sample_scope = models.Q(
            is_sample=True,
            organization__isnull=True,
            workspace__isnull=True,
        )
        organization = request_organization(self.request)
        if organization is None:
            # No organization: only the shared sample folders. Filtering on
            # organization=None would also match every other folder that has
            # no organization.
            scope = sample_scope
        else:
            scope = (
                models.Q(organization=organization)
                & request_workspace_filter(self.request)
            ) | sample_scope
        return PromptFolder.no_workspace_objects.filter(
            scope,
            deleted=False,
        ).select_related("organization", "workspace", "created_by", "parent_folder")

    def create(self, request, *args, **kwargs):
        """Create a new prompt folder"""
        if request_organization(request) is None:
            # A caller with no organization has nowhere to keep a folder. This
            # answer comes before the body is read, so a parent folder of any
            # other organization gets the same answer as an unknown one.
            return self._gm.not_found("Organization not found")
        try:
            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            try:
                with transaction.atomic():
                    self.perform_create(serializer)
            except IntegrityError as e:
                if (
                    "unique_prompt_folder_name_organization_workspace_not_deleted"
                    in str(e)
                ):
                    return self._duplicate_name_response()
                raise
            return self._gm.create_response(serializer.data)
        except Exception as e:
            if "unique_prompt_folder_name_organization_workspace_not_deleted" in str(e):
                return self._duplicate_name_response()
            return self._gm.bad_request(f"Failed to create prompt folder: {str(e)}")

    def perform_create(self, serializer):
        """Automatically set the organization when creating a prompt folder"""
        serializer.save(
            organization=request_organization(self.request),
            created_by=self.request.user,
            workspace=getattr(self.request, "workspace", None),
            is_sample=False,
        )

    def list(self, request, *args, **kwargs):
        """List all prompt folders for the user's organization"""
        try:
            root_folders = self.get_queryset().filter(parent_folder=None)
            response = PromptFolderSerializer(root_folders, many=True).data
            return self._gm.success_response(response)
        except Exception as e:
            traceback.print_exc()
            return self._gm.internal_server_error_response(
                f"Failed to list prompt folders: {str(e)}"
            )

    def retrieve(self, request, *args, **kwargs):
        """Retrieve a specific prompt folder"""
        try:
            instance = self.get_object()
            serializer = self.get_serializer(instance)
            return self._gm.success_response(serializer.data)
        except Http404:
            return self._gm.not_found("Prompt folder not found")
        except Exception as e:
            return self._gm.bad_request(f"Prompt folder not found: {str(e)}")

    def update(self, request, *args, **kwargs):
        """Update a prompt folder"""
        try:
            partial = kwargs.pop("partial", False)
            instance = self.get_object()
            if instance.is_sample:
                return self._gm.bad_request("Sample prompt folders cannot be modified")
            serializer = self.get_serializer(
                instance, data=request.data, partial=partial
            )
            serializer.is_valid(raise_exception=True)
            try:
                with transaction.atomic():
                    self.perform_update(serializer)
            except IntegrityError as e:
                if (
                    "unique_prompt_folder_name_organization_workspace_not_deleted"
                    in str(e)
                ):
                    return self._duplicate_name_response()
                raise
            return self._gm.success_response(serializer.data)
        except Http404:
            return self._gm.not_found("Prompt folder not found")
        except Exception as e:
            if "unique_prompt_folder_name_organization_workspace_not_deleted" in str(e):
                return self._duplicate_name_response()
            return self._gm.bad_request(f"Failed to update prompt folder: {str(e)}")

    def perform_update(self, serializer):
        """Ensure organization is maintained when updating a prompt folder"""
        serializer.save(
            organization=request_organization(self.request),
            workspace=getattr(self.request, "workspace", None),
            is_sample=False,
        )

    def partial_update(self, request, *args, **kwargs):
        """Partially update a prompt folder"""
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        """Delete a prompt folder (soft delete)"""
        try:
            instance = self.get_object()
            if instance.is_sample:
                return self._gm.bad_request("Sample prompt folders cannot be deleted")
            self.perform_destroy(instance)
            return self._gm.success_response("Prompt folder deleted successfully")
        except Http404:
            return self._gm.not_found("Prompt folder not found")
        except Exception as e:
            return self._gm.bad_request(f"Failed to delete prompt folder: {str(e)}")

    def perform_destroy(self, instance):
        """Override destroy to implement soft delete"""
        now = timezone.now()

        prompt_templates = PromptTemplate.objects.filter(prompt_folder=instance)
        prompt_template_ids = list(prompt_templates.values_list("id", flat=True))

        if prompt_template_ids:
            with transaction.atomic(), acting_as(request_actor(self.request)):
                # The one writer locks the prompts and their versions, then releases each prompt's labels (one
                # version_deleted webhook event per label), before the rows are soft-deleted in bulk.
                release_prompts(prompt_template_ids)
                PromptVersion.objects.filter(
                    original_template_id__in=prompt_template_ids
                ).update(
                    deleted=True,
                    deleted_at=now,
                )
                prompt_templates.update(deleted=True, deleted_at=now)

        instance.deleted = True
        instance.deleted_at = now
        instance.save(update_fields=["deleted", "deleted_at", "updated_at"])
