import json
import logging
import os

import pytest

pytestmark = [pytest.mark.external, pytest.mark.live_llm]
if os.getenv("RUN_EXPERIMENT_EXAMPLES") != "1":
    pytest.skip(
        "Experimental live LLM example; set RUN_EXPERIMENT_EXAMPLES=1 to run.",
        allow_module_level=True,
    )

import dspy
from eval_modules import ScorerConcludedChatModule, ScorerMidwayChatModule
from llm import OpenRouterLLM, QualitativeEvaluator
from scorer import ConversationScorer

logging.basicConfig(level=logging.INFO)
openrouter_llm = OpenRouterLLM(temperature=0.5, model_name="anthropic/claude-3-opus")
dspy.settings.configure(lm=openrouter_llm)


def test_conversation_scorer():
    input_eval_prompt = "Evaluate the model's responses based on their relevance, coherence, and helpfulness in the context of the conversation."
    qualitative_evaluator = QualitativeEvaluator()
    output_eval_prompt = qualitative_evaluator.get_qualitative_eval_parameter_prompt(
        input_eval_prompt
    )
    output_eval_prompt = json.loads(output_eval_prompt)
    expanded_eval_instructions = output_eval_prompt

    chat_midway_scorer_module = ScorerMidwayChatModule()
    chat_concluded_scorer_module = ScorerConcludedChatModule()
    conversation_scorer = ConversationScorer(
        expanded_eval_instructions,
        chat_midway_scorer_module,
        chat_concluded_scorer_module,
    )

    conversation = [
        {"role": "user", "content": "Hello, how can I reset my password?"},
        {
            "role": "assistant",
            "content": "You can reset your password by clicking on 'Forgot Password' at the login screen.",
        },
        {
            "role": "user",
            "content": "I tried that, but I didn't receive the reset email.",
        },
        {
            "role": "assistant",
            "content": "Please check your spam folder. If you still can't find it, you may need to contact support.",
        },
        {"role": "user", "content": "Okay, I will do that. Thank you!"},
        {
            "role": "assistant",
            "content": "You're welcome! If you have any other questions, feel free to ask.",
        },
    ]

    (
        scores_unfinished,
        score_finished,
        judgments_unfinished,
        judgments_finished,
    ) = conversation_scorer.process_dataset_item(conversation)

    assert isinstance(scores_unfinished, dict)
    assert isinstance(score_finished, int | float)
    assert isinstance(judgments_unfinished, dict)
    assert isinstance(judgments_finished, dict)

    print("ConversationScorer test passed!")


if __name__ == "__main__":
    test_conversation_scorer()
