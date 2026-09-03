import json
import json_repair
import pandas as pd
from typing import Dict, Any, Optional, List, Union, Tuple
from datetime import datetime
from enum import Enum
import json
import asyncio
from agentic_eval.core.llm.llm import LLM
from agentic_eval.core.utils.model_config import ModelConfigs
from ee.agenthub.synthetic_data_agent.prompts import (
    INSTRUCTION_GENERATION_PROMPT,
    REQUIREMENTS_INFERENCE_PROMPT,
    TASK_CLASSIFICATION_PROMPT,
)
from .taxonomy import TASK_CATEGORIES
from pydantic import BaseModel, Field
import structlog

logger = structlog.get_logger(__name__)

NUM_EXAMPLES = 10


class TaskCategories(str, Enum):
    READING_COMPREHENSION = "Reading comprehension"
    TEXT_MODIFICATION = "Text modification"
    CODING = "Coding"
    RAG = "Retrieval-augmented generation (RAG)"
    CREATIVE_WRITING = "Creative writing"
    TOOL_API_USAGE = "Tool/API usage"
    ANALYTICAL_REASONING = "Analytical reasoning"
    BRAIN_TEASERS = "Brain teasers"
    DATA_TO_TEXT = "Data-to-text generation"
    TEXT_CLASSIFICATION = "Text classification and extraction"
    FEW_SHOT_REASONING = "Few-shot reasoning"
    CONVERSATIONAL = "Conversational tasks"
    EMOTIONAL_INTELLIGENCE = "Emotional intelligence evaluation"
    CONTENT_SAFETY = "Content Safety evaluation"


class TaskCategoryResponse(BaseModel):
    categories: List[TaskCategories] = Field(
        ..., description="List of task categories that match the examples", min_length=1
    )
    confidence: Dict[TaskCategories, float] = Field(
        description="Confidence score for each category (0-1)", default_factory=dict
    )
    reasoning: Optional[str] = Field(
        description="Explanation for why these categories were chosen", default=None
    )


class SeedInstructionAgent:
    """Agent for generating synthetic data based on seed examples and task categories."""

    def __init__(
        self,
        model_name: str = ModelConfigs.VERTEX_GEMINI_2_5_PRO.model_name,
        temperature: float = ModelConfigs.VERTEX_GEMINI_2_5_PRO.temperature,
        max_tokens: int = ModelConfigs.VERTEX_GEMINI_2_5_PRO.max_tokens,
        provider: str = ModelConfigs.VERTEX_GEMINI_2_5_PRO.provider,
        llm: Optional[LLM] = None,
    ):
        """
        Initialize the synthetic data agent.

        Args:
            model_name: Name of the LLM model to use
            temperature: Temperature setting for LLM generation
            max_tokens: Maximum tokens for LLM response
            llm: Optional pre-configured LLM instance
        """
        if llm:
            self.llm = llm
        else:
            self.llm = LLM(
                model_name=model_name,
                temperature=temperature,
                max_tokens=max_tokens,
                provider=provider,
            )

    async def infer_payload(
        self, payload: Dict[str, Any], df: pd.DataFrame
    ) -> Dict[str, Any]:
        payload["requirements"] = await self._infer_requirements(
            df, payload["requirements"]
        )

        if "constraints" not in payload:
            payload["constraints"] = await self._infer_constraints(
                df, json.dumps(payload["requirements"])
            )
        if "schema" not in payload:
            payload["schema"] = self._infer_schema(payload["constraints"])

        return payload

    async def generate_plans_from_examples(
        self, payload: Dict[str, Any], df: pd.DataFrame, num_plans: int
    ) -> Tuple[pd.DataFrame, Dict[str, Any]]:
        """Generate synthetic data generation plans based on provided examples.

        Args:
            payload: Dictionary containing schema, requirements, and constraints
            df: DataFrame containing example data

        Returns:
            Tuple containing:
                - DataFrame with classified examples
                - Dictionary of generation plans by category

        Raises:
            ValueError: If required fields are missing from payload
        """
        # Classify the datapoints

        row_count = len(df.index)

        if row_count < 20:
            df = await self._classify_datapoints(df)
        else:
            sample_size = max(20, int(0.1 * row_count))
            sample_df = df.sample(n=sample_size, random_state=42)
            df = await self._classify_datapoints(sample_df)

        # Generate instructions
        category_plans = await self._generate_instructions(df, num_plans)

        return df, category_plans

    async def _generate_instructions(
        self, df: pd.DataFrame, num_plans: int
    ) -> Dict[str, Any]:
        """Generate detailed instructions for synthetic data generation.

        Args:
            df: DataFrame containing classified example data

        Returns:
            Dictionary mapping categories to their generation instructions and characteristics
        """
        # Convert the lists in __category__ column to tuples and flatten them
        all_categories: Dict[str, int] = {}
        for categories in df["__category__"]:
            categories_list = json.loads(categories)
            # Only consider the first category from each row
            if categories_list and categories_list[0] in TASK_CATEGORIES:
                all_categories[categories_list[0]] = (
                    all_categories.get(categories_list[0], 0) + 1
                )

        # Sort the categories by frequency
        sorted_categories: List[Tuple[str, int]] = sorted(
            all_categories.items(), key=lambda x: x[1], reverse=True
        )
        # datapoint
        # column1, column2, column3, category
        #     x,    y,      z,   , [category1, category2, category3]
        category_plans: Dict[str, Any] = {}
        generated_plans = 0
        for category, count in sorted_categories:
            characteristics = TASK_CATEGORIES[category]["dataset_characteristics"]
            # Filter datapoints where the first category matches
            datapoints = df[
                df["__category__"].apply(lambda x: category in json.loads(x))
            ]
            category_plans[category] = {}
            category_plans[category]["datapoints"] = datapoints.to_dict(
                orient="records"
            )

            remaining = num_plans + 1 - generated_plans
            to_do = characteristics[:remaining]

            tasks = [
                self.llm._get_completion_content_async(
                    messages=[
                        {
                            "role": "user",
                            "content": INSTRUCTION_GENERATION_PROMPT.format(
                                datapoints=json.dumps(
                                    datapoints.to_dict(orient="records")
                                ),
                                category=category,
                                characteristic=char,
                            ),
                        }
                    ],
                    thinking_budget=200,
                )
                for char in to_do
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for i, result in enumerate(results):
                char = to_do[i]
                if isinstance(result, Exception):
                    resp = f"<error: {result}>"
                else:
                    resp = str(result).strip()
                category_plans[category][char] = resp

                generated_plans += 1
                # if we've hit the global cap, bail out of both loops
                if generated_plans >= num_plans + 1:
                    break

            if generated_plans >= num_plans + 1:
                break

        return category_plans

    async def _classify_datapoints(self, df: pd.DataFrame) -> pd.DataFrame:
        """Classify each datapoint into task taxonomy categories in parallel.

        Args:
            df: DataFrame containing example data

        Returns:
            DataFrame with new '__category__' column containing classified categories
        """
        rows = [row for _, row in df.iterrows()]
        tasks = [self._classify_datapoint(row) for row in rows]
        results = await asyncio.gather(*tasks)
        df["__category__"] = results
        return df

    async def _classify_datapoint(self, row: Any) -> str:
        """Classify a single datapoint into task categories.

        Args:
            row: pandas Series containing a single datapoint's information

        Returns:
            JSON string containing ordered list of matching categories
        """

        # Get the datapoint - row is a pd.Series
        if hasattr(row, "to_dict"):
            datapoint = row.to_dict()
        else:
            datapoint = dict(row)

        # convert to text
        datapoint_text = json.dumps(datapoint)

        # Get the categories
        categories = await self._classify_task(datapoint_text)

        return json.dumps(categories)

    async def _classify_task(self, examples_text: str) -> List[str]:
        """Classify examples into task categories using LLM.

        Args:
            examples_text:  string containing example data

        Returns:
            List of category names ordered by confidence score

        Raises:
            Exception: If classification fails
        """
        classification_prompt = {
            "role": "user",
            "content": TASK_CLASSIFICATION_PROMPT.format(
                categories=json.dumps([cat.value for cat in TaskCategories]),
                examples=examples_text,
            ),
        }
        try:
            response = await self.llm._get_completion_content_async(
                messages=[classification_prompt],
                disable_thinking=True,
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
            raise e  # Default fallback

    def _infer_schema(
        self, constraints: List[Dict[str, Any]]
    ) -> Dict[str, Dict[str, str]]:
        """Infer data schema from example DataFrame.

        Args:
            df: DataFrame to analyze

        Returns:
            Dictionary mapping field names to their inferred data types and properties
        """
        schema = {}
        for constraint in constraints:
            schema[constraint["field"]] = {"type": constraint["type"]}

        return schema

    async def _infer_constraints(
        self, df: pd.DataFrame, requirements: str
    ) -> List[Dict[str, Any]]:
        """Infer data constraints from example DataFrame using LLM analysis in parallel.

        Args:
            df: DataFrame to analyze
            requirements: string containing requirements

        Returns:
            List of dictionaries containing field constraints including:
                - Allowed values
                - Length constraints for text
                - Language information
                - Data type information
                - Additional inferred properties and patterns
        """
        # Convert sample of DataFrame to string format for LLM input
        sample_size = min(NUM_EXAMPLES, len(df))
        dataset_str = df.head(sample_size).to_string()

        async def process_column(column: str) -> Dict[str, Any]:
            """Process a single column to infer its constraints."""
            description = await self.llm._get_completion_content_async(
                messages=[
                    {
                        "role": "user",
                        "content": f"Describe the column {column} in a few words. The requirements are {requirements}, the dataset is {dataset_str}",
                    }
                ],
                disable_thinking=True,
            )

            constraint = {}
            # inferring type
            type_ = "string"
            ratio = df[column].nunique() / len(df)
            if ratio < 0.5:
                type_ = "categorical"

            constraint["field"] = column
            constraint["type"] = type_
            constraint["content"] = description
            constraint["values"] = (
                df[column].unique().tolist() if type_ == "categorical" else "dynamic"
            )
            constraint["language"] = "English"

            # Only calculate string lengths for string/object columns
            if type_ == "string" and df[column].dtype == "object":
                # Filter out non-string values before calculating lengths
                string_values = df[column][
                    df[column].apply(lambda x: isinstance(x, str))
                ]
                if not string_values.empty:
                    constraint["min_length"] = str(string_values.str.len().min())
                    constraint["max_length"] = str(string_values.str.len().max())
                else:
                    # If no string values found, set defaults
                    constraint["min_length"] = str(0)
                    constraint["max_length"] = str(0)

            return constraint

        try:
            tasks = [process_column(col) for col in df.columns]
            constraints = await asyncio.gather(*tasks)
            return constraints
        except Exception as e:
            logger.error(f"LLM constraint inference failed: {str(e)}")
            # Fall back to original constraint inference method
            raise ValueError(f"Failed to infer constraints: {str(e)}")

    async def _infer_requirements(
        self, df: pd.DataFrame, requirements: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Infer requirements from example DataFrame using LLM analysis in parallel.

        Args:
            requirements: dictionary containing requirements

        Returns:
            dictionary containing inferred requirements
        """
        keys_to_infer = ["Dataset Description", "Objective", "patterns"]
        keys = [k for k in keys_to_infer if k not in requirements]

        if not keys:
            return requirements

        sample_size = min(NUM_EXAMPLES, len(df))
        dataset_str = df.head(sample_size).to_string()

        async def infer_key_value(key: str) -> tuple[str, str]:
            """Infer value for a single requirement key."""
            value = await self.llm._get_completion_content_async(
                messages=[
                    {
                        "role": "user",
                        "content": REQUIREMENTS_INFERENCE_PROMPT.format(
                            key=key, dataset_str=dataset_str
                        ),
                    }
                ],
                thinking_budget=200,
            )
            return key, value

        tasks = [infer_key_value(key) for key in keys]
        results = await asyncio.gather(*tasks)
        for key, value in results:
            requirements[key] = value

        return requirements
