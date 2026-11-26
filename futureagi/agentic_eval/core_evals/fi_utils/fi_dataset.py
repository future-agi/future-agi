from dataclasses import dataclass, field
from typing import Any

from agentic_eval.core_evals.llm_services.fi_api_service import FiApiService


@dataclass
class DatasetRow:
    query: str | None = None
    context: list[str] | None = None
    response: str | None = None
    expected_response: str | None = None


@dataclass
class Dataset:
    id: str
    source: str
    name: str
    description: str | None = None
    language_model_id: str | None = None
    prompt_template: Any | None = None
    rows: list[DatasetRow] = field(default_factory=list)

    @staticmethod
    def create(
        name: str,
        description: str | None = None,
        language_model_id: str | None = None,
        prompt_template: Any | None = None,
        rows: list[DatasetRow] | None = None,
    ):
        """
        Creates a new dataset with the specified properties.
        Parameters:
        - name (str): The name of the dataset. This is a required field.
        - description (Optional[str]): An optional textual description of the dataset, providing additional context.
        - language_model_id (Optional[str]): An optional identifier for the language model associated with this dataset.
        - prompt_template (Optional[Any]): An optional template for prompts used in this dataset.

        Returns:
        The newly created dataset object

        Raises:
        - Exception: If the dataset could not be created due to an error like invalid parameters, database errors, etc.
        """
        dataset_data = {
            "source": "dev_sdk",
            "name": name,
            "description": description,
            "language_model_id": language_model_id,
            "prompt_template": prompt_template,
            "dataset_rows": rows or [],
        }

        # Remove keys where the value is None
        dataset_data = {k: v for k, v in dataset_data.items() if v is not None}
        try:
            created_dataset_data = FiApiService.create_dataset(dataset_data)
        except Exception:
            raise
        dataset = Dataset(
            id=created_dataset_data["id"],
            source=created_dataset_data["source"],
            name=created_dataset_data["name"],
            description=created_dataset_data["description"],
            language_model_id=created_dataset_data["language_model_id"],
            prompt_template=created_dataset_data["prompt_template"],
        )
        return dataset

    @staticmethod
    def add_rows(dataset_id: str, rows: list[DatasetRow]):
        """
        Adds rows to a dataset in batches of 100.

        Parameters:
        - dataset_id (str): The ID of the dataset to add rows to.
        - rows (List[DatasetRow]): The rows to add to the dataset.

        Raises:
        - Exception: If the API returns an error or the limit of 1000 rows is exceeded.
        """
        batch_size = 100
        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            # Convert DatasetRow objects to dictionaries
            batch_dicts: list[dict[Any, Any]] = [
                {k: v for k, v in vars(row).items() if v is not None} for row in batch
            ]
            try:
                FiApiService.add_dataset_rows(dataset_id, batch_dicts)
            except Exception:
                raise

    @staticmethod
    def fetch_dataset_rows(dataset_id: str, number_of_rows: int | None = None):
        """
        Fetches the rows of a dataset.

        Parameters:
        - dataset_id (str): The ID of the dataset to fetch rows.
        """
        return FiApiService.fetch_dataset_rows(dataset_id, number_of_rows)

    @staticmethod
    def dataset_link(dataset_id: str):
        return f"https://app.fi.ai/develop/{dataset_id}"
