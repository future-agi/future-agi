from django.urls import include, path
from rest_framework.routers import DefaultRouter

from model_hub.views.ai_eval_writer import AIEvalWriterView
from model_hub.views.ai_filter import AIFilterView
from model_hub.views.annotation_queues import (
    AnnotationQueueViewSet,
    AutomationRuleViewSet,
    QueueItemViewSet,
)
from model_hub.views.custom_model import (
    CustomAIModelCreateView,
    CustomAIModelDetailsView,
    CustomAIModelListView,
    CustomAIModelView,
    DeleteCustomAIModelView,
    EditCustomModel,
    UpdateBaselineDatasetCustomAIModelView,
)
from model_hub.views.dataset_optimization import DatasetOptimizationViewSet
from model_hub.views.datasets.add_rows.existing_dataset import AddRowsFromExistingView
from model_hub.views.datasets.add_rows.huggingface import AddRowsFromHuggingFaceView
from model_hub.views.datasets.add_rows.synthetic import AddSyntheticData
from model_hub.views.datasets.create.dataset_from_experiment import (
    CreateDatasetFromExpView,
)
from model_hub.views.datasets.create.empty_dataset import CreateEmptyDatasetView
from model_hub.views.datasets.create.file_upload import (
    CreateDatasetFromLocalFileView,
    DatasetCreationProgressView,
)
from model_hub.views.datasets.create.huggingface import (
    CreateDatasetFromHuggingFaceView,
    GetHuggingFaceDatasetConfigView,
)
from model_hub.views.datasets.create.synthetic import (
    CreateSyntheticDataset,
    GetSyntheticDatasetConfigView,
    UpdateSyntheticDatasetConfigView,
)
from model_hub.views.derived_variables import (
    extract_derived_variables,
    get_dataset_derived_variables_view,
    get_derived_variable_schema_view,
    get_prompt_derived_variables,
    preview_derived_variables,
)
from model_hub.views.develop_annotations import (
    AnnotationsLabelsViewSet,
    AnnotationSummaryView,
    AnnotationTaskViewSet,
    AnnotationsViewSet,
    UserViewSet,
)
from model_hub.views.develop_dataset import *  # noqa: F403
from model_hub.views.develop_dataset import (
    ColumnConfigView,
    CreateKnowledgeBaseView,
    GetCellDataView,
    GetDatasetsView,
    GetEmbeddingsListView,
    GetEvalConfigView,
    GetJsonColumnSchemaView,
    SingleRowEvaluationView,
    StopUserEvalView,
)
from model_hub.views.develop_optimisation import (
    OptimisationCreateView,
    OptimizationDatasetDetailView,
    OptimizationDatasetListView,
    get_metrics_by_column,
)
from model_hub.views.develop_optimiser import OptimizationDetailView
from model_hub.views.dynamic_columns import (
    AddApiColumnView,
    AddVectorDBColumnView,
    ClassifyColumnView,
    ConditionalColumnView,
    ExecutePythonCodeView,
    ExtractEntitiesView,
    ExtractJsonColumnView,
    GetOperationConfigView,
    PreviewDatasetOperationView,
    RerunOperationView,
)
from model_hub.views.eval_group import EvalGroupView
from model_hub.views.eval_runner import (
    CustomEvalTemplateCreateView,
    DatasetEvalStatsView,
    EvalTemplateCreateView,
    EvalUserTemplateCreateView,
)
from model_hub.views.eval_summary_templates import (
    EvalSummaryTemplateDetailView,
    EvalSummaryTemplateListView,
)
from model_hub.views.experiment_feedback_v2 import (
    ExperimentFeedbackCreateV2View,
    ExperimentFeedbackDetailsV2View,
    ExperimentFeedbackGetTemplateV2View,
    ExperimentFeedbackSubmitV2View,
)
from model_hub.views.experiments import (
    AddExperimentEvalView,
    DatasetExperimentsView,
    DownloadExperimentsView,
    ExperimentComparisonDetailsView,
    ExperimentDatasetComparisonV2View,
    ExperimentDatasetComparisonView,
    ExperimentDeleteV2View,
    ExperimentDeleteView,
    ExperimentDerivedVariablesView,
    ExperimentEvaluationStatsView,
    ExperimentJsonSchemaView,
    ExperimentListAPIView,
    ExperimentListV2APIView,
    ExperimentNameSuggestionView,
    ExperimentNameValidationView,
    ExperimentRerunCellsV2View,
    ExperimentRerunV2View,
    ExperimentRerunView,
    ExperimentsTableDetailView,
    ExperimentsTableV2View,
    ExperimentsTableView,
    ExperimentStatsV2View,
    ExperimentStatsView,
    ExperimentStopV2View,
    GetRowDiffV2View,
    GetRowDiffView,
    RunAdditionalEvaluationsView,
)
from model_hub.views.kb import KnowledgeBaseViewSet
from model_hub.views.metric import (
    AllMetricApiView,
    CreateMetricApiView,
    EditMetricApiView,
    GetMetricTagOptions,
    MetricApiView,
    TestMetric,
)
from model_hub.views.optimize_dataset import (
    OptimizeDatasetColumnConfig,
    OptimizeDatasetGet,
    OptimizeDatasetList,
    OptimizeDatasetPromptExploreColumnConfig,
    OptimizeDatasetRightColumnConfig,
    OptimizedDatasetKbView,
    OptimizedDatasetView,
    OptimizeDetailView,
    RightAnswerResultsView,
    TemplateExploreView,
    TemplateResultsView,
)
from model_hub.views.overview import OverviewView
from model_hub.views.performance import (
    GetPerformanceOptionsView,
    GetPerformanceTagDistributionView,
    PerformanceDetailsExport,
    PerformanceDetailsView,
    PerformanceView,
)
from model_hub.views.performance_report import PerformanceReportApiView
from model_hub.views.prompt_base_template import PromptBaseTemplateViewSet
from model_hub.views.prompt_folder import PromptFolderViewSet
from model_hub.views.prompt_labels import PromptLabelViewSet
from model_hub.views.prompt_metrics import (
    FetchPromptMetricsNullView,
    FetchPromptMetricsSpanView,
    FetchPromptObserveMetricsView,
)
from model_hub.views.prompt_template import (  # UserResponseSchemaView,
    ColumnValuesAPIView,
    PromptExecutionViewSet,
    PromptHistoryExecutionViewSet,
    PromptTemplateViewSet,
    UploadFileView,
    UserResponseSchemaViewSet,
)
from model_hub.views.run_prompt import *  # noqa: F403
from model_hub.views.run_prompt import (
    ApiKeyViewSet,
    LitellmAPIView,
    LiteLLMModelListView,
)
from model_hub.views.scores import ScoreViewSet
from model_hub.views.secrets import SecretViewSet
from model_hub.views.separate_evals import (
    CellErrorLocalizerView,
    CompositeEvalAdhocExecuteView,
    CompositeEvalCreateView,
    CompositeEvalDetailView,
    CompositeEvalExecuteView,
    DeleteEvalTemplateView,
    DuplicateEvalTemplateView,
    EvalCodeSnippetAPIView,
    EvalFeedbackListView,
    EvalMetricView,
    EvalPlayGroundAPIView,
    EvalPlayGroundFeedbackAPIView,
    EvalTemplateBulkDeleteView,
    EvalTemplateCreateV2View,
    EvalTemplateDetailView,
    EvalTemplateListChartsView,
    EvalTemplateListView,
)
from model_hub.views.separate_evals import (
    EvalTemplateUpdateView as EvalTemplateUpdateViewV2,
)
from model_hub.views.separate_evals import (
    EvalTemplateVersionCreateView,
    EvalTemplateVersionListView,
    EvalUsageStatsView,
    GetAPICallLogDetailsView,
    GetAPICallLogView,
    GetEvalTemplateNameView,
    GetEvalTemplates,
    GroundTruthConfigView,
    GroundTruthDataView,
    GroundTruthDeleteView,
    GroundTruthListView,
    GroundTruthMappingView,
    GroundTruthRoleMappingView,
    GroundTruthSearchView,
    GroundTruthStatusView,
    GroundTruthTriggerEmbeddingView,
    GroundTruthUploadView,
    RestoreVersionView,
    SetDefaultVersionView,
    TestEvaluationTemplateAPIView,
    UpdateEvalTemplateView,
)
from model_hub.views.tools import ToolsViewSet
from model_hub.views.tts_voices import TTSVoiceViewSet

router = DefaultRouter()
router.register(r"kb", KnowledgeBaseViewSet, basename="knowledge-base")
router.register(r"api-keys", ApiKeyViewSet, basename="api-key")
router.register(r"tools", ToolsViewSet, basename="tool")
router.register(r"tts-voices", TTSVoiceViewSet, basename="tts-voices")
router.register(r"prompt-labels", PromptLabelViewSet, basename="prompt-label")
router.register(r"prompt-folders", PromptFolderViewSet, basename="prompt-folder")
router.register(r"eval-groups", EvalGroupView, basename="eval-groups")
router.register(
    r"prompt-base-templates", PromptBaseTemplateViewSet, basename="prompt-base-template"
)
router.register(r"secrets", SecretViewSet, basename="secret")
router.register(r"prompt-templates", PromptTemplateViewSet, basename="prompt-templates")
router.register(
    r"prompt-executions", PromptExecutionViewSet, basename="prompt-executions"
)
router.register(
    r"prompt-history-executions",
    PromptHistoryExecutionViewSet,
    basename="prompt-history-executions",
)
router.register(r"feedback", FeedbackViewSet, basename="feedback")  # noqa: F405
router.register(r"annotation-tasks", AnnotationTaskViewSet, basename="annotation-tasks")
router.register(r"annotations", AnnotationsViewSet, basename="annotations")
router.register(
    r"annotations-labels", AnnotationsLabelsViewSet, basename="annotations-labels"
)
router.register(
    r"annotation-queues", AnnotationQueueViewSet, basename="annotation-queues"
)
router.register(r"scores", ScoreViewSet, basename="scores")
router.register(
    r"organizations/(?P<organization_id>[^/.]+)/users", UserViewSet, basename="user"
)
router.register(
    r"response_schema", UserResponseSchemaViewSet, basename="response_schema"
)
router.register(
    r"dataset-optimization", DatasetOptimizationViewSet, basename="dataset-optimization"
)


queue_items_router = DefaultRouter()
queue_items_router.register(r"items", QueueItemViewSet, basename="queue-items")
queue_items_router.register(
    r"automation-rules", AutomationRuleViewSet, basename="automation-rules"
)

urlpatterns = [
    path("", include(router.urls)),
    path(
        "annotation-queues/<uuid:queue_id>/",
        include(queue_items_router.urls),
    ),
    path("upload-file/", UploadFileView.as_view(), name="upload-file"),
    # Custom Models
    path("custom-models/", CustomAIModelView.as_view(), name="custom-model-view"),
    path(
        "custom-models/list/", CustomAIModelListView.as_view(), name="custom-model-list"
    ),
    path(
        "custom-models/<uuid:id>/",
        CustomAIModelDetailsView.as_view(),
        name="custom-model-detail",
    ),
    path(
        "custom_models/create/",
        CustomAIModelCreateView.as_view(),
        name="custom-model-create",
    ),
    path(
        "custom_models/update-baseline/<uuid:id>/",
        UpdateBaselineDatasetCustomAIModelView.as_view(),
        name="update-custom-model-baseline",
    ),
    path("custom_models/edit/", EditCustomModel.as_view(), name="edit-cutom-model"),
    path(
        "custom_models/delete/",
        DeleteCustomAIModelView.as_view(),
        name="delete-custom-model",
    ),
    # ==========================================================================
    path(
        "performance/<uuid:id>/",
        PerformanceView.as_view(),
        name="ai-model-performance",
    ),
    path(
        "performance/options/<uuid:model_id>/",
        GetPerformanceOptionsView.as_view(),
        name="get-performance-options",
    ),
    path(
        "performance/tag-distribution/<uuid:model_id>/",
        GetPerformanceTagDistributionView.as_view(),
        name="get-performance-tag-distribution",
    ),
    path(
        "performance/report/<uuid:model_id>/",
        PerformanceReportApiView.as_view(),
        name="performance-report",
    ),
    path(
        "performance/report/<uuid:model_id>/<uuid:report_id>/",
        PerformanceReportApiView.as_view(),
        name="performance-report-detail",
    ),
    path(
        "performance/detail/<uuid:id>/",
        PerformanceDetailsView.as_view(),
        name="ai-model-performance-detail",
    ),
    path(
        "performance/export/<uuid:id>/",
        PerformanceDetailsExport.as_view(),
        name="ai-model-performance-export",
    ),
    path(
        "dataset/columns/<uuid:dataset_id>/",
        GetColumnDetailView.as_view(),  # noqa: F405
        name="get-dataset-column-details",
    ),
    path(
        "datasets/explanation-summary/<uuid:dataset_id>/",
        GetDatasetExplanationSummary.as_view(),
        name="get-dataset-explanation-summary",
    ),
    path(
        "datasets/explanation-summary/<uuid:dataset_id>/refresh/",
        RefreshDatasetExplanationSummary.as_view(),
        name="refresh-dataset-explanation-summary",
    ),
    path(
        "dataset/<uuid:dataset_id>/json-schema/",
        GetJsonColumnSchemaView.as_view(),
        name="get-json-column-schema",
    ),
    path(
        "custom-metric/<uuid:model_id>/",
        MetricApiView.as_view(),
        name="get-custom-metric",
    ),
    path("custom-metric/test/", TestMetric.as_view(), name="test-metric"),
    path(
        "custom-metric/create/",
        CreateMetricApiView.as_view(),
        name="create-custom-metric",
    ),
    path(
        "custom-metric/update/",
        EditMetricApiView.as_view(),
        name="update-custom-metric",
    ),
    path(
        "custom-metric/all/<uuid:model_id>/",
        AllMetricApiView.as_view(),
        name="get-all-custom-metric",
    ),
    path(
        "custom-metric/tag-options/<uuid:metric_id>/",
        GetMetricTagOptions.as_view(),
        name="custom-metric-tag-options",
    ),
    path("overview/", OverviewView.as_view(), name="overview"),
    path(
        "optimize-dataset/<uuid:model_id>/",
        OptimizedDatasetView.as_view(),
        name="optimize-dataset-view",
    ),
    path(
        "optimize-dataset/knowledge-base/",
        OptimizedDatasetKbView.as_view(),
        name="optimize-dataset-kb-view",
    ),
    path(
        "optimize-dataset/<uuid:model_id>/<uuid:optimization_id>/",
        OptimizeDetailView.as_view(),
        name="optimize-dataset-view",
    ),
    path(
        "optimize-dataset/<uuid:model_id>/right-answers/<uuid:optimization_id>/",
        RightAnswerResultsView.as_view(),
        name="get-results-optimize-right-answer",
    ),
    path(
        "optimize-dataset/<uuid:model_id>/prompt-template-result/<uuid:optimization_id>/",
        TemplateResultsView.as_view(),
        name="get-results-optimize-template",
    ),
    path(
        "optimize-dataset/<uuid:model_id>/prompt-template-explore/<uuid:optimization_id>/",
        TemplateExploreView.as_view(),
        name="get-explore-optimize-template",
    ),
    path(
        "optimize-dataset/<uuid:model_id>/column-config/",
        OptimizeDatasetColumnConfig.as_view(),
        name="optimize-dataset-column-config",
    ),
    path(
        "optimize-dataset/",
        OptimizeDatasetList.as_view(),
        name="optimize-dataset-list",
    ),
    path(
        "optimize-dataset/kb/<uuid:optim_id>/",
        OptimizeDatasetGet.as_view(),
        name="optimize-dataset-get",
    ),
    path(
        "optimize-dataset/<uuid:model_id>/column-config/right-answers/<uuid:optimization_id>/",
        OptimizeDatasetRightColumnConfig.as_view(),
        name="optimize-dataset-right-answer-column-config",
    ),
    path(
        "optimize-dataset/<uuid:model_id>/column-config/prompt-template-explore/<uuid:optimization_id>/",
        OptimizeDatasetPromptExploreColumnConfig.as_view(),
        name="optimize-dataset-prompt-template-explore-column-config",
    ),
    path(
        "kb/supported-embedding-models",
        KnowledgeBaseViewSet.as_view({"get": "supported_embedding_models"}),
        name="supported-embedding-models",
    ),
    path(
        "develops/create-dataset-from-local-file/",
        CreateDatasetFromLocalFileView.as_view(),
        name="create-dataset-from-local-file",
    ),
    path(
        "develops/dataset-creation-progress/<uuid:dataset_id>/",
        DatasetCreationProgressView.as_view(),
        name="dataset-creation-progress",
    ),
    path(
        "develops/<uuid:dataset_id>/extract-json-column/",
        ExtractJsonColumnView.as_view(),
        name="extract_json_column",
    ),
    path("embeddings/", GetEmbeddingsListView.as_view(), name="get-embeddings-list"),
    path(
        "embeddings/<str:type>/",
        GetEmbeddingsListView.as_view(),
        name="get-embedding-details",
    ),
    path(
        "datasets/<uuid:dataset_id>/add_vector_db_column/",
        AddVectorDBColumnView.as_view(),
        name="add_vector_db_column",
    ),
    path(
        "datasets/<str:dataset_id>/conditional-column/",
        ConditionalColumnView.as_view(),
        name="conditional-column",
    ),
    # path(
    #     "datasets/<uuid:dataset_id>/execute-code/",
    #     ExecutePythonCodeView.as_view(),
    #     name="execute-python",
    # ),
    path(
        "datasets/<uuid:dataset_id>/add-api-column/",
        AddApiColumnView.as_view(),
        name="add-api-column",
    ),
    path(
        "datasets/<uuid:dataset_id>/extract-entities/",
        ExtractEntitiesView.as_view(),
        name="extract-entities",
    ),
    path(
        "datasets/<uuid:dataset_id>/classify-column/",
        ClassifyColumnView.as_view(),
        name="classify-column",
    ),
    path(
        "datasets/<uuid:dataset_id>/preview/<str:operation_type>/",
        PreviewDatasetOperationView.as_view(),
        name="preview-dataset-operation",
    ),
    path(
        "develops/create-dataset-from-huggingface/",
        CreateDatasetFromHuggingFaceView.as_view(),
        name="create-dataset-from-huggingface",
    ),
    path(
        "develops/create-synthetic-dataset/",
        CreateSyntheticDataset.as_view(),
        name="create-synthetic-dataset",
    ),
    path(
        "develops/<uuid:dataset_id>/synthetic-config/",
        GetSyntheticDatasetConfigView.as_view(),
        name="get-synthetic-dataset-config",
    ),
    path(
        "develops/<uuid:dataset_id>/update-synthetic-config/",
        UpdateSyntheticDatasetConfigView.as_view(),
        name="update-synthetic-dataset-config",
    ),
    path(
        "develops/get-huggingface-dataset-config/",
        GetHuggingFaceDatasetConfigView.as_view(),
        name="get-huggingface-dataset-config",
    ),
    path(
        "datasets/huggingface/list/",
        GetHuggingFaceDatasetListView.as_view(),  # noqa: F405
        name="huggingface-dataset-list",
    ),
    path(
        "datasets/huggingface/detail/",
        GetHuggingFaceDatasetDetailView.as_view(),  # noqa: F405
        name="huggingface-dataset-detail",
    ),
    path(
        "develops/clone-dataset/<uuid:dataset_id>/",
        CloneDatasetView.as_view(),  # noqa: F405
        name="clone-dataset",
    ),
    path(
        "develops/add-as-new/",
        AddAsNewDataset.as_view(),  # noqa: F405
        name="add-as-new",
    ),
    path("develops/get-datasets/", GetDatasetsView.as_view(), name="get-datasets"),
    path(
        "develops/<uuid:dataset_id>/stop_user_eval/<uuid:eval_id>/",
        StopUserEvalView.as_view(),
        name="stop-user-eval",
    ),
    path(
        "develops/<uuid:dataset_id>/get-dataset-table/",
        GetDatasetTableView.as_view(),  # noqa: F405
        name="get-dataset-table",
    ),
    path(
        "develops/<uuid:experiment_dataset_id>/get-experiment-dataset-table/",
        GetExperimentDatasetTableView.as_view(),  # noqa: F405
        name="get-experiment-dataset-table",
    ),
    path(
        "develops/<uuid:exp_dataset_id>/create-dataset/",
        CreateDatasetFromExpView.as_view(),
        name="create-dataset",
    ),
    path(
        "develops/get-derived-datasets/<uuid:dataset_id>/",
        GetDerivedDatasets.as_view(),  # noqa: F405
        name="get-dataset-table",
    ),
    path("run-prompt/", LitellmAPIView.as_view(), name="litellm-api"),
    # path('set-api-key/', ApiKeyCreateView.as_view(), name='key-api'),
    path(
        "dataset/<str:dataset_id>/eval-stats/",
        DatasetEvalStatsView.as_view(),
        name="dataset-eval-stats",
    ),
    path(
        "dataset/<str:dataset_id>/annotation-summary/",
        AnnotationSummaryView.as_view(),
        name="dataset-eval-stats",
    ),
    path(
        "datasets/<uuid:dataset_id>/merge/",
        MergeDatasetView.as_view(),  # noqa: F405
        name="merge-dataset",
    ),
    path(
        "datasets/<uuid:dataset_id>/duplicate-rows/",
        DuplicateRowsView.as_view(),  # noqa: F405
        name="duplicate-rows",
    ),
    path(
        "datasets/<uuid:dataset_id>/duplicate/",
        DuplicateDatasetView.as_view(),  # noqa: F405
        name="duplicate-dataset",
    ),
    path(
        "dataset/<str:dataset_id>/run-prompt-stats/",
        DatasetRunPromptStatsView.as_view(),  # noqa: F405
        name="dataset-run-prompt-stats",
    ),
    path(
        "eval-template/create/",
        EvalTemplateCreateView.as_view(),
        name="eval_template_create",
    ),
    path(
        "eval-user-template/create/",
        EvalUserTemplateCreateView.as_view(),
        name="eval_template_create",
    ),
    path(
        "optimisation/create/",
        OptimisationCreateView.as_view(),
        name="optimisation_create",
    ),
    path(
        "optimisation/update/<uuid:pk>/",
        OptimisationCreateView.as_view(),
        name="optimisation_update",
    ),
    path(
        "optimisation/",
        OptimizationDatasetListView.as_view(),
        name="optimization-dataset-list",
    ),
    path(
        "optimisation/<uuid:pk>/",
        OptimizationDatasetDetailView.as_view(),
        name="optimization-dataset-detail",
    ),
    path(
        "optimisation/<uuid:pk>/details/",
        OptimizationDetailView.as_view(),
        name="optimisation-details",
    ),
    path("metrics/by-column/", get_metrics_by_column, name="metrics-by-column"),
    path(
        "column-config/<uuid:column_id>/",
        ColumnConfigView.as_view(),
        name="column-config",
    ),
    path(
        "experiments/<uuid:experiment_id>/",
        DatasetExperimentsView.as_view(),
        name="experiments-list",
    ),
    path(
        "experiments/<uuid:experiment_id>/<uuid:row_id>/",
        DatasetExperimentsView.as_view(),
        name="experiments-row",
    ),
    # datasets/<uuid:dataset_id>/experiments/
    path(
        "experiment-detail/",
        ExperimentsTableDetailView.as_view(),
        name="experiments-detail",
    ),
    path(
        "experiments/",
        ExperimentsTableView.as_view(),
        name="experiments-create",
    ),
    path(
        "experiments/delete/", ExperimentDeleteView.as_view(), name="experiment-delete"
    ),
    path("experiments/re-run/", ExperimentRerunView.as_view(), name="experiment-rerun"),
    path(
        "experiments/<uuid:experiment_id>/comparisons/",
        ExperimentComparisonDetailsView.as_view(),
        name="experiment-comparisons",
    ),
    path(
        "experiments/<str:experiment_id>/run-evaluations/",
        RunAdditionalEvaluationsView.as_view(),
        name="run-additional-evaluations",
    ),
    path(
        "experiments/<uuid:experiment_id>/add-eval/",
        AddExperimentEvalView.as_view(),
        name="add-experiment-eval",
    ),
    path(
        "experiments/data/",
        ExperimentListAPIView.as_view(),
        name="api-experiments-list",
    ),
    path(
        "experiments/<str:experiment_id>/stats/",
        ExperimentStatsView.as_view(),
        name="experiment-stats",
    ),
    path(
        "experiments/<str:experiment_id>/download/",
        DownloadExperimentsView.as_view(),
        name="experiment-stats",
    ),
    path(
        "experiments/<str:experiment_id>/compare-experiments/",
        ExperimentDatasetComparisonView.as_view(),
        name="compare-experiment-datasets",
    ),
    path(
        "experiments/<uuid:experiment_id>/evaluations/<uuid:evaluation_id>/stats/",
        ExperimentEvaluationStatsView.as_view(),
        name="experiment-evaluation-stats",
    ),
    path(
        "datasets/get-base-columns/",
        GetBaseColumnsView.as_view(),  # noqa: F405
        name="get-base-columns",
    ),
    path(
        "datasets/get-compare-row/<uuid:compare_id>/<uuid:row_id>/",
        GetCompareDatasetRow.as_view(),  # noqa: F405
        name="get-compare-row",
    ),
    path(
        "datasets/delete-compare/<uuid:compare_id>/",
        GetCompareDatasetRow.as_view(),  # noqa: F405
        name="delete-compare",
    ),
    path(
        "datasets/<uuid:dataset_id>/compare-datasets/",
        CompareDatasetsView.as_view(),  # noqa: F405
        name="compare-datasets",
    ),
    path(
        "datasets/<uuid:dataset_id>/compare-datasets/add-eval/",
        AddCompareExperimentEvalView.as_view(),  # noqa: F405
        name="add-compare-experiment-eval",
    ),
    path(
        "datasets/compare/get-evals-list/",
        GetCompareEvalsListView.as_view(),  # noqa: F405
        name="compare-evals-list",
    ),
    path(
        "datasets/compare/preview-run-eval/",
        ComparePreviewRunEvalView.as_view(),  # noqa: F405
        name="compare-preview-run-eval",
    ),
    path(
        "datasets/<uuid:dataset_id>/compare-stats/",
        CompareDatasetsStatsView.as_view(),  # noqa: F405
        name="compare-datasets-stats",
    ),
    path(
        "datasets/<uuid:dataset_id>/compare-datasets/download/",
        DownloadComparisonDatasetView.as_view(),  # noqa: F405
        name="compare-datasets-download",
    ),
    path(
        "datasets/<uuid:dataset_id>/compare-datasets/start-eval/",
        CompareDatasetsStartEvalsProcess.as_view(),  # noqa: F405
        name="compare-datasets-start-eval",
    ),
    path(
        "develops/create-empty-dataset/",
        CreateEmptyDatasetView.as_view(),
        name="create-empty-dataset",
    ),
    path(
        "develops/get-datasets-names/",
        GetDatasetsNamesView.as_view(),  # noqa: F405
        name="get-datasets-names",
    ),
    path(
        "develops/<uuid:dataset_id>/add_static_column/",
        AddStaticColumnView.as_view(),  # noqa: F405
        name="add-static-column",
    ),
    path(
        "develops/<uuid:dataset_id>/add_multiple_static_columns/",
        AddMultipleStaticColumnsView.as_view(),  # noqa: F405
        name="add-multiple-static-columns",
    ),
    path(
        "develops/<uuid:dataset_id>/add_columns/",
        AddColumnsView.as_view(),  # noqa: F405
        name="add-columns",
    ),
    path(
        "develops/<uuid:dataset_id>/add_empty_columns/",
        AddEmptyColumnsView.as_view(),  # noqa: F405
        name="add-columns",
    ),
    path(
        "develops/<uuid:dataset_id>/add_empty_rows/",
        AddEmptyRowsView.as_view(),  # noqa: F405
        name="add-empty-rows",
    ),
    path(
        "develops/<uuid:dataset_id>/add_rows/",
        AddDataRowsView.as_view(),  # noqa: F405
        name="add-rows",
    ),
    path(
        "develops/create-dataset-manually/",
        ManuallyCreateDatasetView.as_view(),  # noqa: F405
        name="create-dataset-manually",
    ),
    path(
        "develops/add_rows_sdk/",
        AddSDKRowsView.as_view(),  # noqa: F405
        name="add-rows-sdk",
    ),
    path(
        "develops/add_rows_from_file/",
        AddRowsFromFile.as_view(),  # noqa: F405
        name="add-rows-file",
    ),
    path(
        "develops/<uuid:dataset_id>/add_rows_from_existing_dataset/",
        AddRowsFromExistingView.as_view(),
        name="add-rows-dataset",
    ),
    path(
        "develops/<uuid:dataset_id>/add_synthetic_data/",
        AddSyntheticData.as_view(),
        name="add-rows-dataset",
    ),
    path(
        "develops/<uuid:dataset_id>/add_rows_from_huggingface/",
        AddRowsFromHuggingFaceView.as_view(),
        name="add-rows-dataset",
    ),
    path(
        "develops/<uuid:dataset_id>/delete_column/<uuid:column_id>/",
        DeleteColumnView.as_view(),  # noqa: F405
        name="delete-column",
    ),
    path(
        "develops/<uuid:dataset_id>/delete_row/",
        DeleteRowView.as_view(),  # noqa: F405
        name="delete-row",
    ),
    path(
        "develops/delete_dataset/",
        DeleteDatasetView.as_view(),  # noqa: F405
        name="delete-dataset",
    ),
    path(
        "develops/<uuid:dataset_id>/update_column_name/<uuid:column_id>/",
        UpdateColumnNameView.as_view(),  # noqa: F405
        name="update-column-name",
    ),
    path(
        "develops/<uuid:dataset_id>/edit_dataset_behavior/",
        EditDatasetBehaviorView.as_view(),  # noqa: F405
        name="edit-dataset-behavior",
    ),
    path(
        "develops/<uuid:dataset_id>/update_cell_value/",
        UpdateCellValueView.as_view(),  # noqa: F405
        name="update-cell-value",
    ),
    path(
        "develops/<uuid:dataset_id>/update_column_type/<uuid:column_id>/",
        UpdateColumnTypeView.as_view(),  # noqa: F405
        name="update_column_type",
    ),
    path(
        "develops/<uuid:dataset_id>/download_dataset/",
        DownloadDatasetView.as_view(),  # noqa: F405
        name="download-dataset",
    ),
    # Evals endpoints
    path(
        "develops/<uuid:dataset_id>/add_user_eval/",
        AddUserEvalView.as_view(),  # noqa: F405
        name="add_user_eval",
    ),
    path(
        "create_custom_evals/",
        CustomEvalTemplateCreateView.as_view(),
        name="custom-eval-template-create",
    ),
    path(
        "develops/<uuid:dataset_id>/get_eval_structure/<uuid:eval_id>/",
        GetEvalStructureView.as_view(),  # noqa: F405
        name="get-eval-structure",
    ),
    path(
        "develops/<uuid:dataset_id>/get_evals_list/",
        GetEvalsListView.as_view(),  # noqa: F405
        name="get-evals-list",
    ),
    path(
        "develops/get_function_list/",
        GetFunctionList.as_view(),  # noqa: F405
        name="get-function-list",
    ),
    path(
        "develops/<uuid:dataset_id>/start_evals_process/",
        StartEvalsProcess.as_view(),  # noqa: F405
        name="start-evals-process",
    ),
    path(
        "develops/<uuid:dataset_id>/delete_user_eval/<uuid:eval_id>/",
        DeleteEvalsView.as_view(),  # noqa: F405
        name="delete-evals",
    ),
    path(
        "develops/<uuid:dataset_id>/delete_template_eval/<uuid:eval_id>/",
        DeleteTemplateEvalsView.as_view(),  # noqa: F405
        name="delete-template-evals",
    ),
    path(
        "develops/<uuid:dataset_id>/edit_and_run_user_eval/<uuid:eval_id>/",
        EditAndRunUserEvalView.as_view(),  # noqa: F405
        name="edit-and-run-column",
    ),
    path(
        "develops/<uuid:dataset_id>/preview_run_eval/",
        PreviewRunEvalView.as_view(),  # noqa: F405
        name="preview-run-eval",
    ),
    path(
        "develops/add_run_prompt_column/",
        AddRunPromptColumnView.as_view(),  # noqa: F405
        name="add-run-prompt-column",
    ),
    path(
        "develops/retrieve_run_prompt_column_config/",
        RetrieveRunPromptColumnConfigView.as_view(),  # noqa: F405
        name="retrieve-run-prompt-column-config",
    ),
    path(
        "develops/edit_run_prompt_column/",
        EditRunPromptColumnView.as_view(),  # noqa: F405
        name="edit-run-prompt-column",
    ),
    path(
        "develops/preview_run_prompt_column/",
        PreviewRunPromptColumnView.as_view(),  # noqa: F405
        name="preview-run-prompt-column",
    ),
    path(
        "develops/provider-status/",
        GetProviderStatusView.as_view(),  # noqa: F405
        name="provider-status",
    ),
    path("get-column-values/", ColumnValuesAPIView.as_view(), name="get-column-values"),
    path(
        "prompt/metrics/",
        FetchPromptObserveMetricsView.as_view(),
        name="fetch-prompt-observe-metrics",
    ),
    path(
        "prompt/span-metrics/",
        FetchPromptMetricsSpanView.as_view(),
        name="fetch-prompt-span-metrics",
    ),
    path(
        "prompt/metrics/empty-screen",
        FetchPromptMetricsNullView.as_view(),
        name="fetch-prompt-span-metrics-empty-screen",
    ),
    path(
        "develops/retrieve_run_prompt_options/",
        RetrieveRunPromptOptionsView.as_view(),  # noqa: F405
        name="retrieve-run-prompt-options",
    ),
    path("api/models_list/", LiteLLMModelListView.as_view(), name="model-list"),
    path("api/model_voices/", LiteLLMModelVoicesView.as_view(), name="model-voices"),
    path(
        "api/model_parameters/", ModelParametersView.as_view(), name="model-parameters"
    ),
    path("evaluate-rows/", SingleRowEvaluationView.as_view(), name="evaluate-rows"),
    path(
        "run-prompt-for-rows/",
        RunPromptForRowsView.as_view(),  # noqa: F405
        name="run_prompt_for_rows",
    ),
    path(
        "develops/<uuid:dataset_id>/get-row-data/",
        GetRowDataView.as_view(),  # noqa: F405
        name="get-row-data",
    ),
    path("develops/get-cell-data/", GetCellDataView.as_view(), name="get-cell-data"),
    path("develops/get-row-diff/", GetRowDiffView.as_view(), name="get-row-diff"),
    path("get-eval-logs", GetAPICallLogView.as_view(), name="get-eval-logs"),
    path(
        "cells/<uuid:cell_id>/run-error-localizer/",
        CellErrorLocalizerView.as_view(),
        name="cell-run-error-localizer",
    ),
    path(
        "get-eval-logs-details",
        GetAPICallLogDetailsView.as_view(),
        name="get-eval-logs-details",
    ),
    path("get-eval-metrics", EvalMetricView.as_view(), name="get-eval-metrics"),
    path(
        "get-eval-templates", GetEvalTemplates.as_view(), name="get-base-eval-templates"
    ),
    path(
        "get-eval-template-names",
        GetEvalTemplateNameView.as_view(),
        name="get-eval-template-names",
    ),
    path("get-eval-config", GetEvalConfigView.as_view(), name="get-eval-config"),
    path("knowledge-base/", CreateKnowledgeBaseView.as_view(), name="knowledge-base"),
    path(
        "knowledge-base/list/",
        ListKnowledgeBaseDetailsView.as_view(),  # noqa: F405
        name="list-knowledge-base",
    ),
    path(
        "knowledge-base/get/",
        GetKnowledgeBaseDetailsView.as_view(),  # noqa: F405
        name="get-knowledge-base",
    ),
    path(
        "knowledge-base/files/",
        ExistingKnowledgeBaseView.as_view(),  # noqa: F405
        name="knowledge-base-files",
    ),
    path("eval-playground/", EvalPlayGroundAPIView.as_view(), name="eval-playground"),
    path("eval-sdk-code/", EvalCodeSnippetAPIView.as_view(), name="eval-playground"),
    path(
        "eval-playground/feedback/",
        EvalPlayGroundFeedbackAPIView.as_view(),
        name="eval-playground-feedback",
    ),
    path(
        "update-eval-template/",
        UpdateEvalTemplateView.as_view(),
        name="update-eval-template",
    ),
    path(
        "delete-eval-template/",
        DeleteEvalTemplateView.as_view(),
        name="delete-eval-template",
    ),
    path(
        "duplicate-eval-template/",
        DuplicateEvalTemplateView.as_view(),
        name="duplicate-eval-template",
    ),
    path(
        "test-evaluation/",
        TestEvaluationTemplateAPIView.as_view(),
        name="test-evaluation-template",
    ),
    path("ai-filter/", AIFilterView.as_view(), name="ai-filter"),
    path("ai-eval-writer/", AIEvalWriterView.as_view(), name="ai-eval-writer"),
    path(
        "eval-summary-templates/",
        EvalSummaryTemplateListView.as_view(),
        name="eval-summary-templates",
    ),
    path(
        "eval-summary-templates/<uuid:template_id>/",
        EvalSummaryTemplateDetailView.as_view(),
        name="eval-summary-template-detail",
    ),
    # --- Evals Revamp endpoints (Phase 1-19) ---
    path(
        "eval-templates/list/",
        EvalTemplateListView.as_view(),
        name="eval-template-list",
    ),
    path(
        "eval-templates/list-charts/",
        EvalTemplateListChartsView.as_view(),
        name="eval-template-list-charts",
    ),
    path(
        "eval-templates/bulk-delete/",
        EvalTemplateBulkDeleteView.as_view(),
        name="eval-template-bulk-delete",
    ),
    path(
        "eval-templates/create-v2/",
        EvalTemplateCreateV2View.as_view(),
        name="eval-template-create-v2",
    ),
    path(
        "eval-templates/create-composite/",
        CompositeEvalCreateView.as_view(),
        name="eval-template-create-composite",
    ),
    path(
        "eval-templates/<uuid:template_id>/detail/",
        EvalTemplateDetailView.as_view(),
        name="eval-template-detail",
    ),
    path(
        "eval-templates/<uuid:template_id>/update/",
        EvalTemplateUpdateViewV2.as_view(),
        name="eval-template-update-v2",
    ),
    path(
        "eval-templates/<uuid:template_id>/versions/",
        EvalTemplateVersionListView.as_view(),
        name="eval-template-version-list",
    ),
    path(
        "eval-templates/<uuid:template_id>/versions/create/",
        EvalTemplateVersionCreateView.as_view(),
        name="eval-template-version-create",
    ),
    path(
        "eval-templates/<uuid:template_id>/versions/<uuid:version_id>/set-default/",
        SetDefaultVersionView.as_view(),
        name="eval-template-version-set-default",
    ),
    path(
        "eval-templates/<uuid:template_id>/versions/<uuid:version_id>/restore/",
        RestoreVersionView.as_view(),
        name="eval-template-version-restore",
    ),
    path(
        "eval-templates/<uuid:template_id>/usage/",
        EvalUsageStatsView.as_view(),
        name="eval-template-usage",
    ),
    path(
        "eval-templates/<uuid:template_id>/feedback-list/",
        EvalFeedbackListView.as_view(),
        name="eval-template-feedback-list",
    ),
    path(
        "eval-templates/<uuid:template_id>/composite/",
        CompositeEvalDetailView.as_view(),
        name="eval-template-composite-detail",
    ),
    path(
        "eval-templates/<uuid:template_id>/composite/execute/",
        CompositeEvalExecuteView.as_view(),
        name="eval-template-composite-execute",
    ),
    path(
        "eval-templates/composite/execute-adhoc/",
        CompositeEvalAdhocExecuteView.as_view(),
        name="eval-template-composite-execute-adhoc",
    ),
    path(
        "eval-templates/<uuid:template_id>/ground-truth/",
        GroundTruthListView.as_view(),
        name="eval-template-ground-truth-list",
    ),
    path(
        "eval-templates/<uuid:template_id>/ground-truth/upload/",
        GroundTruthUploadView.as_view(),
        name="eval-template-ground-truth-upload",
    ),
    path(
        "eval-templates/<uuid:template_id>/ground-truth-config/",
        GroundTruthConfigView.as_view(),
        name="eval-template-ground-truth-config",
    ),
    path(
        "ground-truth/<uuid:ground_truth_id>/mapping/",
        GroundTruthMappingView.as_view(),
        name="ground-truth-mapping",
    ),
    path(
        "ground-truth/<uuid:ground_truth_id>/role-mapping/",
        GroundTruthRoleMappingView.as_view(),
        name="ground-truth-role-mapping",
    ),
    path(
        "ground-truth/<uuid:ground_truth_id>/data/",
        GroundTruthDataView.as_view(),
        name="ground-truth-data",
    ),
    path(
        "ground-truth/<uuid:ground_truth_id>/status/",
        GroundTruthStatusView.as_view(),
        name="ground-truth-status",
    ),
    path(
        "ground-truth/<uuid:ground_truth_id>/search/",
        GroundTruthSearchView.as_view(),
        name="ground-truth-search",
    ),
    path(
        "ground-truth/<uuid:ground_truth_id>/embed/",
        GroundTruthTriggerEmbeddingView.as_view(),
        name="ground-truth-embed",
    ),
    path(
        "ground-truth/<uuid:ground_truth_id>/",
        GroundTruthDeleteView.as_view(),
        name="ground-truth-delete",
    ),
    # --- End Evals Revamp endpoints ---
    path(
        "columns/<uuid:column_id>/operation-config/",
        GetOperationConfigView.as_view(),
        name="get-operation-config",
    ),
    path(
        "columns/<uuid:column_id>/rerun-operation/",
        RerunOperationView.as_view(),
        name="rerun-operation",
    ),
    # Derived Variables endpoints
    path(
        "prompt-templates/<uuid:prompt_id>/derived-variables/",
        get_prompt_derived_variables,
        name="get-prompt-derived-variables",
    ),
    path(
        "prompt-templates/<uuid:prompt_id>/derived-variables/<str:column_name>/schema/",
        get_derived_variable_schema_view,
        name="get-derived-variable-schema",
    ),
    path(
        "prompt-templates/<uuid:prompt_id>/derived-variables/extract/",
        extract_derived_variables,
        name="extract-derived-variables",
    ),
    path(
        "prompt-templates/derived-variables/preview/",
        preview_derived_variables,
        name="preview-derived-variables",
    ),
    path(
        "datasets/<uuid:dataset_id>/derived-variables/",
        get_dataset_derived_variables_view,
        name="get-dataset-derived-variables",
    ),
    # V2 Experiment APIs
    path(
        "experiments/v2/",
        ExperimentsTableV2View.as_view(),
        name="experiments-v2-create",
    ),
    path(
        "experiments/v2/<uuid:experiment_id>/",
        ExperimentsTableV2View.as_view(),
        name="experiments-v2-detail",
    ),
    path(
        "experiments/v2/list/",
        ExperimentListV2APIView.as_view(),
        name="experiments-v2-list",
    ),
    path(
        "experiments/v2/delete/",
        ExperimentDeleteV2View.as_view(),
        name="experiments-v2-delete",
    ),
    path(
        "experiments/v2/re-run/",
        ExperimentRerunV2View.as_view(),
        name="experiments-v2-rerun",
    ),
    path(
        "experiments/v2/<uuid:experiment_id>/rerun-cells/",
        ExperimentRerunCellsV2View.as_view(),
        name="experiments-v2-rerun-cells",
    ),
    path(
        "experiments/v2/<uuid:experiment_id>/stop/",
        ExperimentStopV2View.as_view(),
        name="experiments-v2-stop",
    ),
    path(
        "experiments/v2/suggest-name/<uuid:dataset_id>/",
        ExperimentNameSuggestionView.as_view(),
        name="experiments-v2-suggest-name",
    ),
    path(
        "experiments/v2/validate-name/",
        ExperimentNameValidationView.as_view(),
        name="experiments-v2-validate-name",
    ),
    path(
        "experiments/v2/<uuid:experiment_id>/rows/",
        DatasetExperimentsView.as_view(),
        name="experiments-v2-rows",
    ),
    path(
        "experiments/v2/<uuid:experiment_id>/rows/<uuid:row_id>/",
        DatasetExperimentsView.as_view(),
        name="experiments-v2-row-detail",
    ),
    path(
        "experiments/v2/<uuid:experiment_id>/stats/",
        ExperimentStatsV2View.as_view(),
        name="experiments-v2-stats",
    ),
    path(
        "experiments/v2/<uuid:experiment_id>/evaluations/<uuid:evaluation_id>/stats/",
        ExperimentEvaluationStatsView.as_view(),
        name="experiments-v2-eval-stats",
    ),
    path(
        "experiments/v2/<uuid:experiment_id>/compare-experiments/",
        ExperimentDatasetComparisonV2View.as_view(),
        name="experiments-v2-compare",
    ),
    path(
        "experiments/v2/<uuid:experiment_id>/comparisons/",
        ExperimentComparisonDetailsView.as_view(),
        name="experiments-v2-comparisons",
    ),
    path(
        "experiments/v2/<uuid:experiment_id>/download/",
        DownloadExperimentsView.as_view(),
        name="experiments-v2-download",
    ),
    path(
        "experiments/v2/row-diff/",
        GetRowDiffV2View.as_view(),
        name="experiments-v2-row-diff",
    ),
    path(
        "experiments/v2/<uuid:experiment_id>/json-schema/",
        ExperimentJsonSchemaView.as_view(),
        name="experiments-v2-json-schema",
    ),
    path(
        "experiments/v2/<uuid:experiment_id>/derived-variables/",
        ExperimentDerivedVariablesView.as_view(),
        name="experiments-v2-derived-variables",
    ),
    # Experiment V2 Feedback
    path(
        "experiments/v2/<uuid:experiment_id>/feedback/get-template/",
        ExperimentFeedbackGetTemplateV2View.as_view(),
        name="experiments-v2-feedback-get-template",
    ),
    path(
        "experiments/v2/<uuid:experiment_id>/feedback/",
        ExperimentFeedbackCreateV2View.as_view(),
        name="experiments-v2-feedback-create",
    ),
    path(
        "experiments/v2/<uuid:experiment_id>/feedback/get-feedback-details/",
        ExperimentFeedbackDetailsV2View.as_view(),
        name="experiments-v2-feedback-details",
    ),
    path(
        "experiments/v2/<uuid:experiment_id>/feedback/submit-feedback/",
        ExperimentFeedbackSubmitV2View.as_view(),
        name="experiments-v2-feedback-submit",
    ),
]
