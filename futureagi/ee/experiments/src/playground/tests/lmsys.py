import json
import logging
import multiprocessing
import os

import dspy
from datasets import load_dataset
from experiments.src.agent.modules import OpenRouter
from experiments.src.playground.eval_modules import (
    ScorerConcludedChatModule,
    ScorerMidwayChatModule,
)
from experiments.src.playground.llm import QualitativeEvaluator
from experiments.src.playground.scorer import ConversationScorer


class ChatExperimentRunner:
    def __init__(self, experiment_name, input_eval_prompt):
        self.experiment_name = experiment_name
        self.input_eval_prompt = input_eval_prompt

    def run(self):
        openrouter_lm = OpenRouter(
            model_name="google/gemini-pro", temperature=0.2, max_tokens=22937
        )
        dspy.configure(lm=openrouter_lm)

        qualitative_evaluator = QualitativeEvaluator()
        output_eval_prompt = (
            qualitative_evaluator.get_qualitative_eval_parameter_prompt(
                self.input_eval_prompt
            )
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

        dataset = load_dataset(
            "lmsys/chatbot_arena_conversations",
            use_auth_token=os.getenv("HF_AUTH_TOKEN"),
        )
        dataset = dataset.filter(lambda x: x["turn"] > 5 and x["turn"] < 10)
        dataset = dataset["train"]

        json_file_path = f"score_archive/{self.experiment_name}/criteria_scores.json"
        if os.path.exists(json_file_path):
            logging.info(f"Reading existing scores from {json_file_path}")
            with open(json_file_path) as f:
                existing_scores_dict = json.load(f)
            processed_question_ids = set(existing_scores_dict.keys())
            dataset = dataset.filter(
                lambda x: x["question_id"] not in processed_question_ids
            )
        else:
            logging.info(f"No existing scores found at {json_file_path}")
            existing_scores_dict = {}

        pool = multiprocessing.Pool()
        args = [(item, "winner") for item in dataset]
        results = pool.starmap(conversation_scorer.process_dataset_item, args)
        pool.close()
        pool.join()

        scores_dict = {}
        for item, (
            scores_unfinished,
            score_finished,
            judgments_unfinished,
            judgments_finished,
        ) in zip(dataset, results, strict=False):
            question_id = item["question_id"]
            scores_dict[question_id] = {
                **scores_unfinished,
                "1.0": score_finished,
                "expanded_eval_instructions": expanded_eval_instructions,
                "judgments_unfinished": judgments_unfinished,
                "judgments_finished": judgments_finished,
            }

            os.makedirs(f"score_archive/{self.experiment_name}", exist_ok=True)
            with open(
                f"score_archive/{self.experiment_name}/criteria_scores_intermediate.json",
                "w",
            ) as f:
                json.dump({**existing_scores_dict, **scores_dict}, f)

        merged_scores_dict = {**existing_scores_dict, **scores_dict}

        os.makedirs(f"score_archive/{self.experiment_name}", exist_ok=True)
        with open(
            f"score_archive/{self.experiment_name}/criteria_scores.json", "w"
        ) as f:
            json.dump(merged_scores_dict, f)
