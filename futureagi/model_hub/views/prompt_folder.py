import traceback

from django.db import models
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from model_hub.models.prompt_folders import PromptFolder
from model_hub.models.run_prompt import PromptTemplate, PromptVersion
from model_hub.serializers.prompt_folder import PromptFolderSerializer
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

    def create(self, request, *args, **kwargs):
        """Create a new prompt folder"""
        try:
            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            self.perform_create(serializer)
            return self._gm.create_response(serializer.data)
        except Exception as e:
            if "unique_prompt_folder_name_organization_workspace_not_deleted" in str(e):
                return self._gm.bad_request(
                    "A prompt folder with this name already exists in your organization."
                )
            return self._gm.bad_request(f"Failed to create prompt folder: {str(e)}")

    def perform_create(self, serializer):
        """Automatically set the organization when creating a prompt folder"""
        serializer.save(
            organization=getattr(self.request, "organization", None)
            or self.request.user.organization,
            created_by=self.request.user,
            workspace=self.request.workspace,
        )

    def list(self, request, *args, **kwargs):
        """List all prompt folders for the user's organization"""
        try:
            root_folders = PromptFolder.no_workspace_objects.filter(
                (
                    models.Q(workspace=self.request.workspace)
                    & models.Q(
                        organization=getattr(self.request, "organization", None)
                        or self.request.user.organization
                    )
                )
                | models.Q(is_sample=True),
                parent_folder=None,
                deleted=False,
            )
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
        except Exception as e:
            return self._gm.bad_request(f"Prompt folder not found: {str(e)}")

    def update(self, request, *args, **kwargs):
        """Update a prompt folder"""
        try:
            partial = kwargs.pop("partial", False)
            instance = self.get_object()
            serializer = self.get_serializer(
                instance, data=request.data, partial=partial
            )
            serializer.is_valid(raise_exception=True)
            self.perform_update(serializer)
            return self._gm.success_response(serializer.data)
        except Exception as e:
            return self._gm.bad_request(f"Failed to update prompt folder: {str(e)}")

    def perform_update(self, serializer):
        """Ensure organization is maintained when updating a prompt folder"""
        serializer.save(
            organization=getattr(self.request, "organization", None)
            or self.request.user.organization,
            workspace=self.request.workspace,
        )

    def partial_update(self, request, *args, **kwargs):
        """Partially update a prompt folder"""
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        """Delete a prompt folder (soft delete)"""
        try:
            instance = self.get_object()
            self.perform_destroy(instance)
            return self._gm.success_response("Prompt folder deleted successfully")
        except Exception as e:
            return self._gm.bad_request(f"Failed to delete prompt folder: {str(e)}")

    def perform_destroy(self, instance):
        """Override destroy to implement soft delete"""
        now = timezone.now()

        prompt_templates = PromptTemplate.objects.filter(prompt_folder=instance)
        prompt_template_ids = list(prompt_templates.values_list("id", flat=True))

        if prompt_template_ids:
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
