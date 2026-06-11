"""API call type and status enums shared across OSS and EE.

These enums are the canonical definitions. The EE module re-exports them
from here so that OSS code never needs to import from ee.usage.models.
"""

from django.db import models


class APICallTypeChoices(models.TextChoices):
    PROMPT_BENCH = "prompt_bench", "Prompt Bench"
    DATASET_PROTECT = "dataset_protect", "Dataset Protect"
    DATASET_PROTECT_FLASH = "dataset_protect_flash", "Dataset Protect Flash"

    # Evaluation
    TURING_LARGE_EVALUATOR = "turing_large_evaluator", "Turing Large Evaluator"
    TURING_SMALL_EVALUATOR = "turing_small_evaluator", "Turing Small Evaluator"
    TURING_FLASH_EVALUATOR = "turing_flash_evaluator", "Turing Flash Evaluator"
    PROTECT_EVALUATOR = "protect_evaluator", "Protect Evaluator"
    PROTECT_FLASH_EVALUATOR = "protect_flash_evaluator", "Protect Flash Evaluator"
    CODE_EVALUATOR = "code_evaluator", "Code Evaluator"

    USER_ADD = "user_add", "User Add"
    OBSERVE_ADD = "observe_add", "Observe Add"
    PROTOTYPE_ADD = "prototype_add", "Prototype Add"
    DATASET_ADD = "dataset_add", "Dataset Add"
    ROW_ADD = "row_add", "Row Add"

    KNOWLEDGE_BASE = "knowledge_base", "Knowledge Base"

    SYNTHETIC_DATA_GENERATION = "synthetic_data_generation", "Synthetic Data Generation"

    ERROR_LOCALIZER = "error_localizer", "Error Localizer"

    AUTO_ANNOTATION = "auto_annotation", "Auto Annotation"

    # Do not use these, use the above evaluator choices instead
    DATASET_EVALUATION = "dataset_evaluation", "Dataset Evaluation"
    EXPERIMENT_EVALUATION = "experiment_evaluation", "Experiment Evaluation"
    OPTIMISATION_EVALUATION = "optimisation_evaluation", "Optimisation Evaluation"
    EVAL_EXPLANATION = "eval_explanation", "Eval Explanation"

    DATASET_RUN_PROMPT = "dataset_run_prompt", "Dataset Run Prompt"
    DATASET_OPTIMIZATION = "dataset_optimization", "Dataset Optimization"
    DATASET_EXPERIMENT = "dataset_experiment", "Dataset Experiment"

    VOICE_CALL = "voice_call", "Voice Call"
    TEXT_CALL = "text_call", "Text Call"

    WALLET_REFUND = "wallet_refund", "Wallet Refund"
    WALLET_REFILL = "wallet_refill", "Wallet Refill"
    WALLET_AUTO_RECHARGE = "wallet_auto_recharge", "Wallet Auto Recharge"
    WALLET_ADD_FUNDS = "wallet_add_funds", "Wallet Add Funds"

    TRACE_ERROR_ANALYSIS = "trace_error_analysis", "Trace Error Analysis"

    @classmethod
    def get_choices(cls):
        return [(tag.value, tag.name.replace("_", " ").title()) for tag in cls]


class APICallStatusChoices(models.TextChoices):
    SUCCESS = "success", "Success"
    ERROR = "error", "Error"
    NOT_STARTED = "not_started", "Not Started"
    RATE_LIMITED = "rate_limited", "Rate Limited"
    PROCESSING = "processing", "Processing"
    INSUFFICIENT_CREDITS = "insufficient_credits", "Insufficient Credits"
    RESOURCE_LIMIT = "resource_limit", "Resource Limit"

    @classmethod
    def get_choices(cls):
        return [(tag.value, tag.name.replace("_", " ").title()) for tag in cls]
