from rest_framework import serializers

from agentcc.models.session import AgentccSession


class AgentccSessionSerializer(serializers.ModelSerializer):
    """An explicit session that groups a set of related gateway requests under
    one identifier (e.g. a user conversation or agent run). Use it to tie
    multiple requests together for analytics. Listed/read via
    list_agentcc_sessions / get_agentcc_session; status is active or closed."""

    class Meta:
        model = AgentccSession
        fields = [
            "id",
            "organization",
            "session_id",
            "name",
            "status",
            "metadata",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "organization",
            "created_at",
            "updated_at",
        ]
        extra_kwargs = {
            "session_id": {
                "help_text": "Caller-supplied session identifier (unique per org)."
            },
            "name": {"help_text": "Optional human-readable session name."},
            "status": {"help_text": "Session state: active or closed."},
            "metadata": {
                "help_text": "Arbitrary JSON object of key-value metadata for the session."
            },
        }

    def validate_status(self, value):
        valid = [c[0] for c in AgentccSession.STATUS_CHOICES]
        if value not in valid:
            raise serializers.ValidationError(
                f"status must be one of: {', '.join(valid)}"
            )
        return value
