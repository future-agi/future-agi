import uuid

from django.contrib.postgres.fields import ArrayField
from django.db import models

from model_hub.models.kb import KnowledgeBase
from tfc.utils.base_model import BaseModel


class OptimizeDataset(BaseModel):
    class OptimizeType(models.TextChoices):
        TEMPLATE = "PromptTemplate", "Prompt Template"
        RIGHT_ANSWER = "RightAnswer", "Right Answer"
        RAG_TEMPLATE = "RagPromptTemplate", "Rag Prompt Template"

        @classmethod
        def get_optimize_types(cls, num):
            if num == 1:
                return cls.TEMPLATE
            if num == 2:
                return cls.RIGHT_ANSWER
            if num == 3:
                return cls.RAG_TEMPLATE

        @classmethod
        def get_optim_num_types(cls, typ):
            if typ == cls.TEMPLATE:
                return 1
            if typ == cls.RIGHT_ANSWER:
                return 2
            if typ == cls.RAG_TEMPLATE:
                return 3

    class EnvTypes(models.TextChoices):
        PRODUCTION = "Production", "Production"
        TRAINING = "Training", "Training"
        VALIDATION = "Validation", "Validation"
        CORPUS = "Corpus", "Corpus"

        @classmethod
        def get_env_types(cls, num):
            if num == 1:
                return cls.TRAINING
            if num == 2:
                return cls.VALIDATION
            if num == 3:
                return cls.PRODUCTION
            if num == 4:
                return cls.CORPUS

        @classmethod
        def get_env_num_types(cls, typ):
            if typ == cls.TRAINING:
                return 1
            if typ == cls.VALIDATION:
                return 2
            if typ == cls.PRODUCTION:
                return 3
            if typ == cls.CORPUS:
                return 4

    class StatusType(models.TextChoices):
        NOT_STARTED = "not_started", "Not Started"
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    class OptimizerAlgorithm(models.TextChoices):
        RANDOM_SEARCH = "random_search", "Random Search"
        BAYESIAN = "bayesian", "Bayesian"
        METAPROMPT = "metaprompt", "Metaprompt"
        PROTEGI = "protegi", "Protegi"
        PROMPTWIZARD = "promptwizard", "PromptWizard"
        GEPA = "gepa", "GEPA"

    class UsedInChoices(models.TextChoices):
        MODEL = "model", "Model"
        DEVELOP = "develop", "Develop"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Note: created_at, updated_at, deleted, deleted_at are inherited from BaseModel
    name = models.CharField(max_length=255)
    optimize_type = models.CharField(max_length=255, choices=OptimizeType.choices)
    metrics = models.ManyToManyField(
        "Metric", related_name="optimize_dataset", null=True, blank=True
    )
    start_date = models.DateTimeField(null=True)
    end_date = models.DateTimeField(null=True)
    knowledge_base_filters = models.JSONField(default=list, blank=True, null=True)
    knowledge_base_metrics = models.JSONField(default=list, blank=True, null=True)
    variables = models.JSONField(default=dict, blank=True, null=True)
    knowledge_base = models.ForeignKey(
        KnowledgeBase,
        on_delete=models.CASCADE,
        related_name="optimize_knowledge_base",
        null=True,
        blank=True,
    )
    # model = models.CharField(max_length=255)
    model = models.ForeignKey(
        "AIModel", on_delete=models.CASCADE, null=True, blank=True
    )
    optimized_k_prompts = ArrayField(models.TextField(), null=True)
    environment = models.CharField(max_length=100, choices=EnvTypes.choices)
    prompt = models.CharField(max_length=2000, null=True, blank=True)
    eval_instructions = models.JSONField(
        default=dict,  # Default value is an empty dictionary
        blank=True,
        null=True,
    )
    criteria_breakdown = ArrayField(
        models.CharField(),
        default=list,
        blank=True,
    )
    version = models.CharField(max_length=255)
    status = models.CharField(
        max_length=100, default=StatusType.RUNNING, choices=StatusType.choices
    )
    used_in = models.CharField(
        max_length=10,
        choices=UsedInChoices.choices,
        default=UsedInChoices.MODEL,
        blank=True,
        null=True,
    )
    develop = models.ForeignKey(
        "DevelopAI",  # Ensure this matches the related model's class name
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="optimize_datasets",
    )
    # New fields for optimizer workflow (matching simulation flow)
    optimizer_algorithm = models.CharField(
        max_length=50,
        choices=OptimizerAlgorithm.choices,
        null=True,
        blank=True,
    )
    optimizer_config = models.JSONField(
        default=dict,
        blank=True,
        null=True,
        help_text="Optimizer-specific configuration (num_trials, etc.)",
    )
    optimizer_model = models.ForeignKey(
        "AIModel",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="optimizer_runs",
        help_text="Model used for optimization (separate from eval model)",
    )
    column = models.ForeignKey(
        "Column",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="optimization_runs",
        help_text="Column being optimized",
    )
    error_message = models.TextField(null=True, blank=True)
    best_score = models.FloatField(null=True, blank=True)
    baseline_score = models.FloatField(null=True, blank=True)
    user_eval_template_ids = models.ManyToManyField(
        "UserEvalMetric",
        related_name="dataset_optimization_runs",
        blank=True,
        help_text="Evaluation templates to optimize for",
    )

    def __str__(self):
        return str(self.name)

    def mark_as_pending(self):
        """Mark the optimization run as pending"""
        self.status = self.StatusType.PENDING
        self.save(update_fields=["status"])

    def mark_as_running(self):
        """Mark the optimization run as running"""
        self.status = self.StatusType.RUNNING
        self.save(update_fields=["status"])

    def mark_as_completed(self):
        """Mark the optimization run as completed"""
        self.status = self.StatusType.COMPLETED
        self.save(update_fields=["status"])

    def mark_as_failed(self, error_message=None):
        """Mark the optimization run as failed"""
        self.status = self.StatusType.FAILED
        if error_message:
            self.error_message = error_message
            self.save(update_fields=["status", "error_message"])
        else:
            self.save(update_fields=["status"])

    def mark_as_cancelled(self):
        """Mark the optimization run as cancelled"""
        self.status = self.StatusType.CANCELLED
        self.error_message = "Cancelled by user"
        self.save(update_fields=["status", "error_message"])
