"""
No-op fallbacks for symbols that live in the proprietary ``ee.usage`` package.

On a fresh OSS install, ``from ee.usage.models.usage import APICallTypeChoices``
raises ``ImportError``; many call sites previously fell back to ``None`` and
then crashed on the first attribute access (``APICallTypeChoices.DATASET_ADD``).
Use the stubs from this module as the fallback instead.

Keep the value strings stable so logs and any downstream telemetry remain
parseable when EE is later installed alongside an existing OSS database.
"""

from enum import Enum


class APICallTypeChoices(str, Enum):
    AUTO_ANNOTATION = "AUTO_ANNOTATION"
    DATASET_ADD = "DATASET_ADD"
    DATASET_EVALUATION = "DATASET_EVALUATION"
    DATASET_OPTIMIZATION = "DATASET_OPTIMIZATION"
    DATASET_RUN_PROMPT = "DATASET_RUN_PROMPT"
    ERROR_LOCALIZER = "ERROR_LOCALIZER"
    KNOWLEDGE_BASE = "KNOWLEDGE_BASE"
    OBSERVE_ADD = "OBSERVE_ADD"
    PROMPT_BENCH = "PROMPT_BENCH"
    PROTECT_EVALUATOR = "PROTECT_EVALUATOR"
    PROTECT_FLASH_EVALUATOR = "PROTECT_FLASH_EVALUATOR"
    PROTOTYPE_ADD = "PROTOTYPE_ADD"
    ROW_ADD = "ROW_ADD"
    SYNTHETIC_DATA_GENERATION = "SYNTHETIC_DATA_GENERATION"
    TRACE_ERROR_ANALYSIS = "TRACE_ERROR_ANALYSIS"
    TURING_FLASH_EVALUATOR = "TURING_FLASH_EVALUATOR"
    TURING_LARGE_EVALUATOR = "TURING_LARGE_EVALUATOR"
    TURING_SMALL_EVALUATOR = "TURING_SMALL_EVALUATOR"
    USER_ADD = "USER_ADD"


class APICallStatusChoices(str, Enum):
    ERROR = "ERROR"
    PROCESSING = "PROCESSING"
    RESOURCE_LIMIT = "RESOURCE_LIMIT"
    SUCCESS = "SUCCESS"


ROW_LIMIT_REACHED_MESSAGE = ""


def log_and_deduct_cost_for_resource_request(*args, **kwargs):
    """OSS no-op: usage tracking is an EE feature."""
    return None


class _StubManager:
    """Mimic ``Model.objects`` / ``Model.no_workspace_objects`` enough that
    OSS code paths which only check existence or call ``.filter(...).delete()``
    keep working without an EE-only DB table."""

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return self

    def first(self):
        return None

    def get(self, *args, **kwargs):
        raise APICallLog.DoesNotExist

    def delete(self):
        return (0, {})

    def count(self):
        return 0

    def exists(self):
        return False

    def __iter__(self):
        return iter(())


class _APICallLogDoesNotExist(Exception):
    pass


class APICallLog:
    """OSS stub for the EE-only usage-tracking model. Behaves as an empty queryset
    so existence checks return False and writes are no-ops."""

    DoesNotExist = _APICallLogDoesNotExist
    objects = _StubManager()
    no_workspace_objects = _StubManager()

    class config:
        @staticmethod
        def get(*args, **kwargs):
            return None


def log_and_deduct_cost_for_api_request(*args, **kwargs):
    return None


def refund_cost_for_api_call(*args, **kwargs):
    return None


def log_and_deduct_cost_for_dataset_creation(*args, **kwargs):
    return None


def count_text_tokens(*args, **kwargs):
    return 0


def count_tiktoken_tokens(*args, **kwargs):
    return 0


def deduct_cost_for_request(*args, **kwargs):
    return None


# OrganizationSubscription / SubscriptionTierChoices stubs (EE-only billing model)


class _StubChoices(str, Enum):
    FREE = "FREE"
    PRO = "PRO"
    ENTERPRISE = "ENTERPRISE"


SubscriptionTierChoices = _StubChoices


class OrganizationSubscription:
    DoesNotExist = _APICallLogDoesNotExist
    objects = _StubManager()

    @classmethod
    def is_active_for(cls, *args, **kwargs):
        return False
