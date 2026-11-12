# Register your models here.

from django.contrib import admin

from model_hub.models.ai_model import AIModel
from model_hub.models.annotation import AnnotationTask, ClickHouseAnnotation
from model_hub.models.api_key import ApiKey, SecretModel
from model_hub.models.auto_generate_events_run import AutoGenerateEventsRun
from model_hub.models.column_config import ColumnConfig
from model_hub.models.conversations import Conversation, Message, Node
from model_hub.models.custom_models import CustomAIModel
from model_hub.models.dataset_insight_meta import DatasetInsightMeta
from model_hub.models.dataset_properties import DatasetProperties
from model_hub.models.develop import DevelopAI
from model_hub.models.develop_annotations import Annotations, AnnotationsLabels
from model_hub.models.develop_dataset import Dataset, KnowledgeBaseFile
from model_hub.models.develop_optimisation import OptimizationDataset
from model_hub.models.error_localizer_model import ErrorLocalizerTask
from model_hub.models.evals_metric import (
    EvalSettings,
    EvalTemplate,
    Evaluator,
    Feedback,
    UserEvalMetric,
)
from model_hub.models.evaluation import Evaluation
from model_hub.models.experiments import (
    ExperimentComparison,
    ExperimentDatasetTable,
    ExperimentsTable,
)
from model_hub.models.insight import Insight
from model_hub.models.insight_status import InsightStatus
from model_hub.models.kb import KnowledgeBase
from model_hub.models.metric import Metric
from model_hub.models.metric_prompt_checker import PromptChecker
from model_hub.models.monitor_alert import MonitorAlert
from model_hub.models.monitors import Monitor
from model_hub.models.optimize_dataset import OptimizeDataset
from model_hub.models.performance_report import PerformanceReport
from model_hub.models.prompt import Prompt
from model_hub.models.prompt_label import PromptLabel


class AIModelAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user_model_id",
        "organization",
        "baseline_model_environment",
        "baseline_model_version",
        "default_metric",
        "model_type",
        "deleted",
    )
    list_filter = ("created_at", "deleted")
    search_fields = ("user_model_id", "id")

    list_per_page = 20

    def get_ordering(self, request):
        return ["-created_at"]  # Order by most recently updated

    def get_queryset(self, request):
        return super().get_queryset(request)


admin.site.register(AIModel, AIModelAdmin)


class ConversationModelAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user_provided_id",
        "organization",
        "title",
        "root_node",
        "metadata",
        "deleted",
    )
    list_filter = ("created_at", "deleted", "id")
    search_fields = ("user_provided_id", "id")

    list_per_page = 20

    def get_ordering(self, request):
        return ["-created_at"]  # Order by most recently updated

    def get_queryset(self, request):
        return super().get_queryset(request)


admin.site.register(Conversation, ConversationModelAdmin)


class NodeModelAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user_provided_id",
        "conversation",
        "parent_node",
        "deleted",
        "message",
    )
    list_filter = ("created_at", "deleted", "id")
    search_fields = ("user_provided_id", "id")

    list_per_page = 20

    def get_ordering(self, request):
        return ["-created_at"]  # Order by most recently updated

    def get_queryset(self, request):
        return super().get_queryset(request)


admin.site.register(Node, NodeModelAdmin)


class MessageModelAdmin(admin.ModelAdmin):
    list_display = ("id", "user_provided_id", "author", "metadata", "content")
    list_filter = ("created_at", "deleted", "id")
    search_fields = ("user_provided_id", "id")

    list_per_page = 20

    def get_ordering(self, request):
        return ["-created_at"]  # Order by most recently updated

    def get_queryset(self, request):
        return super().get_queryset(request)


admin.site.register(Message, MessageModelAdmin)


@admin.register(AnnotationTask)
class AnnotationTaskAdmin(admin.ModelAdmin):
    list_display = ("id", "task_name", "ai_model", "organization", "created_at")
    list_filter = ("ai_model", "organization", "created_at")
    search_fields = ("task_name", "organization__name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(ClickHouseAnnotation)
class ClickHouseAnnotationAdmin(admin.ModelAdmin):
    list_display = ("id", "uuid", "annotation_task", "is_annotated")
    list_filter = ("is_annotated",)
    search_fields = ("uuid",)
    readonly_fields = ("id",)


@admin.register(ColumnConfig)
class ColumnConfigAdmin(admin.ModelAdmin):
    list_display = ("id", "table_name", "identifier", "organization", "created_at")
    list_filter = ("table_name", "organization", "created_at")
    search_fields = ("identifier", "organization__name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(DatasetInsightMeta)
class DatasetInsightMetaAdmin(admin.ModelAdmin):
    list_display = ("id", "model", "environment", "version", "created_at")
    list_filter = ("environment", "created_at")
    search_fields = ("model__user_model_id", "version")
    readonly_fields = ("created_at", "updated_at")


@admin.register(DatasetProperties)
class DatasetPropertiesAdmin(admin.ModelAdmin):
    list_display = ("id", "environment", "version", "name", "datatype", "created_at")
    list_filter = ("environment", "datatype", "created_at")
    search_fields = ("name", "environment", "version")
    readonly_fields = ("created_at", "updated_at")


@admin.register(DevelopAI)
class DevelopAIAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "develop_type",
        "knowledge_base",
        "organization",
        "created_at",
    )
    list_filter = ("develop_type", "organization", "created_at")
    search_fields = ("develop_type", "organization__name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(Insight)
class InsightAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "model", "metric_type", "organization", "created_at")
    list_filter = ("metric_type", "organization", "created_at")
    search_fields = ("name", "model__user_model_id", "organization__name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(InsightStatus)
class InsightStatusAdmin(admin.ModelAdmin):
    list_display = ("id", "insight", "status", "message", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("insight__name", "status")
    readonly_fields = ("created_at", "updated_at")


@admin.register(KnowledgeBase)
class KnowledgeBaseAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "organization", "created_at")
    list_filter = ("organization", "created_at")
    search_fields = ("name", "organization__name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(Metric)
class MetricAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "metric_type", "created_at")
    list_filter = ("metric_type", "created_at")
    search_fields = ("name", "description")
    readonly_fields = ("created_at", "updated_at")


@admin.register(MonitorAlert)
class MonitorAlertAdmin(admin.ModelAdmin):
    list_display = ("id", "monitor", "created_at")
    list_filter = ("created_at",)
    search_fields = ("monitor__name",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(Monitor)
class MonitorAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "monitor_type", "status", "created_at")
    list_filter = ("monitor_type", "status", "created_at")
    search_fields = ("name",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(OptimizeDataset)
class OptimizeDatasetAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "optimize_type", "status", "created_at")
    list_filter = ("optimize_type", "status", "created_at")
    search_fields = ("name", "optimize_type")
    readonly_fields = ("created_at",)


@admin.register(PerformanceReport)
class PerformanceReportAdmin(admin.ModelAdmin):
    list_display = ("id", "model", "created_at")
    list_filter = ("created_at",)
    search_fields = ("model__name",)
    readonly_fields = ("created_at",)


@admin.register(Prompt)
class PromptAdmin(admin.ModelAdmin):
    list_display = ("id", "content", "used_in", "created_at")
    list_filter = ("used_in", "created_at")
    search_fields = ("content",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(Evaluation)
class EvaluationAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "organization", "status", "created_at")
    list_filter = ("status", "organization", "created_at")
    search_fields = ("user__email", "organization__name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(PromptLabel)
class PromptLabelAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "type", "organization", "created_at")
    list_filter = ("type", "organization", "created_at")
    search_fields = ("name", "organization__name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(ErrorLocalizerTask)
class ErrorLocalizerTaskAdmin(admin.ModelAdmin):
    list_display = ("id", "source", "status", "organization", "created_at")
    list_filter = ("source", "status", "organization", "created_at")
    search_fields = ("source", "organization__name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(EvalTemplate)
class EvalTemplateAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "owner", "organization", "created_at")
    list_filter = ("owner", "organization", "created_at")
    search_fields = ("name", "description", "organization__name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(UserEvalMetric)
class UserEvalMetricAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "status", "organization", "created_at")
    list_filter = ("status", "organization", "created_at")
    search_fields = ("name", "organization__name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(Feedback)
class FeedbackAdmin(admin.ModelAdmin):
    list_display = ("id", "source", "user", "created_at")
    list_filter = ("source", "created_at")
    search_fields = ("source", "user__email")
    readonly_fields = ("created_at", "updated_at")


@admin.register(EvalSettings)
class EvalSettingsAdmin(admin.ModelAdmin):
    list_display = ("id", "eval_id", "source", "user", "created_at")
    list_filter = ("source", "created_at")
    search_fields = ("eval_id", "source")
    readonly_fields = ("created_at", "updated_at")


@admin.register(Evaluator)
class EvaluatorAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "eval_template", "created_at")
    list_filter = ("eval_template", "created_at")
    search_fields = ("name", "eval_template__name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(Dataset)
class DatasetAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "source", "organization", "created_at")
    list_filter = ("source", "organization", "created_at")
    search_fields = ("name", "organization__name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(KnowledgeBaseFile)
class KnowledgeBaseFileAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "organization", "created_at")
    list_filter = ("organization", "created_at")
    search_fields = ("name", "organization__name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(Annotations)
class AnnotationsAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "organization", "created_at")
    list_filter = ("organization", "created_at")
    search_fields = ("name", "organization__name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(AnnotationsLabels)
class AnnotationsLabelsAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "type", "organization", "created_at")
    list_filter = ("type", "organization", "created_at")
    search_fields = ("name", "organization__name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(CustomAIModel)
class CustomAIModelAdmin(admin.ModelAdmin):
    list_display = ("id", "user_model_id", "provider", "organization", "created_at")
    list_filter = ("provider", "organization", "created_at")
    search_fields = ("user_model_id", "organization__name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(ExperimentsTable)
class ExperimentsTableAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "status", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("name", "description")
    # Never expose the raw JSON widget or replace the server-owned snapshot on
    # an admin save. Other fields remain editable; metadata is a public read.
    exclude = ("run_metadata",)
    readonly_fields = ("created_at", "updated_at", "public_metadata")

    @admin.display(description="Metadata")
    def public_metadata(self, obj):
        from model_hub.services.run_metadata_public import public_run_metadata

        return public_run_metadata(obj.run_metadata)


@admin.register(ExperimentDatasetTable)
class ExperimentDatasetTableAdmin(admin.ModelAdmin):
    list_display = ("id", "created_at")
    list_filter = ("created_at",)
    search_fields = ("id",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(ExperimentComparison)
class ExperimentComparisonAdmin(admin.ModelAdmin):
    list_display = ("id", "experiment", "created_at")
    list_filter = ("created_at",)
    search_fields = ("experiment__name",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(OptimizationDataset)
class OptimizationDatasetAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "optimize_type", "status", "created_at")
    list_filter = ("optimize_type", "status", "created_at")
    search_fields = ("name",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(ApiKey)
class ApiKeyAdmin(admin.ModelAdmin):
    list_display = ("id", "provider", "organization", "created_at")
    list_filter = ("provider", "organization", "created_at")
    search_fields = ("provider", "organization__name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(SecretModel)
class SecretModelAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "secret_type", "organization", "created_at")
    list_filter = ("secret_type", "organization", "created_at")
    search_fields = ("name", "organization__name")
    readonly_fields = ("created_at", "updated_at")


@admin.register(PromptChecker)
class PromptCheckerAdmin(admin.ModelAdmin):
    list_display = ("id", "user_prompt", "ai_prompt", "created_at")
    list_filter = ("created_at",)
    search_fields = ("user_prompt", "ai_prompt")
    readonly_fields = ("created_at",)


@admin.register(AutoGenerateEventsRun)
class AutoGenerateEventsRunAdmin(admin.ModelAdmin):
    list_display = ("id", "ai_model", "last_run_at", "total_events")
    list_filter = ("last_run_at",)
    search_fields = ("ai_model__user_model_id",)
    readonly_fields = ("last_run_at",)
