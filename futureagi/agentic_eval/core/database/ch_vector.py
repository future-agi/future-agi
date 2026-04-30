import os
import re
import uuid
from datetime import datetime
from pprint import pprint

import clickhouse_driver

import structlog

logger = structlog.get_logger(__name__)


def sanitize_sql_value(value: str) -> str:
    """
    Sanitize and escape a string value to make it safe for SQL queries.

    This function handles:
    - Escaping single quotes by replacing them with double single quotes.
    - Escaping backslashes by replacing them with double backslashes.
    - Wrapping reserved SQL keywords in backticks (`` ` ``).
    - Handling null characters and any unexpected special characters.
    """
    value=str(value)
    # Escape single quotes
    value = value.replace("'", "''")

    # Escape backslashes
    value = value.replace("\\", "\\\\")

    # Remove null characters
    value = value.replace("\0", "")

    # If the value matches a reserved SQL keyword, wrap it in backticks
    reserved_keywords = [
        "SELECT",
        "INSERT",
        "UPDATE",
        "DELETE",
        "WHERE",
        "FROM",
        "JOIN",
        "INNER",
        "LEFT",
        "RIGHT",
        "ON",
        "GROUP",
        "ORDER",
        "HAVING",
        "DISTINCT"
        # Add more SQL reserved keywords as needed
    ]
    if value.upper() in reserved_keywords:
        value = f"`{value}`"

    # Optionally, further sanitize by removing or replacing any other potentially harmful characters
    value = re.sub(r"[^\w\s\.,@#\-&()]", "", value)

    return value


def sanitize_metadata(metadata: dict[str, str]) -> dict[str, str]:
    """
    Sanitize all keys and values in the metadata dictionary.

    This function applies the `sanitize_sql_value` function to both keys and values.
    """
    sanitized_metadata = {}
    for key, value in metadata.items():
        sanitized_key = sanitize_sql_value(key)
        sanitized_value = sanitize_sql_value(value)
        sanitized_metadata[sanitized_key] = sanitized_value
        if key == "image_enc":
            sanitized_metadata[sanitized_key] = value

    return sanitized_metadata


def sanitize_keys(keys: list[str]) -> list[str]:
    """
    Sanitize a list of keys.

    This function applies the `sanitize_sql_value` function to each key in the list.
    """
    return [sanitize_sql_value(key) for key in keys]


class ClickHouseVectorDB:
    def __init__(
        self,
    ):
        self.client = clickhouse_driver.Client(
            host=os.getenv("CH_HOST"),
            port=os.getenv("CH_PORT"),
            user=os.getenv("CH_USERNAME"),
            password=os.getenv("CH_PASSWORD"),
            database=os.getenv("CH_DATABASE"),
            # settings={'max_threads': 16}
        )

    def drop_table(self,table_name: str) -> None:
        """
        DROPS a table after use is over. DO NOT USE if not required.
        """
        drop_table_query = f"""
        DROP TABLE IF EXISTS {table_name}
        """
        start_time = datetime.now()
        self.client.execute(drop_table_query)
        end_time = datetime.now()
        elapsed_time = (end_time - start_time).total_seconds()
        logger.info(f"create query took {elapsed_time:.2f} seconds to execute")

    def create_table(self, table_name: str) -> None:
        """
        Creates a table in ClickHouse if it does not already exist.
        """
        create_table_query = f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            id UUID,
            eval_id UUID,
            vector Array(Float32),
            metadata Nested (
                key String,
                value Nullable(String)
            ),
            deleted UInt8 DEFAULT 0
        ) ENGINE = MergeTree()
        ORDER BY id
        """
        start_time = datetime.now()

        self.client.execute(
            create_table_query,
            settings={"data_type_default_nullable": 0},
        )
        end_time = datetime.now()
        elapsed_time = (end_time - start_time).total_seconds()
        logger.info(f"create query took {elapsed_time:.2f} seconds to execute")

    def get_or_create_collection(self, table_name: str) -> None:
        """
        Checks if a table exists and creates it if it does not.
        """
        start_time = datetime.now()

        table_exists_query = f"EXISTS TABLE {table_name}"
        table_exists = self.client.execute(table_exists_query)
        end_time = datetime.now()
        elapsed_time = (end_time - start_time).total_seconds()
        logger.info(f"create query took {elapsed_time:.2f} seconds to execute")
        if not table_exists:
            self.create_table(table_name)




    def upsert_vector(
        self,
        table_name: str,
        eval_id: str,
        vector: list[float],
        metadata: dict[str, str],
        unique_keys: list[str],
        exclude_keys: list[str] | None = None,
    ) -> str:
        """
        Upserts a vector into the specified table, marking previous entries with the same metadata as deleted.
        Returns the ID of the newly inserted or updated entry.
        """
        new_id = str(uuid.uuid4())
        # print( "metadata" ,metadata)
        metadata = sanitize_metadata(metadata)
        unique_keys = sanitize_keys(unique_keys)
        if exclude_keys:
            exclude_keys = sanitize_keys(exclude_keys)

        update_query = (
            f"ALTER TABLE {table_name} UPDATE deleted = 1 WHERE deleted = 0 AND "
        )

        metadata_filter = []
        for unique_key in unique_keys:
            unique_value = metadata[unique_key]
            metadata_filter.append(
                f"has(metadata.key, '{unique_key}') AND metadata.value[indexOf(metadata.key, '{unique_key}')] = '{unique_value}'"
            )

        if exclude_keys:
            for exclude_key in exclude_keys:
                metadata_filter.append(f"NOT has(metadata.key, '{exclude_key}')")

        metadata_filter_query = " AND ".join(metadata_filter)
        update_query += metadata_filter_query
        start_time = datetime.now()
        self.client.execute(update_query)
        end_time = datetime.now()
        elapsed_time = (end_time - start_time).total_seconds()
        logger.info(f"ALTER query took {elapsed_time:.2f} seconds to execute")
        # Flatten metadata into two arrays
        metadata_keys = list(metadata.keys())
        metadata_values = list(metadata.values())

        insert_query = f"INSERT INTO {table_name} (id, eval_id, vector, metadata.key, metadata.value) VALUES"
        start_time = datetime.now()
        # vector_str = "[" + ",".join(map(str, vector)) + "]"
        self.client.execute(
            insert_query,
            [(new_id, eval_id, vector, metadata_keys, metadata_values)],
            types_check=True,
        )
        end_time = datetime.now()
        elapsed_time = (end_time - start_time).total_seconds()
        logger.info(f"Insert query took {elapsed_time:.2f} seconds to execute")
        return new_id

    def fetch_vector_by_id(
        self, table_name: str, id: str
    ) -> dict[str, str | list[float] | dict[str, str]] | None:
        """
        Fetches a vector by its ID from the specified table if it is not marked as deleted.
        Returns the vector row or None if not found.
        """
        id = sanitize_sql_value(id)
        select_query = f"SELECT id, vector, arrayJoin(metadata.key) AS key, arrayJoin(metadata.value) AS value FROM {table_name} WHERE id = '{id}' AND deleted = 0"
        start_time = datetime.now()

        result = self.client.execute(select_query)
        end_time = datetime.now()
        elapsed_time = (end_time - start_time).total_seconds()
        logger.info(f"SELECT query took {elapsed_time:.2f} seconds to execute")
        if result:
            id, vector, keys, values = result[0]
            metadata = dict(zip(keys, values, strict=False))
            return {"id": id, "vector": vector, "metadata": metadata}
        return None

    def fetch_all_vectors(
        self, table_name: str, filter_by: dict[str, str] | None = None
    ) -> list[tuple[str, list[float], dict[str, str]]]:
        """
        Fetches all vectors from the specified table that are not marked as deleted.
        Optionally filters by metadata criteria.
        """

        if filter_by is None:
            filter_by = {}
        start_time = datetime.now()

        select_query = f"SELECT id, vector, arrayJoin(metadata.key) AS key, arrayJoin(metadata.value) AS value FROM {table_name} WHERE deleted = 0"

        if filter_by:
            metadata_filter = [
                f"has(metadata.key, '{key}') AND metadata.value[indexOf(metadata.key, '{key}')] = '{value}'"
                for key, value in filter_by.items()
            ]
            select_query += " AND " + " AND ".join(metadata_filter)

        results = self.client.execute(select_query)
        end_time = datetime.now()
        elapsed_time = (end_time - start_time).total_seconds()
        logger.info(f"select query took {elapsed_time:.2f} seconds to execute")
        vectors = []
        for result in results:
            id, vector, keys, values = result
            metadata = dict(zip(keys, values, strict=False))
            vectors.append((id, vector, metadata))
        return vectors

    def fetch_vectors_by_query(self, query: str):
        start_time = datetime.now()

        results = self.client.execute(query)
        end_time = datetime.now()
        elapsed_time = (end_time - start_time).total_seconds()
        logger.info(f"fetch query took {elapsed_time:.2f} seconds to execute")
        vectors = []

        for result in results:
            id, vector, keys, values = result
            metadata = dict(zip(keys, values, strict=False))
            vectors.append((id, vector, metadata))

        return vectors

    def vector_similarity_search_with_threshold(
        self,
        table_name: str,
        query_vector: list[float],
        filter_by: dict[str, str] | None = None,
        metadata_column_not_null: str | None = None,
        dataset_id: str | None = None,
        top_k: int | None = None,
        threshold: float = 0.75
    ):
        """
        tracebacka similarity search against vectors in the specified table using cosine distance.
        Returns vectors that match the threshold criteria and/or top_k limit.

        Args:
            table_name: The database table to search in
            query_vector: The vector to compare against
            filter_by: Optional metadata filters as key-value pairs
            metadata_column_not_null: Optional metadata column that must not be null
            dataset_id: Optional dataset ID to filter by
            top_k: Optional limit for number of results (default: None, returns all matches)
            threshold: Optional maximum distance threshold (default: 0.7)

        Returns:
            List of tuples containing (id, vector, metadata, similarity)
        """
        if filter_by is None:
            filter_by = {}
        filter_by = sanitize_metadata(filter_by)

        query_vector_str = "[" + ",".join(map(str, query_vector)) + "]"

        metadata_filter_query = ""
        if filter_by:
            metadata_filter = [
                f"has(metadata.key, '{key}') AND metadata.value[indexOf(metadata.key, '{key}')] = '{value}'"
                for key, value in filter_by.items()
            ]
            metadata_filter_query += " AND " + " AND ".join(metadata_filter)

        # Here the column is named eval_id but we are storing the dataset id there in this case
        dataset_id_filter = f" AND eval_id = '{dataset_id}'" if dataset_id else ""

        metadata_not_null_filter = ""
        if metadata_column_not_null:
            metadata_not_null_filter = f"""
                AND has(metadata.key, '{metadata_column_not_null}')
                AND isNotNull(metadata.value[indexOf(metadata.key, '{metadata_column_not_null}')])
            """

        # Add threshold filtering
        threshold_filter = ""
        if threshold is not None:
            threshold_filter = f" AND distance <= {threshold}"

        # Determine limit clause
        limit_clause = f"LIMIT {top_k}" if top_k is not None else ""

        query = f"""
        SELECT
            *,
            cosineDistance(vector, {query_vector_str}) AS distance
        FROM {table_name}
        WHERE deleted = 0
        {dataset_id_filter}
        {metadata_not_null_filter}
        {metadata_filter_query}
        {threshold_filter}
        ORDER BY distance ASC
        {limit_clause}
        """
        # Execute the query
        try:
            results = self.client.execute(query)
        except clickhouse_driver.errors.PartiallyConsumedQueryError as e:
            logger.error(f"PartiallyConsumedQueryError: {e}")
            self.close()
            raise
        except Exception as e:
            logger.info(f"Error executing query vector_similarity_search_with_threshold: {e}")
            return None

        similarities = []
        for row in results:
            id, dataset_id, vector, keys, values, _, similarity = row
            metadata = dict(zip(keys, values, strict=False))
            similarities.append({
                "id": id,
                "dataset_id": dataset_id,
                "vector": vector,
                "metadata": metadata,
                "similarity": similarity
            })

        return similarities

    def vector_similarity_search(
        self,
        table_name: str,
        query_vector: list[float],
        filter_by: dict[str, str] | None = None,
        metadata_column_not_null: str | None = None,
        eval_id: str | None = None,
        top_k: int = 5,
        syn_data_flag=False
    ):
        """
        Performs a similarity search against vectors in the specified table using cosine distance.
        Returns the top_k vectors sorted by similarity to the query vector.
        """
        if filter_by is None:
            filter_by = {}
        filter_by = sanitize_metadata(filter_by)
        # Convert query_vector to a string representation
        query_vector_str = "[" + ",".join(map(str, query_vector)) + "]"

        # Construct the WHERE clause for metadata filtering
        metadata_filter_query = ""
        if filter_by:
            metadata_filter = [
                f"has(metadata.key, '{key}') AND metadata.value[indexOf(metadata.key, '{key}')] = '{value}'"
                for key, value in filter_by.items()
            ]
            metadata_filter_query += " AND " + " AND ".join(metadata_filter)
        if syn_data_flag:
            if eval_id is not None:
                ids_sql = ", ".join(f"'{u}'" for u in eval_id)  # type: ignore[union-attr]
                eval_id_filter = f" AND id IN ({ids_sql})"
            else:
                eval_id_filter = ""
        else:
            eval_id_filter = f" AND eval_id = '{eval_id}'" if eval_id else ""

        metadata_not_null_filter = ""
        if metadata_column_not_null:
            metadata_not_null_filter = f"""
                AND has(metadata.key, '{metadata_column_not_null}')
                AND isNotNull(metadata.value[indexOf(metadata.key, '{metadata_column_not_null}')])
            """
        # Construct the full query
        query = f"""
        SELECT
            *,
            cosineDistance(vector, {query_vector_str}) AS distance
        FROM {table_name}
        WHERE deleted = 0
        {eval_id_filter}
        {metadata_not_null_filter}
        {metadata_filter_query}
        ORDER BY distance ASC
        LIMIT {top_k}
        """
        start_time = datetime.now()
        results = None
        # Execute the query
        try:

            results = self.client.execute(query)
            # print("Executing success query:", query)
        except Exception:
            #print traceback
            # print("Executing broken query:", query)
            # print("len of q vector:", len(query_vector))
            import traceback
            traceback.print_exc()
        end_time = datetime.now()
        elapsed_time = (end_time - start_time).total_seconds()
        logger.info(f"sim search query took {elapsed_time:.2f} seconds to execute")
        similarities = []
        if results:
            for row in results:
                id, eval_id, vector, keys, values, _, similarity = row
                metadata = dict(zip(keys, values, strict=False))
                similarities.append((id, vector, metadata, similarity))
        # Process and return the results
        return similarities

    def close(self) -> None:
        """
        Closes the ClickHouse connection and releases resources.
        Should be called when the database connection is no longer needed.
        """
        if hasattr(self, 'client') and self.client is not None:
            self.client.disconnect()
            self.client = None

    def get_num_vectors(self, doc_ids, table_name):
        ids_sql = ", ".join(f"'{u}'" for u in doc_ids)

        query = f"SELECT COUNT(*) FROM {table_name} WHERE id IN ({ids_sql})"
        return self.client.execute(query)

    def get_random_examples(self, doc_ids: list[str], table_name: str, limit: int) -> list:
        """
        Get random examples from the table for a specific doc_id.

        Args:
            doc_id: The document ID to filter by
            table_name: The name of the table to query
            percentage: Float between 0 and 1 representing the percentage of chunks to return
        """
        ids_sql = ", ".join(f"'{u}'" for u in doc_ids)
        query = f"""
        SELECT *
        FROM {table_name}
        WHERE id IN ({ids_sql})
        ORDER BY rand()
        LIMIT {limit}
        """
        return self.client.execute(query)

    def bulk_upsert_vectors(
        self,
        table_name: str,
        eval_id: str,
        vectors: list[list[float]],
        metadata_list: list[dict[str, str]],
        unique_keys: list[str],
        exclude_keys: list[str] | None = None,
    ) -> list[str]:
        """
        Bulk upserts multiple vectors into the specified table in a single query.

        Args:
            table_name: Name of the table to insert into
            eval_id: Evaluation ID to associate with all vectors
            vectors: List of vector embeddings to insert
            metadata_list: List of metadata dictionaries corresponding to each vector
            unique_keys: List of metadata keys that determine uniqueness
            exclude_keys: Optional list of keys to exclude from uniqueness check

        Returns:
            List of IDs for the newly inserted vectors
        """
        if len(vectors) != len(metadata_list):
            raise ValueError("Number of vectors must match number of metadata dictionaries")

        # Generate IDs for all vectors
        new_ids = [str(uuid.uuid4()) for _ in range(len(vectors))]

        # Sanitize all metadata and keys
        sanitized_metadata_list = [sanitize_metadata(metadata) for metadata in metadata_list]
        unique_keys = sanitize_keys(unique_keys)
        if exclude_keys:
            exclude_keys = sanitize_keys(exclude_keys)

        # Build the update query to mark existing entries as deleted
        update_query = f"ALTER TABLE {table_name} UPDATE deleted = 1 WHERE deleted = 0 AND "

        # For bulk operations, we need to handle the uniqueness check differently
        # We'll create a condition that checks if any of the new entries would match
        metadata_filter = []
        for unique_key in unique_keys:
            # Get all unique values for this key across all metadata dictionaries
            unique_values = {metadata[unique_key] for metadata in sanitized_metadata_list if unique_key in metadata}

            # Create a condition that checks if any of these values match
            value_conditions = [f"metadata.value[indexOf(metadata.key, '{unique_key}')] = '{value}'" for value in unique_values]
            metadata_filter.append(f"has(metadata.key, '{unique_key}') AND ({' OR '.join(value_conditions)})")

        if exclude_keys:
            for exclude_key in exclude_keys:
                metadata_filter.append(f"NOT has(metadata.key, '{exclude_key}')")

        metadata_filter_query = " AND ".join(metadata_filter)
        update_query += metadata_filter_query

        # Execute the update query
        start_time = datetime.now()
        self.client.execute(update_query)
        end_time = datetime.now()
        elapsed_time = (end_time - start_time).total_seconds()
        logger.info(f"Bulk ALTER query took {elapsed_time:.2f} seconds to execute")

        # Prepare the bulk insert
        insert_query = f"INSERT INTO {table_name} (id, eval_id, vector, metadata.key, metadata.value) VALUES"

        # Prepare the data for bulk insert
        insert_data = []
        for i, (vector, metadata) in enumerate(zip(vectors, sanitized_metadata_list, strict=False)):
            metadata_keys = list(metadata.keys())
            metadata_values = list(metadata.values())
            insert_data.append((new_ids[i], eval_id, vector, metadata_keys, metadata_values))

        # Execute the bulk insert
        start_time = datetime.now()
        self.client.execute(insert_query, insert_data, types_check=True)
        end_time = datetime.now()
        elapsed_time = (end_time - start_time).total_seconds()
        logger.info(f"Bulk insert query took {elapsed_time:.2f} seconds to execute")

        return new_ids


# Example Usage
if __name__ == "__main__":
    db = ClickHouseVectorDB()
    # LogExceptions

    db.create_table("vectors")

    id1 = db.upsert_vector(
        "vectors",
        "eval_1",
        [0.1, 0.2, 0.3],
        {"description": "vector1", "category": "A"},
        ["category"],
    )
    id2 = db.upsert_vector(
        "vectors",
        "eval_1",
        [0.4, 0.5, 0.6],
        {"description": "vector2", "category": "B"},
        ["category"],
    )
    id3 = db.upsert_vector(
        "vectors",
        "eval_1",
        [0.7, 0.8, 0.9],
        {"description": "vector1", "category": "A"},
        ["category"],
    )

    print(db.fetch_vector_by_id("vectors", id1))
    print(db.fetch_vector_by_id("vectors", id3))

    print(db.fetch_all_vectors("vectors"))

    query_vector = [0.1, 0.2, 0.3]
    pprint(
        db.vector_similarity_search("vectors", query_vector, {"category": "B"}, top_k=2)
    )
