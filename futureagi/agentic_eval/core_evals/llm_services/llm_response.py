import os

import structlog

logger = structlog.get_logger(__name__)
from agentic_eval.core_evals.fi_utils.fi_meta import FiMeta
from agentic_eval.core_evals.fi_utils.token_count_helper import calculate_total_cost
from agentic_eval.core_evals.llm_services.inference_logger import InferenceLogger


class LiteLlmAgi:
    def __init__(self):
        self.agi_api_key = os.getenv("AGI_API_KEY", None)
        if not self.agi_api_key:
            raise ValueError("AGI_API_KEY is not set in environment variables.")

    def fi_callback(self, response, fi_meta=FiMeta):
        """
        Callback function to process the response and log it to AGI.
        """
        if not self.agi_api_key:
            logger.info("AGI logging is not configured because API key is missing.")
            return

        parsed_data = {
            "id": response.id,
            "created": response.created,
            "model": response.model,
            "object": response.object,
            "system_fingerprint": response.system_fingerprint,
            "usage": {
                "completion_tokens": response.usage.completion_tokens,
                "prompt_tokens": response.usage.prompt_tokens,
                "total_tokens": response.usage.total_tokens,
            },
            "cost": calculate_total_cost(response.model, dict(response.usage)),
        }

        # Parse choices
        parsed_data["choices"] = []
        for choice in response.choices:
            choice_data = {
                "finish_reason": choice.finish_reason,
                "index": choice.index,
                "message": {
                    "content": choice.message.content,
                    "role": choice.message.role,
                    "tool_calls": choice.message.tool_calls,
                    "function_call": choice.message.function_call,
                },
            }
            parsed_data["choices"].append(choice_data)

        log_to_fi(parsed_data, fi_meta)


def log_to_fi(result: dict, fi_meta: FiMeta):
    try:
        prompt_slug = "default"
        context = None
        customer_id = None
        customer_user_id = None
        response_time_ms = None
        session_id = None
        user_query = None
        tool_calls = None
        environment = "production"
        external_reference_id = None
        custom_attributes = None
        custom_eval_metrics = None

        if fi_meta:
            prompt_slug = fi_meta.prompt_slug
            context = fi_meta.context
            response_time_ms = fi_meta.response_time
            customer_id = fi_meta.customer_id
            customer_user_id = fi_meta.customer_user_id
            session_id = fi_meta.session_id
            user_query = fi_meta.user_query
            environment = fi_meta.environment or "production"
            external_reference_id = fi_meta.external_reference_id
            custom_attributes = fi_meta.custom_attributes
            custom_eval_metrics = fi_meta.custom_eval_metrics
            tool_calls = fi_meta.tool_calls
        InferenceLogger.log_inference(
            prompt_slug=prompt_slug,
            prompt=fi_meta.prompt,
            language_model_id=result["model"],
            response=result["choices"][0]["message"]["content"],
            context=context,
            tool_calls=tool_calls,
            response_time=response_time_ms,
            customer_id=customer_id,
            customer_user_id=customer_user_id,
            session_id=session_id,
            user_query=user_query,
            prompt_tokens=result["usage"]["prompt_tokens"],
            completion_tokens=result["usage"]["completion_tokens"],
            total_tokens=result["usage"]["total_tokens"],
            environment=environment,
            external_reference_id=external_reference_id,
            custom_attributes=custom_attributes,
            custom_eval_metrics=custom_eval_metrics,
        )
    except Exception as e:
        logger.exception("Exception while logging to fi: ", e)


"""
response = completion(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "user", "content": "Hi 👋 - I'm openai"}
        ]
    )
    print("Completion successful")
    print(response)
    a.fi_callback(response,FiMeta(prompt=[
            {"role": "user", "content": "Hi 👋 - I'm openai"}
        ]))
"""
