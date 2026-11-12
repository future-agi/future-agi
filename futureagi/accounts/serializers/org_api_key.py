from rest_framework import serializers


class UserSecretKeySerializer(serializers.Serializer):
    key_id = serializers.UUIDField()


class CreateSecretKeySerializer(serializers.Serializer):
    key_name = serializers.CharField(max_length=100, required=True)
    workspace_id = serializers.UUIDField(required=False, allow_null=True)


    # A ``coding_agent`` key may make only the calls of a coding agent's fix loop,
    # on the one simulation environment it names.
    type = serializers.ChoiceField(
        choices=["user", "coding_agent"], default="user", required=False
    )
    simulation_environment_id = serializers.UUIDField(required=False)

    def validate(self, attrs):
        if (attrs["type"] == "coding_agent") != ("simulation_environment_id" in attrs):
            raise serializers.ValidationError(
                {
                    "simulation_environment_id": (
                        "Required for a coding_agent key, and accepted for no other."
                    )
                }
            )
        if attrs["type"] == "coding_agent" and "workspace_id" in attrs:
            raise serializers.ValidationError(
                {"workspace_id": "A coding agent key uses its environment's workspace; omit workspace_id."}
            )
        return attrs
