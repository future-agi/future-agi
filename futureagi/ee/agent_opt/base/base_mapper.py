from abc import ABC, abstractmethod
from typing import Dict, Any


class BaseDataMapper(ABC):
    """
    Abstract base class for Data Mappers. A Data Mapper is responsible for
    transforming data into the format expected by an evaluator.
    """

    @abstractmethod
    def map(
        self, generated_output: str, ground_truth_example: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Maps the generated output and a ground truth example to the format
        expected by the evaluator's `inputs`.

        Args:
            generated_output: The output from the Generator.
            ground_truth_example: A single example from the dataset.

        Returns:
            A dictionary formatted for the evaluator's `inputs` argument.
        """
        pass
