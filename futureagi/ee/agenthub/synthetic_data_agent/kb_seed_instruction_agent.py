import copy
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from enum import Enum
from typing import Any

import json_repair
import pandas as pd
from openai import OpenAI
from pydantic import BaseModel, Field

from ee.agenthub.synthetic_data_agent.prompts import (
    INSTRUCTION_GENERATION_PROMPT,
    TASK_CLASSIFICATION_PROMPT,
    query_distillation_prompt,
)
from ee.agenthub.synthetic_data_agent.taxonomy import TASK_CATEGORIES
from agentic_eval.core.database.ch_vector import ClickHouseVectorDB

# from ee.agenthub.feedback_agent_updated.utils import RAG
from agentic_eval.core.embeddings.embedding_manager import EmbeddingManager
from agentic_eval.core.llm.llm import LLM
from agentic_eval.core.utils.model_config import ModelConfigs


class TaskCategories(str, Enum):
    READING_COMPREHENSION = "Reading comprehension"
    TEXT_MODIFICATION = "Text modification"
    CODING = "Coding"
    RAG = "Retrieval-augmented generation (RAG)"
    CREATIVE_WRITING = "Creative writing"
    TOOL_API_USE = "Tool/API usage"
    ANALYTICAL_REASONING = "Analytical reasoning"
    BRAIN_TEASERS = "Brain teasers"
    DATA_TO_TEXT = "Data-to-text generation"
    TEXT_CLASSIFICATION = "Text classification and extraction"
    FEW_SHOT_REASONING = "Few-shot reasoning"
    CONVERSATIONAL = "Conversational tasks"
    EMOTIONAL_INTELLIGENCE = "Emotional intelligence evaluation"
    CONTENT_SAFETY = "Content Safety evaluation"


class TaskCategoryResponse(BaseModel):
    categories: list[TaskCategories] = Field(
        description="List of task categories that match the examples", min_items=1
    )
    confidence: dict[TaskCategories, float] = Field(
        description="Confidence score for each category (0-1)", default_factory=dict
    )
    reasoning: str | None = Field(
        description="Explanation for why these categories were chosen", default=None
    )


class KBSeedInstructionAgent:
    """
    Agent that fetches seeds from knowledge base and generates data generation instructions
    """

    def __init__(
        self,
        table_name: str,
        doc_ids: list[str],
        model_name: str = ModelConfigs.VERTEX_GEMINI_2_5_PRO.model_name,
        temperature: float = ModelConfigs.VERTEX_GEMINI_2_5_PRO.temperature,
        max_tokens: int = ModelConfigs.VERTEX_GEMINI_2_5_PRO.max_tokens,
        provider: str = ModelConfigs.VERTEX_GEMINI_2_5_PRO.provider,
        llm: LLM | None = None,
    ):
        """Initialize the agent.

        Args:
            doc_id: ID of the knowledge base document
            model: Name of the LLM model to use
            embedding_model: Model for embedding searches
            temperature: Temperature setting for LLM generation
            max_tokens: Maximum tokens for LLM response
        """
        if not doc_ids:
            raise ValueError("doc_id cannot be empty")
        self.doc_ids = doc_ids
        self.table_name = table_name
        if llm is None:
            self.llm = LLM(
                model_name=model_name,
                temperature=temperature,
                max_tokens=max_tokens,
                provider=provider,
            )
        else:
            self.llm = llm
        self.client = OpenAI()
        self.logger = logging.getLogger(__name__)
        self.db_client = ClickHouseVectorDB()
        self.rag = EmbeddingManager()

    def fetch_random_seeds(self, percentage: float) -> list[dict[str, Any]]:
        """Fetch random seeds from knowledge base."""
        total_vectors = self.db_client.get_num_vectors(self.doc_ids, self.table_name)[
            0
        ][0]
        fraction = min(percentage / 100, 1)
        if total_vectors < 20:
            limit = total_vectors
        else:
            limit = max(20, int(total_vectors * fraction))

        return self.db_client.get_random_examples(self.doc_ids, self.table_name, limit)

    def _classify_task(self, seed_data_point: dict[str, Any]) -> list[str]:
        """
        Classify examples into task categories using LLM.

        Args:
            examples: List of example data points

        Returns:
            List of category names ordered by confidence score
        """
        classification_prompt = {
            "role": "user",
            "content": TASK_CLASSIFICATION_PROMPT.format(
                categories=json.dumps([cat.value for cat in TaskCategories]),
                examples=json.dumps(seed_data_point),
            ),
        }

        try:
            response = self.llm._get_completion_content(
                messages=[classification_prompt]
            )
            response = json_repair.loads(response)
            # Return categories sorted by confidence score
            categories = [
                cat
                for cat, _ in sorted(
                    response["confidence"].items(), key=lambda x: x[1], reverse=True
                )
            ]
            return categories
        except Exception as e:
            self.logger.error(f"Task classification failed: {str(e)}")
            raise

    def _classify_seeds(
        self, seed_data_points: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Classify seeds into task categories."""

        with ThreadPoolExecutor(max_workers=5) as executor:
            categories_list = list(executor.map(self._classify_task, seed_data_points))

        # attach back into each seed_data_point exactly as before
        for seed_data_point, categories in zip(
            seed_data_points, categories_list, strict=False
        ):
            seed_data_point["__category__"] = json.dumps(categories)

        return seed_data_points

    def expand_datapoints(
        self, datapoints: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Expand datapoints into multiple datapoints."""
        final_datapoints = []
        for datapoint in datapoints:
            final_datapoints.append(datapoint)
            # Set input type to "text" since we're dealing with chunk_text
            inputs = [datapoint["chunk_text"]]
            input_cols = ["chunk_text"]

            nearest_neighbors = self.rag.retrieve_avg_rag_based_examples(
                self.doc_ids,
                inputs,  # Pass as list of inputs
                input_cols,  # Pass corresponding column names
                table_name=self.table_name,
                top_k=3,
            )

            # Process neighbors if any were returned
            if nearest_neighbors:
                for neighbor in nearest_neighbors:
                    final_datapoints.append(neighbor)

        return final_datapoints

    def _generate_instructions(
        self, seeds_data_points: list[dict[str, Any]], num_plans
    ) -> dict[str, Any]:
        """Generate detailed instructions for synthetic data generation.

        Args:
            few_shot_examples: Dictionary mapping seeds to their relevant examples

        Returns:
            Dictionary mapping seeds to their generation instructions and characteristics
        """

        pd.DataFrame(seeds_data_points)
        all_categories: dict[str, int] = {}
        for seed_data_point in seeds_data_points:
            categories = json.loads(seed_data_point["__category__"])
            for category in categories:
                all_categories[category] = all_categories.get(category, 0) + 1
        # Sort the categories by frequency
        sorted_categories = sorted(
            all_categories.items(), key=lambda x: x[1], reverse=True
        )
        category_plans: dict[Any, Any] = {}
        generated_plans = 0
        for category, _count in sorted_categories:
            characteristics = TASK_CATEGORIES[category]["dataset_characteristics"]
            # Filter datapoints where the first category matches
            category_data = []
            for seed_data_point in seeds_data_points:
                if category in json.loads(seed_data_point["__category__"]):
                    category_data.append(seed_data_point)

            category_plans[category] = {}
            category_plans[category]["datapoints"] = category_data

            remaining = num_plans + 1 - generated_plans
            to_do = characteristics[:remaining]

            # submit each characteristic in its own thread
            with ThreadPoolExecutor(max_workers=5) as executor:
                future_to_char = {
                    executor.submit(
                        self.llm._get_completion_content,
                        messages=[
                            {
                                "role": "user",
                                "content": INSTRUCTION_GENERATION_PROMPT.format(
                                    datapoints=json.dumps(category_data),
                                    category=category,
                                    characteristic=char,
                                ),
                            }
                        ],
                    ): char
                    for char in to_do
                }

                for future in as_completed(future_to_char):
                    char = future_to_char[future]
                    try:
                        resp = future.result().strip()
                    except Exception as e:
                        resp = f"<error: {e}>"
                    category_plans[category][char] = resp

                    generated_plans += 1
                    # if we've hit the global cap, bail out of both loops
                    if generated_plans >= num_plans + 1:
                        break
            if generated_plans >= num_plans + 1:
                break

        return category_plans

    def fetch_few_shots_per_datapoint(self, ins, plan_details, n=4):
        """
        Fetch few-shot examples for each instruction datapoint.

        Args:
            ins (dict): Dictionary of instructions
            plan_details (dict): Plan details containing seed datapoints
            n (int): Number of few-shot examples to fetch per instruction

        Returns:
            dict: Dictionary mapping instruction IDs to their few-shot examples
        """
        # Initialize dictionary to store few-shots for each datapoint
        few_shots_per_datapoints = {}

        # Get seed datapoints from plan details
        seed_datapoints = plan_details.get("Datapoints", [])

        if not seed_datapoints:
            return {}  # Return empty dict if no seed datapoints available

        # For each instruction
        for key, instruction in ins.items():
            # Create combined queries from instruction and each seed datapoint
            instruction_text = list(instruction.values())[0]

            queries = []
            input_col = ["chunk_text"]

            for seed_data in seed_datapoints:
                # Combine instruction with seed data chunk for relevance matching
                queries.append(seed_data)
            queries.append(instruction_text)
            _cfg = ModelConfigs.VERTEX_GEMINI_2_5_PRO
            llm_q = LLM(
                model_name=_cfg.model_name,
                temperature=_cfg.temperature,
                max_tokens=_cfg.max_tokens,
                provider=_cfg.provider,
            )
            content = []
            content.append(
                {
                    "type": "text",
                    "text": query_distillation_prompt.format(
                        instruction=instruction_text
                    ),
                }
            )
            query = llm_q._get_completion_content(
                messages=[{"role": "user", "content": content}]
            )
            try:
                query = json.loads(query)
                queries.append(query.get("Query", ""))
            except:
                print("unable to get query distilled")

            # print("THE QUERIES ARE",queries)
            input_cols = input_col * len(queries)
            rag2 = EmbeddingManager()

            nearest_neighbors = rag2.retrieve_avg_rag_based_examples(
                eval_id=self.doc_ids,
                inputs=queries,  # Pass as single-item list
                input_cols=input_cols,
                table_name=self.table_name,
                top_k=n,
                syn_data_flag=True,
            )
            few_shots_per_datapoints[str(key)] = copy.deepcopy(nearest_neighbors)

        return few_shots_per_datapoints

    def generate_plans_from_kb(
        self, payload: dict[str, Any], num_plans: int
    ) -> dict[str, Any]:
        """Generate synthetic data generation plans based on provided examples."""
        seeds = self.fetch_random_seeds(payload.get("percentage", 10))
        seed_data_points = []
        for seed in seeds:
            id_, eval_id, vector, metadata_key, metadata_value, _ = seed
            seed_data = {}
            for key, value in zip(metadata_key, metadata_value, strict=False):
                seed_data[key] = value
            seed_data_points.append(seed_data)

        seed_data_points = self._classify_seeds(seed_data_points)
        # Generate instructions using seeds and task type
        category_plans = self._generate_instructions(seed_data_points, num_plans)
        total_chunks = self.db_client.get_num_vectors(self.doc_ids, self.table_name)[0][
            0
        ]
        return {
            "seed_data_points": seed_data_points,
            "category_plans": category_plans,
            "total_chunks": total_chunks,
        }


def main():
    try:
        agent = KBSeedInstructionAgent(doc_id="example_doc")  # Need to provide doc_id

        requirements = {
            "data_type": "conversation",
            "num_samples": 10,
            "language": "English",
            "domain": "customer_service",
        }

        agent.generate_plans_from_kb({"requirements": requirements}, 10)

    except Exception as e:
        logging.error(f"Error in main: {str(e)}")
        raise


if __name__ == "__main__":
    main()
