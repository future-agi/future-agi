from dataclasses import dataclass
from typing import Any


@dataclass
class FiMeta:
    prompt_slug: str | None = None
    context: dict | None = None
    session_id: str | None = None
    user_query: str | None = None
    environment: str | None = "production"
    external_reference_id: str | None = None
    customer_id: str | None = None
    customer_user_id: str | None = None
    response_time: int | None = None
    custom_attributes: dict | None = None
    custom_eval_metrics: dict | None = None

    # New fields based on the response structure
    language_model_id: str | None = None
    prompt: list[dict[str, Any]] | None = None
    response: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cost: float | None = None
    expected_response: dict[str, Any] | None = None
    tools: list | None = None
    tool_calls: list | None = None
    functions: dict | None = None
    function_call_response: dict | None = None
    status_code: int | None = None
    error: str | None = None
