import traceback
import uuid
from typing import Any

from agentic_eval.core.utils.model_config import ModelConfigs
from ee.agenthub.eval_recommendation.prompt import RECOMMENDATION_PROMPT
from agentic_eval.core.llm.llm import LLM
from agentic_eval.core.utils.json_utils import extract_dict_from_string
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.utils.evals import EVAL_DESCRIPTION, USE_CASES, evals_template


class EvalRecommender:
    def __init__(self, dataset_id: str):
        self.evals = [
            {"category": eval["eval_tags"], "name": eval["name"]}
            for eval in evals_template
        ]
        self.evals_description = EVAL_DESCRIPTION
        self.dataset_id = dataset_id
        self.model_config = ModelConfigs.CLAUDE_4_5_SONNET_BEDROCK_ARN
        self.llm = LLM(
            model_name=self.model_config.model_name,
            temperature=self.model_config.temperature,
            max_tokens=self.model_config.max_tokens,
            provider=self.model_config.provider,
        )
        self.dataset = Dataset.objects.get(id=self.dataset_id)
        self.eval_categories = {
            "text": ["TEXT", "SAFETY", "HALLUCINATION"],
            "conversation": ["CONVERSATION"],
            "rag": ["RAG"],
            "function": ["FUNCTION"],
            "image": ["IMAGE"],
            "audio": ["AUDIO"],
            "safety": ["SAFETY"],
            "custom": ["CUSTOM"],
            "llms": ["LLMS"],
        }

        # Get columns efficiently using in_bulk and preserve order
        columns = Column.objects.filter(
            id__in=self.dataset.column_order, deleted=False
        ).in_bulk()

        # Preserve column_order by iterating through it
        self.column_name = [
            columns[uuid.UUID(col_id)].name
            for col_id in self.dataset.column_order
            if uuid.UUID(col_id) in columns
        ]

        # Get first row efficiently with only needed fields
        first_row = Row.objects.filter(
            dataset=self.dataset
        ).only("id").order_by("order").first()

        # Get row data in correct order
        if first_row:
            # Fetch cells and create dict in one query using values()
            cells_values = Cell.objects.filter(
                row=first_row,
                column_id__in=self.dataset.column_order,
                column__deleted=False
            ).values("column_id", "value")

            # Create a dict for O(1) lookups instead of complex Case/When
            cells_dict = {str(cell["column_id"]): cell["value"] for cell in cells_values}

            # Preserve column_order
            self.row_data = [
                cells_dict.get(col_id, None)
                for col_id in self.dataset.column_order
            ]
        else:
            self.row_data = []

    def recommend_evals(self, top_n: int = 5) -> list[dict[str, Any]]:
        """
        Recommend evals based on column name and row data.

        Args:
            column_name: Name of the column
            row data: List of data points in the column
            top_n: Number of top recommendations to return

        Returns:
            List of recommended evals with scores and reasons
        """

        prompt = RECOMMENDATION_PROMPT.format(
            column_name=self.column_name,
            row_data=self.row_data,
            evals=self.evals,
            evals_description=self.evals_description,
            use_cases=USE_CASES,
            top_n=top_n,
        )

        messages = [{"role": "user", "content": prompt}]
        try:
            response = self.llm._get_completion_content(
                messages=messages,
            )
            return extract_dict_from_string(response)
        except Exception:
            traceback.print_exc()
            return {
                "recommended_evals": [
                    "Deterministic Evals",
                    "Eval Output",
                    "Agent as a Judge",
                ]
            }
