from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, TypedDict

from agentic_eval.core_evals.fi_utils.evals_result import (
    DatapointFieldAnnotation,
    EvalResultMetric,
    OpenAiPromptMessage,
)


@dataclass
class FiInference:
    """Fi PromptRun class"""

    id: str
    prompt_slug: str | None
    language_model_id: str | None
    user_query: str | None
    context: dict[str, str] | None
    prompt_response: str | None
    expected_response: str | None


@dataclass
class FiFilters:
    prompt_slug: str | None = None
    language_model_id: str | None = None
    environment: str | None = None
    topic: str | None = None
    customer_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FiEvalRunResult(TypedDict):
    failed: bool | None
    runtime: float
    reason: str
    datapoint_field_annotations: list[DatapointFieldAnnotation] | None


class FiEvalResult(TypedDict):
    job_type: str
    failed_percent: float | None
    number_of_runs: int
    flakiness: float
    run_results: list[FiEvalRunResult]
    runtime: float
    data: dict
    display_name: str
    metrics: list[EvalResultMetric]


class FiEvalRequestSource(Enum):
    DEV_SDK = "dev_sdk"
    SCHEDULED_JOB = "scheduled_job"
    UI_DASHBOARD = "ui_dashboard"


class FiEvalRequestCreateRequest(TypedDict):
    request_label: str
    request_data: dict[str, Any]
    request_data_type: str
    source: str


class FiEvalResultCreateRequest(TypedDict):
    org_id: str | None
    prompt_run_id: str | None
    job_config_id: str | None
    eval_job_id: str | None
    language_model_id: str | None
    job_type: str
    eval_type_id: str
    run_results: list[FiEvalRunResult]
    data: dict
    eval_request_id: str | None
    number_of_runs: int
    flakiness: float
    runtime: int
    failed_percent: float | None
    eval_label: str
    metrics: list[EvalResultMetric]


class FiJobType(Enum):
    LLM_EVAL = "LlmEval"


class FiInterfaceHelper:
    @staticmethod
    def eval_result_to_create_request(
        eval_request_id: str,
        eval_type: str,
        language_model_id: str,
        eval_result: FiEvalResult,
    ) -> FiEvalResultCreateRequest:
        return FiEvalResultCreateRequest(
            org_id=None,
            prompt_run_id=None,
            job_config_id=None,
            eval_job_id=None,
            language_model_id=language_model_id,
            job_type=eval_result["job_type"],
            eval_type_id=eval_type,
            run_results=eval_result["run_results"],
            data=eval_result["data"],
            eval_request_id=eval_request_id,
            number_of_runs=eval_result["number_of_runs"],
            flakiness=eval_result["flakiness"],
            runtime=int(eval_result["runtime"]),
            failed_percent=eval_result["failed_percent"],
            eval_label=eval_result["display_name"],
            metrics=eval_result["metrics"],
        )


class FiExperiment(TypedDict):
    """
    Metadata about the experiment you are running.
    - experiment_name: The name of the experiment.
    - experiment_description: A description of the experiment.
    - language_model_provider: The language model provider you are using for your LLM inference (ex: openai).
    - language_model_id: The language model id (ex: gpt-3.5-turbo)
    - prompt_template: The prompt template you are using for your LLM inference.
    - dataset_name: The name of the dataset you are using.
    """

    experiment_name: str
    experiment_description: str
    language_model_provider: str
    language_model_id: str
    prompt_template: list[OpenAiPromptMessage]
    dataset_name: str
