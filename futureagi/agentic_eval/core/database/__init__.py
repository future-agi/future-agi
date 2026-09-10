"""
Vector database backends for knowledge base and embedding storage.

The active backend is controlled by the VECTOR_DB_BACKEND environment variable:
  - "clickhouse" (default): Uses ClickHouse with cosineDistance
  - "valkey": Uses Valkey with the vector search module (FT.*)
"""

import os


def get_vector_db():
    """
    Factory function that returns the configured vector database instance.

    Set VECTOR_DB_BACKEND=valkey to use Valkey; defaults to ClickHouse.
    """
    backend = os.getenv("VECTOR_DB_BACKEND", "clickhouse").lower()

    if backend == "valkey":
        from agentic_eval.core.database.valkey_vector import ValkeyVectorDB
        return ValkeyVectorDB()

    from agentic_eval.core.database.ch_vector import ClickHouseVectorDB
    return ClickHouseVectorDB()
