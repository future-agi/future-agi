import json
import logging
import uuid

from agentic_eval.core.utils.model_config import ModelConfigs
from agentic_eval.core.utils.functions import camel_or_snake_to_normal

from ee.agenthub.tags_generation_agent.prompt import tag_for_reason_prompt_v2
from agentic_eval.core.database.ch_vector import ClickHouseVectorDB
from agentic_eval.core.embeddings.embeddings_v2 import get_embedding_model
from agentic_eval.core.llm.llm import LLM


class TagsV2:
    def __init__(
        self,
        model_name=ModelConfigs.CLAUDE_4_5_SONNET_BEDROCK_ARN.model_name,
        temperature=ModelConfigs.CLAUDE_4_5_SONNET_BEDROCK_ARN.temperature,
        max_tokens=ModelConfigs.CLAUDE_4_5_SONNET_BEDROCK_ARN.max_tokens,
        provider=ModelConfigs.CLAUDE_4_5_SONNET_BEDROCK_ARN.provider,
    ):
        self.llm = LLM(
            model_name=model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            provider=provider,
        )

        self.embedding_fn = get_embedding_model()
        self.vector_db = ClickHouseVectorDB()
        self.db_name = "all_tags"
        self.property_db_name = self.db_name + "_properties"
        self.tag_db_name = self.db_name
        self.vector_db.create_table(self.property_db_name)
        self.vector_db.create_table(self.tag_db_name)

    def add_property(self, metric_id: str, property: str):
        id = str(uuid.uuid4())
        vector = [
            float(x)
            for x in self.embedding_fn([["Represent the following text:", property]])[0]
        ]
        metadata = {"property": property, "metric_id": metric_id}
        self.vector_db.upsert_vector(
            self.property_db_name,
            vector,
            metadata,
            unique_keys=["metric_id", "property"],
        )
        return id

    def add_tag(self, metric_id: str, property: str, tag: str, sentiment: str):
        vector = [
            float(x)
            for x in self.embedding_fn([["Represent the following text:", tag]])[0]
        ]
        metadata = {
            "property": property,
            "tag": tag,
            "sentiment": sentiment,
            "metric_id": metric_id,
        }
        self.vector_db.upsert_vector(
            self.tag_db_name,
            vector,
            metadata,
            unique_keys=["metric_id", "property", "tag"],
        )

    def get_existing_tags(self, metric_id: str):
        existing_tags = self.vector_db.fetch_all_vectors(
            self.tag_db_name, {"metric_id": metric_id}
        )
        return [tag[2] for tag in existing_tags]

    def _get_tags_from_reason(self, metric_properties: dict, reason: str):
        messages = tag_for_reason_prompt_v2.format(
            criteria=metric_properties["eval_instructions"], critique=reason
        )
        tag_list = json.loads(
            self.llm._get_completion_content([{"role": "user", "content": messages}])
        )
        return tag_list

    def generate_tags(self, metric_properties: dict, reason: str):
        metric_id = metric_properties["id"]
        tag_list = self._get_tags_from_reason(metric_properties, reason)
        new_tags = []

        for tag_data in tag_list:
            try:
                property_text = camel_or_snake_to_normal(tag_data["property"])
                tag_text = camel_or_snake_to_normal(tag_data["tag"])
                sentiment = tag_data["sentiment"]

                # Find closest property
                property_vector = self.embedding_fn(
                    [["Represent the following text:", property_text]]
                )[0]
                property_results = self.vector_db.vector_similarity_search(
                    self.property_db_name,
                    property_vector,
                    {"metric_id": metric_id},
                    top_k=1,
                )

                if (
                    property_results and property_results[0][3] < 0.15
                ):  # If a similar property exists
                    closest_property = property_results[0][2]["property"]
                else:
                    closest_property = property_text
                    self.add_property(metric_id, property_text)

                # Find closest tag for the property
                tag_vector = self.embedding_fn(
                    [["Represent the following text:", tag_text]]
                )[0]
                tag_results = self.vector_db.vector_similarity_search(
                    self.tag_db_name,
                    tag_vector,
                    {
                        "metric_id": metric_id,
                        "property": closest_property,
                        "sentiment": sentiment,
                    },
                    top_k=1,
                )

                if (
                    not tag_results or tag_results[0][3] > 0.15
                ):  # If no similar tag exists
                    self.add_tag(metric_id, closest_property, tag_text, sentiment)
                    new_tags.append(f"{sentiment}:{tag_text}")
                else:
                    retrieved_tag = tag_results[0][2]
                    if f"{sentiment}:{retrieved_tag['tag']}" not in new_tags:
                        new_tags.append(f"{sentiment}:{retrieved_tag['tag']}")
            except (KeyError, IndexError, ValueError) as e:
                logging.exception(f"Error processing tag data: {tag_data}, {str(e)}")
                continue

        return new_tags


if __name__ == "__main__":
    metric_properties = {
        "id": "test_metric",
        "eval_instructions": "How polite is the model response?",
    }
    tags = TagsV2()

    test_reason = (
        "The model response was very courteous and used respectful language throughout."
    )
    generated_tags = tags.generate_tags(metric_properties, test_reason)

    test_reason = "The model was very rude and provided inaccurate information."
    generated_tags = tags.generate_tags(metric_properties, test_reason)

    existing_tags = tags.get_existing_tags(metric_id=metric_properties["id"])
