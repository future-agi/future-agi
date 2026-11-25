from agentic_eval.core_evals.fi_utils.fi_interfaces import FiFilters


class ConversationLoader:
    """
    This class is a data loader for conversation data

    Attributes:
        raw_dataset: The raw dataset as loaded from the source.
        processed_dataset: The processed dataset is the list of strings
    """

    def __init__(
        self,
    ):
        """
        Initializes the loader with specified or default column names.
        """
        self._raw_dataset = {}
        self._processed_dataset = []

    def load_fi_inferences(
        self,
        filters: FiFilters | None = None,
        limit: int = 10,
        context_key: str | None = None,
    ):
        """
        Load data from FI API.
        """
        pass

    def load_from_string_array(self, strings: list[str]):
        """
        Loads data from a list of strings.

        :param strings: List of strings to be loaded.
        """
        if strings is None or not all(isinstance(s, str) for s in strings):
            raise ValueError("Input must be a list of strings")

        self._processed_dataset.extend(strings)

    def load_from_openai_messages(self, messages: list[list[dict[str, str]]]):
        """
        Processes and loads data from an array of lists containing messages.

        :param messages: Array of lists of messages with roles and content.
        """
        if not all(isinstance(msg_list, list) for msg_list in messages):
            raise ValueError("Input must be an array of lists")

        for msg_list in messages:
            for msg in msg_list:
                if (
                    not isinstance(msg, dict)
                    or "role" not in msg
                    or "content" not in msg
                ):
                    raise ValueError(
                        "Each message must be a dict with 'role' and 'content' keys"
                    )
                prefix = "AI: " if msg["role"] == "assistant" else "User: "
                self._processed_dataset.append(prefix + msg["content"])
