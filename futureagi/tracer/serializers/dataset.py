from rest_framework import serializers

from model_hub.models.choices import DatasetSourceChoices
from model_hub.models.develop_dataset import Dataset


class ObserveDatasetSerializer(serializers.ModelSerializer):
    organization = serializers.PrimaryKeyRelatedField(read_only=True)
    user = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = Dataset
        fields = ["id", "name", "organization", "model_type", "source", "user"]

    def create(self, validated_data):
        request = self.context.get("request")
        if request is not None:
            validated_data["organization"] = (
                getattr(request, "organization", None) or request.user.organization
            )
            validated_data["workspace"] = getattr(request, "workspace", None)
            validated_data["user"] = request.user
        validated_data.setdefault("source", DatasetSourceChoices.OBSERVE.value)
        return Dataset.no_workspace_objects.create(**validated_data)


class AddToNewDatasetObserveSerializer(serializers.Serializer):
    span_ids = serializers.ListField(child=serializers.CharField(), required=False)
    trace_ids = serializers.ListField(child=serializers.UUIDField(), required=False)
    mapping_config = serializers.ListField(child=serializers.DictField(), required=True)
    new_dataset_name = serializers.CharField(required=True)
    select_all = serializers.BooleanField(required=False)
    # A trace/span already belongs to exactly one project, so the view
    # derives `project` from trace_ids / span_ids when the client doesn't
    # send one. Still required for `select_all` (it's the scope bound).
    project = serializers.UUIDField(required=False)

    def validate_mapping_config(self, value):
        if not value or len(value) == 0:
            raise serializers.ValidationError("Mapping config cannot be empty or null")
        return value


class AddToExistingDatasetObserveSerializer(serializers.Serializer):
    span_ids = serializers.ListField(child=serializers.CharField(), required=False)
    trace_ids = serializers.ListField(child=serializers.UUIDField(), required=False)
    select_all = serializers.BooleanField(required=False)
    project = serializers.UUIDField(required=False)
    mapping_config = serializers.ListField(
        child=serializers.DictField(), required=False
    )
    new_mapping_config = serializers.ListField(
        child=serializers.DictField(), required=False
    )
    dataset_id = serializers.UUIDField(required=True)
