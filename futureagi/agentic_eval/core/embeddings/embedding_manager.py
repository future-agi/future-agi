import base64
import concurrent.futures
import csv
import os
import threading
import time
import traceback
import uuid
from datetime import datetime
from functools import wraps
from io import BytesIO

import numpy as np
from django.db import close_old_connections
from PIL import Image
from tfc.telemetry import wrap_for_thread

from agentic_eval.core.database import get_vector_db
from agentic_eval.core.database.ch_vector import ClickHouseVectorDB
from agentic_eval.core.embeddings.serving_client import get_serving_client
from agentic_eval.core.utils.functions import detect_input_type
import structlog

logger = structlog.get_logger(__name__)
from analytics.utils import mixpanel_slack_notfy
from model_hub.models.choices import DataTypeChoices
from model_hub.models.develop_dataset import Cell
from tfc.utils.storage import (
    image_bytes_from_url_or_base64,
    open_image_from_url,
    upload_image_to_s3,
)

# Thread-local storage for models
_thread_local = threading.local()
FEEDBACK_TABLE_NAME = "feedbacks"

def log_performance(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        # Skip performance logging in production unless explicitly enabled
        enable_logging = os.getenv("ENABLE_PERFORMANCE_CSV_LOGGING", False)

        if not enable_logging:
            return func(*args, **kwargs)

        start_time = time.time()
        result = func(*args, **kwargs)
        end_time = time.time()
        execution_time = end_time - start_time

        # Get function name and arguments
        func_name = func.__name__
        arg_names = func.__code__.co_varnames[: func.__code__.co_argcount]
        arg_values = args[: len(arg_names)]
        kwargs.update(dict(zip(arg_names[len(arg_values) :], arg_values, strict=False)))

        # Create performance log entry
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "function_name": func_name,
            "execution_time": execution_time,
            "file_id": kwargs.get("file_id", "N/A"),
            "kb_id": kwargs.get("kb_id", "N/A"),
            "organization_id": kwargs.get("organization_id", "N/A"),
            "table_name": kwargs.get("table_name", "N/A"),
            "input_size": len(kwargs.get("text", "")) if "text" in kwargs else "N/A",
            "metadata_count": (
                len(kwargs.get("metadatas", [])) if "metadatas" in kwargs else "N/A"
            ),
        }

        # Write to CSV
        csv_file = "performance_log.csv"
        file_exists = os.path.isfile(csv_file)

        with open(csv_file, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=log_entry.keys())
            if not file_exists:
                writer.writeheader()
            writer.writerow(log_entry)

        return result

    return wrapper


class ModelManager:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        logger.info("Creating new ModelManager instance")
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    logger.info("Initializing singleton ModelManager instance")
                    cls._instance = super().__new__(cls)
                    cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        logger.info("Initializing ModelManager components")
        # Initialize serving client
        self._serving_client = None
        self._use_serving = True  # Flag to control whether to use serving or local models

        # Local models as fallback
        self._image_model = None
        self._audio_model = None
        self._image_text_model = None
        self._text_model = None
        self._image_processor = None
        self._syn_data_model = None
        self._serving_client = self.serving_client

        logger.info("ModelManager initialization complete")


    @property
    def serving_client(self):
        logger.info("Accessing serving client")
        if self._serving_client is None:
            try:
                logger.info("Initializing serving client")
                self._serving_client = get_serving_client()
                # Test if serving is available
                if not self._serving_client.health_check():
                    mixpanel_slack_notfy("ALERT: Serving service not available.")
                    logger.exception("Serving service not available, falling back to local models")
                    # self._use_serving = False
            except Exception as e:
                traceback.print_exc()
                mixpanel_slack_notfy(f"Failed to initialize serving client: {e}, falling back to local models")
                logger.exception(f"Failed to initialize serving client: {e}, falling back to local models")
                # self._use_serving = False
        return self._serving_client

    @property
    def syn_data_model(self):
        logger.info("Accessing synthetic data model")
        if self._use_serving and self.serving_client:
            logger.info("Using serving client for synthetic data embeddings")
            # Return a function that uses the serving client
            def syn_data_embedding(text, to_list=False):
                logger.info("Getting synthetic data embedding from serving client")
                embeddings = self.serving_client.get_syn_data_embedding(text)
                return embeddings
            return syn_data_embedding
        else:
            # Fallback to local model
            mixpanel_slack_notfy("ALERT: Synthetic data embeddings are not available")

            return []

    @property
    def text_model(self):
        logger.info("Accessing text model")
        if self._use_serving and self.serving_client:
            logger.info("Using serving client for text embeddings")
            # Return a function that uses the serving client
            def text_embedding(text, to_list=False):
                logger.info("Getting text embedding from serving client")
                embeddings = self.serving_client.embed_text(text)
                return embeddings
            return text_embedding
        else:
            mixpanel_slack_notfy("ALERT: Text embeddings are not available")

            return None

    @property
    def image_model(self):
        logger.info("Accessing image model")
        if self._use_serving and self.serving_client:
            logger.info("Using serving client for image embeddings")
            # Return a function that uses the serving client
            def image_embedding(image):
                logger.info("Getting image embedding from serving client")
                return self.serving_client.embed_image(image)
            return image_embedding
        else:
            mixpanel_slack_notfy("ALERT: Image embeddings are not available")

            return None

    @property
    def audio_model(self):
        logger.info("Accessing audio model")
        if self._use_serving and self.serving_client:
            logger.info("Using serving client for audio embeddings")
            # Return a function that uses the serving client
            def audio_embedding(audio_data):
                logger.info("Getting audio embedding from serving client")
                return self.serving_client.embed_audio(audio_data)
            return audio_embedding
        else:
            mixpanel_slack_notfy("ALERT: Audio embeddings are not available")

            return None

    @property
    def image_text_model(self):
        logger.info("Accessing image-text model")
        if self._use_serving and self.serving_client:
            logger.info("Using serving client for image-text embeddings")
            # Return a function that uses the serving client
            def image_text_embedding(content):
                logger.info("Getting image-text embedding from serving client")
                return self.serving_client.embed_image_text(content)
            return image_text_embedding
        else:
            mixpanel_slack_notfy("ALERT: Image-text embeddings are not available")

            return None

    @property
    def image_processor(self):
        logger.info("Accessing image processor")
        return self._image_processor

    def get_embeddings(self, text, model_provider, model_name, model_params=None):
        return self.serving_client.get_embeddings(text, model_provider, model_name, model_params)

# Create a single instance of ModelManager
model_manager = ModelManager()

class EmbeddingManager:
    def __init__(self):
        self.db_client = get_vector_db()
        self.input_types = None

    def close(self):
        try:
            if self.db_client:
                self.db_client.close()
            # Close all database connections properly
            from django.db import connections

            for conn in connections.all():
                conn.close()
        except Exception as e:
            logger.exception(f"Error closing RAG: {e}")

    def get_feedback_count(self, eval_id: str, organization_id: str = None, workspace_id: str = None) -> int:
        """Return the number of non-deleted feedback rows for a given eval_id."""
        try:
            if hasattr(self.db_client, "get_feedback_count"):
                return self.db_client.get_feedback_count(
                    FEEDBACK_TABLE_NAME, eval_id, organization_id, workspace_id
                )

            where = ["eval_id = %(eval_id)s", "deleted = 0"]
            params = {"eval_id": eval_id}
            if organization_id:
                where.append(
                    "arrayElement(metadata.value, indexOf(metadata.key, 'organization_id')) = %(organization_id)s"
                )
                params["organization_id"] = organization_id
            if workspace_id:
                where.append(
                    "arrayElement(metadata.value, indexOf(metadata.key, 'workspace_id')) = %(workspace_id)s"
                )
                params["workspace_id"] = workspace_id
            sql = f"SELECT COUNT(*) FROM feedbacks WHERE {' AND '.join(where)}"
            result = self.db_client.client.execute(sql, params)
            return result[0][0] if result else 0
        except Exception as e:
            logger.warning("get_feedback_count failed", eval_id=eval_id, error=str(e))
            return 0

    def get_syn_embedding(self):
        return model_manager.syn_data_model

    def get_image_query_embedding(self, type, query):
        if type == "image":
            model = model_manager.image_model
            if isinstance(query, str):
                if "http" in query:
                    query = open_image_from_url(query, save_as="downloaded_image.jpg")
            # Handle both local model (with encode method) and serving client function
            if model_manager._use_serving:
                # For serving client function
                embedding_vector = model(query)
                # print("here is image embedding vector: ", embedding_vector)
                return embedding_vector
            else:
                # For local model
                embedding_vector = model.encode(query)
                return embedding_vector.tolist()
        elif type == "image-text":
            model = model_manager.image_text_model

            if model_manager._use_serving:
                # For serving client function
                embedding_vector = model(query)
                return embedding_vector
            else:
                # For local model
                processor = model_manager.image_processor

                # Check if query is an image or text
                if isinstance(query, str) and "http" not in query:
                    # Process as text
                    inputs = processor(text=[query], return_tensors="pt", padding=True)
                    embedding_vector = model.get_text_features(**inputs)
                else:
                    # Process as image
                    if isinstance(query, str) and "http" in query:
                        query = open_image_from_url(query, save_as="downloaded_image.jpg")

                    inputs = processor(images=query, return_tensors="pt")
                    embedding_vector = model.get_image_features(**inputs)

                # Convert tensor to numpy array and then to list
                embedding_list = (
                    embedding_vector.detach().numpy().flatten().astype(np.float32).tolist()
                )
                return embedding_list

    def get_audio_query_embedding(self, type, query):
        try:
            model = model_manager.audio_model
            # Process URL if query is a string containing URL
            if isinstance(query, str):
                # print(f"Query is a string: {query}")
                if "http" in query:
                    pass

                else:
                    logger.info("Audio query should be a string with http",query)
                    return []

            else:
                logger.info("Audio query should be a string with http",query)
                return []

            if model_manager._use_serving:
                try:
                    embedding_vector = model(query)
                except:
                    embedding_vector = []   
                return embedding_vector
            else:
                return []
        except Exception as e:
            logger.exception(f"Error in get_audio_model: {e}")
            traceback.print_exc()
            return None

    def input_checker(self, input):
        """
        Check input types using detect_input_type function for more robust type detection.

        Args:
            input (dict): Dictionary of input items to check

        Returns:
            dict: Dictionary mapping input keys to their detected types ('text', 'image', 'audio')
        """
        input_dict = {}

        for input_item in input:
            # Use detect_input_type to get the type
            detected_type = detect_input_type(input[input_item])

            # If detect_input_type returned a dictionary with 'type' key
            if isinstance(detected_type, dict):
                input_type = detected_type.get("type", "text")

                # Map the detected type to our expected types
                if input_type == "audio":
                    input_dict[input_item] = "audio"
                elif input_type == "image":
                    input_dict[input_item] = "image"
                else:
                    input_dict[input_item] = "text"
            else:
                # Fallback to text if detection failed
                input_dict[input_item] = "text"

        return input_dict

    def inputs_type_list(self, input):
        """
        Check input types for a list of inputs using detect_input_type function.

        Args:
            input (list): List of input items to check

        Returns:
            list: List of detected types ('text', 'image', 'audio')
        """
        input_list = []

        for input_item in input:
            # Use detect_input_type to get the type
            detected_type = detect_input_type(input_item)

            # If detect_input_type returned a dictionary with 'type' key
            if isinstance(detected_type, dict):
                input_type = detected_type.get("type", "text")

                # Map the detected type to our expected types
                if input_type == "audio":
                    input_list.append("audio")
                elif input_type == "image":
                    input_list.append("image")
                else:
                    input_list.append("text")
            else:
                # Fallback to text if detection failed
                input_list.append("text")

        return input_list

    def insert_embedding(
        self,
        eval_id,
        data,
        index_col_type,
        table_name,
        column_name,
        unique_key_value,
        unique_key="item_id",
        exclude_keys=None,
    ):
        """
        Inserts the embedding of a data item into the ClickHouse vector table.

        Args:
            data (dict): The data item containing text or content to embed.
            unique_key_value (str): The unique identifier for this item.
            unique_key (str, optional): The key for the unique identifier. Defaults to "item_id".
            exclude_keys (list, optional): List of keys to exclude from metadata. Defaults to None.
        """
        db_client = self.db_client
        db_client.create_table(table_name)
        if index_col_type == "image":
            embedding_vector = self.get_image_query_embedding(
                "image", data[column_name]
            )

        elif index_col_type == "audio":
            embedding_vector = self.get_audio_query_embedding(
                "audio", data[column_name]
            )

        elif index_col_type == "image-text":
            embedding_vector = self.get_image_query_embedding(
                "image-text", data[column_name]
            )
        else:
            model = model_manager.text_model
            if model_manager._use_serving:
                embedding_vector = model(data[column_name])
            else:
                embedding_vector = model.encode(data[column_name])
                embedding_vector = embedding_vector.tolist()

        db_client.upsert_vector(
            table_name,
            eval_id,
            embedding_vector,
            data,
            [unique_key],
            exclude_keys,
        )

    # @log_performance
    def data_formatter(
        self,
        row_dict,
        inputs_formater,
        table_name=FEEDBACK_TABLE_NAME,
        insert=False,
        eval_id="",
        organization_id=None,
        workspace_id=None,
    ):
        try:
            if table_name == FEEDBACK_TABLE_NAME and not organization_id:
                raise ValueError("organization_id is required for feedback embeddings")

            # Check inputs using input_checker
            input_dict = self.input_checker(row_dict)

            if input_dict is None:
                raise ValueError("input_checker returned None")

            # Ensure 'item_id' is in row_dict
            if "item_id" not in row_dict:
                row_dict["item_id"] = str(uuid.uuid4()).replace("-", "_")

            index_col_type = []
            vectors = []
            metadata_list = []

            for n, inp in enumerate(inputs_formater):

                if inp not in row_dict:
                    continue

                if input_dict[inp] == "image":

                    # Process image input
                    img_bytes = image_bytes_from_url_or_base64(row_dict[inp])
                    if img_bytes is None:
                        raise ValueError(f"Image bytes for {inp} are None")

                    img = BytesIO(img_bytes)
                    try:
                        row_dict["index_column"] = Image.open(img)
                        row_dict["input_type"] = "image"
                    except Exception as e:
                        logger.exception(f"Error loading image for {inp}: {e}")
                        raise

                elif input_dict[inp] == "audio":
                    row_dict["index_column"] = row_dict[inp]
                    row_dict["input_type"] = "audio"
                else:
                    row_dict["index_column"] = str(row_dict[str(inp)])
                    row_dict["input_type"] = "text"

                # Append input type for embedding
                if inp in input_dict:
                    index_col_type.append(input_dict[inp])
                else:
                    logger.warning(
                        f"Warning: {inp} not in input_dict, skipping index_col_type append"
                    )

                if table_name == FEEDBACK_TABLE_NAME:
                    if organization_id:
                        row_dict["organization_id"] = str(organization_id)
                    if workspace_id:
                        row_dict["workspace_id"] = str(workspace_id)

                mod_dict = row_dict.copy()

                for inp2 in inputs_formater:
                    if "http" in mod_dict[str(inp2)] and table_name == FEEDBACK_TABLE_NAME:
                        mod_dict[inp2] = self.encode_path(mod_dict[str(inp2)])

                mod_dict["input_type"] = input_dict[inp]

                # Get embedding for this input
                try:
                    if index_col_type[n] == "image":
                        embedding_vector = self.get_image_query_embedding(
                            "image", mod_dict["index_column"]
                        )
                    elif index_col_type[n] == "audio":
                        embedding_vector = self.get_audio_query_embedding(
                            "audio", mod_dict["index_column"]
                        )
                    elif index_col_type[n] == "image-text":
                        embedding_vector = self.get_image_query_embedding(
                            "image-text", mod_dict["index_column"]
                        )
                    else:
                        model = model_manager.text_model
                        if model_manager._use_serving:
                            embedding_vector = model(mod_dict["index_column"])
                        else:
                            embedding_vector = model.encode(mod_dict["index_column"])
                            embedding_vector = embedding_vector.tolist()

                    vectors.append(embedding_vector)
                    metadata_list.append(mod_dict)

                except IndexError:
                    logger.exception(
                        f"IndexError: index_col_type[{n}] does not exist"
                    )
                    raise
                except Exception as e:
                    traceback.print_exc()
                    logger.exception(
                        f"Error getting embedding for table {table_name}: {e}"
                    )
                    raise
                try:
                    if insert and table_name==FEEDBACK_TABLE_NAME and eval_id!="":
                        self.insert_embedding(
                            eval_id,
                            data=mod_dict,
                            table_name=f"{table_name}",
                            index_col_type=index_col_type[n],  # Accessing index_col_type[n]
                            column_name="index_column",
                            unique_key_value=mod_dict["item_id"],
                        )

                except Exception:
                    traceback.print_exc()
                    
                    raise

            return vectors, metadata_list

        except Exception as e:
            traceback.print_exc()
            logger.exception(f"Error in data_formatter: {e}")
            return [], []

    # @log_performance
    # Modified version
    def parallel_process_metadata(
        self,
        eval_id,
        metadatas,
        inputs_formater,
        table_name=FEEDBACK_TABLE_NAME,
        batch_size=50,
        organization_id=None,
        workspace_id=None,
    ):
        if table_name == FEEDBACK_TABLE_NAME:
            db_client = get_vector_db()
            db_client.create_table(table_name)
            vectors, metadata_list = self.data_formatter(
                row_dict=metadatas,
                inputs_formater=inputs_formater,
                table_name=table_name,
                insert=True,
                eval_id=eval_id,
                organization_id=organization_id,
                workspace_id=workspace_id,
            )
        else:
            def process_batch(batch):
                vectors_batch = []
                metadata_batch = []
                db_client = get_vector_db()

                try:
                    db_client.create_table(table_name)
                    for metadata in batch:
                        vectors, metadata_list = self.data_formatter(
                            row_dict=metadata,
                            inputs_formater=inputs_formater,
                            table_name=table_name,
                        )
                        if vectors and metadata_list:
                            vectors_batch.extend(vectors)
                            metadata_batch.extend(metadata_list)

                    if vectors_batch and metadata_batch:
                        return db_client.bulk_upsert_vectors(
                            table_name=table_name,
                            eval_id=eval_id,
                            vectors=vectors_batch,
                            metadata_list=metadata_batch,
                            unique_keys=["item_id"],
                        )
                finally:
                    db_client.close()
                return []

            # Split metadatas into batches
            batches = [
                metadatas[i : i + batch_size] for i in range(0, len(metadatas), batch_size)
            ]

            all_item_ids = []
            errors = []

            # Wrap function with OTel context propagation for thread safety
            wrapped_process_batch = wrap_for_thread(process_batch)

            with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
                future_to_batch = {
                    executor.submit(wrapped_process_batch, batch): batch for batch in batches
                }

                for future in concurrent.futures.as_completed(future_to_batch):
                    try:
                        new_ids = future.result()
                        all_item_ids.extend(new_ids)
                    except Exception as e:
                        errors.append(str(e))
                        logger.error(f"Batch processing error: {e}")

            if errors:
                raise RuntimeError(f"Errors occurred during processing:  {''.join(errors)}")

            return all_item_ids

    def bulk_insert_vectors(self, table_name, eval_id, vectors, metadata_list):
        """
        Bulk inserts vectors and their metadata into the specified table.

        Args:
            table_name (str): Name of the table to insert into
            eval_id (str): Evaluation ID to associate with all vectors
            vectors (list): List of embedding vectors
            metadata_list (list): List of metadata dictionaries
        """
        try:
            # Create a new connection for this operation
            db_client = get_vector_db()
            try:
                db_client.create_table(table_name)
                new_ids = db_client.bulk_upsert_vectors(
                    table_name=table_name,
                    eval_id=eval_id,
                    vectors=vectors,
                    metadata_list=metadata_list,
                    unique_keys=["item_id"],
                )
                return new_ids
            finally:
                db_client.close()
        except Exception as e:
            traceback.print_exc()
            logger.exception(f"Error in bulk insert for table {table_name}: {e}")
            raise

    def get_top_union_items(self, results_dict, top_k):
        """
        Finds the union of item_ids and returns top_k results sorted by original similarity.

        Args:
            results_dict (dict): Dictionary with input names as keys and
                                dictionaries of item_id and similarity as values.
            top_k (int): Number of top results to return.

        Returns:
            list: List of full items sorted by highest original similarity.
        """
        input_keys = list(results_dict.keys())
        all_item_ids = set()

        # Collect all unique item_ids across inputs
        for key in input_keys:
            all_item_ids.update(results_dict[key].keys())

        # Store items with their highest similarity
        union_items = []

        for item_id in all_item_ids:
            best_similarity = -float("inf")
            best_item = None

            for key in input_keys:
                if item_id in results_dict[key]:
                    similarity = results_dict[key][item_id][-1]
                    item = results_dict[key][item_id][-2]

                    if similarity > best_similarity:
                        best_similarity = similarity
                        best_item = item

            if best_item is not None:
                union_items.append(
                    {
                        "item_id": item_id,
                        "similarity": best_similarity,
                        "item": best_item,
                    }
                )

        # Sort by highest similarity
        union_items.sort(key=lambda x: x["similarity"])

        # Extract the full items for the top_k results
        top_items = [
            item["item"] for item in union_items[: min(top_k, len(union_items))]
        ]

        return top_items

    def get_top_common_items(self, results_dict, top_k):
        """
        Finds common item_ids, computes average similarity, and returns top_k results.

        Args:
            results_dict (dict): Dictionary with input names as keys and
                                dictionaries of item_id and similarity as values.
            top_k (int): Number of top results to return.

        Returns:
            list: List of dictionaries with item_id and average similarity, sorted by similarity.
        """
        # Get all input keys from the dictionary
        input_keys = list(results_dict.keys())
        if not input_keys:
            return []

        common_item_ids = set(results_dict[input_keys[0]].keys())
        for key in input_keys[1:]:
            common_item_ids &= set(results_dict[key].keys())

        # Compute average similarity for common items
        common_items = [
            {
                "item_id": item_id,
                "avg_similarity": sum(
                    results_dict[key][item_id][-1] for key in input_keys
                )
                / len(input_keys),
                "items": [results_dict[key][item_id][-2] for key in input_keys],
            }
            for item_id in common_item_ids
        ]

        # Sort by average similarity in descending order
        common_items.sort(key=lambda x: x["avg_similarity"], reverse=True)

        # Extract the full items for the top_k common items
        top_items = [
            item["items"] for item in common_items[: min(top_k, len(common_items))]
        ]

        # Return only the list of full items
        return top_items

    def retrieve_rag_based_examples(
        self,
        query,
        table_name,
        eval_id,
        meta_data_col,
        input_type="text",
        rag_type="normal",
        filter_by=None,
        top_k=20,
        threshold=None,
        syn_data_flag=False,
        organization_id=None,
        workspace_id=None,
    ):
        """
        Retrieves examples based on similarity using RAG-based logic.

        Args:
            query (str): The input query to find similar items.
            filter_by (dict, optional): Additional filters to apply. Defaults to {}.
            top_k (int, optional): Number of top similar items to retrieve. Defaults to 5.
            organization_id (str, optional): Organization ID for data isolation.
            workspace_id (str, optional): Workspace ID for data isolation.

        Returns:
            list: List of similar items for the query.
        """
        if filter_by is None:
            filter_by = {}
        results = []
        try:
            if rag_type != "normal":
                model = model_manager.image_text_model
                if model_manager._use_serving:
                    query_embedding = model(query)
                else:
                    query_embedding = model.encode(query)
                    query_embedding = query_embedding.tolist()
            else:
                if input_type == "image":
                    query_embedding = self.get_image_query_embedding("image", query)
                elif input_type == "image-text":
                    query_embedding = self.get_image_query_embedding(
                        "image-text", query
                    )
                elif input_type == "audio":
                    query_embedding = self.get_audio_query_embedding("audio", query)
                else:
                    model = model_manager.text_model
                    if model_manager._use_serving:
                        query_embedding = model(query)
                    else:
                        query_embedding = model.encode(query)
                        query_embedding = query_embedding.tolist()

            db_client = self.db_client
            filter_by["input_type"] = input_type

            if table_name == FEEDBACK_TABLE_NAME:
                if organization_id:
                    filter_by["organization_id"] = str(organization_id)
                if workspace_id:
                    filter_by["workspace_id"] = str(workspace_id)
            # print(f"[FEEDBACK QUERY] retrieve_rag_based_examples: eval_id={eval_id} meta_data_col={meta_data_col} input_type={input_type} filter_by={filter_by} query={str(query)[:100]} top_k={top_k}", flush=True)
            try:
                if threshold:
                    results = db_client.vector_similarity_search_with_threshold(
                        table_name,
                        query_embedding,
                        filter_by,
                        meta_data_col,
                        eval_id,
                    top_k,
                    threshold
                )
                else:
                    results = db_client.vector_similarity_search(
                        table_name,
                        query_embedding,
                        filter_by,
                        meta_data_col,
                        eval_id,
                    top_k,
                    syn_data_flag,
                )
            except:
                traceback.print_exc()
            # print(f"[FEEDBACK QUERY] retrieve_rag_based_examples: returned {len(results)} results", flush=True)

        except Exception as e:
            traceback.print_exc()
            logger.exception(f"Error in retrieve_rag_based_examples: {e}")
        return results

    def retrieve_avg_rag_based_examples(
        self,
        eval_id,
        inputs,
        input_cols,
        table_name=FEEDBACK_TABLE_NAME,
        rag_type="normal",
        filter_by=None,
        top_k=5,
        threshold=None,
        syn_data_flag=False,
        organization_id=None,
        workspace_id=None,
    ):
        # get input types
        # row_dict
        if filter_by is None:
            filter_by = {}

        # Skip the embed loop when there are no rows to match against.
        if table_name == FEEDBACK_TABLE_NAME and eval_id:
            if self.get_feedback_count(
                eval_id=str(eval_id),
                organization_id=organization_id,
                workspace_id=workspace_id,
            ) == 0:
                logger.info(
                    "retrieve_avg_rag_based_examples skipped: no feedback rows",
                    eval_id=str(eval_id),
                )
                return []

        self.input_types = self.inputs_type_list(inputs)

        results = {}
        start_time = datetime.now()
        for n, inp in enumerate(inputs):
            try:
                if n >= len(input_cols) or n >= len(self.input_types):
                    logger.warning(f"Skipping input {n} due to mismatch in column names or types")
                    continue
                    
                # print(f"[FEEDBACK AVG_RAG] input[{n}]: col={input_cols[n]} type={self.input_types[n]} query={str(inp)[:100]} eval_id={eval_id} org={organization_id} ws={workspace_id}", flush=True)
                x = self.retrieve_rag_based_examples(
                    inp,
                    table_name,
                    eval_id,
                    input_cols[n],
                    input_type=self.input_types[n],
                    filter_by={},
                    top_k=20,
                    threshold=threshold,
                    syn_data_flag=syn_data_flag,
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                )
                # print(f"[FEEDBACK AVG_RAG] input[{n}]: got {len(x) if x else 0} raw results", flush=True)
                if threshold and x:
                    # Sort items by similarity score and get top 4
                    results[inp] = [{"similarity": i['similarity'] ,"chunk_text": i['metadata']['chunk_text'] } for i in x]
                elif x:
                    results[inp] = {i[-2]["item_id"]: i for i in x}
                    # print(f"[FEEDBACK AVG_RAG] input[{n}]: {len(results[inp])} unique items by item_id", flush=True)
            except:
                traceback.print_exc()
        end_time = datetime.now()
        elapsed_time = (end_time - start_time).total_seconds()
        logger.info(
            f"retrieve_rag_based_examples query took {elapsed_time:.2f} seconds to execute"
        )
        start_time = datetime.now()
        top_results = []
        if threshold and results.values():
            # Flatten the results into a single list of items
            all_items = []
            for _input_key, items in results.items():
                all_items.extend(items)

            # Sort by similarity and get top k
            top_results = sorted(all_items, key=lambda x: x['similarity'], reverse=True)[:top_k]
        else:
            top_results = (
                self.get_top_common_items(results, top_k)
                if table_name == FEEDBACK_TABLE_NAME
                else self.get_top_union_items(results, top_k)
            )
        end_time = datetime.now()
        elapsed_time = (end_time - start_time).total_seconds()
        logger.info(
            f"get_top_common_items query took {elapsed_time:.2f} seconds to execute"
        )
        return top_results

    def get_relevant_chunks(
        self,
        query,
        table_name,
        eval_id,
        index_col_type,
        input_cols,
        rag_type="normal",
        filter_by=None,
        top_k=5000,
        metadata_data = False
    ):
        """
        Retrieves relevant chunks from a vector database based on a query.

        Args:
            query (str): The query to search for relevant chunks.
            table_name (str): The name of the vector database table.
            eval_id (str): The evaluation ID to associate with the new chunks.
            rag_type (str, optional): Type of RAG to use. Defaults to "normal".
            filter_by (dict, optional): Additional filters to apply. Defaults to {}.
            top_k (int, optional): Number of top similar items to retrieve. Defaults to 100.

        Returns:
            str: The new doc_id created in the same table
        """
        if filter_by is None:
            filter_by = {}
        try:
            db_client = self.db_client

            # Get embedding for the query
            model = model_manager.text_model
            if model_manager._use_serving:
                query_embedding = model(query)
            else:
                query_embedding = model.encode(query)
                query_embedding = query_embedding.tolist()

            # Generate a new doc_id for the relevant chunks
            new_doc_id = []

            # Perform similarity search
            try:
                similar_chunks = db_client.vector_similarity_search(
                    table_name=table_name,
                    query_vector=query_embedding,
                    filter_by=filter_by,
                    metadata_column_not_null=input_cols,
                    eval_id=eval_id,
                    top_k=top_k,
                )
            except Exception as e:
                traceback.print_exc()
                logger.exception(f"Error in vector similarity search: {e}")
                return new_doc_id

            filtered_chunks = []
            # Process and filter similar chunks using knee detection for adaptive threshold
            similarities = [chunk[3] for chunk in similar_chunks]

            import numpy as np

            def knee_threshold(scores):
                if not scores:
                    return 0.0
                sorted_scores = -np.sort(
                    -np.array(scores)
                )  # Sort scores in descending order
                n = len(sorted_scores)
                start = np.array([0, sorted_scores[0]])
                end = np.array([n - 1, sorted_scores[-1]])
                line_vec = end - start
                norm_line = np.linalg.norm(line_vec)
                if norm_line == 0:
                    return sorted_scores[0]
                line_unitvec = line_vec / norm_line
                distances = []
                for i, score in enumerate(sorted_scores):
                    point = np.array([i, score])
                    vec = point - start
                    proj = np.dot(vec, line_unitvec)
                    proj_point = start + proj * line_unitvec
                    distances.append(np.linalg.norm(point - proj_point))
                knee_index = int(np.argmax(distances))
                return sorted_scores[knee_index]

            sim_threshold = knee_threshold(similarities)
            # Filter chunks based on the knee-detected similarity threshold
            for chunk in similar_chunks:
                if chunk[3] >= sim_threshold:
                    filtered_chunks.append(chunk)

            if filtered_chunks:
                # Use adaptive percentile filtering: only keep chunks with similarity at or above the 80th percentile
                similarity_scores = [chunk[3] for chunk in filtered_chunks]
                adaptive_threshold = np.percentile(similarity_scores, 80)
                filtered_chunks = [
                    chunk for chunk in filtered_chunks if chunk[3] >= adaptive_threshold
                ]
            else:
                return False
            logger.info(f"Length of filtered chunks: {len(filtered_chunks)}")

            # Process filtered chunks
            if metadata_data:
                metadata = []
                for chunk in filtered_chunks:
                    metadata.append(chunk[2])
                return metadata
            else:
                for chunk in filtered_chunks:
                    new_doc_id.append(chunk[0])

            return new_doc_id

        except Exception as e:
            traceback.print_exc()
            logger.exception(f"Error in get_relevant_chunks: {e}")
            return str(uuid.uuid4())

    def fix_base64_padding(self, base64_string):
        missing_padding = len(base64_string) % 4
        if missing_padding != 0:
            base64_string += "=" * (4 - missing_padding)  # Add the required padding
        return base64_string

    def to_byte_image(self, image):
        if isinstance(image, str) and os.path.isfile(image):
            image = Image.open(image)

            buffered = BytesIO()
            image.save(buffered, format="JPEG")
            # Get the byte data
            img_byte = buffered.getvalue()
            # Encode the byte data to base64
            img_base64 = base64.b64encode(img_byte).decode("utf-8")
            # Create the base64 string with the data URI scheme
            img_base64_str = f"data:image/jpeg;base64,{img_base64}"
        else:
            img_base64_str = f"data:image/jpeg;base64,{image}"
        # Output the result
        return img_base64_str

    def decode_path(self, encoded_path: str) -> str:
        padding = len(encoded_path) % 4
        if padding:
            encoded_path += "=" * (4 - padding)
        return base64.b64decode(encoded_path.encode()).decode()

    def encode_path(self, path: str) -> str:
        return base64.b64encode(str(path).encode()).decode()

    def process_examples(
        self, dpp_examples, inputs, feedback_col_name, corrected_label_col_name
    ):
        """
        Processes and formats examples for multiple inputs and input types.

        Args:
            dpp_examples (list): List of examples retrieved from vector similarity search.
            input_types (list): List of input types (e.g., "text", "image").
            inputs (list): List of corresponding input column names.

        Returns:
            list: A list of formatted content for display.
        """

        if len(dpp_examples) == 0:
            return ""
        try:
            content = []

            try:
                from ee.prompts.eval_prompts import FEEDBACK_FEWSHOT_PROMPT
            except ImportError:
                FEEDBACK_FEWSHOT_PROMPT = ""

            content.append({"type": "text", "text": FEEDBACK_FEWSHOT_PROMPT})

            for example_idx, example in enumerate(dpp_examples):

                for input_idx, input_type in enumerate(self.input_types):
                    if input_idx >= len(inputs):
                        logger.warning(f"Skipping input_idx {input_idx} in process_examples due to input length mismatch")
                        continue
                    column_name = inputs[input_idx]

                    if input_type == "image":
                        input_string = example[0][column_name]
                        formatted_str = self.decode_path(input_string)
                        fewshot_image_url = upload_image_to_s3(
                            formatted_str,
                            bucket_name=os.environ.get("S3_CUSTOMER_DATA_BUCKET", "fi-customer-data-dev"),
                            object_key=f"tempcust/{uuid.uuid4()}",
                        )

                        content.append(
                            {
                                "type": "text",
                                "text": f"<example_image_{example_idx+1}_{input_idx+1}>",
                            }
                        )
                        content.append(
                            {
                                "type": "image_url",
                                "image_url": {"url": fewshot_image_url},
                            }
                        )
                        content.append(
                            {
                                "type": "text",
                                "text": f"</example_image_{example_idx+1}_{input_idx+1}>",
                            }
                        )

                    elif input_type == "text":
                        input_string = example[0][str(column_name)]
                        content.append({"type": "text", "text": f"{input_string}"})

                # Add feedback and corrected label information if available
                feedback_text = (
                    f"Feedback: {example[0][feedback_col_name]}, "
                    if example[0][feedback_col_name]
                    else ""
                )
                corrected_label = (
                    f"Corrected Label: {example[0][corrected_label_col_name]}"
                    if example[0][corrected_label_col_name]
                    else ""
                )
                label_text = f"{feedback_text}{corrected_label}"

                content.append({"type": "text", "text": label_text})

        except Exception as e:
            logger.exception(f"Error in process_examples: {e}")
            traceback.print_exc()

        return content

    def delete_table(self, table_name, num_tables):
        for i in range(num_tables):
            db_client = self.db_client

            db_client.drop_table(f"{table_name}_{i}")

    def delete_chunks(
        self, file_id: str, kb_id: str, table_name: str, organization_id: str
    ) -> None:
        """
        Deletes chunks from the knowledge base based on eval_id and source.

        Args:
            file_id (str): The file identifier of the chunks to delete
            kb_id (str): The knowledge base ID associated with the chunks
            table_name (str): The name of the table to delete from
            organization_id (str): The organization ID to associate with the chunks
        """
        try:
            if hasattr(self.db_client, "delete_chunks_by_file"):
                self.db_client.delete_chunks_by_file(table_name, kb_id, file_id, organization_id)
                logger.info(
                    f"Successfully deleted chunks with eval_id {kb_id} and file_id {file_id} from table {table_name}"
                )
                return

            # ClickHouse path
            check_query = f"EXISTS TABLE {table_name}"
            table_exists = self.db_client.client.execute(check_query)[0][0]

            if not table_exists:
                logger.warning(
                    f"Table {table_name} does not exist. Skipping delete operation."
                )
                return

            delete_query = f"""
            ALTER TABLE {table_name}
            DELETE WHERE eval_id = '{kb_id}'
            AND arrayExists(x -> x = 'file_id', metadata.key)
            AND arrayElement(metadata.value, indexOf(metadata.key, 'file_id')) = '{file_id}'
            AND arrayExists(x -> x = 'organization_id', metadata.key)
            AND arrayElement(metadata.value, indexOf(metadata.key, 'organization_id')) = '{organization_id}'
            """

            self.db_client.client.execute(delete_query)
            logger.info(
                f"Successfully deleted chunks with eval_id {kb_id} and file_id {file_id} from table {table_name}"
            )

        except Exception as e:
            traceback.print_exc()
            logger.error(f"Error deleting chunks: {e}")

    def retrieve_rag_cells(
        self,
        query,
        dataset_id,
        table_name="dataset_embeddings",
        input_type="text",
        filter_by=None,
        top_k=None,
    ):
        """
        Retrieves cells based on similarity using RAG-based logic.

        Args:
            query (str): The input query to find similar items.
            filter_by (dict, optional): Additional filters to apply. Defaults to {}.
            top_k (int, optional): Number of top similar items to retrieve. Defaults to 5.

        Returns:
            list: List of similar items for the query.
        """
        if filter_by is None:
            filter_by = {}
        try:
            self.db_client.create_table(table_name)
            query_embedding = None
            if input_type == "text":
                if (
                    filter_by
                    and isinstance(filter_by, dict)
                    and "input_type" in filter_by
                ):
                    if filter_by["input_type"] == "image":
                        query_embedding = self.get_image_query_embedding(
                            "image-text", query
                        )
                    elif filter_by["input_type"] == "audio":
                        query_embedding = self.get_image_query_embedding(
                            "image-text", query
                        )

            if not query_embedding:
                return None

            db_client = self.db_client

            results = db_client.vector_similarity_search_with_threshold(
                table_name=table_name,
                query_vector=query_embedding,
                filter_by=filter_by,
                dataset_id=dataset_id,
            )
            return results

        except Exception as e:
            logger.exception(f"Error in retrieve_rag_cells: {e}")
            return None

    def get_embedding_data(self, cells):
        """
        Generate embedding vectors for a batch of cells.

        Args:
            cells: QuerySet of Cell objects to process

        Returns:
            list: List of dictionaries with vector data ready for bulk insertion
        """
        vectors_data = []

        for cell in cells:
            try:
                input_type = cell.column.data_type

                cell_value = cell.value
                if not cell_value:
                    logger.warning(f"Skipping cell {cell.id} - empty value")
                    continue

                metadata = {
                    "cell_id": str(cell.id),
                    "dataset_id": str(cell.dataset.id) if cell.dataset else "unknown",
                    "input_type": input_type,
                    "media_url": cell_value,
                    "processed_timestamp": datetime.now().isoformat(),
                }

                # Generate embedding vector based on input type
                if input_type == DataTypeChoices.IMAGE.value:
                    embedding_vector = self.get_image_query_embedding(
                        "image", cell_value
                    )
                elif input_type == DataTypeChoices.AUDIO.value:
                    embedding_vector = self.get_audio_query_embedding(
                        "audio", cell_value
                    )
                elif input_type == DataTypeChoices.TEXT.value:
                    model = model_manager.text_model
                    if model_manager._use_serving:
                        embedding_vector = model(cell_value)


                else:
                    logger.warning(
                        f"Skipping cell {cell.id} - unsupported input type: {input_type}"
                    )
                    continue

                # Skip if embedding generation failed
                if not embedding_vector:
                    logger.warning(
                        f"Skipping cell {cell.id} - failed to generate embedding for {input_type}"
                    )
                    continue

                vectors_data.append(
                    {
                        "eval_id": cell.dataset.id if cell.dataset else "unknown",
                        "vector": embedding_vector,
                        "metadata": metadata,
                        "cell_id": str(cell.id),
                    }
                )

            except Exception as e:
                logger.exception(f"Error processing cell {cell.id}: {e}")
                continue

        return vectors_data

    def bulk_insert_embeddings(self, table_name, vectors_data, unique_key="cell_id"):
        """
        Perform bulk insertion of embedding vectors and update cell metadata.

        Args:
            table_name: Name of the ClickHouse table
            vectors_data: List of dictionaries with vector data
            unique_key: Key to use for uniqueness constraints

        Returns:
            list: IDs of successfully processed cells
        """
        if not vectors_data:
            return []

        try:
            cell_ids = []
            vectors = []
            metadata_list = []
            eval_id = None

            for item in vectors_data:
                if eval_id is None:
                    eval_id = item["eval_id"]
                vectors.append(item["vector"])
                metadata_list.append(item["metadata"])
                cell_id = item.get("cell_id")
                if cell_id:
                    cell_ids.append(cell_id)

            # Perform bulk upsert
            self.db_client.bulk_upsert_vectors(
                table_name=table_name,
                eval_id=eval_id,
                vectors=vectors,
                metadata_list=metadata_list,
                unique_keys=[unique_key],
            )

            cells_to_update = list(Cell.objects.filter(id__in=cell_ids))

            for cell in cells_to_update:
                metadata = cell.column_metadata if cell.column_metadata else {}
                metadata["embedding"] = True
                if "audio_duration_seconds" not in metadata:
                    metadata["audio_duration_seconds"] = 0
                cell.column_metadata = metadata

            Cell.objects.bulk_update(cells_to_update, ["column_metadata"])

            return cell_ids

        except Exception as e:
            logger.exception(f"Error in bulk_insert_embeddings: {e}")
            return []
