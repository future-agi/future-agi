import logging
from uuid import UUID

import pandas as pd
from tqdm import tqdm

from ee.agenthub.workflow_agent.constants import EVENTS_COLUMNS
from ee.agenthub.workflow_agent.modules.cluster_data import (
    DataClusterer,
    DataClustererConstants,
)
from ee.agenthub.workflow_agent.modules.event_generator_agent import (
    EventGeneratorAgent,
)
from agentic_eval.core.database.ch_vector import ClickHouseVectorDB
from agentic_eval.core.embeddings.embeddings_v2 import get_embedding_model
from agentic_eval.core.utils.functions import camel_or_snake_to_normal
from model_hub.utils.clickhouse import (
    update_eval_record,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

class AutoEventGenerator:
    def __init__(
        self,
        ai_model_id: UUID,
        ai_model_version: str,
        ai_model_environment: int,
        key_table_name: str = "insights_key",
        db_table_name: str = "events_embedding",
    ):
        self.model = get_embedding_model()
        self.db = ClickHouseVectorDB()
        self.db_table_name = db_table_name
        self.key_table_name = key_table_name
        self.ai_model_id = ai_model_id
        self.ai_model_version = ai_model_version
        self.ai_model_environment = ai_model_environment

        self.db.create_table(self.key_table_name)

        self.constants = DataClustererConstants(
            embed_col_name="vector", cluster_label_col_name="cluster"
        )
        self.clusterer = DataClusterer(self.constants)
        self.event_generator_agent = EventGeneratorAgent()

    def _prepare_clickhouse_query(
        self, model_version: str, environment: int, model_id: UUID, status: str
    ) -> str:
        query = f"""
        SELECT ee.conversation_id, ee.vector, ee.metadata.key, ee.metadata.value
            FROM
            (SELECT arrayElement(metadata.value, indexOf(metadata.key, 'conversation_id')) as conversation_id,
                    metadata,
                    vector
            FROM events_embedding
            WHERE deleted = 0
            AND NOT has(metadata.key, 'node_id')) ee
            INNER JOIN
            (SELECT DISTINCT arrayElement(Features.Value, indexOf(Features.Key, 'conversation_id')) as conversation_id
            FROM events
            WHERE ModelVersion = '{model_version}'
            AND Environment = '{environment}'
            AND AIModel = '{model_id}'
            AND (
                    (NOT has(EvalResults.Key, 'auto_eg_status'))
                    OR (
                        has(EvalResults.Key, 'auto_eg_status')
                        AND arrayElement(EvalResults.Value, indexOf(EvalResults.Key, 'auto_eg_status')) = '{status}'
                    )
                )
            AND NOT has(Features.Key, 'node_id')
            AND deleted = 0
            ) e2
            ON e2.conversation_id = ee.conversation_id
        """
        return query

    def fetch_data(self) -> pd.DataFrame:
        query = self._prepare_clickhouse_query(
            self.ai_model_version,
            self.ai_model_environment,
            self.ai_model_id,
            "not_processed",
        )

        results = self.db.fetch_vectors_by_query(query=query)

        data = []
        for id, vector, metadata in results:
            if metadata.get("event_uuid") is None:
                continue
            row = {"id": id, self.constants.embed_col_name: vector}
            row.update(metadata)
            data.append(row)

            # update the status to processing

            update_eval_record(
                "events",
                "original_uuid",
                metadata["event_uuid"],
                updated_values={
                    "auto_eg_status": "processing",
                },
                columns=EVENTS_COLUMNS,
            )

        return pd.DataFrame(data)

    def _encoder(self, text):
        encoded_list = self.model([text])[0].tolist()
        return encoded_list

    def _upsert_events_to_vector_db(self, metadata: dict) -> dict:
        num_upserted = 0
        for key, item_val in metadata.items():
            if isinstance(item_val["explanation"], list):
                explanation = " ".join(item_val["explanation"])
            else:
                explanation = item_val["explanation"]
            explanation_embed = self._encoder(explanation)
            results = self.db.vector_similarity_search(
                table_name=self.key_table_name, query_vector=explanation_embed, top_k=1
            )

            # use 0.2 as the threshold for consine distance
            # lower values mean that the vectors are similar
            if len(results) > 0 and results[0][3] < 0.2:
                pass
            else:
                self.db.upsert_vector(
                    table_name=self.key_table_name,
                    eval_id="",  # Add required eval_id parameter
                    vector=explanation_embed,
                    metadata={"key": key, "explanation": explanation, "type": "key", "datatype": item_val["datatype"]},
                    unique_keys=["key"],
                )
                num_upserted += 1
        return num_upserted

    def cluster_data(self, df: pd.DataFrame) -> pd.DataFrame:
        return self.clusterer.cluster_data(df)

    def sample_from_clusters(self, df: pd.DataFrame, k: int) -> list[dict]:
        sampled_data = []
        for cluster in df[self.constants.cluster_label_col_name].unique():
            cluster_data = df[df[self.constants.cluster_label_col_name] == cluster]
            if len(cluster_data) > k:
                sampled = cluster_data.sample(k)
            else:
                sampled = cluster_data
            sampled_data.extend(sampled.to_dict("records"))
        # Convert the list of dictionaries to a DataFrame
        sampled_df = pd.DataFrame(sampled_data)
        return sampled_df
    def _merge_key_value_pairs(self, events: list[dict]) -> list[dict]:
        merged_events: dict[str, dict] = {}
        for event in events:
            if event.get("name") is None:
                continue
            key = camel_or_snake_to_normal(event.get("name"))
            description = event.get("explanation")
            datatype = event.get("datatype")
            if key in merged_events:
                if description != merged_events[key]["explanation"]:
                    merged_events[key]["description"] = f"{merged_events[key]}\n{description}"
            else:
                merged_events[key] = event
        return list(merged_events.values())

    def _format_output_for_insight_meta(self, merged_events: dict) -> dict:
        return {k: {"explanation": v["explanation"], "datatype": v["datatype"]} for k, v in merged_events.items()}

    def generate_events(self, k: int = 2) -> dict[str, dict]:
        df = self.fetch_data()
        if len(df) == 0:
            logger.info("No data to process")
            return {}
        logger.info(f"Fetched {len(df)} rows from database")
        if len(df) < 5:
            # set entire data as one cluster
            sampled_df = df.copy()
            sampled_df[self.constants.cluster_label_col_name] = 0
        else:
            clustered_df = self.cluster_data(df)
            sampled_df = self.sample_from_clusters(clustered_df, k)

        clusters = sampled_df[self.constants.cluster_label_col_name].unique()
        # sample k items from each cluster
        events = []
        sample_size = 100
        for cluster_idx in tqdm(clusters[:sample_size]):
            filtered = sampled_df[
                sampled_df[self.constants.cluster_label_col_name] == cluster_idx
            ]
            sampled = filtered.sample(n=k) if len(filtered) > k else filtered
            event = self.event_generator_agent.extract_features(
                conversations=sampled["conversation"].tolist()
            )
            events.append(event)


        merged_events = self._merge_key_value_pairs(events)
        self._upsert_events_to_vector_db(merged_events)


        # update the status to processed
        if "metadata" in df.columns:
            for metadata in df["metadata"].tolist():
                update_eval_record(
                    "events",
                    "original_uuid",
                    metadata["event_uuid"],
                    updated_values={
                        "auto_eg_status": "processed",
                    },
                    columns=EVENTS_COLUMNS,
                )

        return self._format_output_for_insight_meta(merged_events)


def main():
    auto_gen_constants = {
        "key_table_name": "insights_key",
        "db_table_name": "events_embedding",
        "ai_model_id": UUID("ffdc7953-d1a8-4943-80d9-6dd38eafe040"),
        "ai_model_version": "v0",
        "ai_model_environment": 3,
    }
    auto_generator = AutoEventGenerator(**auto_gen_constants)
    events = auto_generator.generate_events(k=2)

    print(events)


if __name__ == "__main__":
    main()
