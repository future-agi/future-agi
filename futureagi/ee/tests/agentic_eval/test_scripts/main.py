import json
import logging

from termcolor import colored

from ee.tests.agentic_eval.test_scripts.utils import (
    calculate_score_from_json,
    get_qualitative_eval_parameter_prompt,
    get_score_of_chat_response_against_detailed_criteria,
)

logging.basicConfig(level=logging.INFO)


def main():
    input_eval_instructions = input(
        colored("Enter the evaluation instructions: ", "green")
    )
    while 1:
        response = {
            "Person A": input(colored("Enter the query of Person: ", "blue")),
            "Person B": input(colored("Enter the response of LLM: ", "magenta")),
        }
        eval_instructions = get_qualitative_eval_parameter_prompt(
            input_eval_instructions
        )
        logging.info(f"Generated eval_instructions: {eval_instructions}")
        eval_instructions = json.loads(eval_instructions)
        score = get_score_of_chat_response_against_detailed_criteria(
            eval_instructions, response
        )
        logging.info(f"Generated score: {score}")
        score = json.loads(score)
        print(colored(score, "cyan"))
        print(colored(f"Score: {calculate_score_from_json(score)}", "yellow"))


if __name__ == "__main__":
    main()
