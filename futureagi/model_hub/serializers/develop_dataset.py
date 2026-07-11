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
    """A dataset is a workspace-scoped collection of rows and columns used as
    input or ground-truth for evaluations, experiments, and prompt runs.

    Datasets have columns (typed fields like text, json, image, choice) and
    rows (records). They power experiments (run a prompt against every row)
    and evaluations (score model output against expected fields). Names are
    unique per (organization, model_type). Use list_datasets to discover,
    list_dataset_evals to see what evals are attached, get_dataset_columns
    to inspect schema.
    """

    id = serializers.UUIDField(
        read_only=True,
        help_text=(
            "Unique dataset identifier (UUID v4). **How to get it:** call "
            "`list_datasets` (optionally with name filter) and copy the 'id' field."
        ),
    )
    name = serializers.CharField(
        max_length=255,
        help_text=(
            "Human-readable dataset name. Must be unique within the "
            "organization for the same model_type. Use kebab-case. "
            "Examples: 'rag-eval-set-v3', 'summarization-golden', "
            "'safety-red-team-prompts'."
        ),
    )
    model_type = serializers.CharField(
        required=False,
        help_text=(
            "Type of AI model the dataset is designed for. Common values: "
            "'GenerativeLLM' (chat/completion), 'GenerativeImage', 'TTS', "
            "'STT', 'MultiModal'. Determines which evaluators apply."
        ),
    )
    source = serializers.CharField(
        required=False,
        help_text=(
            "How the dataset was created. Common values: 'prototype' (user "
            "created in UI), 'imported' (CSV/HuggingFace), 'synthetic' "
            "(LLM-generated), 'observe' (sampled from production traces)."
        ),
    )
    organization = serializers.UUIDField(
        read_only=True,
        help_text="Organization UUID, auto-set from the authenticated user.",
    )

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
        fields = ["id", "dataset", "order"]


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


class SyntheticDatasetColumnSerializer(serializers.Serializer):
    name = serializers.CharField()
    data_type = serializers.CharField()
    description = serializers.CharField(allow_blank=True)
    property = serializers.JSONField()
    skip = serializers.BooleanField(required=False)
    is_new = serializers.BooleanField(required=False)


class SyntheticDatasetPayloadSerializer(serializers.Serializer):
    # Optional here; each caller enforces name in `validate_dataset`.
    name = serializers.CharField(required=False, allow_blank=True)
    description = serializers.CharField(allow_blank=True)
    objective = serializers.CharField(allow_blank=True)
    patterns = serializers.CharField(allow_blank=True)


class SyntheticDatasetCreationSerializer(serializers.Serializer):
    num_rows = serializers.IntegerField()
    columns = SyntheticDatasetColumnSerializer(many=True)
    dataset = SyntheticDatasetPayloadSerializer()
    kb_id = serializers.UUIDField(required=False)

    def validate_dataset(self, value):
        if not value.get("name"):
            raise serializers.ValidationError("dataset must contain a 'name'.")
        return value


class SyntheticDataSerializer(SyntheticDatasetCreationSerializer):
    fill_existing_rows = serializers.BooleanField(default=False)

    def validate_dataset(self, value):
        return value


class SyntheticDatasetConfigSerializer(serializers.Serializer):
    """Serializer for getting and updating synthetic dataset configuration"""

    num_rows = serializers.IntegerField()
    columns = SyntheticDatasetColumnSerializer(many=True)
    dataset = SyntheticDatasetPayloadSerializer()
    kb_id = serializers.UUIDField(required=False, allow_null=True)
    regenerate = serializers.BooleanField(required=False, default=False)

    def validate_dataset(self, value):
        if not value.get("name"):
            raise serializers.ValidationError("dataset must contain a 'name'.")
        return value


class FeedbackSerializer(serializers.ModelSerializer):
    """Human feedback on a single evaluated item — e.g. agreeing or disagreeing
    with an eval result on a dataset row, prompt, trace, or experiment. Create
    feedback to record a reviewer's judgement (the value) plus an optional
    explanation; it ties back to the eval metric or custom eval config it
    corrects and the specific row it applies to. Used to curate eval quality and
    drive improvements.
    """

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
        extra_kwargs = {
            "id": {"help_text": "UUID of this feedback record (output)."},
            "source_id": {
                "help_text": (
                    "ID of the originating object the feedback is about (e.g. the "
                    "dataset, prompt, trace, or experiment id)."
                )
            },
            "source": {
                "help_text": (
                    "Where the feedback originated. One of: dataset, prompt, sdk, "
                    "trace, experiment, observe, eval_playground."
                )
            },
            "user_eval_metric": {
                "help_text": "UUID of the eval metric this feedback applies to (optional)."
            },
            "value": {
                "help_text": "The feedback value, e.g. the corrected score or label, stored as text."
            },
            "explanation": {
                "help_text": "Optional free-text reason the reviewer gave for this feedback."
            },
            "row_id": {
                "help_text": "Identifier of the specific dataset/result row the feedback applies to."
            },
            "custom_eval_config_id": {
                "help_text": (
                    "UUID of the custom eval config this feedback corrects (from "
                    "list_custom_eval_configs), if applicable."
                )
            },
            "feedback_improvement": {
                "help_text": "Optional suggested improvement or corrected output text."
            },
            "action_type": {
                "help_text": "Optional tag describing the action this feedback represents."
            },
        }


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
        request = self.context["request"]
        validated_data["organization"] = (
            getattr(request, "organization", None) or request.user.organization
        )
        validated_data["workspace"] = getattr(request, "workspace", None)
        return super().create(validated_data)

    def to_representation(self, instance):
        """Customize the output representation"""
        data = super().to_representation(instance)
        # Add a masked version of the key for display purposes
        actual_key = instance.actual_key
        if actual_key:
            if len(actual_key) <= 8:
                data["masked_key"] = actual_key[:2] + "•" * max(len(actual_key) - 2, 4)
            else:
                data["masked_key"] = (
                    actual_key[:4] + "•" * (len(actual_key) - 8) + actual_key[-4:]
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
