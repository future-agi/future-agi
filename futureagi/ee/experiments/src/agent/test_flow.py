import csv
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
from datasets import load_dataset
from tags import Tags
from tqdm import tqdm

# load_dotenv()

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

dataset = load_dataset("knkarthick/dialogsum", split="test")


def dialogsum_to_conversation(dataset_item):
    dialogue = dataset_item["dialogue"]
    summary = dataset_item["summary"]
    user_query = f"Given the dialogue below, can you summarize it?\n\n{dialogue}"
    response = f"The summary of the dialogue is: {summary}"
    user_conversation = [
        {"role": "user", "content": user_query},
        {"role": "assistant", "content": response},
    ]
    chat_history = {
        "chat_history": user_conversation,
        "chat_type": "conversation",
    }
    return chat_history


metric_properties = {
    "id": "test_metric",
    "eval_instructions": "Evaluate how good the summarization of the conversation is.",
}


def evaluate_conversation(chat_properties, metric_properties, fractions):
    agent_task = AgentTask(chat_properties, metric_properties)
    return agent_task.score_chat_history(fractions=fractions)


tags = Tags(metric_properties)

with open("evaluation_results.csv", "w", newline="") as csvfile:
    fieldnames = [
        "conversation_id",
        "eval_instruction",
        "summary_judgement",
        "summary_tags",
        "score",
        "dialogue",
        "summary",
    ]
    writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
    writer.writeheader()

    logger.info("Starting evaluation of conversations")
    for i in tqdm(range(1000), desc="Processing conversations"):
        conversation = dialogsum_to_conversation(dataset[i])
        dialogue = dataset[i]["dialogue"]
        summary = dataset[i]["summary"]
        score_logs = evaluate_conversation(
            conversation, metric_properties, fractions=[1]
        )
        summary_judgement = score_logs["1"]["summary_judgement"]
        summary_tags = tags.generate_tags(summary_judgement)
        writer.writerow(
            {
                "conversation_id": dataset[i]["id"],
                "summary_judgement": summary_judgement,
                "summary_tags": ",".join(summary_tags),
                "dialogue": dialogue,
                "summary": summary,
                "score": score_logs["1"]["score"],
                "eval_instruction": metric_properties["eval_instructions"],
            }
        )
        logger.debug(f"Processed conversation {i}")

    logger.info("Evaluation complete. Results written to evaluation_results.csv")
