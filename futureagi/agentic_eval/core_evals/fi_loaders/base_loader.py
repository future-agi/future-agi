import json
from abc import ABC, abstractmethod
from enum import Enum

import structlog

from agentic_eval.core_evals.fi_utils.evals_result import DataPoint

logger = structlog.get_logger(__name__)


class LoadFormat(Enum):
    """Supported load formats."""

    JSON = "json"
    JSONL = "jsonl"
    DICT = "dict"
    FI = "fi"


class BaseLoader(ABC):
    """Abstract base class for data loaders."""

    @property
    def processed_dataset(self) -> list[DataPoint]:
        """
        Returns the processed dataset.
        """
        return self._processed_dataset  # type: ignore[attr-defined,no-any-return]

    @property
    def raw_dataset(self):
        """
        Returns the raw dataset.
        """
        return self._raw_dataset

    @abstractmethod
    def process(self) -> list[DataPoint]:
        """Prepare dataset to be consumed by evaluators."""
        pass

    def load(self, format: str, **kwargs) -> list[DataPoint]:
        """
        Loads data based on the format specified.
        """
        if format == LoadFormat.JSON.value:
            return self.load_json(**kwargs)
        elif format == LoadFormat.JSONL.value:
            return self.load_jsonl(**kwargs)
        elif format == LoadFormat.DICT.value:
            return self.load_dict(**kwargs)
        elif format == LoadFormat.FI.value:
            return self.load_fi_inferences(**kwargs)
        else:
            raise NotImplementedError("This file format has not been supported yet.")

    def load_json(self, filename: str) -> list[DataPoint]:
        """
        Loads and processes data from a JSON file.

        Raises:
            FileNotFoundError: If the specified JSON file is not found.
            json.JSONDecodeError: If there's an issue decoding the JSON.
        """
        try:
            with open(filename) as f:
                self._raw_dataset = json.load(f)
                self.process()
                return self._processed_dataset  # type: ignore[attr-defined,no-any-return]
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.error(f"Error loading JSON: {e}")
            raise

    def load_jsonl(self, filename: str) -> list[DataPoint]:
        """
        Loads and processes data from a JSON Lines file.

        Raises:
            FileNotFoundError: If the specified JSONL file is not found.
            json.JSONDecodeError: If a non-blank line is not valid JSON.
        """
        try:
            raw_dataset = []
            with open(filename) as f:
                for line_number, line in enumerate(f, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        raw_dataset.append(json.loads(line))
                    except json.JSONDecodeError as e:
                        raise json.JSONDecodeError(
                            f"{e.msg} at JSONL line {line_number}",
                            e.doc,
                            e.pos,
                        ) from e

            self._raw_dataset = raw_dataset
            self.process()
            return self._processed_dataset  # type: ignore[attr-defined,no-any-return]
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.error(f"Error loading JSONL: {e}")
            raise

    def load_dict(self, data: list) -> list[DataPoint]:
        """
        Loads and processes data from a list of dictionaries.
        """
        self._raw_dataset = data
        self.process()
        return self._processed_dataset  # type: ignore[attr-defined,no-any-return]

    @abstractmethod
    def load_fi_inferences(self, data: dict) -> list[DataPoint]:
        """
        Loads and processes data from a dictionary of FI inferences.
        """
        pass
