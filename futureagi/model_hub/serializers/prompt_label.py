from django.db.models import Q
from rest_framework import serializers

from model_hub.models.prompt_label import LabelTypeChoices, PromptLabel


class PromptLabelSerializer(serializers.ModelSerializer):
    """A named label used to mark prompt-template versions (e.g. Production, Staging, Development, or your own custom tag) so you can promote and reference versions by environment rather than by version number. Built-in Production/Staging/Development are immutable "system" labels shared across the org; via the API you can only create/update/delete your own "custom" labels. List/read with list_prompt_labels / get_prompt_label, create with create_prompt_label, edit with update_prompt_label, and remove a custom label with delete_prompt_label."""

    organization = serializers.UUIDField(
        source="organization_id",
        read_only=True,
        help_text="UUID of the organization that owns this custom label (null for shared system labels); set automatically, not supplied by the caller.",
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
        extra_kwargs = {
            "id": {"help_text": "UUID of this prompt label (from list_prompt_labels)."},
            "name": {
                "help_text": "Display name of the label, e.g. 'Production' or a custom tag; must be unique (case-insensitive) within the organization.",
            },
            "type": {
                "help_text": "Label kind: 'custom' for org-defined labels or 'system' for the built-in Production/Staging/Development. The API only permits creating 'custom' labels; 'system' labels cannot be created, modified, or deleted here.",
            },
            "metadata": {
                "help_text": "Optional JSON object for arbitrary key/value metadata about the label (e.g. a description).",
            },
            "created_at": {"help_text": "Timestamp when the label was created (read-only)."},
            "updated_at": {"help_text": "Timestamp when the label was last updated (read-only)."},
        }

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
