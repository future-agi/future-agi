from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from model_hub.serializers.develop_dataset import SecretSerializer
from tfc.utils.base_viewset import BaseModelViewSetMixinWithUserOrg


class SecretViewSet(BaseModelViewSetMixinWithUserOrg, viewsets.ModelViewSet):
    """
    ViewSet for managing secrets.
    Provides CRUD operations with organization-level isolation.
    """

    serializer_class = SecretSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """
        Get base queryset with automatic filtering from mixin
        """
        return super().get_queryset()

    def perform_create(self, serializer):
        """Automatically set the organization when creating a secret"""
        serializer.save(
            organization=getattr(self.request, "organization", None)
            or self.request.user.organization,
            workspace=getattr(self.request, "workspace", None),
        )

    def perform_update(self, serializer):
        """Ensure organization is maintained when updating a secret"""
        serializer.save(
            organization=getattr(self.request, "organization", None)
            or self.request.user.organization,
            workspace=getattr(self.request, "workspace", None),
        )

    def perform_destroy(self, instance):
        """Override destroy to implement soft delete"""
        instance.delete()
