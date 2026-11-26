import csv
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

from agent import AgentTask
from tags import Tags
from tqdm import tqdm

# load_dotenv()

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def dialogsum_to_conversation(dataset_item):
    knowledge = dataset_item["knowledge"]
    dialogue = dataset_item["dialogue_history"]
    response = dataset_item["response"]
    user_query = f"Given the dialogue and the knowledge can you respond to the last message? Knowledge: {knowledge}\n\n{dialogue}"
    response = f"{response}"
    user_conversation = [
        {"role": "user", "content": user_query},
        {"role": "assistant", "content": f"Response: {response}"},
    ]
    chat_history = {
        "chat_history": user_conversation,
        "chat_type": "conversation",
    }
    return chat_history


metric_properties = {
    "id": "test_metric_1",
    "eval_instructions": "Evaluate if the response is correct or not. Consider the knowledge and dialogue history to be correct and relevant.",
}


def evaluate_conversation(chat_properties, metric_properties, fractions):
    agent_task = AgentTask(chat_properties, metric_properties)
    return agent_task.score_chat_history(fractions=fractions)


def main(output_csv_path: str, input_json_path: str):
    tags = Tags(metric_properties)
    count = 0
    with open(output_csv_path, "w", newline="") as csvfile:
        fieldnames = [
            "conversation_id",
            "eval_instruction",
            "summary_judgement",
            "summary_tags",
            "score",
            "dialogue",
            "knowledge",
            "response",
        ]
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()

        logger.info("Starting evaluation of conversations")
        with open(input_json_path) as jsonfile:
            for i, line in tqdm(enumerate(jsonfile), desc="Processing conversations"):
                data = json.loads(line)
                conversation_data = {
                    "knowledge": data["knowledge"],
                    "dialogue_history": data["dialogue_history"],
                    "response": data["hallucinated_response"],
                }
                conversation = dialogsum_to_conversation(conversation_data)
                score_logs = evaluate_conversation(
                    conversation, metric_properties, fractions=[1]
                )
                summary_judgement = score_logs["1"]["summary_judgement"]
                summary_tags = tags.generate_tags(summary_judgement)
                writer.writerow(
                    {
                        "conversation_id": i,
                        "summary_judgement": summary_judgement,
                        "summary_tags": ",".join(summary_tags),
                        "dialogue": data["dialogue_history"],
                        "knowledge": data["knowledge"],
                        "response": data["hallucinated_response"],
                        "score": score_logs["1"]["score"],
                        "eval_instruction": metric_properties["eval_instructions"],
                    }
                )
                logger.debug(f"Processed conversation {i}")
                count += 1
                if count > 10:
                    break

        logger.info("Evaluation complete. Results written to evaluation_results.csv")


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Usage: python test_flow_halueval.py <output_csv_path> <input_json_path>")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
