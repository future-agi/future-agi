from rest_framework import serializers

from agentcc.models.blocklist import AgentccBlocklist


class AgentccBlocklistSerializer(serializers.ModelSerializer):
    """A named, org-scoped word blocklist that guardrail checks reference to
    flag or block requests/responses containing forbidden terms. Use it to
    define reusable lists of banned words. Listed/read via list_agentcc_blocklists
    / get_agentcc_blocklist; edit via update_agentcc_blocklist."""

    class Meta:
        model = AgentccBlocklist
        fields = [
            "id",
            "organization",
            "name",
            "description",
            "words",
            "is_active",
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
            "name": {"help_text": "Unique (per org) name for this blocklist."},
            "description": {"help_text": "Optional description of what this list blocks."},
            "words": {
                "help_text": "JSON array of strings — the blocked words/phrases."
            },
            "is_active": {
                "help_text": "Whether this blocklist is enabled for guardrail checks."
            },
        }

    def validate_words(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("words must be a JSON array")
        for i, word in enumerate(value):
            if not isinstance(word, str):
                raise serializers.ValidationError(f"Word at index {i} must be a string")
        return value
