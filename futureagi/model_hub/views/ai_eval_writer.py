"""
AI Eval Prompt Writer endpoint.

POST /model-hub/ai-eval-writer/

Takes a user's brief description of what they want to evaluate
and generates a full, structured eval instruction prompt with
template variables.
"""

import traceback

import structlog
from drf_yasg.utils import swagger_auto_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from model_hub.serializers.contracts import (
    MODEL_HUB_ERROR_RESPONSES,
    AIEvalWriterRequestSerializer,
    AIEvalWriterResponseSerializer,
)
from tfc.utils.api_contracts import validated_request
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)

SYSTEM_PROMPT = """You are an expert AI evaluation engineer. Given a user's brief description of what they want to evaluate, generate a comprehensive evaluation instruction prompt.

Rules:
1. The prompt MUST include template variables using double curly braces: {{variable_name}}
2. Common variables: {{input}}, {{output}}, {{expected}}, {{ground_truth}}, {{model_output}}, {{context}}, {{conversation}}
3. The prompt should be specific, actionable, and tell the LLM evaluator exactly what to check
4. Include clear scoring criteria (what constitutes pass/fail or a high/low score)
5. Keep it concise but thorough — typically 5-15 lines
6. Return ONLY the prompt text, no explanation or markdown wrapping

Example input: "check if the response is factually accurate"
Example output:
You are an expert fact-checker evaluating AI-generated responses for factual accuracy.

Given the following:
- User Question: {{input}}
- AI Response: {{output}}
- Reference Answer: {{ground_truth}}

Evaluate whether the AI response is factually accurate by checking:
1. All stated facts match the reference answer
2. No fabricated or hallucinated information is present
3. Key details are not omitted or distorted

Return "Passed" if the response is factually accurate, "Failed" otherwise."""

MESSAGES_SYSTEM_PROMPT = """You are an expert AI evaluation engineer. Given a user's description of what they want to evaluate, generate an LLM-as-a-Judge prompt as a multi-message conversation.

Rules:
1. Return ONLY a valid JSON array of messages. No markdown fences, no explanation, no prose.
2. Each array element must be an object with exactly two keys: "role" and "content".
3. Generate ONLY "system" and "user" roles. Do NOT generate an "assistant" role — the assistant response comes from the actual LLM at evaluation time.
4. The "system" message sets up the evaluator persona, expertise, and scoring criteria.
5. The "user" message contains the evaluation template with variables for dynamic data.
6. Use template variables with double curly braces: {{input}}, {{output}}, {{expected}}, {{ground_truth}}, {{context}}. Prefer whatever variables the user mentioned.
7. If the user supplies "Existing messages" as context, treat them as the current draft and produce an IMPROVED version that preserves intent while applying the requested changes.

Example user request:
check if chatbot answers are polite and accurate

Example output (return exactly this shape, no wrapping):
[
  {"role": "system", "content": "You are a strict evaluator judging chatbot responses for politeness and factual accuracy. Score each response on two dimensions and return a final verdict."},
  {"role": "user", "content": "User question: {{input}}\\nChatbot response: {{output}}\\nReference answer: {{ground_truth}}\\n\\nEvaluate whether the chatbot response is (1) polite in tone and (2) factually consistent with the reference answer. Return 'Passed' only if both criteria are met, otherwise return 'Failed' with a brief reason."}
]"""

OUTPUT_FORMAT_PROMPTS = {
    "prompt": SYSTEM_PROMPT,
    "messages": MESSAGES_SYSTEM_PROMPT,
}


class AIEvalWriterView(APIView):
    """
    POST /model-hub/ai-eval-writer/

    Request:
    {
        "description": "check if response matches ground truth",
        "output_format": "prompt" | "messages"   # optional, defaults to "prompt"
    }

    Response: { "status": true, "result": { "prompt": "..." } }

    For output_format == "messages", the "prompt" field contains a JSON string
    that callers should JSON.parse into an array of {role, content} messages.
    """

    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    @validated_request(
        request_serializer=AIEvalWriterRequestSerializer,
        responses={200: AIEvalWriterResponseSerializer, **MODEL_HUB_ERROR_RESPONSES},
        reject_unknown_fields=True,
    )
    def post(self, request, *args, **kwargs):
        try:
            description = request.validated_data.get("description", "").strip()
            output_format = request.validated_data.get("output_format", "prompt")
            if not description:
                return self._gm.bad_request("Description is required")

            system_prompt = OUTPUT_FORMAT_PROMPTS.get(output_format)
            if system_prompt is None:
                return self._gm.bad_request(
                    f"Invalid output_format: {output_format}. "
                    f"Expected one of: {list(OUTPUT_FORMAT_PROMPTS.keys())}"
                )

            # Use Haiku for speed
            from agentic_eval.core.utils.model_config import ModelConfigs

            haiku_cfg = ModelConfigs.HAIKU_4_5_BEDROCK_ARN

            import litellm

            response = litellm.completion(
                model=haiku_cfg.model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": description},
                ],
                temperature=0.3,
                max_tokens=1500 if output_format == "messages" else 1000,
                num_retries=2,
            )

            prompt_text = response.choices[0].message.content.strip()

            # Strip any markdown wrapping
            if prompt_text.startswith("```"):
                lines = prompt_text.split("\n")
                prompt_text = "\n".join(
                    lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
                )
                prompt_text = prompt_text.strip()

            return self._gm.success_response({"prompt": prompt_text})

        except Exception as e:
            logger.error(
                f"Error in AIEvalWriterView: {str(e)}\n{traceback.format_exc()}"
            )
            return self._gm.bad_request(f"AI eval writer error: {str(e)}")
