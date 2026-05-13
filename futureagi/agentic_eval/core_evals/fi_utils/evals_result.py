from dataclasses import dataclass, field
from typing import NotRequired, TypedDict

import pandas as pd
from pydantic import BaseModel


class OpenAiPromptMessage(TypedDict):
    role: str
    content: str

class DataPoint(TypedDict, total=False):
    """Data point for a single inference."""

    response: str

class EvalResultMetric(TypedDict):
    """
    Represents the LLM evaluation result metric.
    """

    id: str
    value: float


class DatapointFieldAnnotation(TypedDict):
    """
    The annotations to be logged for the datapoint field.
    """

    field_name: str
    text: str
    annotation_type: str
    annotation_note: str


class EvalResult(TypedDict):
    """
    Represents the LLM evaluation result.

    Callers should use `cost` and `token_usage` for billing data.
    `metadata` is a legacy JSON string from LLM evaluators (CustomPromptEvaluator)
    that may contain explanation, response_time, and other fields — do not parse it
    for cost data.
    """

    name: str
    display_name: str
    data: dict
    failure: bool | None
    reason: str
    runtime: int
    model: str | None
    metadata: str | None
    metrics: list[EvalResultMetric]
    datapoint_field_annotations: list[DatapointFieldAnnotation] | None
    cost: NotRequired[dict | None]
    token_usage: NotRequired[dict | None]


@dataclass
class BatchRunResult:
    """
    Represents the result of a batch run of LLM evaluation.
    """

    eval_results: list[EvalResult | None]
    eval_request_id: str | None = field(default=None)

    def to_df(self):
        """
        Converts the batch run result to a Pandas DataFrame, including data and dynamic metrics.
        """
        pd.set_option("display.max_colwidth", 500)

        df_data = []
        for item in self.eval_results:
            if item is None:
                # Add a representation for None entries
                entry = {
                    "display_name": None,
                    "failed": None,
                    "grade_reason": None,
                    "runtime": None,
                    "model": None,
                    # Add more fields as None or with a placeholder as necessary
                }
            else:
                # Start with dynamic fields from the 'data' dictionary
                entry = dict(item["data"].items())

                # Add fixed fields
                entry.update(
                    {
                        "display_name": item["display_name"],
                        "failed": item.get("failure"),
                        "grade_reason": item["reason"],
                        "runtime": item["runtime"],
                        "model": item.get("model"),
                    }
                )

                # Add dynamic metrics
                for metric in item["metrics"]:
                    entry[metric["id"]] = metric["value"]

            df_data.append(entry)

        df = pd.DataFrame(df_data)
        return df


class EvalPerformanceReport(TypedDict):
    """
    Represents the performance metrics for an evaluation.
    """

    true_positives: int
    false_positives: int
    true_negatives: int
    false_negatives: int
    accuracy: float
    precision: float
    recall: float
    f1_score: float
    runtime: int
    dataset_size: int


class GuardResult(BaseModel):
    passed: bool
    reason: str
    runtime: int
