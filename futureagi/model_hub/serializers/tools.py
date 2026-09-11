from django.db import models
from rest_framework import serializers

from model_hub.models.openai_tools import Tools


class ToolsSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tools
        fields = ["id", "name", "description", "config", "config_type", "organization"]
        read_only_fields = ["organization"]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        name = attrs.get("name", getattr(self.instance, "name", None))
        if not name:
            return attrs

        request = self.context.get("request")
        organization = getattr(request, "organization", None)
        if not organization and getattr(request, "user", None):
            organization = getattr(request.user, "organization", None)
        workspace = getattr(request, "workspace", None)

        queryset = Tools.no_workspace_objects.filter(name=name)
        if organization:
            queryset = queryset.filter(organization=organization)

        if workspace:
            if getattr(workspace, "is_default", False):
                queryset = queryset.filter(
                    models.Q(workspace=workspace)
                    | models.Q(
                        workspace__is_default=True,
                        workspace__organization=workspace.organization,
                    )
                    | models.Q(workspace__isnull=True)
                )
            else:
                queryset = queryset.filter(workspace=workspace)
        else:
            queryset = queryset.filter(workspace__isnull=True)

        if self.instance is not None:
            queryset = queryset.exclude(pk=self.instance.pk)

        if queryset.exists():
            raise serializers.ValidationError(
                {"name": "A tool with this name already exists in this workspace."}
            )

        return attrs
