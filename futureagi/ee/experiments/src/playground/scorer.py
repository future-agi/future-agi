import logging
import time
from concurrent.futures import ThreadPoolExecutor

from eval_modules import RAGCheck
from llm import QualitativeEvaluator

from ...core.utils.functions import format_conversation


class ConversationScorer:
    def __init__(
        self,
        expanded_eval_instructions,
        chat_midway_scorer_module,
        chat_concluded_scorer_module,
    ):
        self.expanded_eval_instructions = expanded_eval_instructions
        self.chat_midway_scorer_module = chat_midway_scorer_module
        self.chat_concluded_scorer_module = chat_concluded_scorer_module
        self.qualitative_evaluator = QualitativeEvaluator()

    def get_score_at_unfinished_conversation(self, fraction_finished, conversation):
        clip_index = int(len(conversation) * fraction_finished) - 1
        logging.info(f"len(conversation): {len(conversation)}")
        logging.info(f"clip_index: {clip_index}")
        if conversation[clip_index]["role"] == "assistant":
            clip_index += 1
        if clip_index >= len(conversation):
            clip_index = len(conversation) - 2
        response = conversation[clip_index + 1]["content"]
        conversation = conversation[: clip_index + 1]
        chat_history = format_conversation(conversation)
        judgments = {}

        def score_eval_instruction(eval_instruction):
            logging.info("eval_instruction: " + eval_instruction)
            for _attempt in range(3):
                try:
                    judgment = self.chat_midway_scorer_module(
                        judging_criteria=eval_instruction,
                        query=chat_history,
                        response=response,
                    ).judgment
                    judgments[eval_instruction] = judgment
                    return self.qualitative_evaluator.get_score(judgment)
                except Exception as e:
                    logging.error(
                        f"Error in score_eval_instruction: {e}. Retrying in 2 seconds..."
                    )
                    time.sleep(2)
            logging.error("Failed to get score after 3 attempts. Returning 0.")
            return 0

        with ThreadPoolExecutor() as executor:
            scores = list(
                executor.map(
                    score_eval_instruction,
                    self.expanded_eval_instructions.values(),
                )
            )
        total_score = sum(scores)
        return total_score, judgments

    def get_score_at_finished_conversation(self, conversation):
        conversation = format_conversation(conversation)
        judgments = {}
        # qualitative_evaluator = QualitativeEvaluator()

        def score_eval_instruction(eval_instruction):
            logging.info("eval_instruction: " + eval_instruction)
            for _attempt in range(3):
                try:
                    judgment = self.chat_concluded_scorer_module(
                        judging_criteria=eval_instruction,
                        chat_history=conversation,
                    ).judgment
                    judgments[eval_instruction] = judgment
                    return self.qualitative_evaluator.get_score(judgment)
                except Exception as e:
                    logging.error(
                        f"Error in score_eval_instruction: {e}. Retrying in 2 seconds..."
                    )
                    time.sleep(2)
            logging.error("Failed to get score after 3 attempts. Returning 0.")
            return 0

        with ThreadPoolExecutor() as executor:
            scores = list(
                executor.map(
                    score_eval_instruction,
                    self.expanded_eval_instructions.values(),
                )
            )
        total_score = sum(scores)
        return total_score, judgments

    def process_dataset_item(self, conversation):
        fractions = [0.25, 0.5, 0.75]
        scores_unfinished = {}
        judgments_unfinished = {}
        for fraction in fractions:
            (
                score_unfinished,
                judgments,
            ) = self.get_score_at_unfinished_conversation(fraction, conversation)
            scores_unfinished[str(fraction)] = score_unfinished
            judgments_unfinished[str(fraction)] = judgments
        (
            score_finished,
            judgments_finished,
        ) = self.get_score_at_finished_conversation(conversation)
        return (
            scores_unfinished,
            score_finished,
            judgments_unfinished,
            judgments_finished,
        )


class RAGAnswerEvaluator:
    def __init__(self, evaluation_instructions):
        self.evaluation_instructions = evaluation_instructions
        self.evaluation_criteria_module = RAGCheck(
            reasoning_strategy="chain_of_thought"
        )
        self.qualitative_evaluator = QualitativeEvaluator()

    def evaluate_answer(self, context, query, answer):
        results_dict = {}

        for i, instruction in self.evaluation_instructions.items():
            try:
                logging.info(f"Evaluation Instruction {i}: {instruction}")
                evaluation_judgement = self.evaluation_criteria_module(
                    context=context,
                    question=query,
                    answer=answer,
                    evaluation_instruction=instruction,
                )
                score = self.qualitative_evaluator.get_score(
                    self.qualitative_evaluator.summarize_eval_report(
                        evaluation_judgement
                    )
                )
                logging.info(f"Question: {query}")
                logging.info(f"Answer: {answer}")
                logging.info(f"Evaluation Judgement: {evaluation_judgement}")
                logging.info(f"Score: {score}")
                results_dict[i] = {
                    "question": query,
                    "answer": answer,
                    "context": context,
                    "evaluation_judgement": evaluation_judgement,
                    "evaluation_instruction": instruction,
                    "score": score,
                }
            except Exception as e:
                logging.error(f"Error while evaluating answer: {e}")
                results_dict[i] = {
                    "question": query,
                    "answer": answer,
                    "context": context,
                    "evaluation_judgement": "Error",
                    "evaluation_instruction": instruction,
                    "score": 0,
                }

        return results_dict
