from typing import Any

from ee.agenthub.prompt_eval_agent.prompts import system_message_4
from agentic_eval.core.llm.llm import LLM
from agentic_eval.core.utils.model_config import ModelConfigs
from agentic_eval.core.utils.functions import parse_llm_str_response
from agentic_eval.core.utils.types import PromptEvalLlmResponse, PromptEvalResponse


class PromptValidator:
    def __init__(self):
        _cfg = ModelConfigs.CLAUDE_4_5_SONNET_BEDROCK_ARN
        self.llm = LLM(
            model_name=_cfg.model_name,
            temperature=_cfg.temperature,
            max_tokens=_cfg.max_tokens,
            provider=_cfg.provider,
        )

    def is_valid_prompt(self, prompt: str) -> dict[str, Any]:
        response_dict: dict = self.get_correct_prompt(prompt=prompt)
        llm_response_obj = PromptEvalLlmResponse(**response_dict)
        response_obj = PromptEvalResponse(
            is_ambiguity=llm_response_obj.ambiguous,
            explanation=llm_response_obj.explanation,
            prompts=llm_response_obj.suggested_prompt,
        )
        return response_obj.dict()

    def _invoke_llm(self, prompt: str) -> str:
        return self.llm._get_completion_content(
            messages=[
                {
                    "role": "user",
                    "content": system_message_4.format(prompt=prompt),
                },
            ]
        )

    def contains_ambiguity(self, prompt: str) -> bool:
        response = self._invoke_llm(prompt=prompt)
        llm_response_result = parse_llm_str_response(
            response, PromptEvalLlmResponse, as_dict=False
        )
        llm_response_obj: PromptEvalLlmResponse = llm_response_result
        return llm_response_obj.ambiguous

    def get_correct_prompt(self, prompt: str) -> dict:
        status: bool = True
        retry_counter: int = 0
        MAX_RETRIES: int = 1

        prompt_to_correct: str = prompt

        while status:
            if retry_counter > MAX_RETRIES:
                return PromptEvalResponse().dict()

            response: str = self._invoke_llm(prompt=prompt_to_correct)
            llm_response_result = parse_llm_str_response(
                response, PromptEvalLlmResponse, as_dict=False
            )
            llm_response_obj: PromptEvalLlmResponse = llm_response_result
            # check if the alternative is ambiguous
            # removed the checking
            status = False
            # reset the prompt to correct
            prompt_to_correct = llm_response_obj.suggested_prompt
            retry_counter += 1

        return llm_response_obj.dict()

    def validate_prompt(self, prompt: str) -> dict[str, Any]:
        return self.is_valid_prompt(prompt)


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Validate prompts from an input file and save results to an output file."
    )

    parser.add_argument(
        "--input",
        type=str,
        required=True,
        help="Path to the input CSV file containing prompts to validate",
    )
    parser.add_argument(
        "--output",
        type=str,
        required=True,
        help="Path to save the output CSV file with validation results",
    )

    args = parser.parse_args()

    import pandas as pd

    # Read the CSV file
    df = pd.read_csv(args.input)

    # Iterate through each row
    for _index, row in df.iterrows():
        prompt = row["user_prompt"]  # Assuming 'prompt' is the column name

        agent = PromptValidator()
        result = agent.get_correct_prompt(prompt)
        print(f"Suggested prompt: {result}")


if __name__ == "__main__":
    main()
