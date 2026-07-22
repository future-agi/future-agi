from django.db.models import Q
from rest_framework import serializers

from model_hub.models.prompt_base_template import PromptBaseTemplate
from model_hub.models.run_prompt import PromptVersion


def _workspace_filter(workspace, field_name):
    if workspace is None:
        return Q()
    if getattr(workspace, "is_default", False):
        organization = getattr(workspace, "organization", None)
        query = Q(**{field_name: workspace})
        if organization is not None:
            query |= Q(
                **{
                    f"{field_name}__is_default": True,
                    f"{field_name}__organization": organization,
                }
            )
        query |= Q(**{f"{field_name}__isnull": True})
        return query
    return Q(**{field_name: workspace})


class PromptBaseTemplateSerializer(serializers.ModelSerializer):
    created_by = serializers.SerializerMethodField()

    class Meta:
        model = PromptBaseTemplate
        fields = [
            "id",
            "name",
            "organization",
            "workspace",
            "created_at",
            "updated_at",
            "is_sample",
            "prompt_version",
            "category",
            "prompt_config_snapshot",
            "created_by",
        ]
        read_only_fields = ["organization", "workspace", "is_sample"]

    def get_created_by(self, obj):
        """
        Return the name of the user who created this template.
        Returns None if created_by is None.
        """
        if obj.created_by:
            return obj.created_by.name
        return obj.organization.name if obj.organization else None

    def validate_prompt_version(self, value):
        """
        Validate that the prompt version exists, belongs to the user's organization,
        is not deleted, and is not in draft mode.
        """
        if not value:
            raise serializers.ValidationError("Prompt version is required")

        # Get the organization from the context (should be set in the view)
        request = self.context.get("request")
        if (
            not request
            or not hasattr(request, "user")
            or not (getattr(request, "organization", None) or request.user.organization)
        ):
            raise serializers.ValidationError("User organization not found")

        user_organization = (
            getattr(request, "organization", None) or request.user.organization
        )
        workspace = getattr(request, "workspace", None)

        try:
            prompt_version = PromptVersion.no_workspace_objects.get(
                _workspace_filter(workspace, "original_template__workspace"),
                id=value.id,
                original_template__organization=user_organization,
                original_template__deleted=False,
                deleted=False,
            )
        except PromptVersion.DoesNotExist:
            raise serializers.ValidationError("Prompt version not found")  # noqa: B904

        if prompt_version.is_draft:
            raise serializers.ValidationError(
                "Prompt version is in draft mode. Run it first and then try again."
            )

        return value

    def validate(self, attrs):
        """
        Additional validation that can access all fields.
        Auto-populate prompt_config_snapshot from the prompt_version if not provided.
        """
        attrs = super().validate(attrs)

        # If prompt_version is provided and prompt_config_snapshot is not provided,
        # automatically set it from the prompt_version
        if "prompt_version" in attrs and "prompt_config_snapshot" not in attrs:
            prompt_version = attrs["prompt_version"]
            attrs["prompt_config_snapshot"] = prompt_version.prompt_config_snapshot

        return attrs
