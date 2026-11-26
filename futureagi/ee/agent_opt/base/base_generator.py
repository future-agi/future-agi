from abc import ABC, abstractmethod
from typing import Dict


class BaseGenerator(ABC):
    """
    Abstract base class for all Generators. A Generator is a callable entity
    (like an LLM) that executes a prompt and returns a result. It also manages
    an internal prompt template that can be modified by an optimizer.
    """

    @abstractmethod
    def generate(self, prompt_vars: Dict[str, str], **kwargs) -> str:
        """
        Executes the generator with a given set of input variables.

        Args:
            prompt_vars: A dictionary of variables to fill in the prompt template.

        Returns:
            The string output from the language model.
        """
        pass

    @abstractmethod
    def get_prompt_template(self) -> str:
        """Returns the current internal prompt template."""
        pass

    @abstractmethod
    def set_prompt_template(self, template: str):
        """Updates the internal prompt template."""
        pass
