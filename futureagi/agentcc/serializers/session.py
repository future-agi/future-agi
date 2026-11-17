from rest_framework import serializers

from agentcc.models.session import AgentccSession
from agentcc.services import session_activity


class AgentccSessionSerializer(serializers.ModelSerializer):
    """A session as the console reads it.

    ``status`` is the status as read: a session idle for longer than
    ``idle_timeout_seconds`` reads closed with ``closed_reason`` "idle"; one
    someone closed has "closed"; an open one has "". ``first_request_at``
    is its oldest request, when it started (``created_at`` is when ingestion
    stored it), and ``last_request_at`` its newest
    (``session_activity.with_activity`` annotates them).
    """

    first_request_at = serializers.SerializerMethodField()
    last_request_at = serializers.SerializerMethodField()
    closed_reason = serializers.SerializerMethodField()
    idle_timeout_seconds = serializers.SerializerMethodField()

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
            "first_request_at",
            "last_request_at",
            "closed_reason",
            "idle_timeout_seconds",
        ]
        read_only_fields = [
            "id",
            "organization",
            "created_at",
            "updated_at",
        ]

    @staticmethod
    def _time(instance, name) -> str | None:
        value = getattr(instance, name, None)
        return serializers.DateTimeField().to_representation(value) if value else None

    def get_first_request_at(self, instance) -> str | None:
        return self._time(instance, "first_request_at")

    def get_last_request_at(self, instance) -> str | None:
        return self._time(instance, "last_request_at")

    def get_closed_reason(self, instance) -> str:
        return session_activity.closed_reason(instance)

    def get_idle_timeout_seconds(self, instance) -> int:
        return session_activity.idle_timeout_seconds()

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["status"] = session_activity.effective_status(instance)
        return data

    def validate_status(self, value):
        valid = [c[0] for c in AgentccSession.STATUS_CHOICES]
        if value not in valid:
            raise serializers.ValidationError(
                f"status must be one of: {', '.join(valid)}"
            )
        return value
