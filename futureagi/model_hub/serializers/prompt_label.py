from django.db.models import Q
from rest_framework import serializers

from model_hub.models.prompt_label import LabelTypeChoices, PromptLabel


class PromptLabelSerializer(serializers.ModelSerializer):
    # System labels are global rows with no organization.
    organization = serializers.UUIDField(
        source="organization_id", read_only=True, allow_null=True
    )

    class Meta:
        model = PromptLabel
        fields = [
            "id",
            "organization",
            "name",
            "type",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at", "organization"]

    def validate_type(self, value: str):
        # Only allow creating custom labels via API; system labels are seeded and protected
        if self.instance is None and value != LabelTypeChoices.CUSTOM.value:
            raise serializers.ValidationError(
                "Only custom labels can be created via API"
            )
        return value

    def validate(self, attrs):
        # Block creating/renaming a custom label to a name that clashes (case-insensitive)
        # with any existing label in the same organization (system or custom).
        request = self.context.get("request")
        if not request or not getattr(request.user, "organization", None):
            return attrs

        org = getattr(request, "organization", None) or request.user.organization

        target_name = attrs.get("name")
        if target_name is None and self.instance is not None:

            return attrs

        existing_qs = PromptLabel.no_workspace_objects.filter(
            Q(organization=org, workspace=request.workspace)
            | Q(organization__isnull=True, type=LabelTypeChoices.SYSTEM.value),
            name__iexact=target_name,
        )
        if self.instance is not None:
            existing_qs = existing_qs.exclude(id=self.instance.id)

        if existing_qs.exists():
            raise serializers.ValidationError(
                {"name": "A label with this name already exists in your organization"}
            )

        return attrs
