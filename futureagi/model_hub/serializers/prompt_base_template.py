from django.db.models import Q
from rest_framework import serializers

from model_hub.models.prompt_base_template import PromptBaseTemplate
from model_hub.models.run_prompt import PromptVersion

TEXT_ONLY_BASE_TEMPLATE_ERROR = (
    "Prompt base templates can only contain text content blocks. "
    "Remote media URL blocks are not supported in shared base templates."
)
MEDIA_CONTENT_KEYS = {"image_url", "audio_url", "pdf_url"}


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


def _snapshot_configs(snapshot):
    if isinstance(snapshot, dict):
        return [snapshot]
    if isinstance(snapshot, list):
        return [config for config in snapshot if isinstance(config, dict)]
    return []


def _has_media_content(item):
    return item.get("type", "text") != "text" or bool(MEDIA_CONTENT_KEYS & set(item))


def _validate_text_only_content(content):
    if isinstance(content, str):
        return
    if isinstance(content, dict):
        if _has_media_content(content):
            raise serializers.ValidationError(TEXT_ONLY_BASE_TEMPLATE_ERROR)
        return
    if not isinstance(content, list):
        return

    for item in content:
        if not isinstance(item, dict):
            continue
        if _has_media_content(item):
            raise serializers.ValidationError(TEXT_ONLY_BASE_TEMPLATE_ERROR)


def _validate_text_only_snapshot(snapshot):
    """Keep shared prompt base templates from storing remote media URL blocks."""

    for config in _snapshot_configs(snapshot):
        for message in config.get("messages") or []:
            if not isinstance(message, dict):
                continue

            _validate_text_only_content(message.get("content"))


def _sanitize_text_only_content(content):
    if isinstance(content, str):
        return content

    if isinstance(content, dict):
        if _has_media_content(content):
            return []
        text = content.get("text")
        return [{"type": "text", "text": text}] if isinstance(text, str) else []

    if not isinstance(content, list):
        return content

    text_content = []
    for item in content:
        if isinstance(item, str):
            text_content.append({"type": "text", "text": item})
            continue
        if (
            not isinstance(item, dict)
            or _has_media_content(item)
            or not isinstance(item.get("text"), str)
        ):
            continue
        text_content.append({"type": "text", "text": item["text"]})

    return text_content


def _sanitize_text_only_snapshot(snapshot):
    """Serialize legacy rows without exposing remote media URL blocks."""

    if isinstance(snapshot, list):
        return [_sanitize_text_only_snapshot(config) for config in snapshot]
    if not isinstance(snapshot, dict):
        return snapshot

    sanitized = {**snapshot}
    messages = sanitized.get("messages")
    if not isinstance(messages, list):
        return sanitized

    sanitized_messages = []
    for message in messages:
        if not isinstance(message, dict):
            sanitized_messages.append(message)
            continue

        sanitized_messages.append(
            {**message, "content": _sanitize_text_only_content(message.get("content"))}
        )

    sanitized["messages"] = sanitized_messages
    return sanitized


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

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        representation["prompt_config_snapshot"] = _sanitize_text_only_snapshot(
            representation.get("prompt_config_snapshot")
        )
        return representation

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

    def validate_prompt_config_snapshot(self, value):
        _validate_text_only_snapshot(value)
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

        if "prompt_config_snapshot" in attrs:
            _validate_text_only_snapshot(attrs["prompt_config_snapshot"])

        return attrs
