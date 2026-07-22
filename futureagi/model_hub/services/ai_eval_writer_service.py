"""Service layer for the AI eval-writer.

Generates an evaluation artifact (an instruction prompt, an LLM-as-a-Judge
message array, or test data) from a natural-language description by calling
the LLM. Used by AIEvalWriterView; kept HTTP-free so any caller can reuse it.
"""

import json

import structlog

from agentic_eval.core.utils.json_utils import (
    extract_dict_from_string,
    strip_code_fence,
)

logger = structlog.get_logger(__name__)


class MalformedModelOutput(RuntimeError):
    """The model returned output that couldn't be parsed into the expected shape.

    A model failure (not the caller's fault), so the view maps it to a 5xx.
    """

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

TEST_DATA_SYSTEM_PROMPT = """You generate realistic test data as a JSON object for testing an LLM evaluation.

Rules:
1. Return ONLY a valid JSON object. No markdown fences, no explanation, no prose.
2. The object's keys must be EXACTLY the variable names the user provides — no more, no fewer.
3. Every value must be a realistic string appropriate to the eval's scenario.
4. If the user supplies "Current test data JSON", treat it as the current draft and return an UPDATED object that applies the requested change while keeping the same keys.
5. If the user asks for a "failing case", craft data that should FAIL the evaluation (e.g. an unsupported claim, a factual error, an off-topic response). If they ask for a "passing case", craft data that should PASS.

Example user request:
Generate realistic test data as JSON for variables: output, context. User request: a failing case

Example output (return exactly this shape, no wrapping):
{"output": "Our revenue grew 40% driven by strong demand in Europe.", "context": "Q3 revenue was flat year over year with no regional breakdown provided."}"""

OUTPUT_FORMAT_PROMPTS = {
    "prompt": SYSTEM_PROMPT,
    "messages": MESSAGES_SYSTEM_PROMPT,
    "test_data": TEST_DATA_SYSTEM_PROMPT,
}


def _parse_object(text: str) -> dict:
    """Parse a JSON object from model output, repairing if needed."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        try:
            data = extract_dict_from_string(text)
        except ValueError as e:
            raise MalformedModelOutput(str(e)) from e
    if not isinstance(data, dict):
        raise MalformedModelOutput("Model did not return a JSON object")
    return data


def _parse_array(text: str) -> list:
    """Parse a JSON array from model output."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise MalformedModelOutput(f"Model did not return valid JSON: {e}") from e
    if not isinstance(data, list):
        raise MalformedModelOutput("Model did not return a JSON array")
    return data


def generate_eval_prompt(*, description: str, output_format: str = "prompt") -> dict:
    """Generate an eval artifact from a description via the LLM.

    Args:
        description: Natural-language description of what to generate.
        output_format: One of OUTPUT_FORMAT_PROMPTS — "prompt" (an eval
            instruction), "messages" (a JSON array of judge messages), or
            "test_data" (a JSON object of test data).

    Returns:
        A result dict with exactly the key matching output_format, already
        parsed/validated to its type:
          - "prompt"    -> {"prompt": str}
          - "messages"  -> {"messages": list[dict]}
          - "test_data" -> {"test_data": dict}

    Raises:
        ValueError: description is blank or output_format is unknown (400).
        MalformedModelOutput: the model's response couldn't be parsed (5xx).
    """
    description = (description or "").strip()
    if not description:
        raise ValueError("Description is required")

    system_prompt = OUTPUT_FORMAT_PROMPTS.get(output_format)
    if system_prompt is None:
        raise ValueError(
            f"Invalid output_format: {output_format}. "
            f"Expected one of: {list(OUTPUT_FORMAT_PROMPTS.keys())}"
        )

    # Route through the in-house LLM wrapper (Agentcc usage gateway with
    # litellm fallback) so the call is metered + org-attributed and retries
    # are handled for us — same pattern as ai_filter.py, not a raw Bedrock call.
    from agentic_eval.core.llm.llm import LLM
    from agentic_eval.core.utils.model_config import ModelConfigs

    haiku_cfg = ModelConfigs.HAIKU_4_5_BEDROCK_ARN
    llm = LLM(
        provider=haiku_cfg.provider,
        model_name=haiku_cfg.model_name,
        temperature=0.3,
        max_tokens=1500 if output_format == "messages" else 1000,
    )
    content = llm._get_completion_content(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": description},
        ],
    )

    # strip_code_fence tolerates None and unwraps any ```...``` the model adds.
    text = strip_code_fence(content)

    # Parse + validate backend-side so the response is a typed object per
    # format, not an unvalidated string the client has to re-parse.
    if output_format == "messages":
        return {"messages": _parse_array(text)}
    if output_format == "test_data":
        return {"test_data": _parse_object(text)}
    return {"prompt": text}
