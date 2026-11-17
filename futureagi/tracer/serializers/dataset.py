from rest_framework import serializers

from model_hub.models.choices import DatasetSourceChoices
from model_hub.models.develop_dataset import Dataset
from model_hub.models.row_sources import RowSourceKind


class ImportRowSourceFields(serializers.Serializer):
    """What an Observe import says its rows came from.

    ``source_kind`` names the kind each new row's source is recorded as; each
    row's trace, span and session come from its own span, never from the
    request. Omitted, nothing is recorded. ``session_ids`` is shape-checked
    only: it never decides which rows import or what they record.
    """

    source_kind = serializers.ChoiceField(choices=RowSourceKind.values, required=False)
    session_ids = serializers.ListField(
        child=serializers.CharField(max_length=255), required=False, max_length=200
    )


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


class SessionDatasetSelectionSerializer(ImportRowSourceFields):
    # With trace/span ids, session_ids retain their legacy informational role.
    # Session-only requests instead select whole conversations by UUID.
    session_ids = serializers.ListField(
        child=serializers.CharField(max_length=255), required=False
    )
    session_filters = serializers.ListField(
        child=serializers.DictField(), required=False, default=list
    )

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if "session_ids" in attrs and ("span_ids" in attrs or "trace_ids" in attrs):
            if len(attrs["session_ids"]) > 200:
                raise serializers.ValidationError({"session_ids": "Ensure this field has no more than 200 elements."})
            return attrs
        if "session_ids" in attrs:
            attrs["session_ids"] = [
                serializers.UUIDField().run_validation(value)
                for value in attrs["session_ids"]
            ]
            if not attrs.get("project"):
                raise serializers.ValidationError(
                    "Project is required for session datasets."
                )
            if not attrs["session_ids"] and not attrs.get("select_all"):
                raise serializers.ValidationError("No session ids provided.")
            mappings = (attrs.get("mapping_config") or []) + (
                attrs.get("new_mapping_config") or []
            )
            if not mappings:
                raise serializers.ValidationError("Map at least one session field.")
            for mapping in mappings:
                field = mapping.get("span_field") or mapping.get("col_name")
                if field not in {"turns", "session_id", "user_id", "metrics"}:
                    raise serializers.ValidationError("Unknown session mapping field.")
        return attrs


class AddToNewDatasetObserveSerializer(SessionDatasetSelectionSerializer):
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


class AddToExistingDatasetObserveSerializer(SessionDatasetSelectionSerializer):
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
