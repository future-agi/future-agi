import re
import uuid
from typing import Any, Literal

from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import models, transaction
from django.db.models import Max, Q
from pydantic import BaseModel, validator

from accounts.models.organization import Organization
from accounts.models.user import User
from model_hub.models.choices import (
    FeedbackSourceChoices,
    ModelChoices,
    OwnerChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Column, Dataset, KnowledgeBaseFile
from model_hub.models.eval_groups import (  # Commented out to avoid circular import
    EvalGroup,
)
from tfc.utils.base_model import BaseModel as ModelBaseModel


def validate_eval_name(value):
    cleaned_name = value.strip()

    if cleaned_name == "":
        raise DjangoValidationError("Name cannot be empty.")

    if not re.match(r"^[0-9a-z_-]+$", cleaned_name):
        raise DjangoValidationError(
            "Name can only contain lowercase alphabets, numbers, hyphens (-), or underscores (_)."
        )

    if (
        cleaned_name.startswith("-")
        or cleaned_name.startswith("_")
        or cleaned_name.endswith("-")
        or cleaned_name.endswith("_")
    ):
        raise DjangoValidationError(
            "Name cannot start or end with hyphens (-) or underscores (_)."
        )

    if "_-" in cleaned_name or "-_" in cleaned_name:
        raise DjangoValidationError(
            "Name cannot contain consecutive separators (_- or -_)."
        )

    return cleaned_name


class ConfigParam(BaseModel):
    type: str
    default: Any | None = None


class ConfigParamsDesc(BaseModel):
    text: str | None = None
    response: str | None = None
    rule_prompt: str | None = None
    choices: str | None = None
    input: str | None = None
    multi_choice: str | None = None
    output: str | None = None
    expected_response: str | None = None
    criteria: str | None = None
    context: str | None = None
    query: str | None = None
    actual_json: str | None = None
    expected_json: str | None = None
    code: str | None = None
    model: str | None = None
    prompt: str | None = None
    eval_prompt: str | None = None
    system_prompt: str | None = None
    grading_criteria: str | None = None


class EvalConfigModel(BaseModel):
    required_keys: list[str] | None = None
    optional_keys: list[str] | None = None
    output: Literal["Pass/Fail", "score", "reason", "choices", ""] | None = None
    eval_type_id: str | None = None
    config: dict[str, ConfigParam] | None = None
    config_params_desc: ConfigParamsDesc | None = None
    config_params_option: dict[str, list[str]] | None = None
    config_params_constraints: dict[str, str] | None = None

    class Config:
        extra = "forbid"  # Prevents additional fields from being included


class EvalTemplate(ModelBaseModel):
    """
    Template containing configurations and eval types for evaluation.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=2000)
    description = models.CharField(max_length=1000, blank=True, null=True, default="")
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="template_org",
        null=True,
        blank=True,
    )
    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.CASCADE,
        related_name="eval_templates",
        null=True,
        blank=True,
    )
    owner = models.CharField(
        max_length=50,
        choices=OwnerChoices.get_choices(),
        default=OwnerChoices.SYSTEM.value,
    )
    eval_tags = ArrayField(
        models.CharField(max_length=100),  # Specify the max length for each tag
        blank=True,  # Allows an empty list as a default
        default=list,  # Uses an empty list as the default value
    )
    config = models.JSONField(default=dict, blank=True)
    eval_id = models.IntegerField(default=0)
    criteria = models.CharField(max_length=100000, blank=True, default="", null=True)
    choices = models.JSONField(default=list, blank=True, null=True)
    multi_choice = models.BooleanField(default=False, null=True)
    model = models.CharField(max_length=255, null=True, blank=True)
    visible_ui = models.BooleanField(default=True)
    proxy_agi = models.BooleanField(default=True)
    evaluator_id = models.UUIDField(max_length=255, null=True, blank=True)

    # --- Template Type (Phase 7) ---
    template_type = models.CharField(
        max_length=20,
        default="single",
        help_text="single or composite",
    )

    # --- Eval Type ---
    eval_type = models.CharField(
        max_length=10,
        choices=[("agent", "Agent"), ("llm", "LLM"), ("code", "Code")],
        default="llm",
        help_text="Evaluator type: agent (Falcon AI powered), llm (LLM-as-a-judge), code (custom code)",
    )

    # --- Permissions ---
    allow_edit = models.BooleanField(
        default=True,
        help_text="Whether users can edit this eval template. False for system evals.",
    )
    allow_copy = models.BooleanField(
        default=True,
        help_text="Whether users can duplicate/copy this eval template.",
    )

    # --- Scoring Revamp Fields (Phase 2) ---
    output_type_normalized = models.CharField(
        max_length=20,
        null=True,
        blank=True,
        help_text="Normalized output type: pass_fail, percentage, deterministic",
    )
    pass_threshold = models.FloatField(
        null=True,
        blank=True,
        default=0.5,
        help_text="Score >= threshold means pass. Range 0.0-1.0",
    )
    choice_scores = models.JSONField(
        null=True,
        blank=True,
        help_text='Maps choice labels to 0-1 scores: {"Yes": 1.0, "No": 0.0}',
    )

    # --- Error Localization (Phase 19) ---
    error_localizer_enabled = models.BooleanField(
        default=False,
        help_text="Enable error localization to pinpoint which input parts caused eval failures.",
    )

    # --- Composite Aggregation (Phase 7G) ---
    aggregation_enabled = models.BooleanField(
        default=True,
        help_text="Enable score aggregation for composite evals. When False, children run independently.",
    )
    aggregation_function = models.CharField(
        max_length=20,
        default="weighted_avg",
        choices=[
            ("weighted_avg", "Weighted Average"),
            ("avg", "Average"),
            ("min", "Minimum"),
            ("max", "Maximum"),
            ("pass_rate", "Pass Rate"),
        ],
        help_text="Aggregation function for composite eval scores.",
    )
    composite_child_axis = models.CharField(
        max_length=20,
        blank=True,
        default="",
        choices=[
            ("pass_fail", "Pass / Fail"),
            ("percentage", "Score"),
            ("choices", "Choices"),
            ("code", "Code"),
        ],
        help_text="For composite evals: which tab/axis children must belong to. Enforces homogeneity.",
    )

    def clean(self):
        super().clean()
        if self.owner == OwnerChoices.USER.value:
            validate_eval_name(self.name)
            if (
                EvalTemplate.objects.filter(
                    name=self.name, organization=self.organization, deleted=False
                )
                .exclude(id=self.id)
                .exists()
            ):
                raise DjangoValidationError(
                    f"Eval template with name {self.name} already exists for organization."
                )
            if (
                EvalTemplate.no_workspace_objects.filter(
                    name=self.name, owner=OwnerChoices.SYSTEM.value, deleted=False
                )
                .exclude(id=self.id)
                .exists()
            ):
                raise DjangoValidationError(
                    f"Eval template with name {self.name} already exists for system."
                )
        # try:
        #     EvalConfigModel(**self.config)
        # except ValidationError as e:
        #     raise DjangoValidationError(f"Invalid config format: {str(e)}")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class UserEvalConfigModel(BaseModel):
    mapping: dict[str, str] | None = (
        None  # Allows any key names with UUID string values or None
    )
    config: dict[str, str] | None = None  # Allows any config parameters or None

    class Config:
        extra = "forbid"  # Prevents additional fields from being included

    @validator("mapping")
    def validate_mapping(cls, v):
        if v is not None and not isinstance(v, dict):
            raise ValueError("mapping must be a dictionary")
        return v

    @validator("config")
    def validate_config(cls, v):
        if v is not None and not isinstance(v, dict):
            raise ValueError("config must be a dictionary")
        return v


class UserEvalMetric(ModelBaseModel):
    @classmethod
    def get_metrics_using_column(
        cls, organization_id: str, column_id: str
    ) -> list["UserEvalMetric"]:
        """
        Returns a list of UserEvalMetric instances that use the specified column_id
        in their config mapping, regardless of the mapping key.

        Args:
            organization_id: The ID of the organization to filter metrics
            column_id: The UUID of the column to search for in config mappings

        Returns:
            List of UserEvalMetric instances that use the specified column
        """
        column = Column.objects.filter(
            id=column_id, dataset__organization_id=organization_id
        ).first()
        if column:
            metrics = cls.objects.filter(
                organization_id=organization_id,
                deleted=False,
                show_in_sidebar=True,
                dataset=column.dataset,
            )
        else:
            metrics = cls.objects.filter(
                organization_id=organization_id, deleted=False, show_in_sidebar=True
            )

        def check_value_in_dict(d: dict, search_value: str) -> bool:
            """Helper function to check if value exists in dictionary values, including template strings."""
            for value in d.values():
                if isinstance(value, str):
                    if search_value in value or f"{{{{{search_value}}}}}" in value:
                        return True
                elif isinstance(value, dict):
                    if check_value_in_dict(value, search_value):
                        return True
            return False

        return [
            metric
            for metric in metrics
            if (
                metric.config.get("mapping")
                and check_value_in_dict(metric.config["mapping"], str(column_id))
            )
            or (
                metric.config.get("config")
                and check_value_in_dict(metric.config["config"], str(column_id))
            )
        ]

    """
    Stores individual user-specific variable values required to run the metric.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=2000)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="user_template_org",
        null=True,
        blank=True,
    )
    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.CASCADE,
        related_name="user_eval_metrics",
        null=True,
        blank=True,
    )
    template = models.ForeignKey(
        EvalTemplate,
        on_delete=models.CASCADE,
        related_name="user_metrics",
        help_text="Link to the metric template configuration.",
    )
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE)
    config = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=100,
        default=StatusType.INACTIVE.value,
        choices=StatusType.get_choices(),
    )
    show_in_sidebar = models.BooleanField(default=True)
    source_id = models.CharField(max_length=255, null=True, blank=True, default="")
    column_deleted = models.BooleanField(default=False)
    error_localizer = models.BooleanField(default=False)
    kb_id = models.UUIDField(null=True, blank=True)
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="evaluation_user",
        null=True,
        blank=True,
        default=None,
    )
    model = models.CharField(
        max_length=255, null=True, blank=True, default=ModelChoices.TURING_LARGE.value
    )

    eval_group = models.ForeignKey(
        EvalGroup, on_delete=models.CASCADE, null=True, blank=True
    )

    # --- Composite Eval (Phase 7 wiring) ---
    composite_weight_overrides = models.JSONField(
        null=True,
        blank=True,
        default=None,
        help_text=(
            "Per-binding weight overrides for composite child evals. "
            'Maps {"<child_template_id>": <weight float>}. '
            "When null, runners fall back to CompositeEvalChild.weight on the template. "
            "Ignored for single evals."
        ),
    )

    def clean(self):
        super().clean()
        # try:
        #     UserEvalConfigModel(**self.config)
        # except ValidationError as e:
        #     raise DjangoValidationError(f"Invalid config format: {str(e)}")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class Feedback(ModelBaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source = models.CharField(
        max_length=50, choices=FeedbackSourceChoices.get_choices()
    )
    source_id = models.CharField(max_length=255)
    user_eval_metric = models.ForeignKey(
        UserEvalMetric, on_delete=models.CASCADE, null=True, blank=True
    )
    eval_template = models.ForeignKey(
        EvalTemplate, on_delete=models.CASCADE, null=True, blank=True
    )
    value = models.TextField()  # Store value as text, convert as needed
    explanation = models.TextField(blank=True, null=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    row_id = models.CharField(max_length=255, null=True, blank=True)
    action_type = models.CharField(max_length=255, null=True, blank=True)
    feedback_improvement = models.TextField(blank=True, null=True)
    custom_eval_config_id = models.UUIDField(null=True, blank=True)

    organization = models.ForeignKey(
        "accounts.Organization",
        on_delete=models.CASCADE,
        related_name="feedbacks",
        null=True,  # Nullable for migration compatibility
        blank=True,
    )
    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.CASCADE,
        related_name="feedbacks",
        null=True,  # Nullable (workspace is optional)
        blank=True,
    )

    class Meta:
        indexes = [
            models.Index(fields=["organization", "eval_template"]),
            models.Index(fields=["workspace", "eval_template"]),
        ]

    def clean(self):
        super().clean()

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)


class EvalSettings(ModelBaseModel):
    eval_id = models.UUIDField(null=True, blank=True)
    column_config = ArrayField(
        models.JSONField(max_length=100), blank=True, default=list
    )
    source = models.CharField(max_length=50, blank=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)

    class Meta:
        db_table = "eval_settings"
        unique_together = ("eval_id", "source", "user")


class Evaluator(ModelBaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    eval_template = models.ForeignKey(
        EvalTemplate, on_delete=models.CASCADE, related_name="evaluators"
    )
    name = models.CharField(max_length=2000, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    config = models.JSONField(default=dict, blank=True, null=True)
    source = models.CharField(max_length=100, null=True, blank=True, default="")
    error_localizer = models.BooleanField(default=False)
    kb = models.ForeignKey(
        KnowledgeBaseFile, on_delete=models.CASCADE, null=True, blank=True
    )
    eval_tags = ArrayField(
        models.CharField(max_length=100), blank=True, null=True, default=list
    )
    criteria = models.CharField(max_length=2000, blank=True, default="", null=True)
    choices = models.JSONField(default=list, blank=True, null=True)
    multi_choice = models.BooleanField(default=False, null=True)
    model = models.CharField(
        max_length=255, null=True, blank=True, default=ModelChoices.TURING_LARGE.value
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="evaluator_user",
        null=True,
        blank=True,
        default=None,
    )


# =============================================================================
# Eval Template Versioning (Phase 5)
# =============================================================================


class EvalTemplateVersionManager(models.Manager):
    """Custom manager for EvalTemplateVersion with helper methods."""

    def create_version(
        self,
        eval_template,
        prompt_messages=None,
        config_snapshot=None,
        criteria=None,
        model=None,
        user=None,
        organization=None,
        workspace=None,
    ):
        """
        Create a new version for an eval template.
        Auto-increments version_number based on existing versions.
        Uses select_for_update to prevent race conditions.
        """
        from django.db import transaction

        with transaction.atomic():
            # Lock the template row to prevent concurrent version creation
            last_version = (
                self.filter(eval_template=eval_template)
                .select_for_update()
                .order_by("-version_number")
                .values_list("version_number", flat=True)
                .first()
            )
            next_version = (last_version or 0) + 1
            is_first = next_version == 1

            version = self.create(
                eval_template=eval_template,
                version_number=next_version,
                prompt_messages=prompt_messages or [],
                config_snapshot=config_snapshot or {},
                criteria=criteria or "",
                model=model or "",
                is_default=is_first,
                created_by=user,
                organization=organization,
                workspace=workspace,
            )

            return version

    def get_default(self, eval_template):
        """Get the default (active) version for a template."""
        return self.filter(eval_template=eval_template, is_default=True).first()


class EvalTemplateVersion(ModelBaseModel):
    """
    Immutable version snapshot of an EvalTemplate's configuration.

    Each version captures the prompt/instructions, config, model, and criteria
    at a point in time. Versions are numbered sequentially (1, 2, 3...).
    One version per template is marked as default (the active version).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    eval_template = models.ForeignKey(
        EvalTemplate,
        on_delete=models.CASCADE,
        related_name="versions",
    )
    version_number = models.IntegerField(
        help_text="Sequential version number (1, 2, 3...)",
    )
    prompt_messages = models.JSONField(
        default=list,
        blank=True,
        help_text='Prompt messages: [{"role": "system", "content": "..."}]',
    )
    config_snapshot = models.JSONField(
        default=dict,
        blank=True,
        help_text="Snapshot of the template config at this version",
    )
    criteria = models.TextField(
        blank=True,
        default="",
        help_text="Instructions/criteria text at this version",
    )
    model = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Model used at this version",
    )
    is_default = models.BooleanField(
        default=False,
        help_text="Whether this is the active/default version",
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="eval_versions",
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="eval_versions",
    )
    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="eval_versions",
    )

    objects = EvalTemplateVersionManager()

    # Keep all_objects for accessing all versions including deleted
    all_objects = models.Manager()

    class Meta:
        db_table = "model_hub_eval_template_version"
        ordering = ["-version_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["eval_template", "version_number"],
                condition=Q(deleted=False),
                name="unique_version_per_template",
            ),
        ]
        indexes = [
            models.Index(fields=["eval_template", "is_default"]),
            models.Index(fields=["eval_template", "version_number"]),
        ]

    def __str__(self):
        return f"V{self.version_number} of {self.eval_template_id}"


# =============================================================================
# Composite Eval (Phase 7)
# =============================================================================


class CompositeEvalChild(ModelBaseModel):
    """
    Through-model linking a parent composite EvalTemplate to its child eval templates.
    Maintains ordering and optional version pinning.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    parent = models.ForeignKey(
        EvalTemplate,
        on_delete=models.CASCADE,
        related_name="composite_children",
        help_text="The composite (parent) eval template",
    )
    child = models.ForeignKey(
        EvalTemplate,
        on_delete=models.CASCADE,
        related_name="composite_parents",
        help_text="The child eval template included in this composite",
    )
    order = models.IntegerField(
        default=0,
        help_text="Display order within the composite (0-indexed)",
    )
    pinned_version = models.ForeignKey(
        EvalTemplateVersion,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Optionally pin to a specific version of the child eval",
    )
    weight = models.FloatField(
        default=1.0,
        help_text="Weight for weighted aggregation (0.0-10.0)",
    )

    class Meta:
        db_table = "model_hub_composite_eval_child"
        ordering = ["order"]
        constraints = [
            models.UniqueConstraint(
                fields=["parent", "child"],
                condition=Q(deleted=False),
                name="unique_child_per_composite",
            ),
        ]
        indexes = [
            models.Index(fields=["parent", "order"]),
        ]

    def __str__(self):
        return f"Child #{self.order} of {self.parent_id}"


# =============================================================================
# Ground Truth (Phase 9)
# =============================================================================


class EvalGroundTruth(ModelBaseModel):
    """
    Stores ground truth data for an eval template.
    Data is stored as JSON rows with column headers.
    Embeddings are generated asynchronously for similarity-based retrieval.
    """

    EMBEDDING_STATUS_CHOICES = [
        ("pending", "Pending"),
        ("processing", "Processing"),
        ("completed", "Completed"),
        ("failed", "Failed"),
    ]

    STORAGE_TYPE_CHOICES = [
        ("db", "Database"),
        ("s3", "S3"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    eval_template = models.ForeignKey(
        EvalTemplate,
        on_delete=models.CASCADE,
        related_name="ground_truths",
    )
    name = models.CharField(max_length=255, help_text="Ground truth dataset name")
    description = models.TextField(
        blank=True,
        default="",
        help_text="Optional description of the dataset",
    )
    file_name = models.CharField(
        max_length=500,
        blank=True,
        default="",
        help_text="Original uploaded file name",
    )
    columns = models.JSONField(
        default=list,
        blank=True,
        help_text='Column headers: ["input", "expected_output", ...]',
    )
    data = models.JSONField(
        default=list,
        blank=True,
        help_text='Row data: [{"input": "...", "expected_output": "..."}, ...]',
    )
    row_count = models.IntegerField(default=0)
    variable_mapping = models.JSONField(
        null=True,
        blank=True,
        help_text='Maps eval variables to ground truth columns: {"ground_truth": "expected_output"}',
    )
    role_mapping = models.JSONField(
        null=True,
        blank=True,
        help_text=(
            "Maps semantic roles to GT columns for few-shot formatting: "
            '{"input": "question_col", "expected_output": "answer_col", '
            '"score": "score_col", "reasoning": "notes_col"}'
        ),
    )

    # Embedding fields
    embedding_status = models.CharField(
        max_length=20,
        choices=EMBEDDING_STATUS_CHOICES,
        default="pending",
        help_text="Status of embedding generation for this dataset",
    )
    embedding_model = models.CharField(
        max_length=100,
        default="text-embedding-3-small",
        help_text="Model used for generating embeddings",
    )
    embedded_row_count = models.IntegerField(
        default=0,
        help_text="Number of rows that have been embedded so far",
    )

    # Storage fields
    storage_type = models.CharField(
        max_length=10,
        choices=STORAGE_TYPE_CHOICES,
        default="db",
        help_text="Where the raw data is stored (db for small, s3 for large)",
    )
    s3_key = models.CharField(
        max_length=1000,
        blank=True,
        default="",
        help_text="S3 object key if storage_type='s3'",
    )

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="eval_ground_truths",
    )
    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="eval_ground_truths",
    )

    class Meta:
        db_table = "model_hub_eval_ground_truth"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["eval_template", "created_at"]),
        ]

    def __str__(self):
        return f"GroundTruth '{self.name}' for {self.eval_template_id}"


class EvalGroundTruthEmbedding(models.Model):
    """
    Stores per-row embeddings for ground truth similarity search.
    One embedding per row in the parent ground truth dataset.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ground_truth = models.ForeignKey(
        EvalGroundTruth,
        on_delete=models.CASCADE,
        related_name="embeddings",
    )
    row_index = models.IntegerField(
        help_text="Index of this row in the parent dataset's data array",
    )
    text_content = models.TextField(
        help_text="The text that was embedded (concatenated column values)",
    )
    embedding = models.JSONField(
        help_text="Embedding vector as list of floats",
    )
    row_data = models.JSONField(
        help_text="The full row dict from the parent dataset",
    )

    class Meta:
        db_table = "model_hub_eval_ground_truth_embedding"
        ordering = ["row_index"]
        indexes = [
            models.Index(fields=["ground_truth", "row_index"]),
        ]
        unique_together = [("ground_truth", "row_index")]

    def __str__(self):
        return f"Embedding row {self.row_index} of GT {self.ground_truth_id}"
