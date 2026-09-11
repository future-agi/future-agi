import base64
import time
import traceback
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

from tfc.utils.error_codes import get_error_message
from tfc.utils.ssrf_guard import safe_fetch


# Function to check if URL is valid
def is_valid_url(url):
    # SSRF-guarded reachability check — safe_fetch resolves + pins IP, blocks
    # private/link-local hosts (including 169.254.169.254 cloud metadata),
    # and re-validates each redirect hop.
    try:
        response = safe_fetch(url, method="HEAD", timeout=5)
        return response.status_code == 200
    except ValueError:
        return False


# Function to download image from URL
def download_image_from_url(url):
    response = safe_fetch(url, method="GET", timeout=30)
    return response.content


# Main function to handle URL or Base64 input
def check_image(img_base64_str, size_limit=5 * 1024 * 1024):
    # Check if the input is a URL
    parsed_url = urlparse(img_base64_str)
    if parsed_url.scheme in ("http", "https"):
        # Check if URL is valid and download image
        if is_valid_url(img_base64_str):
            img_bytes = download_image_from_url(img_base64_str)
        else:
            raise ValueError(get_error_message("INVALID_URL"))
    else:
        # Supported image formats in base64
        supported_formats = {
            "jpeg": "image/jpeg",
            "jpg": "image/jpeg",
            "png": "image/png",
            "gif": "image/gif",
        }

        # Check if input is a Base64 image with a data URI scheme
        if img_base64_str.startswith("data:"):
            format_detected = img_base64_str.split(";")[0].split("/")[1]

            # Ensure format is supported
            if format_detected in supported_formats:
                img_base64_str = img_base64_str.split(",")[1]  # Remove data URI header
            else:
                raise ValueError(
                    get_error_message(f"UNSUPPORTED_IMAGE_FORMAT: {format_detected}")
                )

        # Try to decode the base64 string into bytes
        try:
            img_bytes = base64.b64decode(img_base64_str)
        except Exception:
            traceback.print_exc()
            raise ValueError(get_error_message("INVALID_BASE64_IMAGE"))  # noqa: B904

    # Check if the image size exceeds the limit
    if len(img_bytes) > size_limit:
        raise ValueError(
            f"Image size exceeds 5MB limit: {len(img_bytes) / (1024 * 1024):.2f} MB"
        )

    return True


class LogModelPayload(BaseModel):
    model_id: str = Field(..., description="Unique identifier for the model")
    model_type: int = Field(
        ..., ge=0, description="Model type, must be a non-negative integer"
    )
    environment: int = Field(
        ..., ge=0, description="Environment, must be a non-negative integer"
    )
    model_version: str = Field(..., min_length=1, description="Version of the model")
    prediction_timestamp: int | None = Field(
        None, description="Timestamp of the prediction as a Unix timestamp"
    )

    conversation: Any | None = Field(
        None, description="Conversation data which can be any format or None."
    )
    tags: dict[str, str | bool | float | int] | None = Field(
        None, description="Tags related to the model"
    )

    @field_validator("model_id")
    def validate_model_id(cls, value):
        if not value or len(value) < 1:
            raise ValueError("model_id must be at least 1 characters long")
        return value

    @field_validator("prediction_timestamp")
    def validate_timestamp(cls, value):
        if value is None:
            return int(time.time())  # Assign current timestamp if not provided
        if value <= 0:
            raise ValueError("prediction_timestamp must be a positive integer")
        now = int(time.time())
        if value > now:
            raise ValueError("prediction_timestamp cannot be in the future.")
        return value

    @field_validator("tags")
    def validate_tags(cls, value):
        if value:
            if not all(
                isinstance(k, str) and isinstance(v, str | bool | float | int)
                for k, v in value.items()
            ):
                raise ValueError(
                    "tags must be a dictionary with string keys and values of type str, bool, float, or int"
                )
        return value

    @field_validator("model_type")
    def validate_model_type(cls, v):
        if v not in (8, 9):
            raise ValueError("model_type must be 8 or 9")
        return int(v)

    @field_validator("environment")
    def validate_environment(cls, v):
        if v not in (1, 2, 3, 4):
            raise ValueError("environment must be 1, 2, 3, or 4")
        return int(v)

    @field_validator("model_version")
    def validate_model_version(cls, v):
        if len(v) < 1:
            raise ValueError("model_version must be at least 1 characters long")
        return v

    @field_validator("conversation")
    def validate_conversation(cls, value):
        structure = None
        if value:
            # Validation for 'chat_graph' format
            if "chat_graph" in value:
                chat_graph = value["chat_graph"]
                if not isinstance(chat_graph, dict):
                    raise ValueError("chat_graph must be a dictionary")
                if "conversation_id" not in chat_graph or not isinstance(
                    chat_graph["conversation_id"], str
                ):
                    raise ValueError(
                        "chat_graph must contain a valid 'conversation_id' (string)"
                    )

                nodes = chat_graph.get("nodes", [])
                if not isinstance(nodes, list):
                    raise ValueError("chat_graph['nodes'] must be a list")

                for node in nodes:
                    if not isinstance(node, dict):
                        raise ValueError(
                            "Each node in 'chat_graph' must be a dictionary"
                        )
                    required_node_keys = ["message"]
                    for key in required_node_keys:
                        if key not in node:
                            raise ValueError(f"'chat_graph' node missing key: {key}")
                    # if not isinstance(node["node_id"], str):
                    #     raise ValueError("node_id must be a string")
                    # if not isinstance(node.get("parent_id"), (str, type(None))):
                    #     raise ValueError("parent_id must be a string or None")
                    # if not isinstance(node["timestamp"], int):
                    #     raise ValueError("timestamp must be an integer")

                    message = node["message"]
                    if not isinstance(message, dict):
                        raise ValueError("message in 'chat_graph' must be a dictionary")
                    if message.get("id"):
                        if not isinstance(message["id"], str):
                            raise ValueError("message must have a valid 'id' (string)")
                    if "author" not in message or not isinstance(
                        message["author"], dict
                    ):
                        raise ValueError(
                            "message must have a valid 'author' (dictionary)"
                        )
                    if (
                        "role" not in message["author"]
                        or not isinstance(message["author"]["role"], str)
                        or message["author"]["role"] not in ["user", "assistant"]
                    ):
                        raise ValueError(
                            "message author must have a valid 'role' (string)"
                        )
                    if "content" not in message or not isinstance(
                        message["content"], dict
                    ):
                        raise ValueError(
                            "message must have a valid 'content' (dictionary)"
                        )
                    if (
                        "content_type" not in message["content"]
                        or not isinstance(message["content"]["content_type"], str)
                        or message["content"]["content_type"] not in ["text"]
                    ):
                        raise ValueError(
                            "message content must have a valid 'content_type' (string)"
                        )
                    if "parts" not in message["content"] or not isinstance(
                        message["content"]["parts"], list
                    ):
                        raise ValueError(
                            "message content must have a valid 'parts' (list of strings)"
                        )
                    for part in message["content"]["parts"]:
                        if not isinstance(part, str):
                            raise ValueError("Each part in 'parts' must be a string")
                    if message.get("context"):
                        if not all(
                            isinstance(context, list)
                            and all(isinstance(item, str) for item in context)
                            for context in message.get("context")
                        ):
                            raise ValueError(
                                "message context must be a list of pairs of strings"
                            )
                    if message.get("variables"):
                        if not isinstance(message.get("variables"), dict):
                            raise ValueError("variables must be a dict object.")

                    if message.get("prompt_template"):
                        if not isinstance(message.get("prompt_template"), str):
                            raise ValueError("prompt_template must be a string.")
                    current_structure = {
                        "role": True if message.get("role") else False,
                        "content": True if message.get("content") else False,
                        "context": True if message.get("context") else False,
                        "prompt_template": (
                            True if message.get("prompt_template") else False
                        ),
                        "variables": True if message.get("variables") else False,
                    }

                    if structure is None and message["role"] == "user":
                        structure = current_structure
                    elif structure != current_structure and message["role"] == "user":
                        raise ValueError(
                            "Inconsistent structure detected in chat_history items"
                        )

            elif "chat_history" in value:
                chat_history = value["chat_history"]

                if not isinstance(chat_history, list):
                    raise ValueError("'chat_history' must be a list")

                image_counter = 0

                for item in chat_history:
                    if not isinstance(item, dict):
                        raise ValueError("'chat_history' item must be a dictionary")
                    if (
                        "role" not in item
                        or not isinstance(item["role"], str)
                        or item["role"] not in ["user", "assistant"]
                    ):
                        raise ValueError("'role' in 'chat_history' must be a string")
                    if "content" not in item:
                        raise ValueError("'content' in 'chat_history' must be a string")
                    if "content" in item:
                        if isinstance(item["content"], str):
                            pass
                        elif isinstance(item["content"], list):
                            content = item["content"][0]
                            if "text" == content["type"]:
                                if not content.get("text"):
                                    raise ValueError(
                                        "Content with type text must have text in it."
                                    )
                            elif "image_url" == content["type"]:
                                if not content.get("image_url"):
                                    raise ValueError(
                                        "Content with type image_url must have image_url in it."
                                    )
                                else:
                                    if not content["image_url"].get("url"):
                                        raise ValueError(
                                            "Content with type image_url must have URL in it under a dict object image_url with key url."
                                        )

                            for content in item["content"]:
                                if "text" == content["type"]:
                                    if not content.get("text"):
                                        raise ValueError(
                                            "Content with type text must have text in it."
                                        )
                                elif "image_url" == content["type"]:
                                    if not content.get("image_url"):
                                        raise ValueError(
                                            "Content with type image_url must have image_url in it."
                                        )
                                    else:
                                        if not content["image_url"].get("url"):
                                            raise ValueError(
                                                "Content with type image_url must have URL in it under a dict object image_url with key url."
                                            )
                                        check_image(
                                            img_base64_str=content["image_url"].get(
                                                "url"
                                            )
                                        )
                                        image_counter += 1

                        else:
                            raise ValueError(
                                "content must be a either string or list with dict object"
                            )

                    if item.get("context"):
                        context = item["context"]
                        if not isinstance(context, list) or not all(
                            isinstance(i, list) for i in context
                        ):
                            raise ValueError(
                                'context must be a list of pairs of strings [["", ""]...]'
                            )
                    if item.get("variables"):
                        if not isinstance(item.get("variables"), dict):
                            raise ValueError("variables must be a dict object.")

                    if item.get("prompt_template"):
                        if not isinstance(item.get("prompt_template"), str):
                            raise ValueError("prompt_template must be a string.")

                    current_structure = {
                        "role": True if item.get("role") else False,
                        "content": True if item.get("content") else False,
                        "context": True if item.get("context") else False,
                        "prompt_template": (
                            True if item.get("prompt_template") else False
                        ),
                        "variables": True if item.get("variables") else False,
                    }

                    if structure is None and item["role"] == "user":
                        structure = current_structure
                    elif structure != current_structure and item["role"] == "user":
                        raise ValueError(
                            "Inconsistent structure detected in chat_history items"
                        )
                    elif structure != current_structure and item["role"] == "user":
                        raise ValueError(
                            "Inconsistent structure detected in chat_history items"
                        )
                if image_counter > 8:
                    raise ValueError(
                        "Currently we are supporting 8 images in one message including output."
                    )

            else:
                raise ValueError(
                    "conversation",
                    value,
                    "conversation should be dict object with chat history or chat graph keys in it.",
                )

        return value

    class Config:
        protected_namespaces = ()  # Disable protected namespaces warning
