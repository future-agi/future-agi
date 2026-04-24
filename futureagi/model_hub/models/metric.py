import uuid

from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.utils import timezone


class Metric(models.Model):
    class MetricTypes(models.TextChoices):
        WHOLE_USER_OUTPUT = "WholeUserOutput", "Whole User Output"
        STEPWISE_MODEL_INFERENCE = (
            "StepwiseModelInference",
            "Stepwise Model Inference",
        )

        @classmethod
        def get_metric_type(cls, num):
            if num == 1:
                return cls.WHOLE_USER_OUTPUT
            if num == 2:
                return cls.STEPWISE_MODEL_INFERENCE

    class EvalMetricTypes(models.TextChoices):
        EVAL_CONTEXT = "EvalRagContext", "Eval Rag Context"
        EVAL_RAG_OUTPUT = "EvalRagOutput", "Eval Rag Output"
        EVAL_PROMPT_TEMPLATE = "EvalPromptTemplate", "Eval Prompt Template"
        EVAL_OUTPUT = "EvalOutput", "Eval Output"
        EVAL_CONTEXT_RANKING = "EvalRagContextRanking", "Eval Rag Context Ranking"

        @classmethod
        def get_eval_metric_type(cls, num):
            if num == 1:
                return cls.EVAL_CONTEXT
            if num == 2:
                return cls.EVAL_RAG_OUTPUT
            if num == 3:
                return cls.EVAL_PROMPT_TEMPLATE
            if num == 4:
                return cls.EVAL_OUTPUT
            if num == 5:
                return cls.EVAL_CONTEXT_RANKING

    class UsedIn(models.TextChoices):
        MODEL = "model", "Model"
        DEVELOP = "develop", "Develop"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    text_prompt = models.TextField()
    criteria_breakdown = ArrayField(
        models.CharField(),
        default=list,
        blank=True,
    )
    model = models.ForeignKey(
        "AIModel", on_delete=models.CASCADE, related_name="metrics"
    )
    develop = models.ForeignKey(
        "DevelopAI",
        on_delete=models.CASCADE,
        related_name="metrics",
        null=True,
        blank=True,
    )
    metric_type = models.CharField(max_length=100, choices=MetricTypes.choices)
    used_in = models.CharField(
        max_length=10, choices=UsedIn.choices, default=UsedIn.MODEL
    )
    evaluation_type = models.CharField(
        max_length=100,
        choices=EvalMetricTypes.choices,
        default=EvalMetricTypes.EVAL_OUTPUT,
    )
    datasets = models.JSONField(null=True)
    eval_rag_context = models.BooleanField(default=False)
    eval_rag_output = models.BooleanField(default=False)
    eval_prompt_template = models.BooleanField(default=False)
    tags = ArrayField(models.CharField(), blank=True, default=list)

    def __str__(self):
        return self.name


def add_unique_tags(instance, new_tags):
    # Ensure new_tags is a list of strings
    if not isinstance(new_tags, list):
        new_tags = [new_tags]

    # Convert to set to remove duplicates within new_tags
    new_tags_set = set(new_tags)
    # Remove tags that already exist in the instance
    unique_new_tags = list(new_tags_set - set(instance.tags))

    if unique_new_tags:
        instance.tags += unique_new_tags
        instance.save()
        return True
    return False
