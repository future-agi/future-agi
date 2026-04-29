"""
# Note: Moving the extra field down
# model_performance_positive_class = models.CharField(
#     max_length=100, default=None, blank=True, null=True
# )
# model_performance_at_k = models.CharField(
#     max_length=100, default=None, blank=True, null=True
# )
# version = models.CharField(max_length=255)
# environment = models.CharField(max_length=100, choices=EnvTypes.choices)
# baseline_model_environment = models.CharField(
# max_length=100, choices=EnvTypes.choices
# )
# baseline_model_version = models.CharField(max_length=255)
# baseline_model_batch = models.CharField(max_length=100)
"""

import uuid

from django.db import models

from accounts.models import Organization
from tfc.utils.base_model import BaseModel


class ActiveManager(models.Manager):
    def get_queryset(self):
        # Override the get_queryset method to filter out deleted objects
        return super().get_queryset().filter(deleted=False)


class AIModel(models.Model):
    class ModelTypes(models.TextChoices):
        NUMERIC = "Numeric", "Numeric"
        SCORE_CATEGORICAL = "ScoreCategorical", "Score Categorical"
        RANKING = "Ranking", "Ranking"
        BINARY_CF = "BinaryClassification", "Binary Classification"
        REGRESSION = "Regression", "Regression"
        OBJECT_DETECTION = "ObjectDetection", "Object Detection"
        SEGMENTATION = "Segmentation", "Segmentation"
        GENERATIVE_LLM = "GenerativeLLM", "Generative LLM"
        GENERATIVE_IMAGE = "GenerativeImage", "Generative Image"
        GENERATIVE_VIDEO = "GenerativeVideo", "Generative Video"
        TTS = "TTS", "TTS"
        STT = "STT", "STT"
        MULTI_MODAL = "MultiModal", "Multi Modal"

        @classmethod
        def get_model_types(cls, num):
            if num == 1:
                return cls.NUMERIC
            if num == 2:
                return cls.SCORE_CATEGORICAL
            if num == 3:
                return cls.RANKING
            if num == 4:
                return cls.BINARY_CF
            if num == 5:
                return cls.REGRESSION
            if num == 6:
                return cls.OBJECT_DETECTION
            if num == 7:
                return cls.SEGMENTATION
            if num == 8:
                return cls.GENERATIVE_LLM
            if num == 9:
                return cls.GENERATIVE_IMAGE
            if num == 10:
                return cls.GENERATIVE_VIDEO
            if num == 11:
                return cls.TTS
            if num == 12:
                return cls.STT
            if num == 13:
                return cls.MULTI_MODAL

        @classmethod
        def get_model_num(cls, model_type):
            for num, choice in enumerate(cls.choices, start=1):
                if choice[0] == model_type:
                    return num
            return None  # Return None or raise an error if not found

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

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    user_model_id = models.CharField(max_length=255)
    deleted = models.BooleanField(default=False)

    model_type = models.CharField(max_length=100, choices=ModelTypes.choices)

    default_metric = models.ForeignKey(
        "Metric",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="defaulted_by",
    )

    baseline_model_environment = models.CharField(
        max_length=100,
        choices=EnvTypes.choices,
        default=None,
        blank=True,
        null=True,
    )
    baseline_model_version = models.CharField(
        max_length=255, default=None, blank=True, null=True
    )
    # baseline_model_batch = models.CharField(max_length=100)

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="ai_models"
    )
    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.CASCADE,
        related_name="ai_models",
        null=True,
        blank=True,
    )

    # Use the custom manager to filter deleted=False by default
    objects = ActiveManager()

    # Optional: Add a default manager for accessing all objects, including deleted ones
    all_objects = models.Manager()

    class Meta:
        unique_together = ["organization", "user_model_id", "deleted"]

    def __str__(self):
        return str(self.id)


class CriteriaCache(BaseModel):
    text_prompt = models.TextField(unique=True)
    criteria_breakdown = models.JSONField()

    def __str__(self):
        return self.text_prompt
