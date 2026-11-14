"""Feedback vector search limited to an explicit set of feedback rows."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Any

from agentic_eval.core.database.ch_vector import ClickHouseVectorDB
from agentic_eval.core.embeddings.embedding_manager import FEEDBACK_TABLE_NAME

# Keeps each identity list in a single query well below the query-size limit.
ID_BATCH_SIZE = 1000

_FEEDBACK_VECTOR_IDS_QUERY = (
    f"SELECT id FROM {FEEDBACK_TABLE_NAME}"
    " WHERE deleted = 0 AND eval_id = %(eval_id)s"
    " AND has(metadata.key, 'feedback_id')"
    " AND metadata.value[indexOf(metadata.key, 'feedback_id')] IN %(feedback_ids)s"
)


def _uuid_strings(values: Iterable[Any]) -> list[str]:
    return list(dict.fromkeys(str(uuid.UUID(str(value))) for value in values))


class DatasetFeedbackVectorDB(ClickHouseVectorDB):
    """Vector store client whose feedback searches see only ``feedback_ids``.

    Use one instance per retrieval. The first search for a template resolves
    the current vector rows of those feedback records; every search then runs
    the inherited query restricted to those rows, so the restriction applies
    before ranking and LIMIT. Without eligible rows a search returns nothing.
    """

    def __init__(self, feedback_ids: Iterable[Any]):
        super().__init__()
        self.feedback_ids = _uuid_strings(feedback_ids)
        self._vector_ids_by_eval: dict[str, list[str]] = {}

    def feedback_vector_ids(self, eval_id: Any) -> list[str]:
        eval_key = str(uuid.UUID(str(eval_id)))
        if eval_key not in self._vector_ids_by_eval:
            vector_ids: list[str] = []
            for offset in range(0, len(self.feedback_ids), ID_BATCH_SIZE):
                params = {
                    "eval_id": eval_key,
                    "feedback_ids": tuple(
                        self.feedback_ids[offset : offset + ID_BATCH_SIZE]
                    ),
                }
                rows = self.execute_read(_FEEDBACK_VECTOR_IDS_QUERY, params)
                vector_ids.extend(str(row[0]) for row in rows)
            self._vector_ids_by_eval[eval_key] = _uuid_strings(vector_ids)
        return self._vector_ids_by_eval[eval_key]

    def vector_similarity_search(
        self,
        table_name: str,
        query_vector: list[float],
        filter_by: dict[str, str] | None = None,
        metadata_column_not_null: str | None = None,
        eval_id: str | None = None,
        top_k: int = 5,
        syn_data_flag=False,
    ):
        if table_name != FEEDBACK_TABLE_NAME or not eval_id or syn_data_flag:
            return []
        vector_ids = self.feedback_vector_ids(eval_id)
        results = []
        for offset in range(0, len(vector_ids), ID_BATCH_SIZE):
            # With syn_data_flag set, the inherited search reads eval_id as a
            # list of vector row ids and keeps only those rows.
            batch: Any = vector_ids[offset : offset + ID_BATCH_SIZE]
            results.extend(
                super().vector_similarity_search(
                    table_name,
                    query_vector,
                    filter_by,
                    metadata_column_not_null,
                    batch,
                    top_k,
                    syn_data_flag=True,
                )
            )
        # Each batch returns its own nearest rows; rank them together.
        results.sort(key=lambda row: row[-1])
        return results[:top_k]

    def vector_similarity_search_with_threshold(
        self,
        table_name: str,
        query_vector: list[float],
        filter_by: dict[str, str] | None = None,
        metadata_column_not_null: str | None = None,
        dataset_id: str | None = None,
        top_k: int | None = None,
        threshold: float = 0.75,
    ):
        # Feedback retrieval uses vector_similarity_search. This variant cannot
        # be restricted to vector rows, so it returns no rows.
        return []
