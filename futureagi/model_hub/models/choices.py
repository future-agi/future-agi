import base64
import io
import json
import mimetypes
import re
import traceback
import wave
from datetime import datetime
from enum import Enum
from urllib.parse import urlparse

import pandas as pd
import requests
import structlog
from PIL import Image

logger = structlog.get_logger(__name__)
from django.db import models

from agentic_eval.core_evals.fi_evals.eval_type import (
    FunctionEvalTypeId,
    FutureAgiEvalTypeId,
    GroundedEvalTypeId,
    LlmEvalTypeId,
)


class ModalityType(str, Enum):
    """Modality filter values for prompt API endpoints."""

    CHAT = "chat"
    AUDIO = "audio"
    STT = "stt"
    ALL = "all"

    @classmethod
    def default(cls):
        return cls.CHAT


class ModelTypes(Enum):
    NUMERIC = "Numeric"
    SCORE_CATEGORICAL = "ScoreCategorical"
    RANKING = "Ranking"
    BINARY_CF = "BinaryClassification"
    REGRESSION = "Regression"
    OBJECT_DETECTION = "ObjectDetection"
    SEGMENTATION = "Segmentation"
    GENERATIVE_LLM = "GenerativeLLM"
    GENERATIVE_IMAGE = "GenerativeImage"
    GENERATIVE_VIDEO = "GenerativeVideo"
    TTS = "TTS"
    STT = "STT"
    MULTI_MODAL = "MultiModal"

    @classmethod
    def get_choices(cls):
        return [(tag.value, tag.name.replace("_", " ").title()) for tag in cls]


class ModelChoices(Enum):
    TURING_LARGE = "turing_large"
    TURING_SMALL = "turing_small"
    PROTECT = "protect"
    PROTECT_FLASH = "protect_flash"
    TURING_FLASH = "turing_flash"

    @classmethod
    def get_choices(cls):
        return [(tag.value, tag.name.replace("_", " ").title()) for tag in cls]


class DataTypeChoices(Enum):
    TEXT = "text"
    BOOLEAN = "boolean"
    INTEGER = "integer"
    FLOAT = "float"
    JSON = "json"
    ARRAY = "array"
    IMAGE = "image"
    IMAGES = "images"  # Multiple images stored as JSON array
    DATETIME = "datetime"
    AUDIO = "audio"
    DOCUMENT = "document"
    OTHERS = "others"
    PERSONA = "persona"

    @classmethod
    def get_choices(cls):
        return [(tag.value, tag.name.replace("_", " ").title()) for tag in cls]


class AnnotationTypeChoices(Enum):
    TEXT = "text"
    NUMERIC = "numeric"
    CATEGORICAL = "categorical"
    STAR = "star"
    THUMBS_UP_DOWN = "thumbs_up_down"

    @classmethod
    def get_choices(cls):
        return [(tag.value, tag.name.replace("_", " ").title()) for tag in cls]


class AnnotationQueueStatusChoices(Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"

    @classmethod
    def get_choices(cls):
        return [(tag.value, tag.name.replace("_", " ").title()) for tag in cls]


class QueueItemSourceType(Enum):
    DATASET_ROW = "dataset_row"
    TRACE = "trace"
    OBSERVATION_SPAN = "observation_span"
    PROTOTYPE_RUN = "prototype_run"
    CALL_EXECUTION = "call_execution"
    TRACE_SESSION = "trace_session"

    @classmethod
    def get_choices(cls):
        return [(tag.value, tag.name.replace("_", " ").title()) for tag in cls]


class QueueItemStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    SKIPPED = "skipped"

    @classmethod
    def get_choices(cls):
        return [(tag.value, tag.name.replace("_", " ").title()) for tag in cls]


class ScoreSource(Enum):
    HUMAN = "human"
    API = "api"
    AUTO = "auto"
    IMPORTED = "imported"

    @classmethod
    def get_choices(cls):
        return [(tag.value, tag.name.replace("_", " ").title()) for tag in cls]


class AssignmentStrategy(Enum):
    MANUAL = "manual"
    ROUND_ROBIN = "round_robin"
    LOAD_BALANCED = "load_balanced"

    @classmethod
    def get_choices(cls):
        return [(tag.value, tag.name.replace("_", " ").title()) for tag in cls]


class AnnotatorRole(Enum):
    ANNOTATOR = "annotator"
    REVIEWER = "reviewer"
    MANAGER = "manager"

    @classmethod
    def get_choices(cls):
        return [(tag.value, tag.name.replace("_", " ").title()) for tag in cls]


class BooleanChoices(Enum):
    TRUE = "true"
    FALSE = "false"
    TRUE_OPTIONS = [
        "true",
        "True",
        "TRUE",
        "1",
        "yes",
        "Yes",
        "YES",
        "Passed",
        "Passed",
        "PASSED",
    ]
    FALSE_OPTIONS = [
        "false",
        "False",
        "FALSE",
        "0",
        "no",
        "No",
        "NO",
        "Failed",
        "Failed",
        "FAILED",
    ]

    @classmethod
    def get_choices(cls):
        return [(tag.value, tag.name.replace("_", " ").title()) for tag in cls]


class DateTimeFormatChoices(Enum):
    OPTIONS = [
        "%Y-%m-%d",  # 2024-03-21
        "%Y-%m-%d %H:%M:%S",  # 2024-03-21 15:30:45
        "%Y-%m-%d %H:%M",  # 2024-03-21 15:30
        "%d/%m/%Y",  # 21/03/2024
        "%d/%m/%Y %H:%M:%S",  # 21/03/2024 15:30:45
        "%d/%m/%Y %H:%M",  # 21/03/2024 15:30
        "%m/%d/%Y",  # 03/21/2024
        "%m/%d/%Y %H:%M:%S",  # 03/21/2024 15:30:45
        "%m/%d/%Y %H:%M",  # 03/21/2024 15:30
        "%Y/%m/%d",  # 2024/03/21
        "%Y/%m/%d %H:%M:%S",  # 2024/03/21 15:30:45
        "%Y/%m/%d %H:%M",  # 2024/03/21 15:30
        "%d-%m-%Y",  # 21-03-2024
        "%d-%m-%Y %H:%M:%S",  # 21-03-2024 15:30:45
        "%d-%m-%Y %H:%M",  # 21-03-2024 15:30
        "%m-%d-%Y",  # 03-21-2024
        "%m-%d-%Y %H:%M:%S",  # 03-21-2024 15:30:45
        "%m-%d-%Y %H:%M",  # 03-21-2024 15:30
        "%Y%m%d",  # 20240321
        "%Y%m%d%H%M%S",  # 20240321153045
        "%b %d %Y",  # Mar 21 2024
        "%b %d %Y %H:%M:%S",  # Mar 21 2024 15:30:45
        "%B %d %Y",  # March 21 2024
        "%B %d %Y %H:%M:%S",  # March 21 2024 15:30:45
    ]


class SourceChoices(Enum):
    EVALUATION = "evaluation"
    EVALUATION_TAGS = "evaluation_tags"
    EVALUATION_REASON = "evaluation_reason"

    RUN_PROMPT = "run_prompt"
    EXPERIMENT = "experiment"
    OPTIMISATION = "optimisation"

    EXPERIMENT_EVALUATION = "experiment_evaluation"
    EXPERIMENT_EVALUATION_TAGS = "experiment_evaluation_tags"

    OPTIMISATION_EVALUATION = "optimisation_evaluation"
    ANNOTATION_LABEL = "annotation_label"
    OPTIMISATION_EVALUATION_TAGS = "optimisation_evaluation_tags"

    EXTRACTED_JSON = "extracted_json"
    CLASSIFICATION = "classification"
    EXTRACTED_ENTITIES = "extracted_entities"
    API_CALL = "api_call"
    PYTHON_CODE = "python_code"
    VECTOR_DB = "vector_db"
    CONDITIONAL = "conditional"
    EVAL_PLAYGROUND = "eval_playground"

    OTHERS = "OTHERS"

    @classmethod
    def get_choices(cls):
        return [(tag.value, tag.name.replace("_", " ").title()) for tag in cls]


class DatasetSourceChoices(Enum):
    DEMO = "demo"
    BUILD = "build"
    SDK = "sdk"
    OBSERVE = "observe"
    KNOWLEDGE_BASE = "knowledge_base"
    SCENARIO = "scenario"
    EXPERIMENT_SNAPSHOT = "experiment_snapshot"
    GRAPH = "graph"

    @classmethod
    def get_choices(cls):
        return [(tag.value, tag.name.replace("_", " ").title()) for tag in cls]


class FeedbackSourceChoices(Enum):
    DATASET = "dataset"
    PROMPT = "prompt"
    SDK = "sdk"
    TRACE = "trace"
    EXPERIMENT = "experiment"
    OBSERVE = "observe"
    EVAL_PLAYGROUND = "eval_playground"

    @classmethod
    def get_choices(cls):
        return [(tag.value, tag.name.replace("_", " ").title()) for tag in cls]


class EvalSourceChoices(Enum):
    EVALUATION = "evaluation"
    EXPERIMENT = "experiment"
    OPTIMISATION = "optimisation"
    EXPERIMENT_EVALUATION = "experiment_evaluation"
    OPTIMISATION_EVALUATION = "optimisation_evaluation"
    OTHERS = "OTHERS"

    @classmethod
    def get_choices(cls):
        return [(tag.value, tag.name.replace("_", " ").title()) for tag in cls]


class CellStatus(Enum):
    ERROR = "error"
    RUNNING = "running"
    PASS = "pass"

    @classmethod
    def get_choices(cls):
        return [(tag.value, tag.name.replace("_", " ").title()) for tag in cls]


class OwnerChoices(Enum):
    SYSTEM = "system"
    USER = "user"

    @classmethod
    def get_choices(cls):
        return [(tag.value, tag.name.replace("_", " ").title()) for tag in cls]


def get_data_type_choice(num):
    data_type_map = {
        1: DataTypeChoices.TEXT,
        2: DataTypeChoices.BOOLEAN,
        3: DataTypeChoices.INTEGER,
        4: DataTypeChoices.FLOAT,
        5: DataTypeChoices.JSON,
    }
    return data_type_map.get(num, None)


def get_source_choice(num):
    source_map = {
        1: SourceChoices.EVALUATION,
        2: SourceChoices.RUN_PROMPT,
    }
    return source_map.get(num, None)


def get_model_types(num):
    model_type_map = {
        1: ModelTypes.NUMERIC,
        2: ModelTypes.SCORE_CATEGORICAL,
        3: ModelTypes.RANKING,
        4: ModelTypes.BINARY_CF,
        5: ModelTypes.REGRESSION,
        6: ModelTypes.OBJECT_DETECTION,
        7: ModelTypes.SEGMENTATION,
        8: ModelTypes.GENERATIVE_LLM,
        9: ModelTypes.GENERATIVE_IMAGE,
        10: ModelTypes.GENERATIVE_VIDEO,
        11: ModelTypes.TTS,
        12: ModelTypes.STT,
        13: ModelTypes.MULTI_MODAL,
    }
    return model_type_map.get(num, None)  # Return None if num is not found


def get_metrics_name_choices():
    # Collect all choices from each Enum class and combine them
    choices = []
    for eval_enum in [
        LlmEvalTypeId,
        FutureAgiEvalTypeId,
        FunctionEvalTypeId,
        GroundedEvalTypeId,
    ]:
        choices.extend(
            [
                (member.value, member.name.replace("_", " ").title())
                for member in eval_enum
            ]
        )
    return choices


def _get_audio_mime_types():
    """Get comprehensive list of audio MIME types"""
    return {
        "audio/mpeg",
        "audio/mp3",
        "audio/wav",
        "audio/wave",
        "audio/x-wav",
        "audio/flac",
        "audio/x-flac",
        "audio/ogg",
        "audio/vorbis",
        "audio/aac",
        "audio/mp4",
        "audio/m4a",
        "audio/x-m4a",
        "audio/aiff",
        "audio/x-aiff",
        "audio/au",
        "audio/basic",
        "audio/webm",
        "audio/opus",
        "audio/amr",
        "audio/3gpp",
    }


def _get_audio_extensions():
    """Get comprehensive list of audio file extensions"""
    return {
        ".mp3",
        ".wav",
        ".flac",
        ".ogg",
        ".aac",
        ".m4a",
        ".wma",
        ".aiff",
        ".aif",
        ".au",
        ".snd",
        ".opus",
        ".webm",
        ".amr",
        ".3gp",
        ".mp2",
        ".mp1",
        ".ape",
        ".mpc",
        ".wv",
        ".dts",
        ".ac3",
        ".mid",
        ".midi",
        ".ra",
        ".rm",
    }


def _get_image_mime_types():
    """Get comprehensive list of image MIME types"""
    return {
        "image/jpeg",
        "image/jpg",
        "image/png",
        "image/gif",
        "image/bmp",
        "image/webp",
        "image/svg+xml",
        "image/tiff",
        "image/tif",
        "image/ico",
        "image/x-icon",
        "image/vnd.microsoft.icon",
    }


def _get_image_extensions():
    """Get comprehensive list of image file extensions"""
    return {
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".bmp",
        ".webp",
        ".svg",
        ".tiff",
        ".tif",
        ".ico",
        ".heic",
        ".heif",
        ".avif",
    }


def is_audio_url(url):
    """
    Thread-safe audio URL detection using MIME type guessing.
    Alternative to HTTP requests for better performance.
    """
    try:
        url_lower = url.lower()

        # Check file extension
        audio_extensions = _get_audio_extensions()
        if any(url_lower.endswith(ext) for ext in audio_extensions):
            return True

        # Check MIME type based on URL
        mime_type, _ = mimetypes.guess_type(url)
        if mime_type and mime_type.lower() in _get_audio_mime_types():
            return True

        # Check for common audio URL patterns
        audio_patterns = [
            "/audio/",
            "/sound/",
            "/music/",
            "/media/",
            "audio=",
            "sound=",
            "music=",
            ".mp3",
            ".wav",
        ]
        if any(pattern in url_lower for pattern in audio_patterns):
            return True

        return False

    except Exception:
        return False


def is_document_url(url):
    """
    Check if a URL is a valid document URL that points to a downloadable document.
    Returns True if the URL is accessible and serves a document, False otherwise.
    Uses patterns and types from the existing codebase for consistency.
    """
    try:
        if not isinstance(url, str):
            return False

        # Check for data URLs
        if url.startswith("data:application/") or url.startswith("data:text/"):
            return True

        # Check for HTTP/HTTPS URLs
        if url.startswith(("http://", "https://")):
            # Parse the URL to validate its structure
            parsed_url = urlparse(url)
            if not parsed_url.netloc:  # No domain/host
                return False

            # Make a HEAD request to check if the URL is accessible
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
            response = requests.head(
                url, headers=headers, timeout=10, allow_redirects=True
            )

            if response.status_code != 200:
                return False

            # Check if the response indicates it's a downloadable document
            content_type = response.headers.get("content-type", "").lower()
            content_length = response.headers.get("content-length")

            # If content-length is 0 or very small, it's probably not a real file
            if content_length and int(content_length) < 100:
                return False

            # Use document types from the existing codebase
            document_mime_types = {
                "application/pdf",
                "application/msword",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                "application/vnd.ms-excel",
                "application/vnd.ms-powerpoint",
                "text/plain",
                "text/rtf",
                "application/rtf",
                "text/html",
                "application/xml",
                "text/csv",
                "application/octet-stream",
                "application/zip",
                "application/x-zip-compressed",
                # Additional document types
                "application/vnd.ms-word.document.12",
                "application/vnd.ms-excel.12",
                "application/vnd.ms-powerpoint.12",
                "text/markdown",
                "application/json",
                "application/javascript",
                "text/javascript",
                "text/css",
                "text/xml",
            }

            # Check if content-type indicates it's a document
            if content_type in document_mime_types:
                return True

            # If content-type doesn't match, check file extensions
            path_lower = parsed_url.path.lower()
            document_extensions = {
                ".pdf",
                ".doc",
                ".docx",
                ".txt",
                ".rtf",
                ".xls",
                ".xlsx",
                ".ppt",
                ".pptx",
                ".csv",
                ".html",
                ".htm",
                ".xml",
                ".zip",
                ".rar",
                ".7z",
                ".md",
                ".markdown",
                ".json",
                ".js",
                ".css",
            }

            if any(path_lower.endswith(ext) for ext in document_extensions):
                return True

            # If content-type doesn't match, check URL patterns from existing codebase
            url_lower = url.lower()
            document_patterns = [
                "/document/",
                "/doc/",
                "/file/",
                "/download/",
                "/attachment/",
                "document=",
                "doc=",
                "file=",
                "download=",
                "attachment=",
            ]

            if any(pattern in url_lower for pattern in document_patterns):
                return True

        return False

    except Exception:
        return False


def is_image_url(url):
    """
    Thread-safe image URL detection using MIME type guessing.
    Alternative to HTTP requests for better performance.
    """
    try:
        url_lower = url.lower()

        # Check file extension
        image_extensions = _get_image_extensions()
        if any(url_lower.endswith(ext) for ext in image_extensions):
            return True

        # Check MIME type based on URL
        mime_type, _ = mimetypes.guess_type(url)
        if mime_type and mime_type.lower() in _get_image_mime_types():
            return True

        # Check for common image URL patterns
        image_patterns = [
            "/image/",
            "/img/",
            "/photo/",
            "/picture/",
            "/media/",
            "image=",
            "img=",
            "photo=",
            "picture=",
        ]
        if any(pattern in url_lower for pattern in image_patterns):
            return True

        return False

    except Exception:
        return False


def _check_for_images(column_values):
    """
    Check if column contains multiple image URLs.
    Supports two formats:
    1. JSON array of URLs: ["url1", "url2"]
    2. Comma-separated URLs: url1, url2

    Returns True if ALL non-null values are lists of valid image URLs.
    """
    from model_hub.utils.image_utils import parse_image_urls

    try:
        non_null_values = column_values.dropna()
        if non_null_values.empty:
            return False

        for val in non_null_values:
            val_str = str(val).strip()

            parts = parse_image_urls(val_str)

            # Must contain a comma for comma-separated to be considered multi-image
            if not val_str.startswith("[") and "," not in val_str:
                return False

            # Need at least 2 parts to be "images" (multiple)
            if len(parts) < 2:
                return False

            # Each part must be a valid image URL
            for part in parts:
                if not is_image_url(part):
                    return False

        return True
    except Exception:
        logger.exception("Error in _check_for_images")
        return False


def is_base64_audio(data):
    """
    Thread-safe audio detection using file signatures and built-in libraries.
    No external dependencies like pydub required.
    """
    try:
        # Check if it's a valid string first
        if not isinstance(data, str):
            return False

        # Check for data:audio prefix (data URLs)
        if data.startswith("data:audio"):
            return True

        # Try to decode base64
        try:
            audio_data = base64.b64decode(data)
        except Exception:
            return False

        # Check for common audio file signatures (magic numbers)
        audio_signatures = [
            # WAV files
            (b"RIFF", 0, b"WAVE", 8),  # RIFF header + WAVE format
            # MP3 files
            (b"ID3", 0),  # ID3v2 header
            (b"\xff\xfb", 0),  # MP3 frame sync (MPEG-1 Layer 3)
            (b"\xff\xf3", 0),  # MP3 frame sync (MPEG-1 Layer 3)
            (b"\xff\xf2", 0),  # MP3 frame sync (MPEG-2 Layer 3)
            # FLAC files
            (b"fLaC", 0),  # FLAC signature
            # OGG files
            (b"OggS", 0),  # OGG signature
            # M4A/AAC files
            (b"ftypM4A", 4),  # M4A container
            (b"ftypisom", 4),  # ISO Base Media
            (b"ftypmp42", 4),  # MP4 v2
            # AIFF files
            (b"FORM", 0, b"AIFF", 8),  # AIFF signature
            # AU files
            (b".snd", 0),  # AU/SND signature
        ]

        # Check each signature
        for signature_info in audio_signatures:
            if len(signature_info) == 2:
                # Simple signature check
                signature, offset = signature_info
                if len(audio_data) > offset + len(signature):
                    if audio_data[offset : offset + len(signature)] == signature:
                        return True
            elif len(signature_info) == 4:
                # Compound signature check (like RIFF + WAVE)
                sig1, offset1, sig2, offset2 = signature_info
                if (
                    len(audio_data) > max(offset1 + len(sig1), offset2 + len(sig2))
                    and audio_data[offset1 : offset1 + len(sig1)] == sig1
                    and audio_data[offset2 : offset2 + len(sig2)] == sig2
                ):
                    return True

        # Additional check for WAV files using wave module (built-in)
        try:
            audio_file = io.BytesIO(audio_data)
            with wave.open(audio_file, "rb") as wav_file:
                # If we can open it as WAV, it's valid audio
                return wav_file.getnframes() > 0
        except Exception:
            pass

        return False

    except Exception:
        return False


def _check_for_audio(column_values):
    try:
        audio_extensions = [
            # Common audio formats
            ".wav",
            ".mp3",
            ".flac",
            ".ogg",
            ".m4a",
            ".aac",
            ".aiff",
            ".aif",
            ".au",
            ".opus",
            # Additional formats
            ".wma",
            ".mp2",
            ".mp1",
            ".ape",
            ".mpc",
            ".wv",
            ".dts",
            ".ac3",
            ".amr",
            ".mid",
            ".midi",
            ".ra",
            ".rm",
            ".3gp",
            ".aac",
            ".act",
            ".awb",
            ".dct",
            # Less common but still used formats
            ".gsm",
            ".iklax",
            ".ivs",
            ".mmf",
            ".mxmf",
            ".tta",
            ".voc",
            ".vox",
            ".xm",
            ".caf",
            ".dss",
            ".dvf",
            ".iklax",
            ".m4b",
            ".m4p",
            ".nmf",
            ".nsf",
            ".sln",
            # Raw audio formats
            ".raw",
            ".pcm",
            ".snd",
            ".rf64",
            # Professional audio formats
            ".w64",
            ".sds",
            ".paf",
            ".sd2",
            ".caf",
            ".wve",
            ".mka",
            ".aa3",
            ".oma",
        ]

        # Check if ALL non-null values are audio
        non_null_values = column_values.dropna()
        if non_null_values.empty:
            return False

        for val in non_null_values:
            is_audio = False

            if isinstance(val, dict) and "path" in val:
                if val["path"] is not None:
                    is_audio = any(
                        val["path"].lower().endswith(ext) for ext in audio_extensions
                    )
                elif "sampling_rate" in val:
                    is_audio = True
            elif isinstance(val, str) and val.startswith("http"):
                # Option 1: Use thread-safe URL analysis (recommended)
                is_audio = is_audio_url(val)

            elif isinstance(val, str) and is_base64_audio(val):
                is_audio = True

            # If any value is not audio, return False
            if not is_audio:
                return False

        # If we reach here, all values are audio
        return True

    except Exception:
        logger.exception("Error in _check_for_audio")
        return False


def is_base64_image(data):
    try:
        image_data = base64.b64decode(data)
        Image.open(io.BytesIO(image_data))
        return True
    except Exception:
        return False


def _check_for_image(column_values):
    try:
        # Check if ALL non-null values are images
        non_null_values = column_values.dropna()
        if non_null_values.empty:
            return False

        for val in non_null_values:
            is_image = False

            if isinstance(val, Image.Image):
                is_image = True
            elif isinstance(val, str) and val.startswith("data:image"):
                is_image = True
            elif isinstance(val, str) and (
                val.startswith("http://") or val.startswith("https://")
            ):
                # Skip if value contains comma - likely multiple URLs (handled by _check_for_images)
                if "," in val:
                    return False
                # Option 1: Use thread-safe URL analysis (recommended)
                is_image = is_image_url(val)

            elif isinstance(val, str) and is_base64_image(val):
                is_image = True

            # If any value is not an image, return False
            if not is_image:
                return False

        # If we reach here, all values are images
        return True

    except Exception:
        logger.exception("Error in _check_for_image")
        return False


def _all_valid_json_strings(str_values):
    """
    Check if all string values are valid JSON objects (dicts) or arrays containing objects.
    Pure arrays like [], [1,2,3], ["a","b"] should be ARRAY type, not JSON.
    Only complex structures (objects or arrays of objects) should be JSON.
    """
    if not str_values:
        return False

    has_complex_structure = False
    for val in str_values:
        try:
            parsed = json.loads(val)
            # Only consider it JSON if it's a dict or list
            if isinstance(parsed, dict):
                has_complex_structure = True
            elif isinstance(parsed, list):
                # Check if list contains any dicts (complex objects)
                if any(isinstance(item, dict) for item in parsed):
                    has_complex_structure = True
                # Pure arrays (empty or with primitives) should not be JSON
            else:
                # Primitives (int, float, str, bool, None) should not be classified as JSON
                return False
        except (ValueError, TypeError):
            return False

    # Only return True if we found at least one complex structure (dict or array of dicts)
    return has_complex_structure


def _is_integer_string_column(str_values):
    """
    Check if all string values can be parsed as integers.
    Handles strings like "123", "456" from CSV files.
    """
    if not str_values:
        return False
    for val in str_values:
        try:
            # Must be a valid integer (not float)
            int(val)

        except (ValueError, TypeError):
            return False
    return True


def _is_float_string_column(str_values):
    """
    Check if all string values can be parsed as floats.
    Handles strings like "123.45", "67.89" from CSV files.
    """
    if not str_values:
        return False
    for val in str_values:
        try:
            float(val)
        except (ValueError, TypeError):
            return False

    return True


def _is_boolean_string_column(str_values):
    """
    Check if all string values are boolean representations.
    Handles strings like "true", "false", "True", "False", "TRUE", "FALSE" from CSV files.
    """
    if not str_values:
        return False
    boolean_values = {"true", "false", "0", "1"}
    for val in str_values:
        if val and val.lower() not in boolean_values:
            return False
    return True


def _is_strict_datetime_column(str_values):
    """
    Stricter datetime detection - require date-like patterns,
    not just anything pd.to_datetime can parse.
    """
    if not str_values:
        return False

    date_pattern = re.compile(
        r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}"  # YYYY-MM-DD or YYYY/MM/DD
        r"|^\d{1,2}[-/]\d{1,2}[-/]\d{4}"  # DD-MM-YYYY or MM/DD/YYYY
        r"|^\d{4}-\d{2}-\d{2}T"  # ISO format with time
    )
    for val in str_values:
        if not date_pattern.match(val.strip()):
            return False
    return True


def _is_array_column(str_values):
    """
    Check for actual array syntax (brackets), not just commas.
    Only detects simple arrays - arrays containing objects should be JSON.
    """
    if not str_values:
        return False
    for val in str_values:
        stripped = val.strip()
        # Must start with [ and end with ]
        if not (stripped.startswith("[") and stripped.endswith("]")):
            return False
        # Check if it's a simple array (not containing JSON objects)
        # Try to parse as JSON and check if it contains dicts
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, list):
                # If any element is a dict, this should be JSON, not ARRAY
                if any(isinstance(item, dict) for item in parsed):
                    return False
        except (ValueError, TypeError):
            # Not valid JSON, but has bracket syntax - treat as array
            # This handles Python-style arrays like "['a', 'b']" (single quotes)
            pass
    return True


# Base64 prefixes for common image formats (raw base64 without data URL prefix)
# These are the first few characters of base64-encoded image data
IMAGE_BASE64_PREFIXES = (
    "iVBORw0KGgo",  # PNG  - starts with bytes 89 50 4E 47 (‰PNG)
    "/9j/",  # JPEG - starts with bytes FF D8 FF
    "R0lGOD",  # GIF  - starts with bytes 47 49 46 (GIF87a or GIF89a)
    "UklGR",  # WebP - starts with bytes 52 49 46 46 (RIFF)
    "Qk",  # BMP  - starts with bytes 42 4D (BM)
)

# Base64 prefixes for common audio formats (raw base64 without data URL prefix)
AUDIO_BASE64_PREFIXES = (
    "SUQz",  # MP3 with ID3 tag - starts with bytes 49 44 33 (ID3)
    "//uQ",  # MP3 without ID3 - starts with bytes FF FB
    "T2dnU",  # OGG  - starts with bytes 4F 67 67 53 (OggS)
    "UklGR",  # WAV  - starts with bytes 52 49 46 46 (RIFF)
    "ZkxhQ",  # FLAC - starts with bytes 66 4C 61 43 (fLaC)
)


def _check_media_strings(str_values, media_type):
    """
    Check if all string values are data URLs or base64 for the given media type.
    media_type should be 'image' or 'audio'.

    Supports detection of:
    - Data URLs (e.g., "data:image/png;base64,...")
    - Raw base64-encoded media data (detected via format-specific prefixes)
    """
    if not str_values:
        return False
    for val in str_values:
        if media_type == "image":
            is_data_url = val.startswith("data:image")
            is_raw_base64 = val.startswith(IMAGE_BASE64_PREFIXES)
            if not (is_data_url or is_raw_base64):
                return False
        elif media_type == "audio":
            is_data_url = val.startswith("data:audio")
            is_raw_base64 = val.startswith(AUDIO_BASE64_PREFIXES)
            if not (is_data_url or is_raw_base64):
                return False
    return True


def _classify_url_column(str_values):
    """
    Classify a column of URLs by checking if all are of the same media type.
    """
    all_audio_urls = all(is_audio_url(url) for url in str_values)
    all_image_urls = all(is_image_url(url) for url in str_values)
    all_document_urls = all(is_document_url(url) for url in str_values)

    if all_audio_urls:
        return DataTypeChoices.AUDIO.value
    elif all_image_urls:
        return DataTypeChoices.IMAGE.value
    elif all_document_urls:
        return DataTypeChoices.DOCUMENT.value
    else:
        return DataTypeChoices.TEXT.value


def determine_data_type(column_values):
    """
    Determine the data type of a pandas column for dataset schema inference.
    """
    logger.info("Determining data type for column")
    logger.debug("column_values", column_values=column_values)

    if column_values.dropna().empty:
        return DataTypeChoices.TEXT.value  # Default type if the column is empty

    # 1. Check pandas dtype first (fast path for properly typed data)
    if column_values.dtype == bool:
        return DataTypeChoices.BOOLEAN.value

    if pd.api.types.is_integer_dtype(column_values):
        return DataTypeChoices.INTEGER.value

    if pd.api.types.is_float_dtype(column_values):
        return DataTypeChoices.FLOAT.value

    if pd.api.types.is_datetime64_any_dtype(column_values):
        return DataTypeChoices.DATETIME.value

    # 2. Check for audio/image objects (HuggingFace datasets)
    if _check_for_audio(column_values):
        return DataTypeChoices.AUDIO.value

    if _check_for_image(column_values):
        return DataTypeChoices.IMAGE.value

    # 3. Check if values are Python list/dict objects (parsed JSON from pd.read_json)
    non_null = column_values.dropna()
    if len(non_null) > 0 and all(isinstance(val, (dict, list)) for val in non_null):
        # Distinguish between pure arrays and complex JSON
        all_are_lists = all(isinstance(val, list) for val in non_null)
        if all_are_lists:
            # Check if any list contains dicts (complex objects)
            has_complex_content = any(
                any(isinstance(item, dict) for item in val) for val in non_null if val
            )
            if not has_complex_content:
                return DataTypeChoices.ARRAY.value
        return DataTypeChoices.JSON.value

    # 4. Convert to string for further checks
    str_values = column_values.astype(str)
    non_null_strs = [v for v in str_values.dropna() if v]

    # Check if all values are comma-separated image URLs (multiple images)
    # This must come BEFORE the array check to properly detect image arrays
    if _check_for_images(column_values):
        return DataTypeChoices.IMAGES.value

    # 5. Check for string representations of primitive types (from CSV)
    # Boolean strings: "true", "false", "True", "False", etc.
    if _is_boolean_string_column(non_null_strs):
        return DataTypeChoices.BOOLEAN.value

    # Integer strings: "123", "456", etc.
    if _is_integer_string_column(non_null_strs):
        return DataTypeChoices.INTEGER.value

    # Float strings: "123.45", "67.89", etc.
    if _is_float_string_column(non_null_strs):
        return DataTypeChoices.FLOAT.value

    # 6. Check for image/audio data URLs and base64
    if _check_media_strings(non_null_strs, "image"):
        return DataTypeChoices.IMAGE.value
    if _check_media_strings(non_null_strs, "audio"):
        return DataTypeChoices.AUDIO.value

    # 7. Check for document/image/audio URLs
    if non_null_strs and all(
        v.startswith("http://") or v.startswith("https://") for v in non_null_strs
    ):
        return _classify_url_column(non_null_strs)

    # 8. Datetime check with stricter validation (regex-based, not pd.to_datetime)
    if _is_strict_datetime_column(non_null_strs):
        return DataTypeChoices.DATETIME.value

    # 9. Array check - bracket syntax [a,b,c] - BEFORE JSON to catch simple arrays
    if _is_array_column(non_null_strs):
        return DataTypeChoices.ARRAY.value

    # 10. JSON check - only for objects or arrays containing objects
    if _all_valid_json_strings(non_null_strs):
        return DataTypeChoices.JSON.value

    return DataTypeChoices.TEXT.value  # Default to TEXT if none of the conditions match


def determine_cell_type(value):
    # Handle pandas Series/arrays and regular values safely
    try:
        if pd.isna(value):
            return DataTypeChoices.TEXT.value  # Default type if the value is empty
    except (ValueError, TypeError):
        # If pd.isna fails (e.g., for pandas Series), try alternative approach
        if value is None or (hasattr(value, "__len__") and len(value) == 0):
            return DataTypeChoices.TEXT.value  # Default type if the value is empty

    # Check if value is boolean
    if isinstance(value, bool):
        return DataTypeChoices.BOOLEAN.value

    # Check if value is integer
    if isinstance(value, int):
        return DataTypeChoices.INTEGER.value

    # Check if value is float
    if isinstance(value, float):
        return DataTypeChoices.FLOAT.value

    # Check if value is already a datetime object
    if isinstance(value, datetime | pd.Timestamp):
        return DataTypeChoices.DATETIME.value

    # Convert to string for further checks
    str_value = str(value)

    # Check if string is a date
    try:
        pd.to_datetime(str_value)
        return DataTypeChoices.DATETIME.value
    except ValueError:
        pass  # Not a date, continue with other checks

    # Check if value is JSON
    try:
        json.loads(str_value)
        return DataTypeChoices.JSON.value
    except ValueError:
        pass  # Invalid JSON; continue check for other types

    # Check if value is array (assuming comma-separated values)
    if "," in str_value:
        return DataTypeChoices.ARRAY.value

    # Check if value is image bytes (assuming base64 encoded)
    if str_value.startswith(("data:image", "iVBORw0KGgo")):
        return DataTypeChoices.IMAGE.value

    # Check if value is image URL
    url_pattern = re.compile(
        r"https?://\S+(?:jpg|jpeg|png|gif|bmp|webp)", re.IGNORECASE
    )
    if url_pattern.match(str_value):
        return DataTypeChoices.IMAGE.value

    return DataTypeChoices.TEXT.value  # Default to TEXT if none of the conditions match


class LiteLlmModelProvider(Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    AZURE = "azure"
    # AZURE_AI = "azure_ai"
    VERTEX_AI = "vertex_ai"
    PALM = "palm"
    GEMINI = "gemini"
    COHERE = "cohere"
    HUGGINGFACE = "huggingface"
    TEXT_COMPLETION_OPENAI = "text-completion-openai"
    # VERTEX_AI_EMBEDDING_MODELS = "vertex_ai-embedding-models"
    TOGETHER_AI = "together_ai"
    REPLICATE = "replicate"
    CEREBRAS = "cerebras"
    # VERTEX_AI_ANTHROPIC_MODELS = "vertex_ai-anthropic_models"
    COHERE_CHAT = "cohere_chat"
    FRIENDLIAI = "friendliai"
    FIREWORKS_AI = "fireworks_ai"
    # VERTEX_AI_MISTRAL_MODELS = "vertex_ai-mistral_models"
    ANYSCALE = "anyscale"
    TEXT_COMPLETION_CODESTRAL = "text-completion-codestral"
    NLP_CLOUD = "nlp_cloud"
    OPENROUTER = "openrouter"
    CLOUDFLARE = "cloudflare"
    DEEPSEEK = "deepseek"
    ALEPH_ALPHA = "aleph_alpha"
    FIREWORKS_AI_EMBEDDING_MODELS = "fireworks_ai-embedding-models"
    MISTRAL = "mistral"
    BEDROCK = "bedrock"
    OLLAMA = "ollama"
    DEEPINFRA = "deepinfra"
    DATABRICKS = "databricks"
    # VERTEX_AI_TEXT_MODELS = "vertex_ai-text-models"
    # VERTEX_AI_IMAGE_MODELS = "vertex_ai-image-models"
    # VERTEX_AI_CODE_TEXT_MODELS = "vertex_ai-code-text-models"
    # VERTEX_AI_LLAMA_MODELS = "vertex_ai-llama_models"
    # VERTEX_AI_LANGUAGE_MODELS = "vertex_ai-language-models"
    # VERTEX_AI_VISION_MODELS = "vertex_ai-vision-models"
    VOYAGE = "voyage"
    # VERTEX_AI_AI21_MODELS = "vertex_ai-ai21_models"
    GROQ = "groq"
    CODESTRAL = "codestral"
    PERPLEXITY = "perplexity"
    AI21 = "ai21"
    SAGEMAKER = "sagemaker"
    ELEVENLABS = "elevenlabs"
    DEEPGRAM = "deepgram"
    INWORLD = "inworld"
    RIME = "rime"
    NEUPHONIC = "neuphonic"
    HUME = "hume"
    CARTESIA = "cartesia"
    LMNT = "lmnt"
    # VERTEX_AI_CHAT_MODELS = "vertex_ai-chat-models"
    # VERTEX_AI_CODE_CHAT_MODELS = "vertex_ai-code-chat-models"

    @classmethod
    def get_choices(cls):
        return [(tag.value, tag.name.replace("_", " ").title()) for tag in cls]


class ProviderLogoUrls(Enum):
    OPENAI = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/openai-icon.png"
    ANTHROPIC = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/claude-ai-icon.png"
    AZURE = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/azure-icon.png"
    AZURE_AI = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/azure-icon.png"
    VERTEX_AI = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/vertex+ai.png"
    PALM = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/palm.png"
    GEMINI = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/google-gemini-icon.png"
    COHERE = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/cohere-small.png"
    HUGGINGFACE = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/hugginface.png"
    TEXT_COMPLETION_OPENAI = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/openai-icon.png"
    VERTEX_AI_EMBEDDING_MODELS = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/vertex+ai.png"
    TOGETHER_AI = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/together-small.png"
    REPLICATE = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/replicate-small.svg"
    CEREBRAS = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/Cerebras.jpg"
    VERTEX_AI_ANTHROPIC_MODELS = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/vertex+ai.png"
    COHERE_CHAT = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/cohere-small.png"
    FRIENDLIAI = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/friendliai-small.jpeg"
    FIREWORKS_AI = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/fireworks-small.jpeg"
    VERTEX_AI_MISTRAL_MODELS = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/vertex+ai.png"
    ANYSCALE = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/anyscale-small.png"
    TEXT_COMPLETION_CODESTRAL = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/mistral-ai-icon.png"
    NLP_CLOUD = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/NLP+Cloud.png"
    OPENROUTER = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/openrouter.png"
    CLOUDFLARE = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/cloudflare-icon.png"
    DEEPSEEK = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/deep-seek-small.png"
    ALEPH_ALPHA = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/aleph-alpha-small.jpeg"
    FIREWORKS_AI_EMBEDDING_MODELS = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/fireworks-small.jpeg"
    MISTRAL = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/mistral-ai-icon.png"
    BEDROCK = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/bedrock-small.svg"
    OLLAMA = (
        "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/ollama.png"
    )
    DEEPINFRA = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/deepinfram-small.jpeg"
    DATABRICKS = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/databricks-small.png"
    VERTEX_AI_TEXT_MODELS = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/vertex+ai.png"
    VERTEX_AI_IMAGE_MODELS = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/vertex+ai.png"
    VERTEX_AI_CODE_TEXT_MODELS = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/vertex+ai.png"
    VERTEX_AI_LLAMA_MODELS = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/vertex+ai.png"
    VERTEX_AI_LANGUAGE_MODELS = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/vertex+ai.png"
    VERTEX_AI_VISION_MODELS = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/vertex+ai.png"
    VOYAGE = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/Voyage+ai.png"
    VERTEX_AI_AI21_MODELS = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/vertex+ai.png"
    GROQ = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/groq-small.png"
    CODESTRAL = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/mistral-ai-icon.png"
    PERPLEXITY = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/perplexity-ai-icon.png"
    AI21 = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/ai21-small.png"
    SAGEMAKER = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/Sagemaker.png"
    VERTEX_AI_CHAT_MODELS = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/vertex+ai.png"
    VERTEX_AI_CODE_CHAT_MODELS = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/vertex+ai.png"
    ELEVENLABS = "https://fi-content-dev.s3.ap-south-1.amazonaws.com/provider-logos/elevenlabs-small.png"
    DEEPGRAM = "https://fi-content-dev.s3.ap-south-1.amazonaws.com/provider-logos/deepgram-small.png"
    INWORLD = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/inworld-small.png"
    RIME = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/rime-small.png"
    NEUPHONIC = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/neuphonic-small.png"
    HUME = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/hume-small.png"
    CARTESIA = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/cartesia-small.png"
    LMNT = "https://fi-image-assets.s3.ap-south-1.amazonaws.com/provider-logos/lmnt-small.png"

    @classmethod
    def get_url_by_provider(cls, provider_name):
        try:
            # Convert provider name to match enum format
            enum_name = provider_name.upper().replace("-", "_")
            return cls[enum_name].value
        except KeyError:
            return None

    @classmethod
    def get_choices(cls):
        return [(tag.value, tag.name.replace("_", " ").title()) for tag in cls]


class DatasetStatus(Enum):
    PARTIAL_EXTRACTED = "PartialDataExtracted"
    COMPLETED = "Completed"
    PARTIAL_UPLOAD = "PartialUploadProgress"
    RUNNING = "Running"

    @classmethod
    def get_choices(cls):
        return [(tag.value, tag.name.replace("_", " ").title()) for tag in cls]


class StatusType(Enum):
    NOT_STARTED = "NotStarted"
    QUEUED = "Queued"
    RUNNING = "Running"
    COMPLETED = "Completed"
    EDITING = "Editing"
    INACTIVE = "Inactive"
    FAILED = "Failed"
    PARTIAL_RUN = "PartialRun"
    EXPERIMENT_EVALUATION = "ExperimentEvaluation"
    UPLOADING = "Uploading"
    PARTIAL_EXTRACTED = "PartialExtracted"
    PROCESSING = "Processing"
    DELETING = "Deleting"
    PARTIAL_COMPLETED = "PartialCompleted"
    OPTIMIZATION_EVALUATION = "OptimizationEvaluation"
    ERROR = "Error"
    CANCELLED = "Cancelled"

    @classmethod
    def get_choices(cls):
        return [(tag.value, tag.name.replace("_", " ").title()) for tag in cls]


class EnvTypes(Enum):
    PRODUCTION = "Production"
    TRAINING = "Training"
    VALIDATION = "Validation"
    CORPUS = "Corpus"

    @classmethod
    def get_choices(cls):
        return [(tag.value, tag.name.replace("_", " ").title()) for tag in cls]


class EvalExplanationSummaryStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    RUNNING = "running", "Running"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"
    INSUFFICIENT_DATA = "insufficient_data", "Insufficient Data"


class EvalOutputType(Enum):
    PASS_FAIL = "Pass/Fail"
    SCORE = "score"
    NUMERIC = "numeric"
    REASON = "reason"
    CHOICES = "choices"
    EMPTY = ""

    @classmethod
    def get_choices(cls):
        return [(tag.value, tag.name.replace("_", " ").title()) for tag in cls]


class EvalTemplateType(Enum):
    LLM = "Llm"
    FUTUREAGI = "Futureagi"
    FUNCTION = "Function"

    @classmethod
    def get_choices(cls):
        return [(tag.value, tag.name.replace("_", " ").title()) for tag in cls]
