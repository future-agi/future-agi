"""
Serializers for Dataset Optimization

Following the same patterns as simulate.serializers.agent_prompt_optimiser.
"""

from django.db.models import Q
from drf_yasg.utils import swagger_serializer_method
from rest_framework import serializers

from model_hub.models.ai_model import AIModel
from model_hub.models.dataset_optimization_step import DatasetOptimizationStep
from model_hub.models.dataset_optimization_trial import DatasetOptimizationTrial
from model_hub.models.dataset_optimization_trial_item import (
    DatasetOptimizationItemEvaluation,
    DatasetOptimizationTrialItem,
)
from model_hub.models.develop_dataset import Column, Dataset
from model_hub.models.evals_metric import UserEvalMetric
from model_hub.models.optimize_dataset import OptimizeDataset
from model_hub.utils.dataset_optimization import (
    build_parameters_array,
    build_trial_table_data,
)
from model_hub.utils.llm_providers import get_provider_logo_url

COMMON_OPTIONAL_CONFIG_KEYS = {"task_description"}

# Required configuration keys for each optimizer type
OPTIMIZER_REQUIRED_CONFIG_KEYS = {
    "random_search": ["num_variations"],
    "gepa": ["max_metric_calls"],
    "protegi": [
        "beam_size",
        "num_gradients",
        "errors_per_gradient",
        "prompts_per_gradient",
        "num_rounds",
    ],
    "bayesian": ["min_examples", "max_examples", "n_trials"],
    "metaprompt": ["task_description", "num_rounds"],
    "promptwizard": ["mutate_rounds", "refine_iterations", "beam_size"],
}


def _request_org_workspace(serializer):
    request = serializer.context.get("request") if serializer.context else None
    organization = None
    workspace = None
    if request is not None:
        organization = getattr(request, "organization", None)
        workspace = getattr(request, "workspace", None)
        if organization is None and getattr(request, "user", None):
            organization = getattr(request.user, "organization", None)
    return organization, workspace


def _workspace_filter(workspace, field_name):
    if workspace is None:
        return Q()
    if getattr(workspace, "is_default", False):
        organization = getattr(workspace, "organization", None)
        query = Q(**{field_name: workspace})
        if organization is not None:
            query |= Q(
                **{
                    f"{field_name}__is_default": True,
                    f"{field_name}__organization": organization,
                }
            )
        query |= Q(**{f"{field_name}__isnull": True})
        return query
    return Q(**{field_name: workspace})


class DatasetOptimizationCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating a new dataset optimization run."""

    column_id = serializers.UUIDField(write_only=True, required=True)
    optimizer_algorithm = serializers.ChoiceField(
        choices=OptimizeDataset.OptimizerAlgorithm.choices, required=True
    )
    optimizer_model_id = serializers.CharField(
        write_only=True, required=False, allow_blank=True
    )
    user_eval_template_ids = serializers.ListField(
        child=serializers.UUIDField(),
        write_only=True,
        required=False,
        default=list,
    )

    class Meta:
        model = OptimizeDataset
        fields = (
            "id",
            "name",
            "column_id",
            "optimizer_algorithm",
            "optimizer_model_id",
            "optimizer_config",
            "user_eval_template_ids",
            "created_at",
        )
        read_only_fields = ("id", "created_at")

    def validate_column_id(self, value):
        if not self._scoped_column_queryset().filter(id=value).exists():
            raise serializers.ValidationError("Column with this ID does not exist.")
        return value

    def _scoped_column_queryset(self):
        organization, workspace = _request_org_workspace(self)
        queryset = Column.objects.select_related("dataset").filter(
            deleted=False,
            dataset__deleted=False,
        )
        if organization is not None:
            queryset = queryset.filter(dataset__organization=organization)
        queryset = queryset.filter(_workspace_filter(workspace, "dataset__workspace"))
        return queryset

    def _scoped_ai_model_queryset(self):
        organization, workspace = _request_org_workspace(self)
        queryset = AIModel.objects.filter(deleted=False)
        if organization is not None:
            queryset = queryset.filter(organization=organization)
        queryset = queryset.filter(_workspace_filter(workspace, "workspace"))
        return queryset

    def _scoped_eval_metric_queryset(self, dataset):
        organization, workspace = _request_org_workspace(self)
        queryset = UserEvalMetric.no_workspace_objects.filter(
            deleted=False,
            dataset=dataset,
        )
        if organization is not None:
            queryset = queryset.filter(organization=organization)
        queryset = queryset.filter(_workspace_filter(workspace, "workspace"))
        return queryset

    def validate_user_eval_template_ids(self, value):
        if not value:
            return value
        column_id = self.initial_data.get("column_id")
        if not column_id:
            return value
        column = self._scoped_column_queryset().filter(id=column_id).first()
        if column is None:
            return value
        found_ids = set(
            self._scoped_eval_metric_queryset(column.dataset)
            .filter(id__in=value)
            .values_list("id", flat=True)
        )
        requested_ids = set(value)
        if found_ids != requested_ids:
            raise serializers.ValidationError(
                "One or more eval metrics do not belong to this dataset/workspace."
            )
        return value

    def validate_optimizer_config(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("This field must be a valid JSON object.")

        optimizer_algorithm = self.initial_data.get("optimizer_algorithm")
        required_keys = OPTIMIZER_REQUIRED_CONFIG_KEYS.get(optimizer_algorithm)

        if required_keys:
            required_keys_set = set(required_keys)
            provided_keys_set = set(value.keys())

            # Check for missing keys
            missing_keys = required_keys_set - provided_keys_set
            if missing_keys:
                raise serializers.ValidationError(
                    f"Missing required keys for optimizer '{optimizer_algorithm}': "
                    f"{', '.join(missing_keys)}"
                )

            # Check for extra keys
            extra_keys = (
                provided_keys_set - required_keys_set - COMMON_OPTIONAL_CONFIG_KEYS
            )
            if extra_keys:
                raise serializers.ValidationError(
                    f"Unexpected keys provided for optimizer '{optimizer_algorithm}': "
                    f"{', '.join(extra_keys)}"
                )

        return value

    def create(self, validated_data):
        from model_hub.models.develop import DevelopAI

        column = self._scoped_column_queryset().get(id=validated_data.pop("column_id"))
        dataset = column.dataset

        # Extract user_eval_template_ids for later (ManyToMany can't be set before save)
        user_eval_template_ids = validated_data.pop("user_eval_template_ids", [])

        # Set optimizer model if provided (by model name)
        optimizer_model_name = validated_data.pop("optimizer_model_id", None)
        if optimizer_model_name:
            # Try to find an AIModel with this name, but don't fail if not found
            # The model name will be used directly by the optimizer
            try:
                optimizer_model = (
                    self._scoped_ai_model_queryset()
                    .filter(user_model_id__iexact=optimizer_model_name)
                    .first()
                )
                if optimizer_model:
                    validated_data["optimizer_model"] = optimizer_model
            except Exception:
                pass  # If we can't find a matching AIModel, that's okay

            # Store the model name in optimizer_config for the workflow to use
            if "optimizer_config" not in validated_data:
                validated_data["optimizer_config"] = {}
            validated_data["optimizer_config"]["model_name"] = optimizer_model_name

        # Set required fields
        validated_data["column"] = column
        validated_data["status"] = OptimizeDataset.StatusType.PENDING
        validated_data["optimize_type"] = OptimizeDataset.OptimizeType.TEMPLATE
        validated_data["environment"] = OptimizeDataset.EnvTypes.TRAINING
        validated_data["version"] = "1.0"

        # Try to get develop from knowledge base relationship if available
        try:
            if dataset.organization:
                develop = DevelopAI.objects.filter(
                    organization=dataset.organization, knowledge_base__isnull=False
                ).first()
                if develop:
                    validated_data["develop"] = develop
                    validated_data["used_in"] = OptimizeDataset.UsedInChoices.DEVELOP
        except Exception:
            pass  # If we can't find a develop, that's okay

        instance = super().create(validated_data)

        # Set ManyToMany relationship for user eval templates
        if user_eval_template_ids:
            eval_templates = self._scoped_eval_metric_queryset(dataset).filter(
                id__in=user_eval_template_ids
            )
            instance.user_eval_template_ids.set(eval_templates)

        return instance


class DatasetOptimizationSerializer(serializers.ModelSerializer):
    """A dataset optimization run — it iteratively rewrites the prompt for one
    dataset column using an optimizer algorithm (e.g. bayesian, metaprompt,
    GEPA), scoring each trial against the configured eval metrics. Create one to
    auto-improve a column's prompt; read it back to track status and the
    best/baseline scores. The status, score, and optimized-prompt fields are run
    outputs populated as the run executes.
    """

    def _scoped_column_queryset(self):
        organization, workspace = _request_org_workspace(self)
        queryset = Column.objects.select_related("dataset").filter(
            deleted=False,
            dataset__deleted=False,
        )
        if organization is not None:
            queryset = queryset.filter(dataset__organization=organization)
        queryset = queryset.filter(_workspace_filter(workspace, "dataset__workspace"))
        return queryset

    def _scoped_ai_model_queryset(self):
        organization, workspace = _request_org_workspace(self)
        queryset = AIModel.objects.filter(deleted=False)
        if organization is not None:
            queryset = queryset.filter(organization=organization)
        queryset = queryset.filter(_workspace_filter(workspace, "workspace"))
        return queryset

    def validate_column(self, value):
        if value is None:
            return value
        if not self._scoped_column_queryset().filter(id=value.id).exists():
            raise serializers.ValidationError("Column with this ID does not exist.")
        return value

    def validate_optimizer_model(self, value):
        if value is None:
            return value
        if not self._scoped_ai_model_queryset().filter(id=value.id).exists():
            raise serializers.ValidationError("Optimizer model is not accessible.")
        return value

    class Meta:
        model = OptimizeDataset
        fields = [
            "id",
            "name",
            "column",
            "optimizer_algorithm",
            "optimizer_model",
            "optimizer_config",
            "status",
            "error_message",
            "best_score",
            "baseline_score",
            "optimized_k_prompts",
            "created_at",
        ]
        extra_kwargs = {
            "id": {
                "help_text": "UUID of this optimization run (from list_dataset_optimizations)."
            },
            "name": {"help_text": "Human-readable name for this optimization run."},
            "column": {
                "help_text": "UUID of the dataset column whose prompt is being optimized."
            },
            "optimizer_algorithm": {
                "help_text": (
                    "Optimization algorithm to use. One of: random_search, "
                    "bayesian, metaprompt, protegi, promptwizard, gepa."
                )
            },
            "optimizer_model": {
                "help_text": (
                    "UUID of the AI model that runs the optimization (the model "
                    "used to generate candidate prompts), separate from the eval model."
                )
            },
            "optimizer_config": {
                "help_text": (
                    "Optimizer-specific JSON configuration, e.g. {'num_trials': 10}."
                )
            },
            "status": {
                "help_text": (
                    "Run status (output). One of: not_started, pending, running, "
                    "completed, failed, cancelled."
                )
            },
            "error_message": {
                "help_text": "Failure reason populated when the run errors or is cancelled (output)."
            },
            "best_score": {
                "help_text": "Best eval score achieved across all trials in this run (output)."
            },
            "baseline_score": {
                "help_text": "Eval score of the original (un-optimized) prompt, for comparison (output)."
            },
            "optimized_k_prompts": {
                "help_text": "List of the top optimized prompt candidates produced by the run (output)."
            },
            "created_at": {"help_text": "When the optimization run was created."},
        }


class DatasetOptimizationListSerializer(serializers.ModelSerializer):
    """Serializer for listing dataset optimization runs in table format."""

    optimization_name = serializers.CharField(source="name")
    started_at = serializers.DateTimeField(source="created_at")
    trial_count = serializers.SerializerMethodField()
    # Include model name and column_id for rerun functionality
    optimizer_model_id = serializers.SerializerMethodField()
    column_id = serializers.UUIDField(source="column.id", read_only=True)

    class Meta:
        model = OptimizeDataset
        fields = [
            "id",
            "optimization_name",
            "started_at",
            "trial_count",
            "optimizer_algorithm",
            "optimizer_model_id",
            "column_id",
            "status",
            "error_message",
            "optimizer_config",
            "best_score",
            "baseline_score",
        ]

    def get_trial_count(self, obj):
        # Use annotated value if available, otherwise count
        if hasattr(obj, "trial_count"):
            return obj.trial_count
        return obj.trials.count()

    def get_optimizer_model_id(self, obj):
        # Return the model name which is what the frontend expects.
        # Mirror DatasetOptimizationDetailSerializer._model_name so unmatched
        # names stored in optimizer_config["model_name"] surface on the list.
        if obj.optimizer_model:
            return obj.optimizer_model.user_model_id
        if obj.optimizer_config and obj.optimizer_config.get("model_name"):
            return obj.optimizer_config.get("model_name")
        return None


class DatasetOptimizationStepSerializer(serializers.ModelSerializer):
    """Serializer for optimization steps."""

    class Meta:
        model = DatasetOptimizationStep
        fields = (
            "id",
            "name",
            "description",
            "status",
            "metadata",
            "step_number",
            "created_at",
            "updated_at",
        )

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        if instance.status == DatasetOptimizationStep.Status.PENDING:
            representation["created_at"] = None
            representation["updated_at"] = None
        return representation


class DatasetOptimizationTrialSerializer(serializers.ModelSerializer):
    """Serializer for optimization trials."""

    class Meta:
        model = DatasetOptimizationTrial
        fields = (
            "id",
            "trial_number",
            "is_baseline",
            "prompt",
            "average_score",
            "metadata",
            "created_at",
        )


class DatasetOptimizationTrialListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for listing trials."""

    class Meta:
        model = DatasetOptimizationTrial
        fields = (
            "id",
            "trial_number",
            "is_baseline",
            "average_score",
            "created_at",
        )


class DatasetOptimizationParameterItemSerializer(serializers.Serializer):
    key = serializers.CharField()
    label = serializers.CharField()
    description = serializers.CharField(allow_blank=True)
    value = serializers.JSONField()


class DatasetOptimizationEvalTemplateItemSerializer(serializers.Serializer):
    id = serializers.CharField()
    eval_id = serializers.CharField()
    name = serializers.CharField()
    template_id = serializers.CharField(allow_null=True)


class DatasetOptimizationColumnConfigItemSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()
    is_visible = serializers.BooleanField()


class DatasetOptimizationTrialEvalScoreSerializer(serializers.Serializer):
    score = serializers.FloatField(required=False, allow_null=True)
    percentage_change = serializers.FloatField(required=False, allow_null=True)


class DatasetOptimizationTrialTableRowSerializer(serializers.Serializer):
    """Trial Runs table row.

    ``eval_scores`` is a mapping keyed by eval-metric UUID; each value is a
    ``{score, percentage_change}`` object with nullable numbers. Column IDs
    the frontend renders come from ``column_config`` on the same response.
    """

    id = serializers.CharField()
    trial = serializers.CharField()
    prompt = serializers.CharField(allow_blank=True)
    is_best = serializers.BooleanField()
    score_percentage_change = serializers.FloatField(required=False, allow_null=True)
    eval_scores = serializers.DictField(
        child=DatasetOptimizationTrialEvalScoreSerializer(),
    )


class DatasetOptimizationDetailSerializer(serializers.ModelSerializer):
    """Retrieve payload for the dataset-optimization run-detail view; field names
    match the simulation-optimizer FE contract."""

    optimiser_name = serializers.CharField(source="name", read_only=True)
    optimiser_type = serializers.CharField(source="optimizer_algorithm", read_only=True)
    configuration = serializers.JSONField(source="optimizer_config", read_only=True)
    start_time = serializers.DateTimeField(source="created_at", read_only=True)
    model = serializers.SerializerMethodField()
    optimizer_model_id = serializers.SerializerMethodField()
    provider_logo = serializers.SerializerMethodField()
    parameters = serializers.SerializerMethodField()
    column_id = serializers.SerializerMethodField()
    column_name = serializers.SerializerMethodField()
    user_eval_templates = serializers.SerializerMethodField()
    table = serializers.SerializerMethodField()
    column_config = serializers.SerializerMethodField()

    class Meta:
        model = OptimizeDataset
        fields = (
            "optimiser_name",
            "optimiser_type",
            "model",
            "provider_logo",
            "configuration",
            "status",
            "error_message",
            "start_time",
            "parameters",
            "column_id",
            "column_name",
            "best_score",
            "baseline_score",
            "table",
            "column_config",
            "optimizer_model_id",
            "user_eval_templates",
        )

    def to_representation(self, instance):
        self._table, self._column_config = build_trial_table_data(instance)
        return super().to_representation(instance)

    def _model_name(self, obj):
        if obj.optimizer_model:
            return obj.optimizer_model.user_model_id
        if obj.optimizer_config and obj.optimizer_config.get("model_name"):
            return obj.optimizer_config.get("model_name")
        return None

    @swagger_serializer_method(
        serializer_or_field=serializers.CharField(allow_null=True)
    )
    def get_model(self, obj):
        return self._model_name(obj)

    @swagger_serializer_method(
        serializer_or_field=serializers.CharField(allow_null=True)
    )
    def get_optimizer_model_id(self, obj):
        return self._model_name(obj)

    @swagger_serializer_method(
        serializer_or_field=serializers.CharField(allow_null=True)
    )
    def get_provider_logo(self, obj):
        model_name = self._model_name(obj)
        if not model_name or not obj.column:
            return None
        dataset = obj.column.dataset
        organization_id = dataset.organization.id if dataset else None
        workspace = dataset.workspace if dataset else None
        workspace_id = workspace.id if workspace else None
        return get_provider_logo_url(model_name, organization_id, workspace_id)

    @swagger_serializer_method(
        serializer_or_field=DatasetOptimizationParameterItemSerializer(many=True)
    )
    def get_parameters(self, obj):
        return build_parameters_array(obj.optimizer_config, obj.optimizer_algorithm)

    @swagger_serializer_method(
        serializer_or_field=serializers.CharField(allow_null=True)
    )
    def get_column_id(self, obj):
        return str(obj.column.id) if obj.column else None

    @swagger_serializer_method(
        serializer_or_field=serializers.CharField(allow_null=True)
    )
    def get_column_name(self, obj):
        return obj.column.name if obj.column else None

    @swagger_serializer_method(
        serializer_or_field=DatasetOptimizationEvalTemplateItemSerializer(many=True)
    )
    def get_user_eval_templates(self, obj):
        templates = []
        for eval_metric in obj.user_eval_template_ids.all():
            templates.append(
                {
                    "id": str(eval_metric.id),
                    "eval_id": str(eval_metric.id),
                    "name": (
                        eval_metric.template.name
                        if eval_metric.template
                        else "Eval"
                    ),
                    "template_id": (
                        str(eval_metric.template.id)
                        if eval_metric.template
                        else None
                    ),
                }
            )
        return templates

    @swagger_serializer_method(
        serializer_or_field=DatasetOptimizationTrialTableRowSerializer(many=True)
    )
    def get_table(self, obj):
        return self._table

    @swagger_serializer_method(
        serializer_or_field=DatasetOptimizationColumnConfigItemSerializer(many=True)
    )
    def get_column_config(self, obj):
        return self._column_config


class DatasetOptimizationItemEvaluationSerializer(serializers.ModelSerializer):
    """Serializer for individual evaluation scores per trial item."""

    eval_name = serializers.CharField(
        source="eval_metric.template.name", read_only=True
    )
    eval_description = serializers.CharField(
        source="eval_metric.template.description", read_only=True
    )

    class Meta:
        model = DatasetOptimizationItemEvaluation
        fields = (
            "id",
            "eval_metric",
            "eval_name",
            "eval_description",
            "score",
            "reason",
        )


class DatasetOptimizationTrialItemSerializer(serializers.ModelSerializer):
    """Serializer for individual trial item results (per dataset row)."""

    evaluations = DatasetOptimizationItemEvaluationSerializer(many=True, read_only=True)

    class Meta:
        model = DatasetOptimizationTrialItem
        fields = (
            "id",
            "row_id",
            "score",
            "reason",
            "input_text",
            "output_text",
            "filled_prompt",
            "metadata",
            "evaluations",
            "created_at",
        )


class DatasetOptimizationDetailApiResponseSerializer(serializers.Serializer):
    """Envelope for the retrieve endpoint's 200 body, matching what
    ``GeneralMethods.success_response`` returns at runtime.
    """

    status = serializers.BooleanField()
    result = DatasetOptimizationDetailSerializer()
