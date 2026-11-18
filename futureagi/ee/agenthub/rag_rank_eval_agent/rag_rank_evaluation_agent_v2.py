import json
import math
import traceback
from concurrent.futures import ThreadPoolExecutor

from agentic_eval.core.utils.model_config import ModelConfigs
import structlog

logger = structlog.get_logger(__name__)

RAG_RANK_THREAD_WORKER_COUNT = 2

from agentic_eval.core.utils.message_generator import prompt_message_generator

from ee.agenthub.rag_rank_eval_agent.prompts_v2 import (
    rag_analyze_subqueries_gain_prompt,
    rag_generate_subqueries_prompt,
)
from agentic_eval.core.llm.llm import LLM


class RagRankEval:
    def __init__(self, dataset, criteria_breakdown, llm=None):
        self.dataset = dataset
        self.criteria_breakdown = criteria_breakdown
        _cfg = ModelConfigs.CLAUDE_4_5_SONNET_BEDROCK_ARN
        self.llm = LLM(
            model_name=_cfg.model_name,
            temperature=_cfg.temperature,
            max_tokens=_cfg.max_tokens,
            provider=_cfg.provider,
        )
        self.counter = 0

    def calculate_relevance_and_subqueries(self, documents):
        documents_with_relevance_and_subqueries = []
        for document in documents:
            try:
                # Extract relevancy and dependent subqueries
                relevancy = document["relevancy"]
                subquery_relevancies = [
                    subquery["relevance_of_dependent_subquery"]
                    for subquery in document["relevancy_of_dependent_subqueries"]
                ]

                # Calculate the minimum relevance of all subqueries
                min_subquery_relevance = (
                    min(subquery_relevancies) if subquery_relevancies else 1
                )  # Default to 1 if no subqueries

                # Calculate the sum of relevance of all subqueries
                subqueries_answered = sum(subquery_relevancies)

                # Calculate final relevance
                final_relevance = min_subquery_relevance * relevancy

                # Store final relevance and subqueries answered in the document
                document["final_relevance"] = final_relevance
                document["subqueries_answered"] = subqueries_answered
                documents_with_relevance_and_subqueries.append(document)
            except Exception as e:
                logger.error(e)
        return documents_with_relevance_and_subqueries

    def calculate_dcg(self, documents):
        logger.debug("rag_rank_calculating_dcg", documents_count=len(documents))
        dcg = 0.0
        for index, doc in enumerate(documents):
            relevance = doc["information_gained"] * doc["final_relevance"]
            # Calculate discounted gain using the index in the original list
            gain = relevance / math.log2(
                index + 2
            )  # index + 1 in the formula means index + 2 in Python (0-based)
            dcg += gain
        logger.debug("rag_rank_dcg_calculated", dcg=dcg)
        return dcg

    def calculate_idcg(self, documents):
        # Sort documents by relevance in descending order
        # sorted_documents = sorted(documents, key=lambda x: x["information gained"], reverse=True)
        sorted_documents = sorted(
            documents,
            key=lambda x: (
                x.get("subqueries_answered", 0),
                x.get("information_gained", 0),
            ),
            reverse=True,
        )

        logger.debug("rag_rank_calculating_idcg", documents_count=len(sorted_documents))
        return self.calculate_dcg(sorted_documents)

    def calculate_weighted_ndcg(self, evaluation_judgement, subqueries):
        weighted_ndcg = 0.0
        for query, judgement in zip(subqueries, evaluation_judgement, strict=False):
            judgement = self.calculate_relevance_and_subqueries(judgement)
            idcg = self.calculate_idcg(judgement)
            # Handle division by zero case
            if idcg == 0:
                subquery_ndcg = 0
            else:
                subquery_ndcg = (self.calculate_dcg(judgement) / idcg) * query[
                    "relevance"
                ]
            weighted_ndcg = weighted_ndcg + subquery_ndcg
        return weighted_ndcg

    def solve_ranking_problem(self, question, context):
        subqueries = self.generate_subqueries(question)
        solution = self.analyze_subquery_gain(context, subqueries)

        return solution, subqueries

    def analyze_subquery_gain(self, context, subqueries):
        subquery_info_gain_list = []
        ranked_context = [
            f"Document{idx + 1}: {doc} , Rank: {idx + 1}"
            for idx, doc in enumerate(context)
        ]
        for query in subqueries:
            dependent_parent_queries_list = [
                "<dependent_subquery>" + query + "</dependent_subquery>"
                for query in query.get("parent", [])
            ]
            prompt = rag_analyze_subqueries_gain_prompt.format(
                query=query["subquery"],
                dependent_subqueries="\n".join(dependent_parent_queries_list),
                documents="\n".join(ranked_context),
            )
            messages = prompt_message_generator(prompt)

            data = self.llm._get_completion_content(messages=messages)
            data = (
                data.strip()  # Remove leading/trailing whitespace
                .replace("\n", "")  # Remove newlines
                .replace("\r", "")  # Remove carriage returns
                .replace("\t", "")  # Remove tabs
                .replace("\\r", "")  # Remove escaped carriage returns
                .replace("\\t", "")
                .replace("```json", "")
                .replace("```", "")
            )

            subquery_info_gain = []
            try:
                subquery_info_gain = json.loads(
                    data.strip().replace("```json", "").replace("```", "")
                )
            except json.JSONDecodeError as e:
                logger.exception(e)
                # Try to recover partial data
                # subquery_info_gain = loads(data, Allow.ALL)

            subquery_info_gain_list.append(subquery_info_gain)
        return subquery_info_gain_list

    def generate_subqueries(self, question):
        try:
            logger.debug("rag_rank_generating_subqueries")
            prompt = rag_generate_subqueries_prompt.format(query=question)
            messages = prompt_message_generator(prompt)

            subqueries = json.loads(
                self.llm._get_completion_content(messages=messages)
                .strip()
                .replace("```json", "")
                .replace("```", "")
            )
            return subqueries
        except Exception as e:
            logger.exception("rag_rank_generate_subqueries_failed", error=str(e))

    def process_instruction(self, data_item):
        question = data_item["question"]
        answer = data_item["answer"]
        context = data_item["context"]

        # logging.info(f"Context == {context}")

        try:
            logger.debug("rag_rank_process_instruction_started", counter=self.counter)

            evaluation_judgement, subqueries = self.solve_ranking_problem(
                question, context
            )

            # evaluation_judgement = self.calculate_relevance_and_subqueries(evaluation_judgement)
            score = self.calculate_weighted_ndcg(evaluation_judgement, subqueries)
            # evaluation_judgement = [query_judgement[0] for query_judgement in evaluation_judgement]
            logger.debug("rag_rank_scored_instruction", score=score)
            # logging.info(f"Evaluation Judgement: {score.get('judgment')}")
            # logging.info(f"Score: {score.get('score')}")
            self.counter += 1
            logger.debug(
                "rag_rank_instruction_result",
                question=question,
                answer=answer,
                context=context,
                score=score,
            )
            return {
                "instruction_id": self.counter,
                "result": {
                    "question": question,
                    "answer": answer,
                    "context": context,
                    "subqueries": subqueries,
                    "score": score,
                    "judgment": evaluation_judgement,
                },
            }

        except Exception as e:
            logger.exception(e)
            traceback.print_exc()
            self.counter += 1
            logger.error(
                "rag_rank_process_instruction_failed",
                question=question,
                error=str(e),
            )
            return {
                "instruction_id": self.counter,
                "result": {
                    "question": question,
                    "answer": answer,
                    "context": context,
                    "subqueries": [],
                    "score": 0,
                    "judgment": "Error",
                },
            }

    def process_data(self):
        results_final = []
        task_list = list(self.dataset)

        with ThreadPoolExecutor(max_workers=RAG_RANK_THREAD_WORKER_COUNT) as executor:
            results = list(
                executor.map(lambda args: self.process_instruction(args), task_list)
            )

        # Combining results
        for result in results:
            if result:
                results_final.append(result["result"])
        return results_final
