import re
from typing import Literal, Union

import requests
import structlog

from tfc.ee_stub import _ee_stub

try:
    from ee.agenthub.eval_recommendation.eval_recommendation import (
        EvalRecommender,
    )
except ImportError:
    EvalRecommender = _ee_stub("EvalRecommender")

logger = structlog.get_logger(__name__)
from model_hub.views.utils.constants import PLACEHOLDER_PATTERN

# Pattern to match UUID placeholders with optional .property suffix
# Matches: {{uuid}}, {{uuid.property}}, {{uuid.nested.property}}
# Uses [a-fA-F0-9] to match both uppercase and lowercase hex characters
UUID_PLACEHOLDER_PATTERN = re.compile(
    r"\{\{([a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12})(\.[\w.]+)?\}\}",
    re.IGNORECASE,
)


def sanitize_uuid_for_jinja(uuid_str: str) -> str:
    """
    Convert UUID to valid Jinja2/Python identifier by replacing hyphens with underscores
    and prefixing with underscore if it starts with a digit.

    Jinja2 interprets hyphens as subtraction operators, so {{a1b2-c3d4}} is parsed
    as "a1b2 - c3d4" (subtraction). Also, Python/Jinja2 identifiers cannot start
    with digits, so UUIDs like "00724a6a-..." need a prefix.

    Args:
        uuid_str: UUID string with hyphens (e.g., "a1b2c3d4-e5f6-7890-abcd-ef1234567890")

    Returns:
        Sanitized UUID with underscores (e.g., "a1b2c3d4_e5f6_7890_abcd_ef1234567890")
        If it starts with a digit, prefixed with underscore (e.g., "_00724a6a_...")
    """
    if not uuid_str:
        return "" if uuid_str is None else uuid_str
    sanitized = uuid_str.replace("-", "_")
    # Python/Jinja2 identifiers cannot start with digits
    if sanitized and sanitized[0].isdigit():
        sanitized = "_" + sanitized
    return sanitized


def sanitize_uuids_in_template(text: str) -> str:
    """
    Replace UUID placeholders in template text with sanitized versions.

    Converts {{uuid-with-hyphens}} to {{uuid_with_underscores}} so Jinja2
    can parse them as valid variable names. Also prefixes with underscore
    if UUID starts with a digit (e.g., {{00724a6a-...}} -> {{_00724a6a_...}}).

    Args:
        text: Template text containing {{uuid}} placeholders

    Returns:
        Text with UUIDs sanitized for valid Jinja2 identifiers
    """
    if not text:
        return text or ""

    def replace_match(match):
        uuid_str = match.group(1)
        suffix = match.group(2) or ""  # .property or empty
        sanitized_uuid = sanitize_uuid_for_jinja(uuid_str)
        return "{{" + sanitized_uuid + suffix + "}}"

    return UUID_PLACEHOLDER_PATTERN.sub(replace_match, text)


def replace_uuids_with_names(text: str, uuid_to_name: dict) -> str:
    """
    Replace UUID placeholders in template text with column names.

    Handles both simple {{uuid}} and nested {{uuid.property}} patterns.
    UUIDs contain hyphens which Jinja2 interprets as subtraction, so we must
    convert to column names (which don't have hyphens) before rendering.

    Args:
        text: Template text containing {{uuid}} or {{uuid.property}} placeholders
        uuid_to_name: Dict mapping column UUID strings to column names

    Returns:
        Text with UUIDs replaced by column names
    """

    def replace_match(match):
        uuid_str = match.group(1)
        suffix = match.group(2) or ""  # .property or empty
        if uuid_str in uuid_to_name:
            return "{{" + uuid_to_name[uuid_str] + suffix + "}}"
        return match.group(0)  # Return original if UUID not found

    return UUID_PLACEHOLDER_PATTERN.sub(replace_match, text)


def replace_uuids_in_messages(messages: list, uuid_to_name: dict) -> list:
    """
    Replace UUID placeholders with column names in a list of messages.

    Handles both string content and list content (mixed text/media).

    Args:
        messages: List of message dicts with 'role' and 'content'
        uuid_to_name: Dict mapping column UUID strings to column names

    Returns:
        Messages with UUIDs replaced by column names
    """
    if not messages or not uuid_to_name:
        return messages

    converted_messages = []
    for message in messages:
        # Copy all message keys first (preserves 'name', 'tool_calls', 'tool_call_id', etc.)
        converted_message = {**message}
        content = message.get("content", "")

        if isinstance(content, str):
            converted_message["content"] = replace_uuids_with_names(
                content, uuid_to_name
            )
        elif isinstance(content, list):
            converted_content = []
            for item in content:
                if isinstance(item, dict) and "text" in item:
                    converted_content.append(
                        {
                            **item,
                            "text": replace_uuids_with_names(
                                item["text"], uuid_to_name
                            ),
                        }
                    )
                else:
                    converted_content.append(item)
            converted_message["content"] = converted_content
        else:
            converted_message["content"] = content

        converted_messages.append(converted_message)

    return converted_messages


# Validation functions for file/media URLs

# File type configurations
FILE_TYPE_CONFIG = {
    "image": {
        "extensions": (
            ".jpg",
            ".jpeg",
            ".png",
            ".gif",
            ".bmp",
            ".webp",
            ".svg",
            ".ico",
        ),
        "content_type_prefix": "image/",
        "check_content_type": True,
    },
    "document": {
        "extensions": (
            ".pdf",
            ".doc",
            ".docx",
            ".txt",
            ".rtf",
            ".odt",
            ".xls",
            ".xlsx",
            ".ppt",
            ".pptx",
            ".csv",
        ),
        "content_type_prefix": None,
        "check_content_type": False,
    },
    "audio": {
        "extensions": (
            ".mp3",
            ".wav",
            ".ogg",
            ".m4a",
            ".flac",
            ".aac",
            ".wma",
            ".aiff",
            ".ape",
        ),
        "content_type_prefix": "audio/",
        "check_content_type": True,
    },
}


def is_valid_url(url_string: str) -> bool:
    """Check if string is a valid URL"""
    try:
        url_pattern = re.compile(
            r"^https?://"  # http:// or https://
            r"(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|"  # domain
            r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})"  # or IP
            r"(?::\d+)?"  # optional port
            r"(?:/?|[/?]\S+)$",
            re.IGNORECASE,
        )
        return bool(url_pattern.match(url_string))
    except Exception:
        return False


def validate_file_url(
    url: str, file_type: Union[Literal["image"], Literal["document"], Literal["audio"]]
) -> None:
    """
    Generic validation for file URLs (image, document, audio).

    Args:
        url: The URL to validate
        file_type: Type of file - "image", "document", or "audio"

    Raises:
        ValueError: If URL is invalid, inaccessible, or not the expected file type
    """
    if file_type not in FILE_TYPE_CONFIG:
        raise ValueError(
            f"Unsupported file_type: {file_type}. Must be one of {list(FILE_TYPE_CONFIG.keys())}"
        )

    if not url or not isinstance(url, str):
        raise ValueError(f"{file_type.capitalize()} URL cannot be empty")

    url = url.strip()

    # Check if it's a valid URL format
    if not is_valid_url(url):
        raise ValueError(f"Invalid {file_type} URL format: '{url}'")

    # Get configuration for this file type
    config = FILE_TYPE_CONFIG[file_type]
    valid_extensions = config["extensions"]

    # Check file extension
    url_lower = url.lower().split("?")[0]  # Remove query params
    if not any(url_lower.endswith(ext) for ext in valid_extensions):
        raise ValueError(
            f"URL does not appear to be a {file_type}. Expected extensions: {', '.join(valid_extensions)}"
        )

    # Verify URL is accessible
    try:
        response = requests.head(url, timeout=5, allow_redirects=True)
        if response.status_code >= 400:
            raise ValueError(
                f"{file_type.capitalize()} URL returned status code {response.status_code}"
            )

        # Check content type if configured for this file type
        if config["check_content_type"]:
            content_type = response.headers.get("Content-Type", "").lower()
            expected_prefix = config["content_type_prefix"]
            if content_type and not content_type.startswith(expected_prefix):
                raise ValueError(
                    f"URL content-type is '{content_type}', not a {file_type} type (expected {expected_prefix}*)"
                )
    except requests.exceptions.RequestException as e:
        raise ValueError(f"Cannot access {file_type} URL: {str(e)}")


def get_recommendations(new_dataset):
    try:
        recommendations = EvalRecommender(dataset_id=new_dataset.id).recommend_evals()
    except Exception:
        recommendations = {}
    recommend_evals = recommendations.get("recommended_evals", [])
    (
        recommend_evals.append("Deterministic Evals")
        if "Deterministic Evals" not in recommend_evals
        else recommend_evals
    )
    new_dataset.dataset_config.update({"eval_recommendations": recommend_evals})
    new_dataset.save(update_fields=["dataset_config"])

    return recommendations


def replace_column_ids(string, column_mapping):
    def replace_placeholder(match):
        placeholder_id = match.group("placeholder")

        # Try exact match first (covers simple {{uuid}} case)
        updated_value = column_mapping.get(placeholder_id)
        if updated_value is not None:
            return f"{{{{{updated_value}}}}}"

        # Handle dot-notation: {{uuid.json.path}}
        # Split base ID from .suffix, remap the base, preserve the suffix
        dot_idx = placeholder_id.find(".")
        if dot_idx > 0:
            base_id = placeholder_id[:dot_idx]
            suffix = placeholder_id[dot_idx:]  # e.g. ".a.b"
            updated_value = column_mapping.get(base_id)
            if updated_value is not None:
                return f"{{{{{updated_value}{suffix}}}}}"

        logger.info(f"NOT FOUND MAPPING FOR COL ID: {placeholder_id}")
        return match.group(0)

    updated_content = PLACEHOLDER_PATTERN.sub(replace_placeholder, string)
    return updated_content


def update_column_id(message, column_mapping):
    content = message.get("content", "")
    if isinstance(content, list):
        updated_content = []
        for item in content:
            if item.get("type") == "text":
                updated_text = replace_column_ids(item["text"], column_mapping)
                item["text"] = updated_text
            updated_content.append(item)
        message["content"] = updated_content

    elif isinstance(content, str):
        updated_content = replace_column_ids(content, column_mapping)
        message["content"] = updated_content

    return message


def fetch_required_keys_for_eval_template(eval_templates):
    required_keys = []
    for eval_template in eval_templates:
        required_keys.extend(eval_template.config.get("required_keys", []))
    return list(set(required_keys))  # Remove duplicates using set


def fetch_specific_mapping_for_specific_eval_template(mapping, eval_template):
    required_keys = eval_template.config.get("required_keys", [])
    optional_keys = eval_template.config.get("optional_keys", [])
    parsed_mapping = {}

    for key in required_keys:
        if key not in mapping:
            raise Exception(f"Required key {key} not found in mapping")
        parsed_mapping[key] = mapping[key]
    for key in optional_keys:
        if key in mapping and mapping[key] is not None and mapping[key] != "":
            parsed_mapping[key] = mapping[key]

    return parsed_mapping
