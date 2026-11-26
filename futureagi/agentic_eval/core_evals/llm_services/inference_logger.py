import asyncio
import threading
from typing import Any

import structlog

logger = structlog.get_logger(__name__)
from agentic_eval.core_evals.fi_utils.token_count_helper import calculate_total_cost
from agentic_eval.core_evals.keys.fi_api_key import FiApiKey


class InferenceLogger(FiApiKey):
    @staticmethod
    def log_inference(
        prompt: list[dict[str, Any]] | dict[str, Any] | str | None = None,
        response: Any | None = None,
        prompt_slug: str | None = None,
        language_model_id: str | None = None,
        environment: str | None = "production",
        functions: list[dict] | None = None,
        function_call_response: Any | None = None,
        tools: Any | None = None,
        tool_calls: Any | None = None,
        external_reference_id: str | None = None,
        customer_id: str | None = None,
        customer_user_id: str | None = None,
        session_id: str | None = None,
        user_query: str | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
        response_time: int | None = None,
        context: dict | None = None,
        expected_response: str | None = None,
        custom_attributes: dict | None = None,
        custom_eval_metrics: dict | None = None,
        cost: float | None = None,
    ) -> None:
        try:
            args = (
                prompt,
                response,
                prompt_slug,
                language_model_id,
                environment,
                functions,
                function_call_response,
                tools,
                tool_calls,
                external_reference_id,
                customer_id,
                customer_user_id,
                session_id,
                user_query,
                prompt_tokens,
                completion_tokens,
                total_tokens,
                response_time,
                context,
                expected_response,
                custom_attributes,
                cost,
                custom_eval_metrics,
            )
            threading.Thread(
                target=lambda: asyncio.run(
                    InferenceLogger._log_inference_asynchronously(*args)
                )
            ).start()
        except Exception as e:
            logger.error("Error in logging inference to Fi client: ", str(e))

    @staticmethod
    async def _log_inference_asynchronously(
        prompt,
        response,
        prompt_slug,
        language_model_id,
        environment,
        functions,
        function_call_response,
        tools,
        tool_calls,
        external_reference_id,
        customer_id,
        customer_user_id,
        session_id,
        user_query,
        prompt_tokens,
        completion_tokens,
        total_tokens,
        response_time,
        context,
        expected_response,
        custom_attributes,
        cost,
        custom_eval_metrics,
    ) -> None:
        """
        logs the llm inference to Fi client
        """
        try:
            payload = {
                "prompt": prompt,
                "response": response,
                "prompt_slug": prompt_slug,
                "language_model_id": language_model_id,
                "functions": functions,
                "function_call_response": function_call_response,
                "tools": tools,
                "tool_calls": tool_calls,
                "response_time": response_time,
                "context": context,
                "environment": environment,
                "customer_id": str(customer_id) if customer_id is not None else None,
                "customer_user_id": str(customer_user_id)
                if customer_user_id is not None
                else None,
                "session_id": str(session_id) if session_id is not None else None,
                "user_query": str(user_query) if user_query is not None else None,
                "external_reference_id": str(external_reference_id)
                if external_reference_id is not None
                else None,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "expected_response": expected_response,
                "custom_attributes": custom_attributes,
                "custom_eval_metrics": custom_eval_metrics,
                "cost": cost
                if cost
                else calculate_total_cost(
                    language_model_id,
                    {
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                    },
                ).get("total_cost", 0.0),
            }
            # Remove None fields from the payload
            payload = {k: v for k, v in payload.items() if v is not None}
            # print(payload,"pa****")
            # RequestHelper.make_post_request(endpoint=f'{API_BASE_URL}/api/v1/log/inference', payload=payload, headers={
            #     'fi-api-key': InferenceLogger.get_api_key(),
            # })
        except Exception as e:
            logger.error("Error in logging inference to Fi client: ", str(e))
