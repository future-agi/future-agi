from rest_framework import serializers

from model_hub.models.dataset_eval_config import DatasetEvalConfig


class DatasetEvalConfigSerializer(serializers.ModelSerializer):
    eval_template_name = serializers.CharField(
        source="eval_template.name", read_only=True
    )

    class Meta:
        model = DatasetEvalConfig
        fields = [
            "id",
            "dataset",
            "eval_template",
            "eval_template_name",
            "enabled",
            "debounce_seconds",
            "max_concurrent",
            "column_mapping",
            "filter_tags",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at", "eval_template_name"]


class DatasetEvalConfigCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = DatasetEvalConfig
        fields = [
            "dataset",
            "eval_template",
            "enabled",
            "debounce_seconds",
            "max_concurrent",
            "column_mapping",
            "filter_tags",
        ]

    def validate_debounce_seconds(self, value):
        if value < 5 or value > 3600:
            raise serializers.ValidationError(
                "debounce_seconds must be between 5 and 3600."
            )
        return value

    def validate_max_concurrent(self, value):
        if value < 1 or value > 50:
            raise serializers.ValidationError(
                "max_concurrent must be between 1 and 50."
            )
        return value

    def validate(self, data):
        # Enforce one active config per (dataset, eval_template) pair.
        dataset = data.get("dataset")
        eval_template = data.get("eval_template")
        if dataset and eval_template:
            qs = DatasetEvalConfig.objects.filter(
                dataset=dataset,
                eval_template=eval_template,
                deleted=False,
            )
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError(
                    "A config for this (dataset, eval_template) pair already exists."
                )
        return data
