import ast
import json
import re
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import requests
import structlog
import weaviate
from django.db import close_old_connections, transaction
from django.shortcuts import get_object_or_404
from pinecone import Pinecone
from qdrant_client import QdrantClient
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from weaviate import AuthApiKey

from agentic_eval.core.embeddings.embedding_manager import (
    model_manager,
)

logger = structlog.get_logger(__name__)
from agentic_eval.core_evals.fi_evals import *  # noqa: F403
from agentic_eval.core_evals.run_prompt.litellm_response import RunPrompt
from model_hub.models.api_key import ApiKey, SecretModel
from model_hub.models.choices import (
    CellStatus,
    DataTypeChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import (
    Cell,
    Column,
    Dataset,
    Row,
)
from model_hub.utils.json_path_resolver import parse_json_safely, resolve_json_path
from model_hub.utils.utils import (
    contains_sql,
    remove_empty_text_from_messages,
)
from model_hub.views.run_prompt import populate_placeholders

# Define a Celery task for running the evaluation
# Define a Celery task for running the evaluation
from tfc.telemetry import wrap_for_thread
from tfc.temporal import temporal_activity
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods

# =============================================================================
# Constants for batch processing
# =============================================================================
BATCH_SIZE = 500  # Number of cells to process in each batch for bulk operations


class AddVectorDBColumnView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]
    organization_id = None

    def _get_api_key_for_provider(self, organization_id, workspace_id, provider):
        """Get API key for a provider, filtering by workspace"""
        api_key_entry = ApiKey.objects.filter(
            organization_id=organization_id,
            workspace_id=workspace_id,
            provider=provider,
        ).first()
        if not api_key_entry:
            raise ValueError(
                f"API key not configured for {provider}. Please add your API key in settings."
            )
        return api_key_entry.actual_key

    def _query_vector_db(self, text_input, config, organization_id, workspace_id=None):
        """Query vector database using text input"""
        try:
            # 1. Set up the embedding model based on config
            embedding_config = config.get("embedding_config", {})
            embedding_type = embedding_config.get("type", "")
            if embedding_type == "openai":
                api_key = self._get_api_key_for_provider(
                    organization_id, workspace_id, "openai"
                )
                embedding_vector = model_manager.get_embeddings(
                    text_input,
                    "openai",
                    embedding_config.get("model", "text-embedding-3-small"),
                    model_params={"api_key": api_key},
                )
            elif embedding_type == "huggingface":
                api_key = self._get_api_key_for_provider(
                    organization_id, workspace_id, "huggingface"
                )
                embedding_vector = model_manager.get_embeddings(
                    text_input,
                    "huggingface",
                    embedding_config.get("model", "all-mpnet-base-v2"),
                    model_params={"api_key": api_key},
                )
            elif embedding_type == "sentence_transformers":
                api_key = self._get_api_key_for_provider(
                    organization_id, workspace_id, "huggingface"
                )
                embedding_vector = model_manager.get_embeddings(
                    text_input,
                    "sentence_transformers",
                    embedding_config.get("model", "all-mpnet-base-v2"),
                    model_params={"api_key": api_key},
                )
            else:
                raise ValueError(f"Unsupported embedding type: {embedding_type}")

            if isinstance(embedding_vector, str):
                embedding_vector = [embedding_vector]

            # Ensure the vector has 512 dimensions
            if isinstance(embedding_vector, list) or hasattr(
                embedding_vector, "tolist"
            ):
                embedding_vector = np.array(
                    embedding_vector
                )  # Convert to numpy array for processing
                embedding_vector = embedding_vector[
                    : config.get("vector_length", 512)
                ]  # Truncate to 512 dimensions
                embedding_vector = (
                    embedding_vector.tolist()
                )  # Convert back to list for Weaviat

            # 3. Initialize vector store based on type
            sub_type = config.get("sub_type")

            if sub_type == "pinecone":
                return self._query_pinecone(embedding_vector, config)

            elif sub_type == "qdrant":
                return self._query_qdrant(embedding_vector, config)

            elif sub_type == "weaviate":
                return self._query_weaviate(
                    embedding_vector, text_input, config, organization_id, workspace_id
                )

            else:
                raise ValueError(f"Unsupported vector database type: {sub_type}")

        except Exception as e:
            logger.exception(f"Error in vector embedding database: {str(e)}")
            return str(e)

    def _query_pinecone(self, query, config):
        """
                {
            "indexName": "apicall2",
            "namespace": "garvit",
            "topK": 2,
            "queryKey": "vector",
            "embeddingConfig": {
                "model": "text-embedding-3-small",
                "type": "openai"
            },
            "key": "text",
            "concurrency": 2,
            "vectorLength": 512
        }

        """
        pc = Pinecone(api_key=SecretModel.objects.get(id=config["api_key"]).actual_key)
        index = pc.Index(config["index_name"])
        query_object = {}

        # Validate that query is not None or empty
        if not query or (isinstance(query, list) and len(query) == 0):
            raise ValueError("Query vector is empty or None")

        # Ensure query is a list of numbers
        if not isinstance(query, list):
            raise ValueError(f"Query must be a list, got {type(query)}")

        # Validate that all elements are numbers
        if not all(isinstance(x, int | float) for x in query):
            raise ValueError("All elements in query vector must be numbers")

        query_object["vector"] = query
        query_object["top_k"] = config["top_k"]
        query_object["namespace"] = config.get("namespace", "default")
        # Add include_metadata=True to get metadata
        query_object["include_metadata"] = True
        # Add include_values=True if you want vector values
        query_object["include_values"] = True

        results = index.query(**query_object)
        if not results["matches"]:
            return "No matches found"

        metadata_list = []
        for result in results["matches"]:
            metadata = result.get("metadata", {})
            if config.get("key"):
                metadata = metadata.get(config["key"])
            metadata_list.append(metadata)

        return metadata_list

    def _query_qdrant(self, query, config):
        """Query Qdrant vector database

                {
            "subType": "qdrant",
            "newColumnName": "valuss",
            "columnId": "dddb8ffb-1de1-4b98-a489-de6dd33eb884",
            "apiKey": "your-qdrant-api-key",
            "topK": 2,
            "embeddingConfig": {
                "model": "text-embedding-3-small",
                "type": "openai"
            },
            "concurrency": 2,
            "url": "https://0763e883-fc79-4636-a8e3-cdeb47f024be.us-east-1-0.aws.cloud.qdrant.io:6333",
            "collectionName": "mid",
            "key": "name",
            "vectorLength": 512
        }

                Args:
                    query: Vector to search for
                    config: Dictionary containing:
                        - api_key: Qdrant API key
                        - url: Qdrant instance URL (including port)
                        - collection_name: Name of collection to search
                        - top_k: Number of results to return (default: 1)
                        - query_key: Key for vector query (default: 'vector')

                Returns:
                    Dictionary containing search results and metadata
        """
        try:
            # Validate required config parameters
            required_params = ["api_key", "url", "collection_name"]
            missing_params = [p for p in required_params if not config.get(p)]
            if missing_params:
                raise ValueError(
                    f"Missing required parameters: {', '.join(missing_params)}"
                )

            # Initialize Qdrant client
            client = QdrantClient(
                url=config["url"],
                api_key=SecretModel.objects.get(id=config["api_key"]).actual_key,
            )
            # query=[0.2, 0.1, 0.9, 0.7]

            # Build search query
            query_object = {
                "collection_name": config["collection_name"],
                "query_vector": query,  # Qdrant expects 'query_vector' instead of 'vector'
                "limit": config.get("top_k", 5),  # Default to 1 if not specified
                "with_payload": True,  # Always get metadata
                "with_vectors": False,  # Don't return vectors by default for efficiency
            }

            # Execute search
            results = client.search(**query_object)

            if not results:
                return "No matches found"

            metadata_list = []
            for result in results:
                metadata = result.payload
                if config.get("key"):
                    metadata = metadata.get(config.get("key"))
                metadata_list.append(metadata)
            return metadata_list

        except Exception as e:
            logger.error(f"Error querying Qdrant: {str(e)}")
            raise ValueError(f"Failed to query Qdrant: {str(e)}")  # noqa: B904

    def get_client(self, config, organization_id, workspace_id=None, use_hybrid=False):
        embedding_config = config.get("embedding_config", {})
        embedding_type = embedding_config.get("type", "")
        key = None
        if embedding_type:
            key = self._get_api_key_for_provider(
                organization_id, workspace_id, embedding_type
            )

        auth = AuthApiKey(
            api_key=SecretModel.objects.get(id=config["api_key"]).actual_key
        )
        connect_kwargs = {"auth_client_secret": auth}
        if key and use_hybrid:
            connect_kwargs["additional_headers"] = {"X-OpenAI-Api-Key": key}

        return weaviate.connect_to_wcs(
            cluster_url=config["url"],
            auth_credentials=auth,
            # additional_headers=additional_headers or None
        )

    def _query_weaviate(
        self, query, text_input, config, organization_id, workspace_id=None
    ):
        try:
            client = self.get_client(
                config,
                organization_id,
                workspace_id,
                config.get("search_type") == "hybrid",
            )

            search_type = config.get("search_type", "semantic_search")
            limit = config.get("top_k", 5)
            class_name = config["collection_name"]
            return_field = config.get("key")

            if search_type == "hybrid" and text_input:
                resp = (
                    client.query.get(class_name, [return_field])
                    .with_hybrid(query=text_input, alpha=0.5)
                    .with_limit(limit)
                    .do()
                )
            else:
                if not query:
                    raise ValueError("Vector query is required for semantic search")
                resp = (
                    client.query.get(class_name, [return_field])
                    .with_near_vector(
                        {"vector": query, "certainty": config.get("certainty", 0.0)}
                    )
                    .with_limit(limit)
                    .do()
                )

            data = resp.get("data", {})
            hits = data.get("Get", {}).get(class_name, [])
            if not hits:
                err = resp.get("errors", [{}])[0].get("message", "No results")
                return err

            results = [
                (
                    m.get(return_field)
                    if return_field
                    else {k: v for k, v in m.items() if k not in ("id", "_additional")}
                )
                for m in hits
            ]
            return results

        except Exception as e:
            logger.error("Error in _query_weaviate", exc_info=True)
            raise ValueError(f"Failed to query Weaviate: {e}")  # noqa: B904

    def _process_row(self, row, column, config, organization_id, workspace_id=None):
        try:
            input_cell = Cell.objects.get(row=row, column=column)
            query = input_cell.value
            result_info = self._query_vector_db(
                query, config, organization_id, workspace_id
            )
            return result_info, {}
        except Exception as e:
            logger.error("traceback : ", traceback.format_exc())
            logger.error(f"Error processing row: {str(e)}")
            return str(e), {"reason": str(e)}

    def post(self, request, dataset_id, *args, **kwargs):
        try:
            config = request.data
            self.organization_id = (
                getattr(request, "organization", None) or request.user.organization.id
            )
            column_id = config.get("column_id")
            new_column_name = config.get("new_column_name", "Vector DB Result")
            concurrency = config.get("concurrency", 5)

            if not all([column_id, config.get("sub_type"), config.get("api_key")]):
                return self._gm.bad_request(
                    get_error_message("MISSING_COLUMN_ID_SUB_TYPE_AND_API_KEY")
                )

            input_column = get_object_or_404(
                Column, id=column_id, dataset_id=dataset_id
            )

            if Column.objects.filter(
                name=new_column_name, dataset=dataset_id, deleted=False
            ).exists():
                return self._gm.bad_request(get_error_message("COLUMN_NAME_EXISTS"))

            new_column = Column.objects.create(
                name=new_column_name,
                data_type=DataTypeChoices.ARRAY.value,
                source=SourceChoices.VECTOR_DB.value,
                dataset_id=dataset_id,
                metadata={
                    "sub_type": config["sub_type"],
                    "collection_name": config.get("collection_name"),
                    "url": config.get("url"),
                    "search_type": config.get("search_type"),
                    "key": config.get("key"),
                    "limit": config.get("limit", 1),
                    "index_name": config.get("index_name"),
                    "top_k": config.get("top_k", 1),
                    "namespace": config.get("namespace"),
                    "api_key": config.get("api_key"),
                    "embedding_config": config.get("embedding_config"),
                    "column_id": str(column_id),
                    "concurrency": concurrency,
                    "query_key": config.get("query_key"),
                    "vector_length": config.get("vector_length"),
                },
            )

            dataset = Dataset.objects.get(id=dataset_id)
            column_order = dataset.column_order or []
            column_order.append(str(new_column.id))

            column_config = dataset.column_config or {}
            column_config[str(new_column.id)] = {"is_visible": True, "is_frozen": None}

            dataset.column_order = column_order
            dataset.column_config = column_config
            dataset.save()

            # Ensure config is JSON serializable
            serializable_config = json.loads(json.dumps(config))
            Column.objects.filter(id=new_column.id).update(
                status=StatusType.RUNNING.value
            )

            add_vector_db_column_async.delay(
                serializable_config,
                dataset_id,
                concurrency,
                str(input_column.id),
                getattr(request, "organization", None) or request.user.organization.id,
                new_column.id,
                str(request.workspace.id) if request.workspace else None,
            )

            return self._gm.success_response(
                {
                    "message": "Vector DB column created successfully",
                    "new_column_id": str(new_column.id),
                    "new_column_name": new_column.name,
                }
            )

        except Exception as e:
            try:
                if "new_column" in locals():
                    new_column.delete()
            except Exception:
                pass
            logger.exception(f"Error in creating vector db column: {str(e)}")
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_CREATE_VECTOR_DB_COLUMN")
            )


class ExtractJsonColumnView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def _process_cell(self, cell, json_key):
        """Process a single cell and extract JSON value.

        Raises exceptions on error to let the async task handle error status.
        """
        try:
            close_old_connections()
            if not cell.value:
                return None

            # Parse the string as a Python literal
            try:
                python_obj = ast.literal_eval(cell.value)
            except (ValueError, SyntaxError) as e:
                raise ValueError(f"Invalid data format - cannot parse as JSON: {e}")

            # Convert Python object to JSON
            json_data = json.dumps(python_obj)
            json_data = json.loads(json_data)

            # Handle nested keys using dot notation (e.g., "key1.key2")
            value = json_data
            for key in json_key.split("."):
                if not isinstance(value, dict):
                    raise ValueError(
                        f"Cannot extract key '{key}' - parent value is not a valid JSON object"
                    )
                value = value.get(key, None)
                if value is None:
                    raise KeyError(f"Key '{key}' not found in JSON")
            return str(value) if value is not None else None
        finally:
            close_old_connections()

    def post(self, request, dataset_id, *args, **kwargs):
        try:
            column_id = request.data.get("column_id")
            json_key = request.data.get("json_key")
            new_column_name = request.data.get("new_column_name")
            concurrency = request.data.get(
                "concurrency", 5
            )  # Default to 5 concurrent workers

            if not all([column_id, json_key]):
                return self._gm.bad_request(
                    get_error_message("MISSING_COLUMN_ID_AND_JSON_KEY")
                )

            # Validate max_workers
            try:
                concurrency = int(concurrency)
                if concurrency < 1:
                    return self._gm.bad_request(
                        get_error_message("CONCURRENCY_NOT_POSITIVE")
                    )
            except ValueError:
                return self._gm.bad_request(get_error_message("CONCURRENCY_INVALID"))

            if Column.objects.filter(
                name=new_column_name, dataset=dataset_id, deleted=False
            ).exists():
                return self._gm.bad_request(get_error_message("COLUMN_NAME_EXISTS"))

            # Get source column with related dataset (optimization: select_related)
            source_column = get_object_or_404(
                Column.objects.select_related("dataset"),
                id=column_id,
                dataset_id=dataset_id,
                deleted=False,
            )

            # Use transaction for atomic column creation and dataset update
            with transaction.atomic():
                # Create new column
                new_column = Column.objects.create(
                    name=new_column_name or f"{source_column.name}_{json_key}",
                    data_type=DataTypeChoices.TEXT.value,
                    source=SourceChoices.EXTRACTED_JSON.value,
                    dataset_id=dataset_id,
                    metadata={
                        "column_id": str(source_column.id),
                        "json_key": json_key,
                        "concurrency": concurrency,
                    },
                )

                # Update dataset's column order and config
                dataset = source_column.dataset
                column_order = dataset.column_order or []
                column_order.append(str(new_column.id))

                column_config = dataset.column_config or {}
                column_config[str(new_column.id)] = {
                    "is_visible": True,
                    "is_frozen": None,
                }

                dataset.column_order = column_order
                dataset.column_config = column_config
                dataset.save(update_fields=["column_order", "column_config"])
            Column.objects.filter(id=new_column.id).update(
                status=StatusType.RUNNING.value
            )
            extract_json_async.delay(
                column_id, json_key, concurrency, dataset_id, new_column.id
            )

            return self._gm.success_response(
                {
                    "message": "New column created successfully",
                    "new_column_id": str(new_column.id),
                    "new_column_name": new_column.name,
                }
            )

        except Exception as e:
            # If an error occurs, clean up the partially created column
            try:
                if "new_column" in locals():
                    new_column.delete()
            except Exception:
                pass
            logger.exception(f"Error in creation of json column: {str(e)}")
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_CREATE_JSON_COLUMN")
            )


class ClassifyColumnView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def _classify_cell(self, cell, labels, model):
        """Classify a single cell's content into one of the given labels"""
        try:
            close_old_connections()
            if not cell.value:
                return None

            prompt = (
                f"Classify the following text into exactly one of these labels: {', '.join(labels)}.\n\n"
                f"Text: {cell.value}\n\n"
                f"Strictly return only the label, nothing else."
            )

            run_prompt = RunPrompt(
                model=model,
                organization_id=cell.column.dataset.organization.id,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,  # Lower temperature for more consistent results
                frequency_penalty=0.0,  # Default frequency penalty
                presence_penalty=0.0,  # Default presence penalty
                max_tokens=4000,  # Default max tokens
                top_p=1.0,  # Default top_p
                response_format={},  # Default response format
                tool_choice=None,  # Default tool choice
                tools=[],  # Default tools
                output_format="string",  # Default output format
                workspace_id=(
                    cell.column.dataset.workspace.id
                    if cell.column.dataset.workspace
                    else None
                ),
            )

            classification, value_infos = run_prompt.litellm_response()
            # Clean up response and verify it's in labels
            classification = classification.strip().lower()
            return (
                (
                    classification
                    if classification in [label.lower() for label in labels]
                    else None
                ),
                value_infos,
            )

        except Exception as e:
            logger.exception(f"Error classifying cell: {str(e)}")
            return str(e), {"reason": str(e)}
        finally:
            close_old_connections()

    def post(self, request, dataset_id, *args, **kwargs):
        try:
            column_id = request.data.get("column_id")
            labels = request.data.get("labels", [])
            model = request.data.get("language_model_id", "gpt-4o")
            concurrency = request.data.get("concurrency", 5)
            new_column_name = request.data.get("new_column_name")

            # Validation
            if not column_id or not labels:
                return self._gm.bad_request(
                    get_error_message("MISSING_COLUMN_ID_AND_LABELS")
                )

            if not isinstance(labels, list) or len(labels) < 2:
                return self._gm.bad_request(get_error_message("LABELS_LIST_NOT_VALID"))

            # Get source column with related dataset (optimization: select_related)
            source_column = get_object_or_404(
                Column.objects.select_related("dataset"),
                id=column_id,
                dataset_id=dataset_id,
                deleted=False,
            )

            if Column.objects.filter(
                name=new_column_name, dataset=dataset_id, deleted=False
            ).exists():
                return self._gm.bad_request(get_error_message("COLUMN_NAME_EXISTS"))

            # Use transaction for atomic column creation and dataset update
            with transaction.atomic():
                # Create new column
                new_column = Column.objects.create(
                    name=new_column_name or f"{source_column.name}_classification",
                    data_type=DataTypeChoices.TEXT.value,
                    source=SourceChoices.CLASSIFICATION.value,
                    dataset_id=dataset_id,
                    metadata={
                        "labels": labels,
                        "language_model_id": model,
                        "column_id": str(column_id),
                        "concurrency": concurrency,
                    },
                )

                # Update dataset's column order and config
                dataset = source_column.dataset
                column_order = dataset.column_order or []
                column_order.append(str(new_column.id))

                column_config = dataset.column_config or {}
                column_config[str(new_column.id)] = {
                    "is_visible": True,
                    "is_frozen": None,
                }

                dataset.column_order = column_order
                dataset.column_config = column_config
                dataset.save(update_fields=["column_order", "column_config"])

            Column.objects.filter(id=new_column.id).update(
                status=StatusType.RUNNING.value
            )
            classify_column_async.delay(
                column_id, labels, model, concurrency, dataset_id, new_column.id
            )

            return self._gm.success_response(
                {
                    "message": "Classification column created successfully",
                    "new_column_id": str(new_column.id),
                    "new_column_name": new_column.name,
                }
            )

        except Exception as e:
            # Cleanup on error
            try:
                if "new_column" in locals():
                    new_column.delete()
            except Exception:
                pass
            logger.exception(f"Error in creating the classification column: {str(e)}")
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_CREATE_CLASSIFY_COLUMN")
            )


class ExtractEntitiesView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def _extract_entities(self, cell, instruction, model):
        """Extract entities from a single cell's content based on instruction"""
        try:
            close_old_connections()
            if not cell.value:
                return None, None

            prompt = f"""
            You are an AI assistant tasked with extracting entities from a given text based on a specific instruction. Your goal is to accurately identify and list the entities that match the given criteria. Follow these steps carefully:

1. First, you will be given an instruction for entity extraction. This instruction will guide what kind of entities you need to identify. Pay close attention to it:

<instruction>
{instruction}
</instruction>

2. Next, you will be presented with a text to analyze. Read through this text carefully, keeping the extraction instruction in mind:

<text>
{cell.value}
</text>

3. Extract the entities from the text according to the given instruction. Be thorough and accurate in your extraction.

4. Format your response as a JSON array of strings. Each entity you extract should be a separate string within this array.

5. Your output should strictly adhere to this format:
['entity1', 'entity2', 'entity3', ...]

6. Do not include any explanations, comments, or additional information. Your response should consist solely of the JSON array.

7. If no entities are found that match the instruction, return an empty array: []

8. Before finalizing your response, double-check the following:
   - Have you extracted all relevant entities according to the instruction?
   - Is your output formatted correctly as a JSON array of strings?
   - Have you removed any explanations or additional text?


9. Output your final response, ensuring it meets all the above requirements.

Remember, accuracy and adherence to the specified format are crucial. Your task is complete once you have provided the correctly formatted JSON array of extracted entities.

            """

            run_prompt = RunPrompt(
                model=model,
                organization_id=cell.column.dataset.organization.id,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                frequency_penalty=0.0,
                presence_penalty=0.0,
                max_tokens=4000,
                top_p=1.0,
                response_format={},
                tool_choice=None,
                tools=[],
                output_format="array",
                workspace_id=(
                    cell.column.dataset.workspace.id
                    if cell.column.dataset.workspace
                    else None
                ),
            )

            entities, value_infos = run_prompt.litellm_response()

            # Ensure the response is a valid array
            if isinstance(entities, list):
                return json.dumps(entities), value_infos
            elif isinstance(entities, dict) and "entities" in entities:
                return json.dumps(entities["entities"]), value_infos
            else:
                return None, None

        except Exception as e:
            logger.exception(f"Error extracting entities: {str(e)}")
            return str(e), {"reason": str(e)}
        finally:
            close_old_connections()

    def post(self, request, dataset_id, *args, **kwargs):
        try:
            column_id = request.data.get("column_id")
            instruction = request.data.get("instruction")
            model = request.data.get("language_model_id", "gpt-4")
            concurrency = request.data.get("concurrency", 5)
            new_column_name = request.data.get("new_column_name")

            # Validation
            if not all([column_id, instruction]):
                return self._gm.bad_request(
                    get_error_message("MISSING_COLUMN_ID_AND_INSTRUCTIONS")
                )

            # Get source column
            source_column = get_object_or_404(
                Column, id=column_id, dataset_id=dataset_id, deleted=False
            )

            if Column.objects.filter(
                name=new_column_name, dataset=dataset_id, deleted=False
            ).exists():
                return self._gm.bad_request(get_error_message("COLUMN_NAME_EXISTS"))

            # Create new column
            new_column = Column.objects.create(
                name=new_column_name or f"{source_column.name}_entities",
                data_type=DataTypeChoices.ARRAY.value,
                source=SourceChoices.EXTRACTED_ENTITIES.value,
                dataset_id=dataset_id,
                metadata={
                    "instruction": instruction,
                    "language_model_id": model,
                    "column_id": str(column_id),
                    "concurrency": concurrency,
                },
            )

            # Update dataset's column order and config
            dataset = source_column.dataset
            column_order = dataset.column_order or []
            column_order.append(str(new_column.id))

            column_config = dataset.column_config or {}
            column_config[str(new_column.id)] = {"is_visible": True, "is_frozen": None}

            dataset.column_order = column_order
            dataset.column_config = column_config
            dataset.save()
            Column.objects.filter(id=new_column.id).update(
                status=StatusType.RUNNING.value
            )

            extract_async.delay(
                column_id, instruction, model, concurrency, dataset_id, new_column.id
            )

            return self._gm.success_response(
                {
                    "message": "Entity extraction completed successfully",
                }
            )

        except Exception as e:
            # Cleanup on error
            try:
                if "new_column" in locals():
                    new_column.delete()
            except Exception:
                pass
            logger.exception(f"Error in extracting the entities: {str(e)}")
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_EXTRACT_ENTITY")
            )


class AddApiColumnView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    @staticmethod
    def _resolve_cell_value(variable_id, row):
        """Resolve a column reference (UUID, optionally followed by a JSON path) to its value."""
        base_col_id = variable_id
        json_path = None
        if len(variable_id) > 36 and variable_id[36] in ('.', '['):
            base_col_id = variable_id[:36]
            json_path = variable_id[37:] if variable_id[36] == '.' else variable_id[36:]

        cell = Cell.objects.get(column__id=base_col_id, row=row)
        if json_path and cell.value is not None:
            parsed, is_valid = parse_json_safely(cell.value)
            if is_valid:
                return resolve_json_path(parsed, json_path)
        return cell.value

    def _replace_variables(self, value, row):
        """Replace variables in a string with actual cell values"""
        if isinstance(value, str) and re.search(r"\{{.*?\}}", value):
            matches = re.findall(r"\{{(.*?)\}}", value)
            for match in matches:
                try:
                    cell_value = self._resolve_cell_value(match, row)
                    value = value.replace(
                        f"{{{{{match}}}}}",
                        str(cell_value) if cell_value is not None else "",
                    )
                except Exception as e:
                    logger.error(f"Error replacing variable: {str(e)}")
        return value

    def _make_api_call(self, cell, config):
        """Make API call for a single cell and return the result"""
        try:
            # Process parameters
            processed_params = {}
            for param_name, param_config in config.get("params", {}).items():
                if param_config["type"] == "PlainText":
                    processed_params[param_name] = param_config["value"]
                elif param_config["type"] == "Secret":
                    processed_params[param_name] = SecretModel.objects.get(
                        id=param_config["value"]
                    ).actual_key
                elif param_config["type"] == "Variable":
                    try:
                        raw_val = param_config["value"]
                        if "{{" in raw_val:
                            processed_params[param_name] = self._replace_variables(raw_val, cell.row)
                        else:
                            processed_params[param_name] = self._resolve_cell_value(raw_val, cell.row)
                    except Exception as e:
                        logger.error(f"Error replacing variable: {str(e)}")

            # Process headers
            processed_headers = {}
            for header_name, header_config in config.get("headers", {}).items():
                if header_config["type"] == "PlainText":
                    processed_headers[header_name] = header_config["value"]
                elif header_config["type"] == "Secret":
                    # secret = get_object_or_404(Secret, id=header_config['value'])
                    processed_headers[header_name] = SecretModel.objects.get(
                        id=header_config["value"]
                    ).actual_key
                elif header_config["type"] == "Variable":
                    try:
                        raw_val = header_config["value"]
                        if "{{" in raw_val:
                            processed_headers[header_name] = self._replace_variables(raw_val, cell.row)
                        else:
                            processed_headers[header_name] = self._resolve_cell_value(raw_val, cell.row)
                    except Exception as e:
                        logger.error(f"Error replacing variable: {str(e)}")

            # Process body if it exists
            body = {}
            if "body" in config:
                raw_body = config["body"]
                if isinstance(raw_body, str):
                    if cell and cell.row:
                        raw_body = self._replace_variables(raw_body, cell.row)
                    try:
                        body = json.loads(raw_body)
                    except (json.JSONDecodeError, ValueError, TypeError):
                        body = raw_body
                else:
                    for key, values in raw_body.items():
                        if not cell:
                            body[key] = values
                        else:
                            body[key] = self._replace_variables(values, cell.row)

            # Process URL — apply variable substitution if a cell/row is available
            url = config["url"]
            if cell and cell.row:
                url = self._replace_variables(url, cell.row)

            # Make the API call
            response = requests.request(
                method=config["method"],
                url=url,
                params=processed_params,
                headers=processed_headers,
                json=body if isinstance(body, (dict, list)) else None,
                data=body if isinstance(body, str) else None,
                timeout=30,
            )
            response.raise_for_status()

            # Process response based on output_type
            if config["output_type"] == "string":
                return str(response.text), {"response_status": response.status_code}
            elif config["output_type"] == "object":
                return response.json(), {"response_status": response.status_code}
            elif config["output_type"] == "array":
                return json.dumps(response.json()), {
                    "response_status": response.status_code
                }
            elif config["output_type"] == "number":
                return str(response.text), {"response_status": response.status_code}
            else:
                return response.text, {"response_status": response.status_code}

        except Exception as e:
            logger.exception(f"API call error: {str(e)}")
            return str(e), {"response_status": 400}

    def post(self, request, dataset_id, *args, **kwargs):
        try:
            column_name = request.data.get("column_name")
            config = request.data.get("config")  # URL, method, params, headers, body
            concurrency = request.data.get("concurrency", 5)

            if not all([column_name, config]):
                return self._gm.bad_request(
                    get_error_message("MISSING_COLUMN_NAME_AND_CONFIG")
                )

            # Validate config
            required_fields = ["url", "method", "output_type"]
            if not all(field in config for field in required_fields):
                return self._gm.bad_request(
                    f"Config must include: {', '.join(required_fields)}"
                )

            if Column.objects.filter(
                name=column_name, dataset=dataset_id, deleted=False
            ).exists():
                return self._gm.bad_request(get_error_message("COLUMN_NAME_EXISTS"))

            # Create new column
            new_column = Column.objects.create(
                name=column_name,
                data_type=DataTypeChoices.TEXT.value,  # You might want to make this configurable
                source=SourceChoices.API_CALL.value,
                dataset_id=dataset_id,
                metadata={
                    "url": config["url"],
                    "method": config["method"],
                    "output_type": config["output_type"],
                    "params": config.get("params", {}),
                    "headers": config.get("headers", {}),
                    "body": config.get("body", {}),
                    "concurrency": concurrency,
                },
            )

            # Update dataset's column order and config
            dataset = Dataset.objects.get(id=dataset_id)
            column_order = dataset.column_order or []
            column_order.append(str(new_column.id))

            column_config = dataset.column_config or {}
            column_config[str(new_column.id)] = {"is_visible": True, "is_frozen": None}

            dataset.column_order = column_order
            dataset.column_config = column_config
            dataset.save()
            Column.objects.filter(id=new_column.id).update(
                status=StatusType.RUNNING.value
            )
            add_api_column_async.delay(config, dataset_id, concurrency, new_column.id)

            return self._gm.success_response(
                {
                    "message": "API column created successfully",
                    "new_column_id": str(new_column.id),
                    "new_column_name": new_column.name,
                }
            )

        except Exception as e:
            # Clean up on error
            try:
                if "new_column" in locals():
                    new_column.delete()
            except Exception:
                pass
            logger.exception(f"Error in creating the api column: {str(e)}")
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_CREATE_API_COLUMN")
            )


class ExecutePythonCodeView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def _execute_python_code(self, row, code):
        """Execute restricted Python code for a single row

        WARNING: This executes user-provided code. Use with caution and only
        for trusted users. Consider implementing proper sandboxing.
        """
        try:
            if contains_sql(code):
                return "Raw SQL queries are not allowed in Python code.", {
                    "reason": "Raw SQL queries are not allowed in Python code."
                }

            # Additional security checks - use word boundaries to avoid false positives
            # (e.g., "diagnosis" contains "os" but is not dangerous)
            dangerous_patterns = [
                r"\bimport\s+os\b",
                r"\bfrom\s+os\b",
                r"\bimport\s+sys\b",
                r"\bfrom\s+sys\b",
                r"\bimport\s+subprocess\b",
                r"\bfrom\s+subprocess\b",
                r"\beval\s*\(",
                r"\bexec\s*\(",
                r"__import__\s*\(",
                r"\bopen\s*\(",
                r"\bcompile\s*\(",
                r"\bglobals\s*\(",
                r"\blocals\s*\(",
                r"\bgetattr\s*\(",
                r"\bsetattr\s*\(",
                r"\bdelattr\s*\(",
            ]
            for pattern in dangerous_patterns:
                if re.search(pattern, code):
                    return f"Dangerous pattern '{pattern}' detected in code.", {
                        "reason": f"Code contains potentially dangerous pattern for security reasons."
                    }

            # Fetch cells for the row with column names
            cells = Cell.objects.filter(row=row, deleted=False).select_related("column")

            # Create kwargs from cell data
            kwargs = {cell.column.name: cell.value for cell in cells}

            # Restricted globals - only safe builtins
            safe_builtins = {
                "abs": abs,
                "all": all,
                "any": any,
                "bool": bool,
                "dict": dict,
                "enumerate": enumerate,
                "filter": filter,
                "float": float,
                "int": int,
                "len": len,
                "list": list,
                "map": map,
                "max": max,
                "min": min,
                "range": range,
                "round": round,
                "set": set,
                "sorted": sorted,
                "str": str,
                "sum": sum,
                "tuple": tuple,
                "zip": zip,
            }
            global_namespace = {"__builtins__": safe_builtins}
            local_namespace = {}

            # Execute the provided code with restricted globals
            # WARNING: This still has security implications and should be properly sandboxed
            # exec(
            #     code, global_namespace, local_namespace
            # )  # nosec B102 - sandboxed execution

            # Validate presence of `main()` function
            if "main" not in local_namespace or not callable(local_namespace["main"]):
                raise ValueError("Code must define a callable 'main' function.")

            result = local_namespace["main"](**kwargs)

            return str(result), None

        except Exception as e:
            traceback.format_exc()
            return str(e), {"reason": str(e)}

    def post(self, request, dataset_id, *args, **kwargs):
        try:
            code = request.data.get("code")
            new_column_name = request.data.get("new_column_name")
            concurrency = request.data.get("concurrency", 5)

            # Validation
            if not all([code]):
                return self._gm.bad_request(get_error_message("CODE_MISSING"))

            if Column.objects.filter(
                name=new_column_name, dataset=dataset_id, deleted=False
            ).exists():
                return self._gm.bad_request(get_error_message("COLUMN_NAME_EXISTS"))

            # Create new column
            new_column = Column.objects.create(
                name=new_column_name if new_column_name else "Python Code Output",
                data_type=DataTypeChoices.TEXT.value,
                source=SourceChoices.PYTHON_CODE.value,
                dataset_id=dataset_id,
                metadata={"code": code, "concurrency": concurrency},
            )

            # Update dataset's column order and config
            dataset = Dataset.objects.get(id=dataset_id)
            column_order = dataset.column_order or []
            column_order.append(str(new_column.id))

            column_config = dataset.column_config or {}
            column_config[str(new_column.id)] = {"is_visible": True, "is_frozen": None}

            dataset.column_order = column_order
            dataset.column_config = column_config
            dataset.save()
            Column.objects.filter(id=new_column.id).update(
                status=StatusType.RUNNING.value
            )
            execute_python_code_async.delay(
                code, dataset_id, concurrency, new_column.id
            )

            return self._gm.success_response(
                {
                    "message": "Python code execution completed successfully",
                    "new_column_id": str(new_column.id),
                    "new_column_name": new_column.name,
                }
            )

        except Exception as e:
            # Cleanup on error
            try:
                if "new_column" in locals():
                    new_column.delete()
            except Exception:
                pass
            logger.exception(f"Error in execution of code: {str(e)}")
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_EXECUTE_CODE")
            )


class ConditionalColumnView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def _evaluate_condition(self, condition, row, org_id=None):
        """Evaluate a condition for a given row"""
        try:
            # Replace variables in condition with actual values
            if isinstance(condition, str) and re.search(r"\{{.*?\}}", condition):
                matches = re.findall(r"\{{(.*?)\}}", condition)
                for match in matches:
                    try:
                        cell = Cell.objects.get(column__id=match, row=row)
                        value = cell.value
                        if value:
                            try:
                                json_data = json.loads(value)
                                value = json.dumps(
                                    json_data
                                )  # Keep JSON data as string for evaluation
                            except json.JSONDecodeError:
                                value = str(value).strip().lower()
                        condition = condition.replace(
                            f"{{{{{match}}}}}",
                            str(cell.value) if cell.value is not None else "",
                        )
                    except Exception as e:
                        logger.error(f"Error replacing variable in condition: {str(e)}")
                        return False

            prompt = f"""
            You are an AI expert that has to evaluate if the given condition according to provided information is True or False . You will be given condition just strictly return True or False according to the condition.
            condition: {condition}
            """
            try:
                run_prompt = RunPrompt(
                    model="gpt-4o-mini",
                    organization_id=row.dataset.organization.id,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,  # Lower temperature for more consistent results
                    frequency_penalty=0.0,  # Default frequency penalty
                    presence_penalty=0.0,  # Default presence penalty
                    max_tokens=4000,  # Default max tokens
                    top_p=1.0,  # Default top_p
                    response_format={},  # Default response format
                    tool_choice=None,  # Default tool choice
                    tools=[],  # Default tools
                    output_format="string",  # Default output format
                    workspace_id=(
                        row.dataset.workspace.id if row.dataset.workspace else None
                    ),
                )

            except Exception:
                run_prompt = RunPrompt(
                    model="gpt-4o-mini",
                    organization_id=org_id,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,  # Lower temperature for more consistent results
                    frequency_penalty=0.0,  # Default frequency penalty
                    presence_penalty=0.0,  # Default presence penalty
                    max_tokens=4000,  # Default max tokens
                    top_p=1.0,  # Default top_p
                    response_format={},  # Default response format
                    tool_choice=None,  # Default tool choice
                    tools=[],  # Default tools
                    output_format="string",  # Default output format
                    workspace_id=(
                        row.dataset.workspace.id if row.dataset.workspace else None
                    ),
                )

            condition_status, value_infos = run_prompt.litellm_response()

            # Case-insensitive comparison for LLM response
            condition_status = (
                str(condition_status).strip().lower() == "true"
                if condition_status
                else False
            )
            return condition_status

        except Exception as e:
            logger.error(f"Error evaluating condition: {str(e)}")
            logger.error("traceback : ", traceback.format_exc())
            return False

    def _process_branch(self, row, branch_config, org_id=None):
        """Process a single branch configuration"""
        try:
            node_config = branch_config.get("branch_node_config", {})
            output_type = node_config.get("type")
            config = node_config.get("config", {})

            logger.debug(f"Processing branch config: {config}")

            # Get source column if specified
            source_column = None
            if config.get("column_id"):
                source_column = Column.objects.get(id=config["column_id"])

            if output_type == "static_value":
                return config.get("value"), None

            elif output_type == "column_value":
                if not config.get("column_id"):
                    return None, None
                cell = Cell.objects.get(column_id=config["column_id"], row=row)
                return cell.value, None

            elif output_type == "classification":
                if not source_column:
                    return None, None
                classifier = ClassifyColumnView()
                return classifier._classify_cell(
                    cell=Cell.objects.get(column=source_column, row=row),
                    labels=config.get("labels", []),
                    model=config.get("language_model_id"),
                )

            elif output_type == "extract_entities":
                if not source_column:
                    return None, None
                extractor = ExtractEntitiesView()
                return extractor._extract_entities(
                    cell=Cell.objects.get(column=source_column, row=row),
                    instruction=config.get("instruction"),
                    model=config.get("language_model_id"),
                )

            elif output_type == "extract_json":
                if not source_column:
                    return None, None
                json_extractor = ExtractJsonColumnView()
                result = json_extractor._process_cell(
                    cell=Cell.objects.get(column=source_column, row=row),
                    json_key=config.get("json_key"),
                )
                return result, None

            elif output_type == "extract_code":
                executor = ExecutePythonCodeView()
                return executor._execute_python_code(row, config.get("code"))

            elif output_type == "api_call":
                executor = AddApiColumnView()
                if not source_column:
                    return executor._make_api_call(
                        cell=None, config=config.get("config")
                    )
                return executor._make_api_call(
                    cell=Cell.objects.get(column=source_column, row=row),
                    config=config.get("config"),
                )

            elif output_type == "run_prompt":
                messages = populate_placeholders(
                    config.get("messages"),
                    dataset_id=row.dataset.id,
                    row_id=row.id,
                    col_id=None,
                    model_name=config.get("model"),
                    template_format=config.get("configuration", {}).get("template_format"),
                )
                messages = remove_empty_text_from_messages(messages)
                executor = RunPrompt(
                    model=config.get("model"),
                    organization_id=org_id,
                    messages=messages,
                    temperature=config.get(
                        "temperature"
                    ),  # Lower temperature for more consistent results
                    frequency_penalty=config.get(
                        "frequency_penalty"
                    ),  # Default frequency penalty
                    presence_penalty=config.get(
                        "presence_penalty"
                    ),  # Default presence penalty
                    max_tokens=config.get("max_tokens"),  # Default max tokens
                    top_p=config.get("top_p"),  # Default top_p
                    response_format=config.get(
                        "response_format"
                    ),  # Default response format
                    tool_choice=config.get("tool_choice"),  # Default tool choice
                    tools=config.get("tools"),  # Default tools
                    output_format=config.get("output_format"),  # Default output format
                    workspace_id=(
                        row.dataset.workspace.id if row.dataset.workspace else None
                    ),
                )
                return executor.litellm_response()

            elif output_type == "retrieval":
                executor = AddVectorDBColumnView()
                return executor._process_row(row, source_column, config, org_id)

            else:
                return None, None

        except Exception as e:
            traceback.print_exc()
            logger.error(f"Error processing branch: {str(e)}")
            return None, None

    def _process_row(self, row, config, org_id=None):
        """Process a single row through all conditions"""
        try:
            final_value = None
            final_value_infos = None

            # Track if any condition has been met
            condition_met = False

            logger.debug(f"Processing row with config: {config}")

            for branch in config:
                if not isinstance(branch, dict):
                    logger.error(f"Invalid branch format: {branch}")
                    continue

                branch_type = branch.get("branch_type", "").lower()
                condition = branch.get("condition", "")

                should_execute = False

                if branch_type == "if":
                    condition_met = self._evaluate_condition(condition, row, org_id)
                    should_execute = condition_met

                elif branch_type == "elif":
                    if (
                        not condition_met
                    ):  # Only check elif if no previous condition was true
                        condition_met = self._evaluate_condition(condition, row, org_id)
                        should_execute = condition_met

                elif branch_type == "else":
                    should_execute = not condition_met

                if should_execute:
                    value, value_infos = self._process_branch(
                        row, branch, org_id=org_id
                    )
                    if value is not None:
                        final_value = value
                        final_value_infos = value_infos
                        break  # Exit after first matching condition

            return final_value, final_value_infos

        except Exception as e:
            logger.error("traceback : ", traceback.format_exc())
            logger.error(f"Error processing row: {str(e)}")
            return str(e), {"reason": str(e)}

    def post(self, request, dataset_id, *args, **kwargs):
        try:
            config = request.data.get("config", [])
            new_column_name = request.data.get("new_column_name")
            concurrency = request.data.get("concurrency", 5)
            self.organization_id = (
                getattr(request, "organization", None) or request.user.organization.id
            )

            if not config:
                return self._gm.bad_request(get_error_message("CONFIG_MISSING"))
            if not new_column_name:
                return self._gm.bad_request(
                    get_error_message("NEW_COLUMN_NAME_MISSING")
                )

            dataset = Dataset.objects.get(id=dataset_id)

            if Column.objects.filter(
                name=new_column_name, dataset=dataset_id, deleted=False
            ).exists():
                return self._gm.bad_request(get_error_message("COLUMN_NAME_EXISTS"))

            # Create new column
            new_column = Column.objects.create(
                name=new_column_name,
                data_type=DataTypeChoices.TEXT.value,
                source=SourceChoices.CONDITIONAL.value,
                dataset_id=dataset_id,
                metadata={"config": config, "concurrency": concurrency},
            )

            # Update dataset configuration
            column_order = dataset.column_order or []
            column_order.append(str(new_column.id))

            column_config = dataset.column_config or {}
            column_config[str(new_column.id)] = {"is_visible": True, "is_frozen": None}

            dataset.column_order = column_order
            dataset.column_config = column_config
            dataset.save()
            Column.objects.filter(id=new_column.id).update(
                status=StatusType.RUNNING.value
            )

            conditional_column_async.delay(
                config, dataset_id, concurrency, new_column.id
            )

            return self._gm.success_response(
                {
                    "message": "Conditional column created successfully",
                    "new_column_id": str(new_column.id),
                    "new_column_name": new_column.name,
                }
            )

        except Exception as e:
            try:
                if "new_column" in locals():
                    new_column.delete()
            except Exception:
                pass
            logger.exception(f"Error in creating the conditional column: {str(e)}")
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_CRETE_CONDITIONAL_COLUMN")
            )


class GetOperationConfigView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def get(self, request, column_id, *args, **kwargs):
        """Get the configuration for all operations in a dataset"""
        try:
            # Get column that has operation metadata
            operation_column = Column.objects.filter(
                id=column_id,
                deleted=False,
                metadata__isnull=False,
                dataset__organization_id=getattr(request, "organization", None)
                or request.user.organization.id,
            ).first()

            if not operation_column:
                return self._gm.bad_request(get_error_message("COLUMN_NOT_FOUND"))

            return self._gm.success_response(
                {
                    "column_id": str(operation_column.id),
                    "metadata": operation_column.metadata,
                }
            )

        except Exception as e:
            logger.exception(f"Error in getting operation configurations: {str(e)}")
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_GET_OPERATION_CONFIGURATIONS")
            )


class RerunOperationView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def post(self, request, column_id, *args, **kwargs):
        """Rerun a specific operation with its stored configuration"""
        try:
            operation_type = request.data.get("operation_type")
            metadata = request.data.get("config", {})

            if not operation_type:
                return self._gm.bad_request(get_error_message("MISSING_OPERATION_TYPE"))

            # Get the column with its metadata
            column = get_object_or_404(
                Column,
                id=column_id,
                deleted=False,
                dataset__organization_id=getattr(request, "organization", None)
                or request.user.organization.id,
            )

            # Update column metadata with new configuration
            if metadata:
                # Merge new config with existing metadata, preserving structure
                existing_metadata = column.metadata or {}

                # Update metadata based on operation type
                if operation_type == "classify":
                    existing_metadata.update(
                        {
                            "labels": metadata.get("labels"),
                            "language_model_id": metadata.get(
                                "language_model_id", "gpt-4o"
                            ),
                            "column_id": metadata.get("column_id"),
                            "concurrency": metadata.get("concurrency", 5),
                        }
                    )
                elif operation_type == "extract_entities":
                    existing_metadata.update(
                        {
                            "instruction": metadata.get("instruction"),
                            "language_model_id": metadata.get(
                                "language_model_id", "gpt-4"
                            ),
                            "column_id": metadata.get("column_id"),
                            "concurrency": metadata.get("concurrency", 5),
                        }
                    )
                elif operation_type == "extract_json":
                    existing_metadata.update(
                        {
                            "column_id": metadata.get("column_id"),
                            "json_key": metadata.get("json_key"),
                            "concurrency": metadata.get("concurrency", 5),
                        }
                    )
                elif operation_type == "execute_code":
                    existing_metadata.update(
                        {
                            "code": metadata.get("code"),
                            "concurrency": metadata.get("concurrency", 5),
                        }
                    )
                elif operation_type == "conditional":
                    existing_metadata.update(
                        {
                            "config": metadata.get("config"),
                            "concurrency": metadata.get("concurrency", 5),
                        }
                    )
                elif operation_type == "vector_db":
                    existing_metadata.update(
                        {
                            "sub_type": metadata.get("sub_type"),
                            "collection_name": metadata.get("collection_name"),
                            "url": metadata.get("url"),
                            "search_type": metadata.get("search_type"),
                            "key": metadata.get("key"),
                            "limit": metadata.get("limit", 1),
                            "index_name": metadata.get("index_name"),
                            "top_k": metadata.get("top_k", 1),
                            "namespace": metadata.get("namespace"),
                            "api_key": metadata.get("api_key"),
                            "embedding_config": metadata.get("embedding_config"),
                            "column_id": metadata.get("column_id"),
                            "concurrency": metadata.get("concurrency", 5),
                            "query_key": metadata.get("query_key"),
                            "vector_length": metadata.get("vector_length"),
                        }
                    )
                elif operation_type == "api_call":
                    api_config = metadata.get("config", {})
                    existing_metadata.update(
                        {
                            "url": api_config.get("url", existing_metadata.get("url")),
                            "method": api_config.get(
                                "method", existing_metadata.get("method")
                            ),
                            "output_type": api_config.get(
                                "output_type",
                                existing_metadata.get("output_type"),
                            ),
                            "params": api_config.get(
                                "params", existing_metadata.get("params", {})
                            ),
                            "headers": api_config.get(
                                "headers", existing_metadata.get("headers", {})
                            ),
                            "body": api_config.get(
                                "body", existing_metadata.get("body", {})
                            ),
                            "concurrency": metadata.get("concurrency", 5),
                        }
                    )

                # Save updated metadata
                column.metadata = existing_metadata
                column.save()

            # Clear existing cell values but keep the cells
            Cell.objects.filter(column_id=column.id, deleted=False).update(
                value=None, value_infos=json.dumps({}), status=CellStatus.RUNNING.value
            )

            # Reset column status
            column.status = StatusType.RUNNING.value
            column.save()

            if operation_type == "classify":
                return self._rerun_classification(column, metadata, column.dataset.id)
            elif operation_type == "extract_entities":
                return self._rerun_entity_extraction(
                    column, metadata, column.dataset.id
                )
            elif operation_type == "extract_json":
                return self._rerun_json_extraction(column, metadata, column.dataset.id)
            elif operation_type == "execute_code":
                return self._rerun_python_code(column, metadata, column.dataset.id)
            elif operation_type == "conditional":
                return self._rerun_conditional(column, metadata, column.dataset.id)
            elif operation_type == "vector_db":
                return self._rerun_vector_db(column, metadata, column.dataset.id)
            elif operation_type == "api_call":
                return self._rerun_api_call(column, metadata, column.dataset.id)
            else:
                return self._gm.bad_request(f"Invalid operation type: {operation_type}")

        except Exception as e:
            logger.exception(f"Error in rerunning operation: {str(e)}")
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_RERUN_OPERATION")
            )

    def _rerun_classification(self, column, metadata, dataset_id):
        """Rerun classification operation"""
        try:
            # Extract configuration from metadata
            source_column_id = metadata.get("column_id")
            labels = metadata.get("labels")
            model = metadata.get("language_model_id", "gpt-4o")
            concurrency = metadata.get("concurrency", 5)

            if not all([source_column_id, labels]):
                return self._gm.bad_request(
                    get_error_message("INVALID_CLASSIFICATION_CONFIGURATION")
                )

            # Trigger the async task
            classify_column_async.delay(
                source_column_id,
                labels,
                model,
                concurrency,
                dataset_id,
                str(column.id),
                True,
            )

            return self._gm.success_response(
                {
                    "message": "Classification operation rerun successfully",
                    "column_id": str(column.id),
                    "status": "running",
                }
            )

        except Exception as e:
            logger.exception(f"Error in rerunning classification: {str(e)}")
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_RERUN_CLASSIFICATION")
            )

    def _rerun_entity_extraction(self, column, metadata, dataset_id):
        """Rerun entity extraction operation"""
        try:
            # Extract configuration from metadata
            source_column_id = metadata.get("column_id")
            instruction = metadata.get("instruction")
            model = metadata.get("language_model_id", "gpt-4")
            concurrency = metadata.get("concurrency", 5)
            if not all([source_column_id, instruction]):
                return self._gm.bad_request(
                    get_error_message("INVALID_ENTITY_EXTRACTION_CONFIGURATION")
                )

            # Trigger the async task
            extract_async.delay(
                source_column_id,
                instruction,
                model,
                concurrency,
                dataset_id,
                str(column.id),
                True,
            )

            return self._gm.success_response(
                {
                    "message": "Entity extraction operation rerun successfully",
                    "column_id": str(column.id),
                    "status": "running",
                }
            )

        except Exception as e:
            logger.exception(f"Error in rerunning entity extraction: {str(e)}")
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_RERUN_ENTITY_EXTRACTION")
            )

    def _rerun_json_extraction(self, column, metadata, dataset_id):
        """Rerun JSON extraction operation"""
        try:
            # Extract configuration from metadata
            source_column_id = metadata.get("column_id")
            json_key = metadata.get("json_key")
            concurrency = metadata.get("concurrency", 5)

            if not all([source_column_id, json_key]):
                return self._gm.bad_request(
                    get_error_message("INVALID_JSON_EXTRACTION_CONFIGURATION")
                )

            # Trigger the async task
            extract_json_async.delay(
                source_column_id,
                json_key,
                concurrency,
                dataset_id,
                str(column.id),
                True,
            )

            return self._gm.success_response(
                {
                    "message": "JSON extraction operation rerun successfully",
                    "column_id": str(column.id),
                    "status": "running",
                }
            )

        except Exception as e:
            logger.exception(f"Error in rerunning JSON extraction: {str(e)}")
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_RERUN_JSON_EXTRACTION")
            )

    def _rerun_python_code(self, column, metadata, dataset_id):
        """Rerun Python code execution operation"""
        try:
            # Extract configuration from metadata
            code = metadata.get("code")
            concurrency = metadata.get("concurrency", 5)

            if not code:
                return self._gm.bad_request(
                    get_error_message("INVALID_PYTHON_CODE_CONFIGURATION")
                )

            # Trigger the async task
            execute_python_code_async.delay(
                code, dataset_id, concurrency, str(column.id), True
            )

            return self._gm.success_response(
                {
                    "message": "Python code execution operation rerun successfully",
                    "column_id": str(column.id),
                    "status": "running",
                }
            )

        except Exception as e:
            logger.exception(f"Error in rerunning Python code execution: {str(e)}")
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_RERUN_PYTHON_CODE")
            )

    def _rerun_conditional(self, column, metadata, dataset_id):
        """Rerun conditional operation"""
        try:
            # Extract configuration from metadata
            conditional_config = metadata.get("config")
            concurrency = metadata.get("concurrency", 5)

            if not conditional_config:
                return self._gm.bad_request(
                    get_error_message("INVALID_CONDITIONAL_CONFIGURATION")
                )

            # Trigger the async task
            conditional_column_async.delay(
                conditional_config, dataset_id, concurrency, str(column.id), True
            )

            return self._gm.success_response(
                {
                    "message": "Conditional operation rerun successfully",
                    "column_id": str(column.id),
                    "status": "running",
                }
            )

        except Exception as e:
            logger.exception(f"Error in rerunning conditional operation: {str(e)}")
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_RERUN_CONDITIONAL")
            )

    def _rerun_vector_db(self, column, metadata, dataset_id):
        """Rerun vector database operation"""
        try:
            # Extract configuration from metadata
            vector_db_config = metadata
            source_column_id = metadata.get("column_id")
            concurrency = metadata.get("concurrency", 5)
            organization_id = column.dataset.organization.id

            if not all([vector_db_config, source_column_id]):
                return self._gm.bad_request(
                    get_error_message("INVALID_VECTOR_DB_CONFIGURATION")
                )

            # Trigger the async task
            add_vector_db_column_async.delay(
                vector_db_config,
                dataset_id,
                concurrency,
                source_column_id,
                organization_id,
                str(column.id),
                True,
            )

            return self._gm.success_response(
                {
                    "message": "Vector database operation rerun successfully",
                    "column_id": str(column.id),
                    "status": "running",
                }
            )

        except Exception as e:
            logger.exception(f"Error in rerunning vector database operation: {str(e)}")
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_RERUN_VECTOR_DB")
            )

    def _rerun_api_call(self, column, metadata, dataset_id):
        """Rerun API call operation"""
        try:
            # Extract configuration from metadata
            api_config = metadata.get("config")
            concurrency = metadata.get("concurrency", 5)

            if not api_config:
                return self._gm.bad_request(
                    get_error_message("INVALID_API_CALL_CONFIGURATION")
                )

            # Trigger the async task
            add_api_column_async.delay(
                api_config, dataset_id, concurrency, str(column.id), True
            )

            return self._gm.success_response(
                {
                    "message": "API call operation rerun successfully",
                    "column_id": str(column.id),
                    "status": "running",
                }
            )

        except Exception as e:
            logger.exception(f"Error in rerunning API call operation: {str(e)}")
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_RERUN_API_CALL")
            )


@temporal_activity(time_limit=3600, queue="tasks_l")
def classify_column_async(
    column_id, labels, model, concurrency, dataset_id, new_column_id, is_rerun=False
):
    view = ClassifyColumnView()
    # Process cells concurrently with select_related for optimization
    source_cells = list(
        Cell.objects.filter(column_id=column_id, deleted=False).select_related(
            "row", "column"
        )
    )
    total_processed = 0
    failed_cells = 0
    Column.objects.filter(id=new_column_id).update(status=StatusType.RUNNING.value)

    if is_rerun:
        # Update existing cells with select_related
        existing_cells = list(
            Cell.objects.filter(column_id=new_column_id, deleted=False).select_related(
                "row"
            )
        )
        existing_cells_map = {str(cell.row_id): cell for cell in existing_cells}
        cells_to_update = []
        cells_to_create = []

        # Wrap function with OTel context propagation for thread safety
        wrapped_classify_cell = wrap_for_thread(view._classify_cell)

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            future_to_cell = {
                executor.submit(wrapped_classify_cell, cell, labels, model): cell
                for cell in source_cells
            }

            for future in as_completed(future_to_cell):
                cell = future_to_cell[future]
                try:
                    classification, value_infos = future.result()
                    existing_cell = existing_cells_map.get(str(cell.row_id))

                    if existing_cell:
                        if value_infos and "reason" in value_infos:
                            failed_cells += 1
                            existing_cell.value = None
                            existing_cell.value_infos = json.dumps(value_infos)
                            existing_cell.status = CellStatus.ERROR.value
                        else:
                            existing_cell.value = classification
                            existing_cell.value_infos = json.dumps(
                                value_infos if value_infos else {}
                            )
                            existing_cell.status = CellStatus.PASS.value
                            total_processed += 1
                        cells_to_update.append(existing_cell)
                    else:
                        # Create new cell if it doesn't exist during rerun
                        cells_to_create.append(
                            Cell(
                                dataset_id=dataset_id,
                                column_id=new_column_id,
                                row=cell.row,
                                value=None,
                                value_infos=json.dumps(
                                    value_infos if value_infos else {}
                                ),
                                status=CellStatus.PASS.value,
                            )
                        )
                        logger.warning(
                            f"Created new cell for row {cell.row_id} in column {new_column_id} during rerun"
                        )

                except Exception as e:
                    failed_cells += 1
                    logger.error(f"Error processing cell: {str(e)}")

        # Bulk update and create (optimization)
        if cells_to_update:
            Cell.objects.bulk_update(
                cells_to_update,
                ["value", "value_infos", "status"],
                batch_size=BATCH_SIZE,
            )
        if cells_to_create:
            Cell.objects.bulk_create(cells_to_create, batch_size=BATCH_SIZE)
    else:
        # Create new cells (original behavior) with rate limiting
        new_cells = []

        # Wrap function with OTel context propagation for thread safety
        wrapped_classify_cell = wrap_for_thread(view._classify_cell)

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            future_to_cell = {
                executor.submit(wrapped_classify_cell, cell, labels, model): cell
                for cell in source_cells
            }

            for future in as_completed(future_to_cell):
                cell = future_to_cell[future]
                try:
                    classification, value_infos = future.result()
                    if value_infos and "reason" in value_infos:
                        failed_cells += 1
                        new_cells.append(
                            Cell(
                                dataset_id=dataset_id,
                                column_id=new_column_id,
                                row=cell.row,
                                value=None,
                                value_infos=json.dumps(value_infos),
                                status=CellStatus.ERROR.value,
                            )
                        )
                    else:
                        new_cells.append(
                            Cell(
                                dataset_id=dataset_id,
                                column_id=new_column_id,
                                row=cell.row,
                                value=classification,
                                value_infos=json.dumps(
                                    value_infos if value_infos else {}
                                ),
                            )
                        )
                        total_processed += 1
                except Exception:
                    failed_cells += 1

        # Bulk create all cells with batch_size (optimization)
        if new_cells:
            Cell.objects.bulk_create(new_cells, batch_size=BATCH_SIZE)

    Column.objects.filter(id=new_column_id).update(status=StatusType.COMPLETED.value)


@temporal_activity(time_limit=3600, queue="tasks_l")
def extract_async(
    source_column_id,
    instruction,
    model,
    concurrency,
    dataset_id,
    new_column_id,
    is_rerun=False,
):
    # Process cells concurrently with select_related (optimization)
    source_cells = list(
        Cell.objects.filter(column_id=source_column_id, deleted=False).select_related(
            "row", "column"
        )
    )
    total_processed = 0
    failed_cells = 0
    view = ExtractEntitiesView()
    Column.objects.filter(id=new_column_id).update(status=StatusType.RUNNING.value)

    if is_rerun:
        # Update existing cells with select_related
        existing_cells = list(
            Cell.objects.filter(column_id=new_column_id, deleted=False).select_related(
                "row"
            )
        )
        existing_cells_map = {str(cell.row_id): cell for cell in existing_cells}
        cells_to_update = []
        cells_to_create = []

        # Wrap function with OTel context propagation for thread safety
        wrapped_extract_entities = wrap_for_thread(view._extract_entities)

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            future_to_cell = {
                executor.submit(
                    wrapped_extract_entities, cell, instruction, model
                ): cell
                for cell in source_cells
            }

            for future in as_completed(future_to_cell):
                cell = future_to_cell[future]
                try:
                    entities, value_infos = future.result()
                    existing_cell = existing_cells_map.get(str(cell.row_id))

                    if existing_cell:
                        if value_infos and "reason" in value_infos:
                            failed_cells += 1
                            existing_cell.value = None
                            existing_cell.value_infos = json.dumps(value_infos)
                            existing_cell.status = CellStatus.ERROR.value
                        else:
                            existing_cell.value = entities
                            existing_cell.value_infos = json.dumps(
                                value_infos if value_infos else {}
                            )
                            existing_cell.status = CellStatus.PASS.value
                            total_processed += 1
                        cells_to_update.append(existing_cell)
                    else:
                        # Create new cell if it doesn't exist during rerun
                        cells_to_create.append(
                            Cell(
                                dataset_id=dataset_id,
                                column_id=new_column_id,
                                row=cell.row,
                                value=None,
                                value_infos=json.dumps(
                                    value_infos if value_infos else {}
                                ),
                                status=CellStatus.PASS.value,
                            )
                        )
                        logger.warning(
                            f"Created new cell for row {cell.row_id} in column {new_column_id} during rerun"
                        )

                except Exception as e:
                    traceback.print_exc()
                    logger.error(f"Failed to process cell: {str(e)}")
                    failed_cells += 1

        # Bulk update and create (optimization)
        if cells_to_update:
            Cell.objects.bulk_update(
                cells_to_update,
                ["value", "value_infos", "status"],
                batch_size=BATCH_SIZE,
            )
        if cells_to_create:
            Cell.objects.bulk_create(cells_to_create, batch_size=BATCH_SIZE)
    else:
        # Create new cells (original behavior) with rate limiting
        new_cells = []

        # Wrap function with OTel context propagation for thread safety
        wrapped_extract_entities = wrap_for_thread(view._extract_entities)

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            future_to_cell = {
                executor.submit(
                    wrapped_extract_entities, cell, instruction, model
                ): cell
                for cell in source_cells
            }

            for future in as_completed(future_to_cell):
                cell = future_to_cell[future]
                try:
                    entities, value_infos = future.result()
                    if value_infos and "reason" in value_infos:
                        failed_cells += 1
                        new_cells.append(
                            Cell(
                                dataset_id=dataset_id,
                                column_id=new_column_id,
                                row=cell.row,
                                value=None,
                                value_infos=json.dumps(value_infos),
                                status=CellStatus.ERROR.value,
                            )
                        )
                    else:
                        new_cells.append(
                            Cell(
                                dataset_id=dataset_id,
                                column_id=new_column_id,
                                row=cell.row,
                                value=entities,
                                value_infos=json.dumps(
                                    value_infos if value_infos else {}
                                ),
                            )
                        )
                        total_processed += 1
                except Exception as e:
                    logger.error(f"Failed to process cell: {str(e)}")
                    failed_cells += 1

        # Bulk create all cells with batch_size (optimization)
        if new_cells:
            Cell.objects.bulk_create(new_cells, batch_size=BATCH_SIZE)

    Column.objects.filter(id=new_column_id).update(status=StatusType.COMPLETED.value)


@temporal_activity(time_limit=3600, queue="tasks_l")
def extract_json_async(
    column_id, json_key, concurrency, dataset_id, new_column_id, is_rerun=False
):
    view = ExtractJsonColumnView()
    # Process cells concurrently
    Column.objects.filter(id=new_column_id).update(status=StatusType.RUNNING.value)
    source_cells = Cell.objects.filter(column_id=column_id, deleted=False)
    total_processed = 0
    failed_cells = 0

    if is_rerun:
        # Update existing cells
        existing_cells = Cell.objects.filter(column_id=new_column_id, deleted=False)
        existing_cells_map = {str(cell.row_id): cell for cell in existing_cells}

        # Wrap function with OTel context propagation for thread safety
        wrapped_process_cell = wrap_for_thread(view._process_cell)

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            # Submit all cells for processing
            future_to_cell = {
                executor.submit(wrapped_process_cell, cell, json_key): cell
                for cell in source_cells
            }

            # Process results as they complete
            for future in as_completed(future_to_cell):
                cell = future_to_cell[future]
                try:
                    value = future.result()
                    existing_cell = existing_cells_map.get(str(cell.row_id))

                    if existing_cell:
                        existing_cell.value = value
                        existing_cell.status = CellStatus.PASS.value
                        existing_cell.save()
                        total_processed += 1
                    else:
                        # Create new cell if it doesn't exist during rerun
                        new_cell = Cell(
                            dataset_id=dataset_id,
                            column_id=new_column_id,
                            row=cell.row,
                            value=None,
                            value_infos={},
                            status=CellStatus.PASS.value,
                        )
                        new_cell.save()
                        logger.error(
                            f"Created new cell for row {cell.row_id} in column {new_column_id} during rerun"
                        )

                except Exception as e:
                    failed_cells += 1
                    logger.error(f"Error processing cell: {str(e)}")
                    existing_cell = existing_cells_map.get(str(cell.row_id))
                    if existing_cell:
                        existing_cell.value = None
                        existing_cell.value_infos = json.dumps({"reason": str(e)})
                        existing_cell.status = CellStatus.ERROR.value
                        existing_cell.save()
    else:
        # Create new cells (original behavior)
        new_cells = []

        # Wrap function with OTel context propagation for thread safety
        wrapped_process_cell = wrap_for_thread(view._process_cell)

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            # Submit all cells for processing
            future_to_cell = {
                executor.submit(wrapped_process_cell, cell, json_key): cell
                for cell in source_cells
            }

            # Process results as they complete
            for future in as_completed(future_to_cell):
                cell = future_to_cell[future]
                try:
                    value = future.result()
                    new_cells.append(
                        Cell(
                            dataset_id=dataset_id,
                            column_id=new_column_id,
                            row=cell.row,
                            value=value,
                        )
                    )
                    total_processed += 1
                except Exception as e:
                    failed_cells += 1
                    logger.error(f"Error processing cell: {str(e)}")
                    # Create a failed cell with error information
                    new_cells.append(
                        Cell(
                            dataset_id=dataset_id,
                            column_id=new_column_id,
                            row=cell.row,
                            value=None,
                            value_infos=json.dumps({"reason": str(e)}),
                            status=CellStatus.ERROR.value,
                        )
                    )

        # Bulk create all cells at once
        if new_cells:
            Cell.objects.bulk_create(new_cells)

    Column.objects.filter(id=new_column_id).update(status=StatusType.COMPLETED.value)
    # insert_embeddings_task.delay(column_ids=[str(new_column_id)])


@temporal_activity(time_limit=3600, queue="tasks_l")
def execute_python_code_async(
    code, dataset_id, concurrency, new_column_id, is_rerun=False
):
    view = ExecutePythonCodeView()
    # Process rows concurrently
    rows = Row.objects.filter(dataset_id=dataset_id, deleted=False)
    total_processed = 0
    failed_cells = 0
    Column.objects.filter(id=new_column_id).update(status=StatusType.RUNNING.value)

    if is_rerun:
        # Update existing cells
        existing_cells = Cell.objects.filter(column_id=new_column_id, deleted=False)
        existing_cells_map = {str(cell.row_id): cell for cell in existing_cells}

        # Wrap function with OTel context propagation for thread safety
        wrapped_execute_python_code = wrap_for_thread(view._execute_python_code)

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            future_to_row = {
                executor.submit(wrapped_execute_python_code, row, code): row
                for row in rows
            }

            for future in as_completed(future_to_row):
                row = future_to_row[future]
                try:
                    value, value_infos = future.result()
                    existing_cell = existing_cells_map.get(str(row.id))

                    if existing_cell:
                        if value_infos and "reason" in value_infos:
                            # Handle case where function returned error
                            existing_cell.value = None
                            existing_cell.value_infos = json.dumps(value_infos)
                            existing_cell.status = CellStatus.ERROR.value
                            failed_cells += 1
                        else:
                            existing_cell.value = value
                            existing_cell.value_infos = json.dumps(
                                value_infos if value_infos else {}
                            )
                            existing_cell.status = CellStatus.PASS.value
                            total_processed += 1
                        existing_cell.save()
                    else:
                        # Create new cell if it doesn't exist during rerun
                        new_cell = Cell(
                            dataset_id=dataset_id,
                            column_id=new_column_id,
                            row=row,
                            value=None,
                            value_infos=json.dumps(value_infos if value_infos else {}),
                            status=CellStatus.PASS.value,
                        )
                        new_cell.save()
                        logger.error(
                            f"Created new cell for row {row.id} in column {new_column_id} during rerun"
                        )

                except Exception as e:
                    logger.exception(f"Failed to process row: {str(e)}")
                    failed_cells += 1
                    existing_cell = existing_cells_map.get(str(row.id))
                    if existing_cell:
                        existing_cell.value = None
                        existing_cell.value_infos = json.dumps({"reason": str(e)})
                        existing_cell.status = CellStatus.ERROR.value
                        existing_cell.save()
    else:
        # Create new cells (original behavior)
        new_cells = []

        # Wrap function with OTel context propagation for thread safety
        wrapped_execute_python_code = wrap_for_thread(view._execute_python_code)

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            future_to_row = {
                executor.submit(wrapped_execute_python_code, row, code): row
                for row in rows
            }

            for future in as_completed(future_to_row):
                row = future_to_row[future]
                try:
                    value, value_infos = future.result()
                    if value_infos and "reason" in value_infos:
                        # Handle case where function returned error
                        new_cells.append(
                            Cell(
                                dataset_id=dataset_id,
                                column_id=new_column_id,
                                row=row,
                                value=None,
                                value_infos=json.dumps(value_infos),
                                status=CellStatus.ERROR.value,
                            )
                        )
                        failed_cells += 1
                    else:
                        new_cells.append(
                            Cell(
                                dataset_id=dataset_id,
                                column_id=new_column_id,
                                row=row,
                                value=value,
                                value_infos=json.dumps(
                                    value_infos if value_infos else {}
                                ),
                            )
                        )
                        total_processed += 1
                except Exception as e:
                    logger.exception(f"Failed to process row: {str(e)}")
                    failed_cells += 1
                    # Create a failed cell with error information
                    new_cells.append(
                        Cell(
                            dataset_id=dataset_id,
                            column_id=new_column_id,
                            row=row,
                            value=None,
                            value_infos=json.dumps({"reason": str(e)}),
                            status=CellStatus.ERROR.value,
                        )
                    )

        # Bulk create all cells
        if new_cells:
            Cell.objects.bulk_create(new_cells)

    Column.objects.filter(id=new_column_id).update(status=StatusType.COMPLETED.value)
    # insert_embeddings_task.delay(column_ids=[str(new_column_id)])


@temporal_activity(time_limit=3600, queue="tasks_l")
def conditional_column_async(
    config, dataset_id, concurrency, new_column_id, is_rerun=False
):
    view = ConditionalColumnView()
    # Process rows
    rows = Row.objects.filter(dataset_id=dataset_id, deleted=False)
    organization_id = Dataset.objects.get(id=dataset_id).organization.id
    total_processed = 0
    failed_cells = 0
    Column.objects.filter(id=new_column_id).update(status=StatusType.RUNNING.value)

    if is_rerun:
        # Update existing cells
        existing_cells = Cell.objects.filter(column_id=new_column_id, deleted=False)
        existing_cells_map = {str(cell.row_id): cell for cell in existing_cells}

        # Wrap function with OTel context propagation for thread safety
        wrapped_process_row = wrap_for_thread(view._process_row)

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            future_to_row = {
                executor.submit(wrapped_process_row, row, config, organization_id): row
                for row in rows
            }

            for future in as_completed(future_to_row):
                row = future_to_row[future]
                try:
                    value, value_infos = future.result()
                    existing_cell = existing_cells_map.get(str(row.id))

                    if existing_cell:
                        if value_infos and "reason" in value_infos:
                            existing_cell.value = None
                            existing_cell.value_infos = json.dumps(value_infos)
                            existing_cell.status = CellStatus.ERROR.value
                        else:
                            existing_cell.value = value
                            existing_cell.value_infos = json.dumps(
                                value_infos if value_infos else {}
                            )
                            existing_cell.status = CellStatus.PASS.value
                            total_processed += 1
                        existing_cell.save()
                    else:
                        # Create new cell if it doesn't exist during rerun
                        new_cell = Cell(
                            dataset_id=dataset_id,
                            column_id=new_column_id,
                            row=row,
                            value=None,
                            value_infos=json.dumps(value_infos if value_infos else {}),
                            status=CellStatus.PASS.value,
                        )
                        new_cell.save()
                        logger.error(
                            f"Created new cell for row {row.id} in column {new_column_id} during rerun"
                        )

                except Exception as e:
                    logger.error("traceback : ", traceback.format_exc())
                    logger.error(f"Error processing row: {str(e)}")
                    failed_cells += 1
                    existing_cell = existing_cells_map.get(str(row.id))
                    if existing_cell:
                        existing_cell.value = None
                        existing_cell.value_infos = json.dumps({"reason": str(e)})
                        existing_cell.status = CellStatus.ERROR.value
                        existing_cell.save()
    else:
        # Create new cells (original behavior)
        new_cells = []

        # Wrap function with OTel context propagation for thread safety
        wrapped_process_row = wrap_for_thread(view._process_row)

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            future_to_row = {
                executor.submit(wrapped_process_row, row, config, organization_id): row
                for row in rows
            }

            for future in as_completed(future_to_row):
                row = future_to_row[future]
                try:
                    value, value_infos = future.result()
                    if value_infos and "reason" in value_infos:
                        new_cells.append(
                            Cell(
                                dataset_id=dataset_id,
                                column_id=new_column_id,
                                row=row,
                                value=None,
                                value_infos=json.dumps(value_infos),
                                status=CellStatus.ERROR.value,
                            )
                        )
                    else:
                        new_cells.append(
                            Cell(
                                dataset_id=dataset_id,
                                column_id=new_column_id,
                                row=row,
                                value=value,
                                value_infos=json.dumps(
                                    value_infos if value_infos else {}
                                ),
                            )
                        )
                        total_processed += 1
                except Exception as e:
                    logger.error("traceback : ", traceback.format_exc())
                    logger.error(f"Error processing row: {str(e)}")
                    failed_cells += 1
                    # Create a failed cell with error information
                    new_cells.append(
                        Cell(
                            dataset_id=dataset_id,
                            column_id=new_column_id,
                            row=row,
                            value=None,
                            value_infos=json.dumps({"reason": str(e)}),
                            status=CellStatus.ERROR.value,
                        )
                    )

        if new_cells:
            Cell.objects.bulk_create(new_cells)

    Column.objects.filter(id=new_column_id).update(status=StatusType.COMPLETED.value)
    # insert_embeddings_task.delay(column_ids=[str(new_column_id)])


@temporal_activity(time_limit=3600, queue="tasks_l")
def add_vector_db_column_async(
    config,
    dataset_id,
    concurrency,
    input_column_id,
    org_id,
    new_column_id,
    workspace_id=None,
    is_rerun=False,
):
    # Ensure config is properly deserialized if needed
    if isinstance(config, str):
        config = json.loads(config)
    view = AddVectorDBColumnView()
    input_column = Column.objects.get(id=input_column_id)
    rows = Row.objects.filter(dataset_id=dataset_id, deleted=False)
    total_processed = 0
    failed_cells = 0
    Column.objects.filter(id=new_column_id).update(status=StatusType.RUNNING.value)

    if is_rerun:
        # Update existing cells
        existing_cells = Cell.objects.filter(column_id=new_column_id, deleted=False)
        existing_cells_map = {str(cell.row_id): cell for cell in existing_cells}

        # Wrap function with OTel context propagation for thread safety
        wrapped_process_row = wrap_for_thread(view._process_row)

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            future_to_row = {
                executor.submit(
                    wrapped_process_row, row, input_column, config, org_id, workspace_id
                ): row
                for row in rows
            }

            for future in as_completed(future_to_row):
                row = future_to_row[future]
                try:
                    value, value_infos = future.result()
                    existing_cell = existing_cells_map.get(str(row.id))

                    if existing_cell:
                        if value_infos and "reason" in value_infos:
                            # Handle case where function returned error
                            existing_cell.value = None
                            existing_cell.value_infos = json.dumps(value_infos)
                            existing_cell.status = CellStatus.ERROR.value
                            failed_cells += 1
                        else:
                            existing_cell.value = value
                            existing_cell.value_infos = json.dumps(
                                value_infos if value_infos else {}
                            )
                            existing_cell.status = CellStatus.PASS.value
                            total_processed += 1
                        existing_cell.save()
                    else:
                        # Create new cell if it doesn't exist during rerun
                        new_cell = Cell(
                            dataset_id=dataset_id,
                            column_id=new_column_id,
                            row=row,
                            value=None,
                            value_infos=json.dumps(value_infos if value_infos else {}),
                            status=CellStatus.PASS.value,
                        )
                        new_cell.save()
                        logger.error(
                            f"Created new cell for row {row.id} in column {new_column_id} during rerun"
                        )

                except Exception as e:
                    logger.exception(f"Failed to process row: {str(e)}")
                    failed_cells += 1
                    existing_cell = existing_cells_map.get(str(row.id))
                    if existing_cell:
                        existing_cell.value = None
                        existing_cell.value_infos = json.dumps({"reason": str(e)})
                        existing_cell.status = CellStatus.ERROR.value
                        existing_cell.save()
    else:
        # Create new cells (original behavior)
        new_cells = []

        # Wrap function with OTel context propagation for thread safety
        wrapped_process_row = wrap_for_thread(view._process_row)

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            future_to_row = {
                executor.submit(
                    wrapped_process_row, row, input_column, config, org_id, workspace_id
                ): row
                for row in rows
            }

            for future in as_completed(future_to_row):
                row = future_to_row[future]
                try:
                    value, value_infos = future.result()
                    if value_infos and "reason" in value_infos:
                        # Handle case where function returned error
                        new_cells.append(
                            Cell(
                                dataset_id=dataset_id,
                                column_id=new_column_id,
                                row=row,
                                value=None,
                                value_infos=json.dumps(value_infos),
                                status=CellStatus.ERROR.value,
                            )
                        )
                        failed_cells += 1
                    else:
                        new_cells.append(
                            Cell(
                                dataset_id=dataset_id,
                                column_id=new_column_id,
                                row=row,
                                value=value,
                                value_infos=json.dumps(
                                    value_infos if value_infos else {}
                                ),
                            )
                        )
                        total_processed += 1
                except Exception as e:
                    logger.exception(f"Failed to process row: {str(e)}")
                    failed_cells += 1
                    # Create a failed cell with error information
                    new_cells.append(
                        Cell(
                            dataset_id=dataset_id,
                            column_id=new_column_id,
                            row=row,
                            value=None,
                            value_infos=json.dumps({"reason": str(e)}),
                            status=CellStatus.ERROR.value,
                        )
                    )

        if new_cells:
            Cell.objects.bulk_create(new_cells)

    Column.objects.filter(id=new_column_id).update(status=StatusType.COMPLETED.value)
    # insert_embeddings_task.delay(column_ids=[str(new_column_id)])


@temporal_activity(time_limit=3600, queue="tasks_l")
def add_api_column_async(
    config, dataset_id, concurrency, new_column_id, is_rerun=False
):
    view = AddApiColumnView()
    # Process all rows ordered by row order
    rows = list(Row.objects.filter(dataset_id=dataset_id, deleted=False).order_by("order"))
    total_processed = 0
    failed_cells = 0
    Column.objects.filter(id=new_column_id).update(status=StatusType.RUNNING.value)

    if is_rerun:
        # Update existing cells with select_related
        existing_cells = list(
            Cell.objects.filter(column_id=new_column_id, deleted=False).select_related(
                "row"
            )
        )
        existing_cells_map = {str(cell.row_id): cell for cell in existing_cells}

        # Wrap function with OTel context propagation for thread safety
        wrapped_make_api_call = wrap_for_thread(view._make_api_call)

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            future_to_row = {}
            for row in rows:
                existing_cell = existing_cells_map.get(str(row.id))
                cell = existing_cell if existing_cell else Cell(
                    dataset_id=dataset_id,
                    column_id=new_column_id,
                    row=row,
                    value=None,
                )
                future_to_row[executor.submit(wrapped_make_api_call, cell, config)] = row

            # Flush buffers to DB in batches as results complete
            cells_to_update = []
            cells_to_create = []

            def _flush_rerun_buffers():
                nonlocal cells_to_update, cells_to_create
                if cells_to_update:
                    Cell.objects.bulk_update(
                        cells_to_update,
                        ["value", "value_infos", "status"],
                        batch_size=concurrency,
                    )
                    cells_to_update = []
                if cells_to_create:
                    Cell.objects.bulk_create(cells_to_create, batch_size=concurrency)
                    cells_to_create = []

            for future in as_completed(future_to_row):
                row = future_to_row[future]
                try:
                    value, value_infos = future.result()
                    existing_cell = existing_cells_map.get(str(row.id))

                    if existing_cell:
                        existing_cell.value = value
                        existing_cell.value_infos = (
                            json.dumps(value_infos) if value_infos else None
                        )
                        existing_cell.status = CellStatus.PASS.value
                        cells_to_update.append(existing_cell)
                        total_processed += 1
                    else:
                        cells_to_create.append(
                            Cell(
                                dataset_id=dataset_id,
                                column_id=new_column_id,
                                row=row,
                                value=value,
                                value_infos=json.dumps(
                                    value_infos if value_infos else {}
                                ),
                                status=CellStatus.PASS.value,
                            )
                        )
                        total_processed += 1
                        logger.warning(
                            f"Created new cell for row {row.id} in column {new_column_id} during rerun"
                        )

                except Exception as e:
                    failed_cells += 1
                    logger.error(f"Error processing cell: {str(e)}")
                    existing_cell = existing_cells_map.get(str(row.id))
                    if existing_cell:
                        existing_cell.value = None
                        existing_cell.value_infos = json.dumps({"reason": str(e)})
                        existing_cell.status = CellStatus.ERROR.value
                        cells_to_update.append(existing_cell)
                    else:
                        cells_to_create.append(
                            Cell(
                                dataset_id=dataset_id,
                                column_id=new_column_id,
                                row=row,
                                value=None,
                                value_infos=json.dumps({"reason": str(e)}),
                                status=CellStatus.ERROR.value,
                            )
                        )

                # Flush every `concurrency` completed results
                if len(cells_to_update) + len(cells_to_create) >= concurrency:
                    _flush_rerun_buffers()

            # Flush any remaining results
            _flush_rerun_buffers()
    else:
        # Create new cells — flush to DB in batches as results complete
        # Wrap function with OTel context propagation for thread safety
        wrapped_make_api_call = wrap_for_thread(view._make_api_call)

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            future_to_row = {}
            for row in rows:
                cell = Cell(
                    dataset_id=dataset_id, column_id=new_column_id, row=row, value=None
                )
                future_to_row[executor.submit(wrapped_make_api_call, cell, config)] = row

            # Flush buffer to DB in batches as results complete
            cells_to_create = []

            for future in as_completed(future_to_row):
                row = future_to_row[future]
                try:
                    value, value_infos = future.result()
                    cells_to_create.append(
                        Cell(
                            dataset_id=dataset_id,
                            column_id=new_column_id,
                            row=row,
                            value=value,
                            value_infos=json.dumps(value_infos) if value_infos else None,
                        )
                    )
                    total_processed += 1
                except Exception as e:
                    failed_cells += 1
                    logger.error(f"Error processing cell: {str(e)}")
                    cells_to_create.append(
                        Cell(
                            dataset_id=dataset_id,
                            column_id=new_column_id,
                            row=row,
                            value=None,
                            value_infos=json.dumps({"reason": str(e)}),
                            status=CellStatus.ERROR.value,
                        )
                    )

                # Flush every `concurrency` completed results
                if len(cells_to_create) >= concurrency:
                    Cell.objects.bulk_create(cells_to_create, batch_size=concurrency)
                    cells_to_create = []

            # Flush any remaining results
            if cells_to_create:
                Cell.objects.bulk_create(cells_to_create, batch_size=concurrency)

    Column.objects.filter(id=new_column_id).update(status=StatusType.COMPLETED.value)


class PreviewDatasetOperationView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def _get_sample_rows(self, dataset_id, sample_size=3):
        """Get a sample of rows from the dataset"""
        return Row.objects.filter(dataset_id=dataset_id, deleted=False).order_by(
            "created_at"
        )[:sample_size]

    def post(self, request, dataset_id, operation_type):
        try:
            # Get sample rows
            sample_rows = self._get_sample_rows(dataset_id)
            organization_id = (
                getattr(request, "organization", None) or request.user.organization.id
            )

            # Get the appropriate preview handler based on operation type
            preview_handlers = {
                "extract_json": self._preview_extract_json,
                "classify": self._preview_classify,
                "extract_entities": self._preview_extract_entities,
                "api_call": self._preview_api_call,
                "execute_code": self._preview_execute_code,
                "conditional": self._preview_conditional,
                "vector_db": self._preview_vector_db,
            }

            handler = preview_handlers.get(operation_type)
            if not handler:
                return self._gm.bad_request(f"Invalid operation type: {operation_type}")

            # Execute preview
            preview_results = handler(request.data, sample_rows, organization_id)

            return self._gm.success_response(
                {
                    "message": "Preview completed successfully",
                    "preview_results": preview_results,
                    "sample_size": len(sample_rows),
                }
            )

        except Exception as e:
            traceback.print_exc()
            logger.exception(f"Error in preview the datasets operations: {str(e)}")
            return self._gm.internal_server_error_response(
                get_error_message("FAILED_TO_PREVIEW_DATASET_OPERATIONS")
            )

    def _preview_extract_json(self, config, sample_rows, organization_id):
        """Preview JSON extraction operation"""
        extractor = ExtractJsonColumnView()
        results = []

        for row in sample_rows:
            cell = Cell.objects.get(row=row, column_id=config["column_id"])
            value = extractor._process_cell(cell, config["json_key"])
            results.append(
                {"row_id": str(row.id), "input": cell.value, "output": value}
            )

        return results

    def _preview_classify(self, config, sample_rows, organization_id):
        """Preview classification operation"""
        classifier = ClassifyColumnView()
        results = []

        for row in sample_rows:
            cell = Cell.objects.get(row=row, column_id=config["column_id"])
            value, value_infos = classifier._classify_cell(
                cell=cell,
                labels=config["labels"],
                model=config.get("language_model_id", "gpt-4"),
            )
            results.append(
                {
                    "row_id": str(row.id),
                    "input": cell.value,
                    "output": value,
                    "details": value_infos,
                }
            )

        return results

    def _preview_extract_entities(self, config, sample_rows, organization_id):
        """Preview entity extraction operation"""
        extractor = ExtractEntitiesView()
        results = []

        for row in sample_rows:
            cell = Cell.objects.get(row=row, column_id=config["column_id"])
            value, value_infos = extractor._extract_entities(
                cell=cell,
                instruction=config["instruction"],
                model=config.get("language_model_id", "gpt-4"),
            )
            results.append(
                {
                    "row_id": str(row.id),
                    "input": cell.value,
                    "output": json.dumps(value),
                    "details": value_infos,
                }
            )

        return results

    def _preview_api_call(self, config, sample_rows, organization_id):
        """Preview API call operation"""
        api_caller = AddApiColumnView()
        results = []

        api_config = config.get("config", config)

        for row in sample_rows:
            if "column_id" not in config:
                # Create a temporary cell with the row so variable substitution works
                cell = Cell(row=row, value=None)
                value = api_caller._make_api_call(cell=cell, config=api_config)
                results.append({"row_id": str(row.id), "output": json.dumps(value)})
            else:
                cell = Cell.objects.get(row=row, column_id=config["column_id"])
                value = api_caller._make_api_call(cell, api_config)
                results.append(
                    {
                        "row_id": str(row.id),
                        "input": cell.value,
                        "output": json.dumps(value),
                    }
                )

        return results

    def _preview_execute_code(self, config, sample_rows, organization_id):
        """Preview Python code execution"""
        executor = ExecutePythonCodeView()
        results = []

        for row in sample_rows:
            value, value_infos = executor._execute_python_code(row, config["code"])
            results.append(
                {
                    "row_id": str(row.id),
                    "output": json.dumps(value),
                    "details": value_infos,
                }
            )

        return results

    def _preview_conditional(self, config, sample_rows, organization_id):
        """Preview conditional operation"""
        conditional = ConditionalColumnView()
        results = []

        for row in sample_rows:
            value, value_infos = conditional._process_row(
                row, config["config"], organization_id
            )
            results.append(
                {
                    "row_id": str(row.id),
                    "output": json.dumps(value),
                    "details": value_infos,
                }
            )

        return results

    def _preview_vector_db(self, config, sample_rows, organization_id):
        """Preview vector database operation"""
        vector_db = AddVectorDBColumnView()
        results = []

        for row in sample_rows:
            column = Column.objects.get(id=config["column_id"])
            value, value_infos = vector_db._process_row(
                row, column, config, organization_id
            )
            results.append(
                {
                    "row_id": str(row.id),
                    "input": Cell.objects.get(row=row, column=column).value,
                    "output": json.dumps(value),
                }
            )

        return results
