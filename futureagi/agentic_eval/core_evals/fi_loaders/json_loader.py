import json

from ..fi_utils.evals_result import DataPoint
from ..fi_utils.fi_interfaces import FiFilters
from .base_loader import BaseLoader


class JsonLoader(BaseLoader):
    """
    This class is a data loader for json evals

    Attributes:
        col_actual_json (dict or str): The column name corresponding to the actual JSON.
        col_expected_json (dict or str): The column name corresponding to the expected JSON.
        raw_dataset (dict): The raw dataset as loaded from the source.
        processed_dataset (list): The processed dataset with responses.
    """

    def __init__(
        self,
        col_actual_json: str = "actual_json",
        col_expected_json: str = "expected_json",
    ):
        """
        Initializes the loader with specified or default column names.
        """
        self.col_actual_json = col_actual_json
        self.col_expected_json = col_expected_json
        self._raw_dataset = {}
        self._processed_dataset: list[DataPoint] = []

    def process(self) -> None:  # type: ignore[override]
        """# type: ignore[override]
        Transforms the raw data into a structured format. Processes each entry from the raw dataset, and extracts attributes.

        Raises:
            KeyError: If mandatory columns (response) are missing in the raw dataset.
        """
        for raw_instance in self._raw_dataset:
            # Check for mandatory columns in raw_instance
            if self.col_actual_json not in raw_instance:
                raise KeyError(f"'{self.col_actual_json}' not found in provided data.")
            # Create a processed instance with mandatory fields
            processed_instance = {
                # if self.col_actual_json is string then do a json load
                "actual_json": json.loads(raw_instance[self.col_actual_json])
                if isinstance(raw_instance[self.col_actual_json], str)
                else raw_instance[self.col_actual_json]
            }
            if self.col_expected_json in raw_instance:
                processed_instance["expected_json"] = (
                    json.loads(raw_instance[self.col_expected_json])
                    if isinstance(raw_instance[self.col_expected_json], str)
                    else raw_instance[self.col_expected_json]
                )
            # removing keys with None values
            processed_instance = {
                k: v for k, v in processed_instance.items() if v is not None
            }
            # Store the results
            self._processed_dataset.append(processed_instance)

    def load_fi_inferences(  # type: ignore[override]
        self,
        filters: FiFilters | None = None,
        limit: int | None = None,
    ):
        """
        Load data from FI API.
        """
        raise NotImplementedError(
            "This loader does not support loading data from FI API."
        )
