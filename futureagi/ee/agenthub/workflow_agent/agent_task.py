import json
import traceback

from agentic_eval.core.embeddings.embeddings_v2 import get_embedding_model
from agentic_eval.core.llm.llm import LLM
from agentic_eval.core.utils.model_config import ModelConfigs
from agentic_eval.core.utils.functions import camel_or_snake_to_normal

from agentic_eval.core.database.ch_vector import ClickHouseVectorDB
from .prompts import (
    EXPLANATION_PROMPT,
    EXTRACTING_VALUE_FROM_CHAT_PROMPT,
    PARSE_DISTRIBUTION_PROMPT,
    PARSE_FILTER_PROMPT,
)


class AgentTask:
    """
    A class to handle agent tasks related to processing and analyzing chat histories.
    """

    def __init__(
        self,
        key_table_name,
        value_table_name,
        metric=None,
        parsed_metrics=None,
        metric_meta=None,
        chat_processor=False,
    ):
        """
        Initialize the AgentTask with a given metric.

        Args:
            metric (str): The metric to analyze chat histories.
        """
        if not chat_processor:
            assert metric or parsed_metrics, (
                "Either metric or parsed_metrics must be provided"
            )
        self.metric = metric
        self.key_table_name = key_table_name
        self.value_table_name = value_table_name
        self.model = get_embedding_model()
        db = ClickHouseVectorDB()
        db.create_table(self.key_table_name)
        db.create_table(self.value_table_name)
        self.db_connector = db
        self.llm = LLM(
            model_name=ModelConfigs.CLAUDE_4_5_SONNET_BEDROCK_ARN.model_name,
            temperature=ModelConfigs.CLAUDE_4_5_SONNET_BEDROCK_ARN.temperature,
            max_tokens=ModelConfigs.CLAUDE_4_5_SONNET_BEDROCK_ARN.max_tokens,
            provider=ModelConfigs.CLAUDE_4_5_SONNET_BEDROCK_ARN.provider,
        )
        if parsed_metrics:
            self.parsed_metric = parsed_metrics
        else:
            self.parsed_metric = self.parse_metric(metric)
        self.metric_meta = metric_meta or {"metadata": []}
        if not metric_meta:
            self.metric_meta = {"metadata": []}
            for filter_dict in self.parsed_metric.get("filter", []):
                if "key" not in filter_dict:
                    continue
                key_and_explanation = self.get_explanation(filter_dict["key"], metric)
                key_ = self.map_metadata_key(
                    filter_dict["key"],
                    values=filter_dict["value"],
                    explanation=key_and_explanation["explanation"],
                )
                if key_ != filter_dict["key"]:
                    filter_dict["key"] = key_
                    # TODO: Trigger metadata for this property for all chats

                self.metric_meta["metadata"].append(key_and_explanation)

            for idx, distribution_property in enumerate(
                self.parsed_metric.get("distribution_key", [])
            ):
                key_and_explanation = self.get_explanation(
                    distribution_property, metric
                )
                key_ = self.map_metadata_key(
                    distribution_property,
                    values=[],
                    explanation=key_and_explanation["explanation"],
                )
                if key_ != distribution_property:
                    distribution_property = key_
                    self.parsed_metric.get("distribution_key", [])[idx] = key_
                    # TODO: Trigger metadata for this property for all chats

                self.metric_meta["metadata"].append(key_and_explanation)
        else:
            self.metric_meta = metric_meta

    def encoder(self, text):
        encoded_list = self.model([text])[0].tolist()
        return encoded_list

    def format_conversation(self, conversation):
        """
        Format a conversation into a string representation.

        Args:
            conversation (list): List of message dictionaries.

        Returns:
            str: Formatted conversation string.
        """
        formatted_lines = []
        for message in conversation:
            role = "User" if message["role"] == "user" else "Model"
            content = message["content"]
            formatted_lines.append(f"{role}: {content}")
        return "\n".join(formatted_lines)

    def get_metric_meta(self):
        """
        Get the metadata to be extracted.

        Returns:
            dict: Metadata to be extracted.
        """
        return self.metric_meta

    def parse_metric(self, metric):
        """
        Parse the given metric into filter and breakdown components.

        Args:
            metric (str): The metric to parse.

        Returns:
            dict: Parsed metric with filter and breakdown information.
        """
        filter_data = self._parse_filter(metric)
        distribution_data = self._parse_distribution_key(metric)

        return {**filter_data, **distribution_data}

    def _parse_filter(self, metric):
        """
        Parse the filter component of the metric.

        Args:
            metric (str): The metric to parse.

        Returns:
            dict: Parsed filter information or empty dict if no filter.
        """

        processed_prompt = PARSE_FILTER_PROMPT.format(metric_input=metric.strip())
        filters = json.loads(
            self.llm._get_completion_content(
                messages=[{"role": "user", "content": processed_prompt}]
            )
        )
        for metadata_dict in filters.get("filter", []):
            metadata_dict["value"] = [
                camel_or_snake_to_normal(v.lower()) for v in metadata_dict.pop("values")
            ]
            metadata_dict["key"] = camel_or_snake_to_normal(
                metadata_dict["key"].lower()
            )
        return filters

    def _parse_distribution_key(self, metric):
        """
        Parse the distribution key component of the metric.

        Args:
            metric (str): The metric to parse.

        Returns:
            dict: Parsed distribution key information or empty dict if no distribution key.
        """
        processed_prompt = PARSE_DISTRIBUTION_PROMPT.format(metric_input=metric.strip())
        distribution_keys = json.loads(
            self.llm._get_completion_content(
                messages=[
                    {"role": "user", "content": processed_prompt},
                ]
            )
        )

        if "distribution_key" in distribution_keys:
            distribution_keys["distribution_key"] = [
                camel_or_snake_to_normal(v.lower())
                for v in distribution_keys["distribution_key"]
            ]
        else:
            return {}

        return distribution_keys

    def get_explanation(self, metric_key, metric):
        """
        Get an explanation for a given metric key.

        Args:
            metric_key (str): The metric key to explain.
            metric (str): The original metric string.

        Returns:
            dict: Explanation for the metric key.
        """
        messages = [
            {
                "role": "user",
                "content": EXPLANATION_PROMPT.format(
                    metric_key=metric_key, metric=metric
                ),
            }
        ]
        explanations = json.loads(self.llm._get_completion_content(messages=messages))
        return explanations

    def process_chat_meta_single_key(self, key_dict, chat_text):
        key = key_dict["key"]
        explanation = key_dict["explanation"]
        messages = [
            {
                "role": "user",
                "content": EXTRACTING_VALUE_FROM_CHAT_PROMPT.format(
                    key=key, explanation=explanation, chat_text=chat_text
                ),
            }
        ]
        try:
            new_value = json.loads(self.llm._get_completion_content(messages=messages))
            new_value = [camel_or_snake_to_normal(v.lower()) for v in new_value[key]]
        except json.JSONDecodeError:
            traceback.print_exc()
            new_value = []

        if len(new_value) == 0:
            return {key: []}

        elif len(new_value) > 0:
            encoded_new_value = self.encoder(new_value[0])
            existing_values = self.db_connector.vector_similarity_search(
                table_name=self.value_table_name,
                query_vector=encoded_new_value,
                filter_by={"type": "value", "key": key},
                top_k=1,
            )
            if len(existing_values) > 0 and existing_values[0][3] < 0.2:
                existing_value = existing_values[0][2]["value"]
                return {key: [existing_value]}
            else:
                existing_value = None

            if not existing_value:
                self.db_connector.upsert_vector(
                    self.value_table_name,
                    encoded_new_value,
                    {"value": new_value[0], "key": key, "type": "value"},
                    ["key", "value"],
                )

                return {key: new_value}

        return {key: []}

    def process_chat_metadata(self, chat, chat_keys):
        """
        Process metadata for a given chat.

        Args:
            chat (list): List of chat messages.

        Returns:
            dict: Processed metadata for the chat.
        """
        chat_text = self.format_conversation(chat)
        meta_data = {}
        for key_dict in chat_keys:
            m = self.process_chat_meta_single_key(key_dict, chat_text)

            for m_k, m_value in m.items():
                meta_data[m_k] = m_value

        return meta_data

    def process_additional_query(self, query):
        """
        Process an additional query and update the parsed metric.

        Args:
            query (str): The additional query to process.

        Returns:
            dict: Updated parsed metric.
        """
        parsed_metrics = self.parse_metric(query)
        for metadata_dict in parsed_metrics.get("filter", []):
            key = metadata_dict["key"]
            key_and_explanation = self.get_explanation(key, query)
            mapped_key = self.map_metadata_key(
                key,
                values=metadata_dict["value"],
                explanation=key_and_explanation["explanation"],
            )
            if mapped_key != key:
                metadata_dict["key"] = mapped_key

            self.metric_meta["metadata"].append(key_and_explanation)

        for idx, distribution_property in enumerate(
            parsed_metrics.get("distribution_key", [])
        ):
            key_and_explanation = self.get_explanation(distribution_property, query)
            key_ = self.map_metadata_key(
                distribution_property,
                values=[],
                explanation=key_and_explanation["explanation"],
            )
            if key_ != distribution_property:
                distribution_property = key_
                parsed_metrics.get("distribution_key", [])[idx] = key_

            self.metric_meta["metadata"].append(key_and_explanation)

        # TODO Trigger metadata for this property for all chats

        self.parsed_metric = self.merge_dictionaries(self.parsed_metric, parsed_metrics)
        return self.parsed_metric

    def upsert_values(self, key, values):
        for value in values:
            encoded_value = self.encoder(value)
            similar_values = self.db_connector.vector_similarity_search(
                self.value_table_name,
                encoded_value,
                filter_by={"key": key},
                top_k=1,
            )
            if len(similar_values) == 0 or similar_values[0][3] > 0.2:
                self.db_connector.upsert_vector(
                    self.value_table_name,
                    encoded_value,
                    {"value": value, "key": key, "type": "value"},
                    unique_keys=["key", "value"],
                )

    def map_metadata_key(self, key, values=None, explanation=None):
        """
        Map a metadata key to an existing key or create a new one.

        Args:
            key (str): The metadata key to map.
            values (list): Optional list of values for the key.
            explanation (str): Optional explanation for the key.

        Returns:
            str: Mapped or new metadata key.
        """
        if values is None:
            values = []
        encoded_explanation = self.encoder(explanation)
        results = self.db_connector.vector_similarity_search(
            self.key_table_name, encoded_explanation, top_k=1
        )
        if len(results) > 0 and results[0][3] < 0.2:
            self.upsert_values(results[0][2]["key"], values)
            return results[0][2]["key"]
        else:
            self.upsert_values(key, values)
            self.db_connector.upsert_vector(
                self.key_table_name,
                encoded_explanation,
                {"key": key, "explanation": explanation, "type": "key"},
                unique_keys=["key"],
            )
            return key

    def merge_dictionaries(self, dict1, dict2):
        # Initialize the result dictionary with the structure of dict1
        result = dict1.copy()

        # Merge the 'filter' key
        if "filter" in dict2:
            if "filter" not in result:
                result["filter"] = []

            # For each filter in dict2, check if a filter with the same key exists in result
            for new_filter in dict2["filter"]:
                existing_filter = next(
                    (f for f in result["filter"] if f["key"] == new_filter["key"]),
                    None,
                )

                if existing_filter:
                    # If it exists, extend the values list
                    existing_filter["value"].extend(new_filter["value"])
                    # Remove duplicates from values list
                    existing_filter["value"] = list(set(existing_filter["value"]))
                else:
                    # If it doesn't exist, append the new filter
                    result["filter"].append(new_filter)

        # Merge the 'distribution_key'
        if "distribution_key" in dict2:
            if "distribution_key" not in result:
                result["distribution_key"] = []
            result["distribution_key"].extend(dict2["distribution_key"])
            # Remove duplicates from distribution_key list
            result["distribution_key"] = list(set(result["distribution_key"]))

        return result


def test_agent_task():
    # Sample chat history
    sample_chat = [
        {
            "role": "user",
            "content": "Hola! Puedes ayudarme con un problema de Python?",
        },
        {
            "role": "assistant",
            "content": "¡Claro! Estaré encantado de ayudarte con tu problema de Python. ¿Puedes describir el problema que estás enfrentando?",
        },
        {
            "role": "user",
            "content": "Estoy tratando de crear una función para calcular el factorial de un número, pero estoy obteniendo un error.",
        },
        {
            "role": "assistant",
            "content": "Entiendo. Vamos a resolver esto paso a paso. Primero, ¿podrías mostrarme el código que has escrito hasta ahora para la función factorial?",
        },
        {
            "role": "user",
            "content": "Aquí está mi código:\n\ndef factorial(n):\n    if n == 0:\n        return 1\n    else:\n        return n * factorial(n-1)\n\nprint(factorial(-5))",
        },
        {
            "role": "assistant",
            "content": "Gracias por compartir tu código. He identificado el problema. Tu función factorial está bien definida para números no negativos, pero estás intentando calcular el factorial de un número negativo (-5), lo cual no está definido matemáticamente.\n\nAquí hay una versión mejorada de tu función que maneja este caso:",
        },
    ]

    metric = "I want to find the chats where the user is talking to the assistant in Spanish. Show the distribution of programming language of those chats."

    agent_task = AgentTask(
        metric=metric,
        key_table_name="insights_key",
        value_table_name="insights_value",
    )

    chat_keys = list(agent_task.get_metric_meta()["metadata"])

    agent_task.process_chat_metadata(sample_chat, chat_keys)

    chat_keys = list(agent_task.get_metric_meta()["metadata"])

    # processed_metadata = agent_task.process_chat_metadata(sample_chat, chat_keys)


if __name__ == "__main__":
    test_agent_task()
