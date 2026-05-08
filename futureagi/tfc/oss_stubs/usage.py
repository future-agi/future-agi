"""
OSS stubs for EE billing/usage symbols.

When ee/ is absent (self-hosted OSS), these replace the real EE implementations
so that call sites don't crash on None dereferences. All operations are no-ops:
no metering, no quota enforcement, no billing events.
"""
from enum import Enum


class APICallTypeChoices(str, Enum):
    AUTO_ANNOTATION = "auto_annotation"
    DATASET_ADD = "dataset_add"
    DATASET_EVALUATION = "dataset_evaluation"
    DATASET_OPTIMIZATION = "dataset_optimization"
    DATASET_RUN_PROMPT = "dataset_run_prompt"
    ERROR_LOCALIZER = "error_localizer"
    KNOWLEDGE_BASE = "knowledge_base"
    OBSERVE_ADD = "observe_add"
    PROMPT_BENCH = "prompt_bench"
    PROTECT_EVALUATOR = "protect_evaluator"
    PROTECT_FLASH_EVALUATOR = "protect_flash_evaluator"
    PROTOTYPE_ADD = "prototype_add"
    ROW_ADD = "row_add"
    SYNTHETIC_DATA_GENERATION = "synthetic_data_generation"
    TRACE_ERROR_ANALYSIS = "trace_error_analysis"
    TURING_FLASH_EVALUATOR = "turing_flash_evaluator"
    TURING_LARGE_EVALUATOR = "turing_large_evaluator"
    TURING_SMALL_EVALUATOR = "turing_small_evaluator"
    USER_ADD = "user_add"


class APICallStatusChoices(str, Enum):
    SUCCESS = "success"
    ERROR = "error"
    PROCESSING = "processing"
    RESOURCE_LIMIT = "resource_limit"


class SubscriptionTierChoices(str, Enum):
    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class _NullCallLog:
    """Returned by stub log functions. Passes all existing guard checks.

    status starts as PROCESSING because call sites guard with:
        if call_log.status != APICallStatusChoices.PROCESSING.value: raise ...
    The subsequent .status = SUCCESS assignment and .save() are no-ops.
    """

    def __init__(self):
        self.status = APICallStatusChoices.PROCESSING.value

    def save(self):
        pass

    def __setattr__(self, name, value):
        # Allow any attribute to be set (mimics a real model row)
        object.__setattr__(self, name, value)


class APICallLog(_NullCallLog):
    pass


class OrganizationSubscription:
    """Stub for EE OrganizationSubscription model.

    .objects.get() always raises DoesNotExist so callers fall through to
    SubscriptionTierChoices.FREE.value — the correct OSS behaviour.
    """

    class DoesNotExist(Exception):
        pass

    class objects:
        @staticmethod
        def get(**kwargs):
            raise OrganizationSubscription.DoesNotExist()


def log_and_deduct_cost_for_resource_request(*args, **kwargs) -> _NullCallLog:
    return _NullCallLog()


def log_and_deduct_cost_for_api_request(*args, **kwargs) -> _NullCallLog:
    return _NullCallLog()


def refund_cost_for_api_call(*args, **kwargs) -> None:
    pass


def count_text_tokens(*args, **kwargs) -> int:
    return 0


def count_tiktoken_tokens(*args, **kwargs) -> int:
    return 0


ROW_LIMIT_REACHED_MESSAGE = "Row limit reached (OSS: no quota enforced)."
