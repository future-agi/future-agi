import ast
import difflib
import json
import time
from collections import Counter
from typing import Literal

import litellm
import nltk
import requests
import structlog
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from datasets import load_dataset
from django.core.cache import cache
from huggingface_hub.errors import HfHubHTTPError
from litellm.llms.custom_llm import CustomLLM, ModelResponse
from nltk.corpus import stopwords, wordnet
from nltk.stem import WordNetLemmatizer

logger = structlog.get_logger(__name__)

from agentic_eval.core_evals.run_prompt.available_models import AVAILABLE_MODELS
# (available_models always available)
from model_hub.models.ai_model import AIModel
from model_hub.models.api_key import ApiKey
from model_hub.models.choices import DataTypeChoices
from model_hub.models.conversations import Message, Node
from model_hub.models.metric import Metric
from model_hub.utils.azure_endpoints import normalize_azure_custom_model_config
from tfc.settings.settings import (
    HUGGINGFACE_API_TOKEN,
    HUGGINGFACE_API_TOKEN_1,
    HUGGINGFACE_API_TOKEN_2,
)
from tfc.utils.clickhouse import ClickHouseClientSingleton
from tfc.utils.error_codes import get_error_message
from tfc.utils.types import ClickhouseDatatypes

# The HuggingFace Hub now emits the `List` feature type (datasets 4.0) in dataset
# metadata, which pinned datasets 3.6.0 can't parse: load_dataset() raises
# "Feature type 'List' not found" and streaming ingestion loads zero rows. Alias it
# to the 3.6.0 equivalent (LargeList); setdefault leaves a future upgrade untouched.
try:
    from datasets.features import features as _hf_features

    if hasattr(_hf_features, "_FEATURE_TYPES") and hasattr(_hf_features, "LargeList"):
        _hf_features._FEATURE_TYPES.setdefault("List", _hf_features.LargeList)
except Exception:  # defensive: datasets internals moved
    logger.warning("hf List feature-type shim did not install", exc_info=True)


class MyCustomLLM(CustomLLM):
    def __init__(self, **kwargs):
        super().__init__()

    def completion(
        self,
        model,
        api_base,
        api_key,
        messages,
        headers=None,
        max_tokens=100,
        temperature=0.7,
        top_p=1.0,
        stream=False,
        **kwargs,
    ):
        if headers is None:
            headers = {}
        url = api_base
        if url is None or url == "":
            raise ValueError("api_base not set. Set api_base for custom endpoints")
        model_response = ModelResponse()
        headers = headers or {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        # prompt = " ".join([message["content"] for message in messages])  # type: ignore
        json_data = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        try:
            resp = litellm.module_level_client.post(
                url,
                json=json_data,
                headers=headers,
                stream=stream,
            )

            if resp.status_code != 200:
                resp.raise_for_status()

            response_json = resp.json()
            model_response.choices[0].message.content = (
                response_json.get("choices", {})[0]
                .get("message", {})
                .get("content", "")
            )
            model_response.created = int(time.time())
            model_response.model = model
            logger.info(f"model_response********: {model_response}")
            return model_response
        except Exception as e:
            logger.exception(f"Error in CustomLLM completion {str(e)}")
            raise Exception("Error in CustomLLM completion")  # noqa: B904


def remove_empty_text_from_messages(messages):
    """
    Remove empty text content from messages.

    Handles both string content (simple format) and list content (multi-modal format).
    - String content: kept as-is if non-empty
    - List content: filters out items with empty type values
    """
    final_messages = []
    for message in messages:
        if message.get("content"):
            contents = message.get("content")
            # Handle string content (simple message format)
            if isinstance(contents, str):
                if contents.strip():
                    final_messages.append(
                        {"role": message["role"], "content": contents}
                    )
                continue
            # Handle list content (multi-modal format)
            final_content = []
            for content in contents:
                if isinstance(content, dict):
                    key_to_check = content.get("type")
                    if key_to_check:
                        if content.get(key_to_check):
                            final_content.append(content)
                    else:
                        final_content.append(content)
                else:
                    # Non-dict items in list, keep as-is
                    final_content.append(content)
            if final_content:
                final_messages.append(
                    {"role": message["role"], "content": final_content}
                )
    return final_messages


def convert_messages_to_text_only(messages):
    """
    Convert multi-modal messages to text-only messages for providers that don't support multi-modal content.
    If a system message is present, include it as the first message (content always as string, even if empty).
    All other messages: only include user messages with non-empty content (converted to string).
    """
    converted_messages = []

    for message in messages:
        role = message.get("role")
        content = message.get("content", "")

        if role == "system" and content:
            # Convert content to string (text extraction for multimodal)
            if isinstance(content, list):
                text_parts = []
                for item in content:
                    if isinstance(item, dict):
                        if item.get("type") == "text" and item.get("text"):
                            text_parts.append(item["text"])
                converted_content = " ".join(text_parts).strip()
            elif isinstance(content, str):
                converted_content = content.strip()
            else:
                converted_content = str(content) if content else ""
            # Always include the first system message, even if content is empty
            if converted_content not in ["", None, "None", [], {}]:
                converted_messages.append(
                    {"role": "system", "content": converted_content}
                )
        elif role == "user" and content:
            converted_messages.append({"role": "user", "content": content})

    return converted_messages


def convert_vals(vals):
    if ClickhouseDatatypes.get_data_type(vals) == ClickhouseDatatypes.JSON:
        return json.dumps(vals)

    return str(vals)


def check_valid_metrics(metric_type, ai_model_id):
    client = ClickHouseClientSingleton()

    filter_query = f"""
        SELECT DISTINCT arrayElement(Features.Value, indexOf(Features.Key, 'node_id')) AS FeatureValue
        FROM events
        WHERE AIModel = '{ai_model_id}'
        AND has(Features.Key, 'node_id')
        AND deleted=0
        ORDER BY EventDateTime ASC
        LIMIT 10

    """
    clickhouse_data = client.execute(filter_query)
    node_ids = [d[0] for d in clickhouse_data]
    prompt_template = False
    context = False
    content = False
    for node in node_ids:
        node_data = Node.objects.filter(id=node).get()
        message = Message.objects.filter(id=node_data.message.id).get()
        if message.author.get("role") == "user":
            content = message.content
            rag_info = content.get("rag_info")

            if rag_info.get("variables"):
                pass
            if rag_info.get("prompt_template"):
                prompt_template = True
            if rag_info.get("context"):
                context = True
            break

    if metric_type == "EVALUATE_CONTEXT":
        if not context:
            return False, get_error_message("METRIC_VALID_CONTEXT")
    elif metric_type == "EVALUATE_PROMPT_TEMPLATE":
        if not prompt_template:
            return False, get_error_message("METRIC_VALID_TEMPLATE")
    else:
        if not content:
            return False, get_error_message("METRIC_VALID_CONTENT")
    return True, True


def check_data_valid_for_model(model_type, ai_model_id, conversation):
    client = ClickHouseClientSingleton()

    filter_query = f"""
        SELECT DISTINCT arrayElement(Features.Value, indexOf(Features.Key, 'node_id')) AS FeatureValue
        FROM events
        WHERE AIModel = '{ai_model_id}'
        AND has(Features.Key, 'node_id')
        AND deleted=0
        ORDER BY EventDateTime ASC
        LIMIT 10

    """
    clickhouse_data = client.execute(filter_query)
    node_ids = [d[0] for d in clickhouse_data]
    if node_ids:
        variables = False
        prompt_template = False
        context = False
        content = False
        for node in node_ids:
            node_data = Node.objects.filter(id=node).get()
            message = Message.objects.filter(id=node_data.message.id).get()
            if message.author.get("role") == "user":
                rag_info = message.content.get("rag_info")

                if message.content:
                    content = True
                if rag_info.get("variables"):
                    variables = True
                if rag_info.get("prompt_template"):
                    prompt_template = True
                if rag_info.get("context"):
                    context = True
                break
        variables_input = False
        prompt_template_input = False
        context_input = False
        content_input = False

        if model_type == AIModel.ModelTypes.GENERATIVE_LLM:
            if "chat_history" in conversation:
                chat_history = conversation["chat_history"]
                for _index, item in enumerate(chat_history):
                    if item.get("role") == "user":
                        if item.get("content"):
                            content_input = True
                        if item.get("context"):
                            context_input = True
                        if item.get("variables"):
                            variables_input = True
                        if item.get("prompt_template"):
                            prompt_template_input = True
                        break
            elif "chat_graph" in conversation:
                chat_graph = conversation["chat_graph"]
                for node_data in chat_graph["nodes"]:
                    item = node_data["message"]
                    if item["author"].get("role") == "user":
                        if item.get("content"):
                            content_input = True
                        if item.get("context"):
                            context_input = True
                        if item.get("variables"):
                            variables_input = True
                        if item.get("prompt_template"):
                            prompt_template_input = True
                        break

        elif model_type == AIModel.ModelTypes.GENERATIVE_IMAGE:
            chat_history = conversation["chat_history"]
            for _index, item in enumerate(chat_history):
                if item.get("role") == "user":
                    if item.get("content"):
                        content_input = True
                    if item.get("context"):
                        context_input = True
                    if item.get("variables"):
                        variables_input = True
                    if item.get("prompt_template"):
                        prompt_template_input = True
                    break

        if (
            variables != variables_input
            or prompt_template != prompt_template_input
            or content != content_input
            or context != context_input
        ):
            return False, get_error_message("INPUT_DATA_MISMATCHED")
    return True, True


def get_evaluation_type(evaluation_type):
    if evaluation_type == "EVALUATE_CONTEXT":
        return Metric.EvalMetricTypes.get_eval_metric_type(1)

    if evaluation_type == "EVALUATE_PROMPT_TEMPLATE":
        return Metric.EvalMetricTypes.get_eval_metric_type(3)

    if evaluation_type == "EVAL_CONTEXT_RANKING":
        return Metric.EvalMetricTypes.get_eval_metric_type(5)

    return Metric.EvalMetricTypes.get_eval_metric_type(4)


def validate_model_working(model_name, api_key, provider):
    msg = [
        {"role": "system", "content": "Be a helpful Assistant."},
        {"role": "user", "content": "What is the weather like today?"},
    ]
    try:
        payload = {
            "messages": msg,
            "model": model_name,
            # "temperature": 0.7,
            # "max_tokens": 1,
        }
        if "key" in api_key.keys():
            response = litellm.completion(**payload, api_key=api_key["key"])
        else:
            if provider == "bedrock" or provider == "sagemaker":
                response = litellm.completion(
                    **payload,
                    custom_llm_provider=provider,
                    aws_access_key_id=api_key["aws_access_key_id"],
                    aws_secret_access_key=api_key["aws_secret_access_key"],
                    aws_region_name=api_key["aws_region_name"],
                )
            elif provider == "azure":
                normalized = normalize_azure_custom_model_config(api_key)
                if normalized.get("azure_endpoint_type") == "foundry":
                    if isinstance(payload.get("model"), str) and not payload[
                        "model"
                    ].startswith("azure_ai/"):
                        payload["model"] = f"azure_ai/{payload['model']}"
                    response = litellm.completion(
                        **payload,
                        custom_llm_provider="azure_ai",
                        api_base=normalized["api_base"],
                        api_key=normalized["api_key"],
                    )
                else:
                    response = litellm.completion(
                        **payload,
                        custom_llm_provider=provider,
                        api_base=normalized["api_base"],
                        api_version=normalized["api_version"],
                        api_key=normalized["api_key"],
                    )
            elif provider == "vertex_ai":
                vertex_config = api_key["config_json"]
                vertex_location = vertex_config.get("location")
                creds = {k: v for k, v in vertex_config.items() if k != "location"}
                completion_kwargs = {
                    **payload,
                    "custom_llm_provider": provider,
                    "vertex_credentials": json.dumps(creds),
                }
                if vertex_location:
                    completion_kwargs["vertex_location"] = vertex_location
                response = litellm.completion(**completion_kwargs)
            elif provider == "openai":
                payload["model"] = "openai/" + payload["model"]
                payload.update(api_key)
                response = litellm.completion(**payload)
            else:
                try:
                    url = api_key["config_json"].pop("api_base")
                    if not url:
                        raise Exception("Please provide the API Base.")
                    headers = api_key["config_json"].pop("headers")
                    response = requests.post(
                        url,
                        headers=headers,
                        json=payload,
                        timeout=30,  # 30 seconds timeout
                    )
                    response.raise_for_status()
                    return response.text
                except Exception as e:
                    return Exception(e)
            return response.choices[0].message.content

    except Exception as e:
        # Expected user-input/connectivity validation failure (e.g. a bad
        # Vertex/Bedrock credential or an invalid private key). This function
        # only validates user-supplied model credentials and always returns the
        # error to the caller as an Exception (-> clean 400), so log at WARNING
        # to keep it out of Sentry (event_level=ERROR) while preserving a
        # breadcrumb. See CORE-BACKEND-119X.
        logger.warning(f"An error occurred: {str(e)}")
        status = False
        try:
            error_message = str(e).split("litellm.")[1].split("Traceback")[0]
            status = True
            return Exception(error_message)
        except Exception as e:
            if status:
                return Exception(error_message)
            else:
                return Exception(str(e))


async def send_message_to_channel_async(organization_id, message):
    if not isinstance(message, dict):
        logger.exception(f"Message is not a dictionary: {message}")
        return
    channel_name = f"org_{organization_id}"
    channel_layer = get_channel_layer()
    try:
        await channel_layer.group_send(
            channel_name, {"type": "send_data", "data": message}
        )
    except Exception as e:
        logger.exception(
            f"websocket: Error sending message to channel {channel_name}: {str(e)}"
        )
        # raise e


async def send_message_to_uuid_async(uuid, message):
    if not isinstance(message, dict):
        logger.exception(f"Message is not a dictionary: {message}")
        return
    channel_name = f"uuid_{uuid}"
    channel_layer = get_channel_layer()
    try:
        await channel_layer.group_send(
            channel_name, {"type": "send_data", "data": message}
        )
    except Exception as e:
        logger.exception(
            f"websocket: Error sending message to channel {channel_name}: {str(e)}"
        )
        # raise e


async def broadcast_to_uuid_async(uuid, data):
    if not isinstance(data, dict):
        logger.exception(f"Message is not a dictionary: {data}")
        return
    channel_layer = get_channel_layer()
    try:
        await channel_layer.group_send(
            f"uuid_{uuid}",
            {
                "type": "send_data",
                "data": {"type": "message_type", "data": data, "uuid": uuid},
            },
        )
    except Exception as e:
        logger.exception(
            f"websocket: Error sending message to channel uuid {uuid}: {str(e)}"
        )
        # raise e


def send_message_to_channel(organization_id, message):
    """
    Synchronous wrapper for send_message_to_channel_async
    """
    try:
        # Try using async_to_sync first
        async_to_sync(send_message_to_channel_async)(organization_id, message)
    except RuntimeError:
        # If that fails (which can happen in Celery), use a more
        # careful approach to event loop management
        import asyncio

        # Create a new event loop
        new_loop = asyncio.new_event_loop()

        # Create a fresh channel layer within this new loop context
        async def run_with_new_channel_layer():
            if not isinstance(message, dict):
                logger.exception(f"Message is not a dictionary: {message}")
                return
            channel_name = f"org_{organization_id}"
            # Get a fresh channel layer in this loop's context
            channel_layer = get_channel_layer()
            try:
                await channel_layer.group_send(
                    channel_name, {"type": "send_data", "data": message}
                )
            except Exception as e:
                logger.exception(
                    f"websocket: Error sending message to channel {channel_name}: {str(e)}"
                )
                # raise e

        try:
            # Run the coroutine in the new loop
            new_loop.run_until_complete(run_with_new_channel_layer())
        finally:
            # Clean up
            new_loop.close()


def get_diff(base_text, modified_text):
    """
    Compare two strings and return a list of dictionaries with text and color properties.
    Black: words that are common to both strings
    Red: words that are in base_text but not in modified_text (removed)
    Green: words that are in modified_text but not in base_text (added)

    Example usage:
        str1 = "the quick brown fox jumps over the lazy dog"
        str2 = "the brown fox quickly jumps over the dog"

        result = get_diff(str1, str2)
        print(result)
    """

    base_words = str(base_text).split()
    modified_words = str(modified_text).split()

    sm = difflib.SequenceMatcher(None, base_words, modified_words)
    result = []

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            # Words are the same in both texts
            for word in base_words[i1:i2]:
                result.append({"text": word, "status": "default"})
        elif tag == "delete":
            # Words removed from base text
            for word in base_words[i1:i2]:
                result.append({"text": word, "status": "removed"})
        elif tag == "insert":
            # Words added to modified text
            for word in modified_words[j1:j2]:
                result.append({"text": word, "status": "added"})
        elif tag == "replace":
            # Words replaced - show removed words in removed and added words in added
            for word in base_words[i1:i2]:
                result.append({"text": word, "status": "removed"})
            for word in modified_words[j1:j2]:
                result.append({"text": word, "status": "added"})

    return result


def send_message_to_uuid(uuid, message):
    """
    Synchronous wrapper for sending messages to a specific UUID channel
    """
    try:
        # Try using async_to_sync first
        async_to_sync(send_message_to_uuid_async)(uuid, message)
    except RuntimeError:
        # If that fails (which can happen in Celery), use a more
        # careful approach to event loop management
        import asyncio

        # Create a new event loop
        new_loop = asyncio.new_event_loop()

        # Create a fresh channel layer within this new loop context
        async def run_with_new_channel_layer():
            if not isinstance(message, dict):
                logger.exception(f"Message is not a dictionary: {message}")
                return
            channel_name = f"uuid_{uuid}"
            # Get a fresh channel layer in this loop's context
            channel_layer = get_channel_layer()
            try:
                await channel_layer.group_send(
                    channel_name, {"type": "send_data", "data": message}
                )
            except Exception as e:
                logger.exception(
                    f"websocket: Error sending message to channel {channel_name}: {str(e)}"
                )
                # raise e

        try:
            # Run the coroutine in the new loop
            new_loop.run_until_complete(run_with_new_channel_layer())
        finally:
            # Clean up
            new_loop.close()


def broadcast_to_uuid(uuid, data):
    """
    Synchronous wrapper for broadcasting messages to a specific UUID channel
    """
    try:
        # Try using async_to_sync first
        async_to_sync(broadcast_to_uuid_async)(uuid, data)
    except RuntimeError:
        # If that fails (which can happen in Celery), use a more
        # careful approach to event loop management
        import asyncio

        # Create a new event loop
        new_loop = asyncio.new_event_loop()

        # Create a fresh channel layer within this new loop context
        async def run_with_new_channel_layer():
            if not isinstance(data, dict):
                logger.exception(f"Message is not a dictionary: {data}")
                return
            # Get a fresh channel layer in this loop's context
            channel_layer = get_channel_layer()
            try:
                await channel_layer.group_send(
                    f"uuid_{uuid}",
                    {
                        "type": "send_data",
                        "data": {"type": "message_type", "data": data, "uuid": uuid},
                    },
                )
            except Exception as e:
                logger.exception(
                    f"websocket: Error sending message to channel uuid {uuid}: {str(e)}"
                )
                # raise e

        try:
            # Run the coroutine in the new loop
            new_loop.run_until_complete(run_with_new_channel_layer())
        finally:
            # Clean up
            new_loop.close()


# Function to load huggingface dataset with token rotation
def load_hf_dataset_with_retries(
    dataset_name, config_name, split, organization_id, max_reties=3, streaming=True
):
    auth_token = ApiKey.objects.filter(
        organization_id=organization_id, provider="huggingface"
    ).first()
    hf_token = (
        auth_token._actual_key
        if auth_token and auth_token._actual_key
        else HUGGINGFACE_API_TOKEN
    )
    for attempt in range(max_reties):
        try:
            if not streaming:
                headers = {"Authorization": f"Bearer {hf_token}"}
                API_URL = f"https://datasets-server.huggingface.co/first-rows?dataset={dataset_name}&config={config_name}&split={split}"
                response = requests.get(API_URL, headers=headers, timeout=30)
                response.raise_for_status()
                return response.json()
            else:
                hf_dataset = load_dataset(
                    dataset_name,
                    name=config_name,
                    split=split,
                    streaming=streaming,
                    token=hf_token,
                )
                return hf_dataset
        except (
            HfHubHTTPError,
            requests.exceptions.ConnectionError,
            requests.exceptions.HTTPError,
        ):
            if hf_token == HUGGINGFACE_API_TOKEN:
                hf_token = HUGGINGFACE_API_TOKEN_1
            elif hf_token == HUGGINGFACE_API_TOKEN_1:
                hf_token = HUGGINGFACE_API_TOKEN_2
            else:
                hf_token = HUGGINGFACE_API_TOKEN
            logger.error(
                f"Error in loading huggingface dataset attempt({attempt + 1}). Retrying with another token"
            )
            time.sleep(2**attempt)
        except Exception as e:
            logger.exception(f"Error fetching Huggingface Dataset: {e}")
            raise e
    return None


# Get the data type of the column
def get_data_type_huggingface(column_info):
    data_type_map = {
        "string": DataTypeChoices.TEXT.value,
        "int8": DataTypeChoices.INTEGER.value,
        "int16": DataTypeChoices.INTEGER.value,
        "int32": DataTypeChoices.INTEGER.value,
        "int64": DataTypeChoices.INTEGER.value,
        "uint8": DataTypeChoices.INTEGER.value,
        "uint16": DataTypeChoices.INTEGER.value,
        "uint32": DataTypeChoices.INTEGER.value,
        "uint64": DataTypeChoices.INTEGER.value,
        "integer": DataTypeChoices.INTEGER.value,
        "int": DataTypeChoices.INTEGER.value,
        "bigint": DataTypeChoices.INTEGER.value,
        "smallint": DataTypeChoices.INTEGER.value,
        "tinyint": DataTypeChoices.INTEGER.value,
        "decimal128": DataTypeChoices.FLOAT.value,
        "decimal256": DataTypeChoices.FLOAT.value,
        "decimal": DataTypeChoices.FLOAT.value,
        "float": DataTypeChoices.FLOAT.value,
        "float16": DataTypeChoices.FLOAT.value,
        "float32": DataTypeChoices.FLOAT.value,
        "float64": DataTypeChoices.FLOAT.value,
        "datetime": DataTypeChoices.DATETIME.value,
        "Image": DataTypeChoices.IMAGE.value,
        "Audio": DataTypeChoices.AUDIO.value,
        "date32": DataTypeChoices.DATETIME.value,
        "date64": DataTypeChoices.DATETIME.value,
        "date": DataTypeChoices.DATETIME.value,
        "duration[s]": DataTypeChoices.DATETIME.value,
        "duration[ms]": DataTypeChoices.DATETIME.value,
        "duration[us]": DataTypeChoices.DATETIME.value,
        "duration[ns]": DataTypeChoices.DATETIME.value,
        "time32[s]": DataTypeChoices.DATETIME.value,
        "time32[ms]": DataTypeChoices.DATETIME.value,
        "time64[us]": DataTypeChoices.DATETIME.value,
        "time64[ns]": DataTypeChoices.DATETIME.value,
        "timestamp": DataTypeChoices.DATETIME.value,
        "timestamp[s]": DataTypeChoices.DATETIME.value,
        "timestamp[ms]": DataTypeChoices.DATETIME.value,
        "timestamp[us]": DataTypeChoices.DATETIME.value,
        "timestamp[ns]": DataTypeChoices.DATETIME.value,
        "ClassLabel": DataTypeChoices.TEXT.value,
        "void": DataTypeChoices.TEXT.value,
        "text": DataTypeChoices.TEXT.value,
        "varchar": DataTypeChoices.TEXT.value,
        "char": DataTypeChoices.TEXT.value,
        "large_string": DataTypeChoices.TEXT.value,
        "str": DataTypeChoices.TEXT.value,
        "double": DataTypeChoices.FLOAT.value,
        "numeric": DataTypeChoices.FLOAT.value,
        "real": DataTypeChoices.FLOAT.value,
        "time": DataTypeChoices.DATETIME.value,
        "dict": DataTypeChoices.JSON.value,
        "dictionary": DataTypeChoices.JSON.value,
        "object": DataTypeChoices.JSON.value,
        "json": DataTypeChoices.JSON.value,
        "jsonb": DataTypeChoices.JSON.value,
        "list": DataTypeChoices.ARRAY.value,
        "array": DataTypeChoices.ARRAY.value,
        "vector": DataTypeChoices.ARRAY.value,
        "tensor": DataTypeChoices.ARRAY.value,
        "Value": DataTypeChoices.TEXT.value,
        "Translation": DataTypeChoices.JSON.value,
        "TranslationVariableLanguages": DataTypeChoices.JSON.value,
        "category": DataTypeChoices.TEXT.value,
        "bool": DataTypeChoices.BOOLEAN.value,
        "boolean": DataTypeChoices.BOOLEAN.value,
        "bool_": DataTypeChoices.BOOLEAN.value,
        None: DataTypeChoices.TEXT.value,
    }
    if isinstance(column_info["type"], list):
        data_type = DataTypeChoices.ARRAY.value
    else:
        if column_info["type"].get("_type") == "Sequence":
            if column_info["type"].get("feature", {}).get("dtype"):
                data_type = DataTypeChoices.ARRAY.value
            else:
                data_type = DataTypeChoices.JSON.value
        else:
            try:
                data_type = data_type_map[
                    (
                        column_info["type"].get("dtype")
                        if "dtype" in column_info["type"]
                        else column_info["type"].get("_type")
                    )
                ]
            except KeyError:
                data_type = DataTypeChoices.TEXT.value

    return data_type


FORBIDDEN_SQL_PATTERNS = {
    "execute",
    "executemany",
    "raw",
    "RawSQL",
    "cursor",
    "sql",
    "psycopg2",
    "sqlite3",
}


class SQLDetector(ast.NodeVisitor):
    def __init__(self):
        self.found_sql = False

    def visit_Attribute(self, node):
        if isinstance(node.attr, str) and node.attr.lower() in FORBIDDEN_SQL_PATTERNS:
            self.found_sql = True
        self.generic_visit(node)

    def visit_Name(self, node):
        if isinstance(node.id, str) and node.id.lower() in FORBIDDEN_SQL_PATTERNS:
            self.found_sql = True


def contains_sql(code: str) -> bool:
    try:
        tree = ast.parse(code)
        detector = SQLDetector()
        detector.visit(tree)
        return detector.found_sql
    except Exception:
        # If AST parsing fails, better to be cautious
        return True


def submit_with_retry(executor, func, *args, **kwargs):
    """
    Submit a task to the executor with retry logic for shutdown errors.

    This function wraps the task execution with proper database connection
    handling to avoid stale connections in background threads.

    Args:
        executor: The executor instance (ThreadPoolExecutor)
        func: Function to execute
        *args, **kwargs: Arguments for the function

    Returns:
        Future object from the executor
    """
    from django.db import close_old_connections, connection

    def safe_execution():
        """Wrapper that ensures proper database connection handling."""
        try:
            close_old_connections()
            connection.ensure_connection()
            return func(*args, **kwargs)
        except Exception as e:
            logger.exception(f"Background task {func.__name__} failed: {e}")
            raise
        finally:
            close_old_connections()

    max_retries = 3
    for attempt in range(max_retries):
        try:
            return executor.submit(safe_execution)
        except RuntimeError as e:
            if (
                "cannot schedule new futures after shutdown" in str(e)
                and attempt < max_retries - 1
            ):
                logger.warning(
                    f"Executor shutdown detected, retrying attempt {attempt + 1}/{max_retries}"
                )
                import time

                time.sleep(0.1 * (attempt + 1))  # Exponential backoff
                continue
            else:
                raise


def track_running_eval_count(
    prompt_config_eval_id: str,
    start: bool = False,
    operation: Literal["get", "set"] = "set",
    num=None,
):
    cache_key = f"prompt_eval_{prompt_config_eval_id}"
    existing_count = cache.get(cache_key, 0)
    if operation == "get":
        return existing_count == 0
    else:
        if start:
            if num:
                cache.set(cache_key, existing_count + num)
            else:
                cache.set(cache_key, existing_count + 1)
        else:
            cache.set(cache_key, existing_count - 1)


class AnnotationCorpusBuilder:
    def __init__(self):
        # Download necessary resources once
        # Catch FileExistsError in case NLTK data directory already exists
        try:
            nltk.download("punkt", quiet=True)
            nltk.download("averaged_perceptron_tagger", quiet=True)
            nltk.download("wordnet", quiet=True)
            nltk.download("omw-1.4", quiet=True)
            nltk.download("stopwords", quiet=True)
        except FileExistsError:
            # Directory already exists, downloads can proceed
            pass

        self.lemmatizer = WordNetLemmatizer()
        self.stop_words = set(stopwords.words("english"))

    def get_wordnet_pos(self, tag):
        """Map POS tag to WordNet POS tag for lemmatization."""
        if tag.startswith("J"):
            return wordnet.ADJ
        elif tag.startswith("V"):
            return wordnet.VERB
        elif tag.startswith("N"):
            return wordnet.NOUN
        elif tag.startswith("R"):
            return wordnet.ADV
        else:
            return wordnet.NOUN  # default to noun

    def build_annotation_corpus(self, sentences):
        lemmatized_words = []
        sentence_words = []

        for sent in sentences:
            tokens = nltk.word_tokenize(sent)
            pos_tags = nltk.pos_tag(tokens)

            # Lemmatize and remove stopwords
            lemmas = [
                self.lemmatizer.lemmatize(word.lower(), self.get_wordnet_pos(pos))
                for word, pos in pos_tags
                if word.lower() not in self.stop_words and word.isalpha()
            ]

            lemmatized_words.extend(lemmas)
            sentence_words.append(len(lemmas))

        # Create vocabulary
        vocab = sorted(set(lemmatized_words))

        min_sen_len = min(sentence_words)
        max_sen_len = max(sentence_words)
        avg_len = sum(sentence_words) / len(sentence_words)

        # Word counts
        word_counts = Counter(lemmatized_words)
        top_20 = [word for word, count in word_counts.most_common(20)]

        return vocab, top_20, min_sen_len, max_sen_len, avg_len


corpus_builder = AnnotationCorpusBuilder()


def get_model_mode(model_name: str) -> str:
    """
    Determine the operational mode of a model.

    Args:
        model_name: The name of the model (e.g., 'whisper-1', 'gpt-4', 'elevenlabs/scribe_v1')

    Returns:
        str: One of 'chat', 'audio' (TTS), or 'stt'. Defaults to 'chat' if not found
            or if an error occurs.

    Examples:
        >>> get_model_mode('whisper-1')
        'stt'
        >>> get_model_mode('gpt-4')
        'chat'
        >>> get_model_mode('aura-2')
        'audio'
    """
    try:
        info = next(
            (m for m in AVAILABLE_MODELS if m.get("model_name") == model_name), None
        )
        return info.get("mode", "chat") if info else "chat"
    except Exception as e:
        logger.warning(f"Failed to determine model mode for {model_name}: {e}")
        return "chat"
