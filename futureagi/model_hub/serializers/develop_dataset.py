from django import forms
from rest_framework import serializers

from model_hub.models.api_key import SecretModel
from model_hub.models.choices import FeedbackSourceChoices, ModelTypes
from model_hub.models.develop_dataset import (
    Cell,
    Column,
    Dataset,
    Files,
    KnowledgeBaseFile,
    Row,
)
from model_hub.models.evals_metric import Feedback


class DatasetSerializer(serializers.ModelSerializer):
    class Meta:
        model = Dataset
        fields = ["id", "name", "organization", "model_type", "source", "user"]


class ColumnSerializer(serializers.ModelSerializer):
    class Meta:
        model = Column
        fields = ["id", "name", "data_type", "dataset", "source", "source_id"]


class RowSerializer(serializers.ModelSerializer):
    class Meta:
        model = Row
        fields = ["id", "dataset", "order", "source_trace_id", "source_span_id"]
        read_only_fields = ["source_trace_id", "source_span_id"]


class CellSerializer(serializers.ModelSerializer):
    class Meta:
        model = Cell
        fields = ["id", "dataset", "column", "row", "value"]


class UploadFileForm(forms.Form):
    file = forms.FileField()
    model_type = forms.CharField(
        initial=ModelTypes.GENERATIVE_LLM.value,
        max_length=100,
        required=False,  # Adjust the max_length as needed
    )


class SyntheticDatasetCreationSerializer(serializers.Serializer):
    num_rows = serializers.IntegerField()
    columns = serializers.ListField()
    dataset = serializers.JSONField()
    kb_id = serializers.UUIDField(required=False)

    def validate_dataset(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("dataset must be a JSON object.")

        required_keys = {"name", "description", "objective", "patterns"}

        missing_keys = required_keys - value.keys()
        if missing_keys:
            raise serializers.ValidationError(
                f"dataset must contain the keys: {', '.join(required_keys)}."
            )

        return value

    def validate_columns(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("columns must be a list of JSON objects.")

        required_keys = {"name", "data_type", "description", "property"}

        for item in value:
            if not isinstance(item, dict):
                raise serializers.ValidationError(
                    "Each item in columns must be a JSON object."
                )

            missing_keys = required_keys - item.keys()
            if missing_keys:
                raise serializers.ValidationError(
                    f"Each JSON object in columns must contain the keys: {', '.join(required_keys)}."
                )

        return value


class SyntheticDataSerializer(SyntheticDatasetCreationSerializer):
    fill_existing_rows = serializers.BooleanField(default=False)

    def validate_columns(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("columns must be a list of JSON objects.")

        required_keys = {
            "name",
            "data_type",
            "description",
            "skip",
            "is_new",
            "property",
        }

        for item in value:
            if not isinstance(item, dict):
                raise serializers.ValidationError(
                    "Each item in columns must be a JSON object."
                )

            missing_keys = required_keys - item.keys()
            if missing_keys:
                raise serializers.ValidationError(
                    f"Each JSON object in columns must contain the keys: {', '.join(required_keys)}."
                )

        return value

    def validate_dataset(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("dataset must be a JSON object.")
        required_keys = {"description", "objective", "patterns"}
        missing_keys = required_keys - value.keys()
        if missing_keys:
            raise serializers.ValidationError(
                f"dataset must contain the keys: {', '.join(required_keys)}."
            )
        return value


class SyntheticDatasetConfigSerializer(serializers.Serializer):
    """Serializer for getting and updating synthetic dataset configuration"""

    num_rows = serializers.IntegerField()
    columns = serializers.ListField()
    dataset = serializers.JSONField()
    kb_id = serializers.UUIDField(required=False, allow_null=True)

    def validate_dataset(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("dataset must be a JSON object.")

        required_keys = {"name", "description", "objective", "patterns"}

        missing_keys = required_keys - value.keys()
        if missing_keys:
            raise serializers.ValidationError(
                f"dataset must contain the keys: {', '.join(required_keys)}."
            )

        return value

    def validate_columns(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("columns must be a list of JSON objects.")

        required_keys = {"name", "data_type", "description", "property"}

        for item in value:
            if not isinstance(item, dict):
                raise serializers.ValidationError(
                    "Each item in columns must be a JSON object."
                )

            missing_keys = required_keys - item.keys()
            if missing_keys:
                raise serializers.ValidationError(
                    f"Each JSON object in columns must contain the keys: {', '.join(required_keys)}."
                )

        return value


class FeedbackSerializer(serializers.ModelSerializer):
    def validate_source(self, value):
        valid_choices = [choice[0] for choice in FeedbackSourceChoices.get_choices()]
        if value not in valid_choices:
            raise serializers.ValidationError(
                f"Source must be one of: {', '.join(valid_choices)}"
            )
        return value

    class Meta:
        model = Feedback
        fields = [
            "id",
            "source_id",
            "source",
            "user_eval_metric",
            "value",
            "explanation",
            "row_id",
            "custom_eval_config_id",
            "feedback_improvement",
            "action_type",
        ]


class SecretSerializer(serializers.ModelSerializer):
    secret_type_display = serializers.CharField(
        source="get_secret_type_display", read_only=True
    )

    class Meta:
        model = SecretModel
        fields = [
            "id",
            "name",
            "description",
            "secret_type",
            "secret_type_display",
            "key",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]
        extra_kwargs = {
            "key": {"write_only": True}  # Key will not be included in responses
        }

    def create(self, validated_data):
        # Add organization from request context
        validated_data["organization"] = self.context["request"].user.organization
        return super().create(validated_data)

    def to_representation(self, instance):
        """Customize the output representation"""
        data = super().to_representation(instance)
        # Add a masked version of the key for display purposes
        if instance.key:
            data["masked_key"] = (
                instance.actual_key[:4]
                + "•" * (len(instance.actual_key) - 8)
                + instance.actual_key[-4:]
            )
        return data


class CompareDatasetSerializer(serializers.Serializer):
    compare_id = serializers.UUIDField(allow_null=True, required=False, default=None)
    page_size = serializers.IntegerField(
        required=False, default=10
    )  # Default value set to 10
    current_page_index = serializers.IntegerField(
        required=False, default=0
    )  # Default value set to 0
    base_column_name = serializers.CharField(required=True)
    dataset_info = serializers.JSONField(
        required=False, default=dict
    )  # Initialize as empty dict
    common_column_names = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
        default=list,  # Initialize as empty list
    )
    dataset_ids = serializers.ListField(
        child=serializers.UUIDField(), required=True, allow_empty=False
    )


class CreateKnowledgeBaseFileSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    organization = serializers.UUIDField()
    created_by = serializers.UUIDField()
    name = serializers.CharField(max_length=255, required=False, allow_null=True)
    files = serializers.ListField(
        child=serializers.UUIDField(), required=False, allow_null=True
    )


class KnowledgeBaseFileSerializer(serializers.ModelSerializer):
    class Meta:
        model = KnowledgeBaseFile
        fields = [
            "id",
            "name",
            "organization",
            "status",
            "files",
            "updated_at",
            "created_by",
            "last_error",
        ]


class FileSerializer(serializers.ModelSerializer):
    class Meta:
        model = Files
        fields = ["id", "name", "status", "metadata", "updated_at", "updated_by"]


class EvalPlayGroundFeedbackSerializer(serializers.Serializer):
    log_id = serializers.UUIDField(required=True)
    action_type = serializers.CharField(required=True)
    value = serializers.CharField(required=True)
    explanation = serializers.CharField(required=False)

    def validate_action_type(self, value):
        allowed_values = ["retune", "recalculate"]
        if value not in allowed_values:
            raise serializers.ValidationError(
                f"Invalid action_type. Must be one of: {', '.join(allowed_values)}"
            )
        return value
