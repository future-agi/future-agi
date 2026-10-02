# serializers.py
import re

from rest_framework import serializers

from model_hub.models.choices import SourceChoices
from model_hub.models.develop_dataset import Column, Dataset
from model_hub.models.develop_optimisation import OptimizationDataset
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
from model_hub.utils.workspace_scope import (
    scoped_column_queryset,
    scoped_dataset_queryset,
    scoped_user_eval_metric_queryset,
)
from tfc.utils.functions import calculate_column_average


def get_optimization_link_errors(dataset, column=None, user_eval_template_ids=None):
    errors = {}
    if dataset is not None and column is not None and column.dataset_id != dataset.id:
        errors["column_id"] = ["Column must belong to the selected dataset."]

    mismatched_metric_ids = [
        str(metric.id)
        for metric in user_eval_template_ids or []
        if dataset is not None and metric.dataset_id != dataset.id
    ]
    if mismatched_metric_ids:
        errors["user_eval_template_ids"] = [
            "Evaluation metrics must belong to the selected dataset."
        ]

    return errors


class OptimizationDatasetSerializer(serializers.ModelSerializer):
    dataset_id = serializers.PrimaryKeyRelatedField(
        queryset=Dataset.objects.all(), source="dataset"
    )
    column_id = serializers.PrimaryKeyRelatedField(
        queryset=Column.objects.all(), source="column", required=False, allow_null=True
    )
    user_eval_template_ids = serializers.PrimaryKeyRelatedField(
        many=True, queryset=UserEvalMetric.objects.all(), required=False
    )
    model_config = serializers.JSONField()

    class Meta:
        model = OptimizationDataset
        fields = [
            "id",
            "name",
            "dataset_id",
            "column_id",
            "messages",
            "user_eval_template_ids",
            "model_config",
            "optimize_type",
            "user_eval_template_mapping",
            "prompt_name",
            "created_at",
            "status",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request") if self.context else None
        self.fields["dataset_id"].queryset = scoped_dataset_queryset(request)
        self.fields["column_id"].queryset = scoped_column_queryset(request)
        # `user_eval_template_ids` is many=True → the outer ManyRelatedField
        # only stores queryset for validation of the list wrapper; the actual
        # per-pk lookup runs on `child_relation`. Assign the scoped queryset
        # to both so cross-request workspace scoping is enforced.
        scoped_metrics = scoped_user_eval_metric_queryset(request)
        self.fields["user_eval_template_ids"].queryset = scoped_metrics
        self.fields["user_eval_template_ids"].child_relation.queryset = scoped_metrics

    def validate_messages(self, value):
        for message in value:
            if "role" not in message or "content" not in message:
                raise serializers.ValidationError(
                    "Each message must contain 'role' and 'content' keys."
                )
        return value

    def validate(self, attrs):
        attrs = super().validate(attrs)
        errors = get_optimization_link_errors(
            attrs.get("dataset"),
            attrs.get("column"),
            attrs.get("user_eval_template_ids"),
        )
        if errors:
            raise serializers.ValidationError(errors)
        return attrs

    # def validate_model_config(self, value):
    #     required_fields = ['model_name', 'temperature', 'frequency_penalty', 'presence_penalty', 'max_tokens', 'top_p', 'response_format', 'tool_choice', 'tools']
    #     for field in required_fields:
    #         if field not in value:
    #             raise serializers.ValidationError(f"'{field}' is required in model_config.")
    #     return value


class OptimizationDatasetGetSerializer(serializers.ModelSerializer):
    class Meta:
        model = OptimizationDataset
        fields = [
            "id",
            "name",
            "dataset",
            "column",
            "messages",
            "user_eval_template_ids",
            "model_config",
            "optimize_type",
            "status",
            "created_at",
            "optimized_k_prompts",
            "user_eval_template_mapping",
            "prompt_name",
        ]
        read_only_fields = ["id", "status", "created_at"]


class OptimizationDetailSerializer(serializers.ModelSerializer):
    optimized_columns = serializers.SerializerMethodField()
    evaluation_columns = serializers.SerializerMethodField()
    optimized_k_prompts = serializers.SerializerMethodField()
    user_eval_template_ids = serializers.SerializerMethodField()

    class Meta:
        model = OptimizationDataset
        fields = [
            "id",
            "created_at",
            "optimized_k_prompts",
            "user_eval_template_ids",
            "user_eval_template_mapping",
            "optimized_columns",
            "evaluation_columns",
        ]

    def get_optimized_columns(self, obj):
        columns = Column.objects.filter(
            source=SourceChoices.OPTIMISATION.value, source_id=str(obj.id)
        )
        return [{"id": col.id, "name": col.name} for col in columns]

    def get_evaluation_columns(self, obj):
        # Dictionary to group columns by eval_id
        eval_groups = {}

        for eval_metric in obj.user_eval_template_ids.all():
            eval_id = eval_metric.id
            columns = Column.objects.filter(
                source=SourceChoices.OPTIMISATION_EVALUATION.value,
                source_id=f"{obj.id}-sourceid-{eval_id}",
            )

            # Get eval metric details
            eval_metric_obj = UserEvalMetric.objects.filter(id=eval_id).first()
            output_type = (
                eval_metric_obj.template.config.get("output")
                if eval_metric_obj
                else None
            )

            # Initialize group for this eval_id
            if eval_id not in eval_groups:
                eval_groups[eval_id] = {
                    "eval_id": eval_id,
                    "new_prompt": None,
                    "old_prompt": None,
                    "eval_name": eval_metric_obj.name,
                }

            # Process each column
            for col in columns:
                avg_value = calculate_column_average(col.id)
                column_data = {"average": avg_value, "output_type": output_type}

                # Determine if it's old or new prompt based on column name
                if "old-prompt" in col.name:
                    eval_groups[eval_id]["old_prompt"] = column_data
                elif "new-prompt" in col.name:
                    eval_groups[eval_id]["new_prompt"] = column_data

        # Convert dictionary to list
        return list(eval_groups.values())

    def get_optimized_k_prompts(self, obj):
        updated_prompts = []
        try:
            for prompt in obj.optimized_k_prompts:
                # Find all placeholders in the prompt
                placeholders = re.findall(r"\{\{(.*?)\}\}", prompt)
                for placeholder in placeholders:
                    try:
                        # Fetch the column name using the placeholder as the column ID
                        column = Column.objects.filter(id=placeholder).first()
                        if column:
                            # Replace the placeholder with the column name
                            prompt = prompt.replace(
                                f"{{{{{placeholder}}}}}", f"{{{{{column.name}}}}}"
                            )
                    except Exception:
                        pass
                updated_prompts.append(prompt)
        except Exception:
            pass
        return updated_prompts

    def get_user_eval_template_ids(self, obj):
        return [
            str(metric_id)
            for metric_id in obj.user_eval_template_ids.values_list("id", flat=True)
        ]


class EvalTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = EvalTemplate
        fields = ["description", "config", "name", "criteria", "id"]


class UserEvalMetricSerializer(serializers.ModelSerializer):
    template_details = EvalTemplateSerializer(source="template", read_only=True)
    eval_group = serializers.SerializerMethodField()

    class Meta:
        model = UserEvalMetric
        fields = [
            "id",
            "name",
            "organization",
            "template_details",
            "dataset",
            "config",
            "status",
            "show_in_sidebar",
            "eval_group",
            "composite_weight_overrides",
            "error_localizer",
        ]

    def get_eval_group(self, obj):
        if obj.eval_group:
            return obj.eval_group.name
        return obj.organization.name if obj.organization else None
