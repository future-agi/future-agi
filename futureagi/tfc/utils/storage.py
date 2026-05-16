import base64
import binascii
import io
import json
import os
import subprocess

# from pydub import AudioSegment
import tempfile
import time
import traceback
import uuid
from io import BytesIO
from urllib.parse import urlparse

import av
import numpy as np
import requests
import soundfile as sf
import structlog
from PIL import Image

# from pydub import AudioSegment
from requests.exceptions import ChunkedEncodingError, ConnectionError, RequestException

logger = structlog.get_logger(__name__)
from tfc.settings.settings import MINIO_URL, UPLOAD_BUCKET_NAME
from tfc.utils.error_codes import get_error_message
from tfc.utils.storage_client import ensure_bucket, get_object_url, get_storage_client

# Map raw format names from detect_audio_format() (ffmpeg) to proper MIME types.
# Shared by upload_audio_to_s3() and upload_audio_to_s3_duration().
_FORMAT_TO_MIME = {
    "mp3": "audio/mpeg",
    "mpeg": "audio/mpeg",
    "wav": "audio/wav",
    "ogg": "audio/ogg",
    "flac": "audio/flac",
    "aac": "audio/aac",
    "m4a": "audio/mp4",
    "webm": "audio/webm",
    "wma": "audio/x-ms-wma",
    "aiff": "audio/aiff",
    "aif": "audio/aiff",
    "au": "audio/basic",
}

# Reverse map: prefer the canonical extension for each MIME type. Used
# to append a .ext to MinIO object keys so the frontend (which detects
# media by extension regex) can render the URL as an image/audio/video
# instead of a plain link.
_MIME_TO_EXT = {
    # audio
    "audio/mpeg": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/ogg": "ogg",
    "audio/flac": "flac",
    "audio/aac": "aac",
    "audio/mp4": "m4a",
    "audio/webm": "webm",
    "audio/x-ms-wma": "wma",
    "audio/aiff": "aiff",
    "audio/basic": "au",
    # image
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/svg+xml": "svg",
    # video
    "video/mp4": "mp4",
    "video/webm": "webm",
    "video/quicktime": "mov",
    "video/x-msvideo": "avi",
    # docs
    "application/pdf": "pdf",
    "text/plain": "txt",
    "text/csv": "csv",
    "text/html": "html",
    "application/json": "json",
}

_FFMPEG_DETECT_AUDIO_TIMEOUT_SECONDS = 10
_FFMPEG_CONVERT_AUDIO_TIMEOUT_SECONDS = 60


def _ext_from_mime(content_type: str | None) -> str:
    """Map a MIME type to a canonical file extension (no dot). Returns
    an empty string for unknown types."""
    if not content_type:
        return ""
    return _MIME_TO_EXT.get(content_type.split(";")[0].strip().lower(), "")


# Maximum audio file size (50MB) - prevents memory exhaustion attacks
MAX_AUDIO_FILE_SIZE = 50 * 1024 * 1024


def get_audio_duration(audio_bytes: bytes) -> float:
    """
    Get audio duration in seconds using the av library, with fallbacks.
    """
    if not audio_bytes:
        return 0.0

    try:
        with io.BytesIO(audio_bytes) as buf:
            container = av.open(buf)
            try:
                # 1. Try container duration (most reliable)
                if container.duration and container.duration > 0:
                    return container.duration / 1_000_000.0

                # 2. Fallback to stream duration if container duration is not available
                if container.streams.audio:
                    audio_stream = container.streams.audio[0]
                    if audio_stream.duration and audio_stream.time_base:
                        return float(audio_stream.duration * audio_stream.time_base)

                    # 3. Last resort: decode frames and calculate from sample rate
                    if audio_stream.rate:
                        total_frames = 0
                        try:
                            for packet in container.demux(audio_stream):
                                for frame in packet.decode():
                                    total_frames += frame.samples
                            if total_frames > 0:
                                return total_frames / float(audio_stream.rate)
                        except Exception as frame_err:
                            logger.warning(
                                f"[Audio] Error during frame decoding for duration: {frame_err}"
                            )

                logger.warning(
                    "[Audio] Could not determine duration from container, stream, or frames."
                )
                return 0.0

            finally:
                container.close()
    except Exception as e:
        logger.warning(f"[Audio] Failed to determine duration with PyAV: {e}")
        return 0.0


# def is_valid_url(url: str) -> bool:
#     """
#     Validates if the given string is a valid URL.
#     Supports both HTTP(S) URLs and data URLs.
#     """
#     try:
#         # Check if it's a data URL (base64 encoded)
#         if url.startswith('data:'):
#             return True

#         # Check if it's a valid HTTP(S) URL
#         parsed = urlparse(url)
#         if parsed.scheme not in ('http', 'https'):
#             return False

#         # Basic URL validation
#         if not all([parsed.scheme, parsed.netloc]):
#             return False

#         # Add more comprehensive headers to mimic a browser request
#         headers = {
#             'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
#             'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
#             'Accept-Language': 'en-US,en;q=0.5',
#             'Connection': 'keep-alive',
#         }

#         # Try GET request directly instead of HEAD
#         response = requests.get(url, stream=True, timeout=5, allow_redirects=True, headers=headers)
#         print(f"Status code: {response.status_code}")  # Debug print

#         # Accept any status code that isn't an error (4xx or 5xx)
#         return response.status_code < 400

#     except Exception as e:
#         print(f"URL validation error: {str(e)}")  # Logging for debugging
#         return False


def get_storage_error_message(error_code):
    """Returns a human-readable error message for a given error code."""
    error_messages = {
        "INVALID_URL": "The provided URL is not valid.",
        "CORRUPTED_AUDIO": "Could not process the file. Check if the file is valid/not corrupted",
        "UNSUPPORTED_IMAGE_FORMAT": "The provided image format is not supported.",
        "INVALID_BASE64_AUDIO": "Invalid Base64 audio format.",
        "ERROR_AUDIO_UPLOAD": "Audio upload failed. Try again.",
        "INACCESSIBLE_LINK": "The link you provided is not accessible. Please check the link and try again.",
        "INVALID_AUDIO_FORMAT": "The provided audio format is not supported.",
        "UNABLE_TO_PROCESS_AUDIO": "Unable to Process Audio at this time, Please try again later.",
        "UNSUPPORTED_AUDIO_FORMAT": "The provided audio format is not supported.",
        "EMPTY_AUDIO_DATA": "The provided audio data is empty.",
        "INVALID_FILE_TYPE": "The provided file type is not supported.",
    }
    return error_messages.get(error_code, "An unknown error occurred.")


def is_valid_url(url: str) -> bool:
    """
    Validates if the given string is a valid URL (syntax + scheme).
    """
    try:
        # Check if it's a data URL
        if url.startswith("data:"):
            return True

        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False

        if not all([parsed.scheme, parsed.netloc]):
            return False

        # Based on syntax check, return True
        return True

    except Exception:
        return False


def download_document_from_url(doc_url, max_retries=5, timeout=20):
    """
    Downloads PDF from the provided URL with retries and error handling.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/91.0.4472.124 Safari/537.36"
        )
    }

    valid_doc_types = {
        "application/pdf",
        "application/msword",  # .doc
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
        "text/plain",  # .txt
        "application/rtf",  # .rtf
        "text/rtf",  # Alternative RTF MIME type
    }

    file_signatures = {
        b"%PDF": "pdf",
        b"\xd0\xcf\x11\xe0": "doc",  # DOC signature
        b"PK\x03\x04": "docx",  # DOCX signature (ZIP-based)
        # RTF and TXT don't have specific binary signatures, rely on MIME type
    }

    for attempt in range(max_retries):
        try:
            response = requests.get(doc_url, headers=headers, timeout=timeout)

            if response.status_code == 200:
                # Get the content
                doc_bytes = response.content

                # Basic validation - check if we got actual content
                if len(doc_bytes) == 0:
                    raise ValueError("Downloaded file is empty")

                # Validate content type from headers
                content_type = response.headers.get("Content-Type", "").lower()
                content_type_main = content_type.split(";")[0].strip()

                # Check file signature (magic bytes) for additional validation
                is_valid_doc = False
                detected_format = None

                # Check magic bytes
                for signature, format_type in file_signatures.items():
                    if doc_bytes.startswith(signature):
                        is_valid_doc = True
                        detected_format = format_type
                        break

                # If no magic bytes match, check content type
                if not is_valid_doc and content_type_main in valid_doc_types:
                    is_valid_doc = True
                    detected_format = content_type_main

                # Additional validation for pdf formats
                if detected_format == "pdf":
                    # Basic PDF validation - check for PDF trailer
                    if b"%%EOF" not in doc_bytes[-1024:]:
                        logger.warning("PDF file may be incomplete or corrupted")

                if is_valid_doc:
                    logger.info(
                        f"Successfully downloaded document (format: {detected_format}, size: {len(doc_bytes)} bytes)"
                    )
                    return doc_bytes, content_type_main

                else:
                    raise ValueError(
                        f"Invalid document data (Content-Type: {content_type})"
                    )

            elif 500 <= response.status_code < 600:
                logger.warning(
                    f"Server error {response.status_code}, retrying in {2**attempt} seconds..."
                )
                time.sleep(2**attempt)
            else:
                raise ValueError(
                    f"Unable to process link. Status Code: {response.status_code}"
                )

        except RequestException as e:
            logger.error(f"Attempt {attempt + 1} failed with error: {e}")
            if attempt < max_retries - 1:
                time.sleep(2**attempt)

    raise ValueError("ERROR_DOWNLOADING_DOCUMENT: Max retries exceeded")


def upload_document_to_s3(
    file_url, bucket_name=os.getenv("MINIO_BUCKET_NAME"), object_key=None, org_id=None
):
    """
    Uploads a document to S3 bucket.
    Supports multiple document formats: PDF, DOCX, DOC, TXT, RTF, etc.
    """
    try:
        if not file_url:
            raise ValueError(get_error_message("EMPTY_DATA"))

        _generated_object_key = object_key is None

        bucket_name = UPLOAD_BUCKET_NAME

        if bucket_name in file_url:
            return file_url

        # Supported document formats
        supported_document_types = {
            "application/pdf",  # PDF
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # DOCX
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # XLSX
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",  # PPTX
            "application/msword",  # DOC
            "application/vnd.ms-excel",  # XLS
            "application/vnd.ms-powerpoint",  # PPT
            "text/plain",  # TXT
            "text/rtf",  # RTF
            "application/rtf",  # RTF alternative
            "text/html",  # HTML
            "application/xml",  # XML
            "text/csv",  # CSV
        }

        parsed_url = urlparse(file_url)
        if parsed_url.scheme in ("http", "https"):
            # Check if the provided URL is valid
            if is_valid_url(file_url):
                doc_bytes, content_type = download_document_from_url(file_url)
                if content_type not in supported_document_types:
                    raise ValueError(get_storage_error_message("INVALID_FILE_TYPE"))
            else:
                raise ValueError(get_storage_error_message("INVALID_URL"))
        else:
            # Handle base64 input
            try:
                if file_url.startswith("data:"):
                    # Split the data URI to get the metadata and base64 data
                    header, base64_data = file_url.split(",", 1)

                    # Extract content type from the header (format: "data:content/type;base64")
                    content_type = header.replace("data:", "").split(";")[0]
                    if content_type not in supported_document_types:
                        raise ValueError(get_storage_error_message("INVALID_FILE_TYPE"))

                    # Use the base64 data for decoding
                    file_url = base64_data
                    doc_bytes = base64.b64decode(file_url)
                else:
                    # Plain base64 without data URI prefix - decode first to detect content type
                    doc_bytes = base64.b64decode(file_url)

                    # Try to detect content type from magic bytes
                    file_signatures = {
                        b"%PDF": "application/pdf",
                        b"\xd0\xcf\x11\xe0": "application/msword",  # DOC signature
                        b"PK\x03\x04": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # DOCX signature (ZIP-based)
                    }

                    # Check magic bytes to detect content type
                    content_type = None
                    for signature, detected_type in file_signatures.items():
                        if doc_bytes.startswith(signature):
                            content_type = detected_type
                            break

                    # Default to text/plain if no magic bytes match (for plain text input)
                    if content_type is None:
                        content_type = "text/plain"

                    # Validate the detected/default content type
                    if content_type not in supported_document_types:
                        raise ValueError(get_storage_error_message("INVALID_FILE_TYPE"))

            except binascii.Error as e:
                raise ValueError(get_error_message("INVALID_BASE64_STRING")) from e
            except Exception as e:
                traceback.print_exc()
                raise ValueError(get_error_message("INVALID_BASE64_STRING")) from e

        if _generated_object_key:
            ext = _ext_from_mime(content_type) or "bin"
            object_key = f"tempcust/{uuid.uuid4()}.{ext}"

        minio_client = get_storage_client()
        ensure_bucket(minio_client, bucket_name)
        # Upload the document bytes to S3
        minio_client.put_object(
            bucket_name=bucket_name,
            object_name=object_key,
            data=BytesIO(doc_bytes),
            length=len(doc_bytes),
            content_type=content_type,
        )

        # Generate and return the public URL of the uploaded document
        url = get_object_url(bucket_name, object_key)

        if org_id:
            try:
                try:
                    from ee.usage.schemas.event_types import BillingEventType
                except ImportError:
                    BillingEventType = None
                try:
                    from ee.usage.schemas.events import UsageEvent
                except ImportError:
                    UsageEvent = None
                try:
                    from ee.usage.services.emitter import emit
                except ImportError:
                    emit = None

                emit(
                    UsageEvent(
                        org_id=str(org_id),
                        event_type=BillingEventType.OBSERVE_ADD,
                        amount=len(doc_bytes),
                        properties={"source": "dataset_document"},
                    )
                )
            except ImportError:
                pass

        return url
    except ValueError as e:
        traceback.print_exc()
        raise e
    except Exception as e:
        logger.error(f"Error uploading document to S3: {str(e)}")
        traceback.print_exc()
        raise ValueError(
            get_error_message("ERROR_DOCUMENT_UPLOAD").format(str(e))
        ) from e


def upload_image_to_s3(
    img_base64_str,
    bucket_name=os.getenv("MINIO_BUCKET_NAME"),
    object_key=None,
    org_id=None,
):
    try:
        # Supported image formats
        supported_formats = {
            "jpeg": "image/jpeg",
            "jpg": "image/jpg",
            "png": "image/png",
            "gif": "image/gif",
            "webp": "image/webp",
        }
        if not img_base64_str:
            raise ValueError(get_error_message("EMPTY_DATA"))

        # Defer object_key generation until we know the format so the
        # URL carries an extension (.png/.jpg/...). Frontend renderers
        # detect media by extension, so a key like "tempcust/{uuid}"
        # without one would render as a plain link.
        _generated_object_key = object_key is None

        bucket_name = UPLOAD_BUCKET_NAME

        if isinstance(img_base64_str, Image.Image):
            in_mem_file = io.BytesIO()
            img_base64_str.save(in_mem_file, format=img_base64_str.format)
            img_bytes = in_mem_file.getvalue()

        else:
            if bucket_name in img_base64_str:
                return img_base64_str

            # Check if the input is already a URL
            parsed_url = urlparse(img_base64_str)
            if parsed_url.scheme in ("http", "https"):
                # Check if the provided URL is valid
                if is_valid_url(img_base64_str):
                    img_bytes = download_image_from_url(img_base64_str)
                else:
                    raise ValueError(get_storage_error_message("INVALID_URL"))
            else:
                # Handle base64 input
                try:
                    # If it's a data URI, extract the base64 part
                    if img_base64_str.startswith("data:"):
                        img_base64_str = img_base64_str.split(",")[1]

                    # Decode the base64 string back to bytes
                    img_bytes = base64.b64decode(img_base64_str)

                except binascii.Error as e:
                    raise ValueError(get_error_message("INVALID_BASE64_STRING")) from e
                except Exception as e:
                    traceback.print_exc()
                    raise ValueError(get_error_message("INVALID_BASE64_STRING")) from e

        img = Image.open(BytesIO(img_bytes))
        format_detected = img.format
        if format_detected:
            format_detected = format_detected.lower()

        if not format_detected or format_detected not in supported_formats:
            in_mem_file = io.BytesIO()
            img.convert("RGB").save(in_mem_file, format="JPEG")
            in_mem_file.seek(0)
            img_bytes = in_mem_file.getvalue()
            format_detected = "jpeg"

        if _generated_object_key:
            object_key = f"tempcust/{uuid.uuid4()}.{format_detected}"

        minio_client = get_storage_client()
        ensure_bucket(minio_client, bucket_name)
        # Upload the image bytes to S3
        minio_client.put_object(
            bucket_name=bucket_name,
            object_name=object_key,
            data=BytesIO(img_bytes),
            length=len(img_bytes),
            content_type=supported_formats[format_detected],
        )

        if org_id:
            try:
                from ee.usage.schemas.event_types import BillingEventType
            except ImportError:
                BillingEventType = None
            try:
                from ee.usage.schemas.events import UsageEvent
            except ImportError:
                UsageEvent = None
            try:
                from ee.usage.services.emitter import emit
            except ImportError:
                emit = None

            emit(
                UsageEvent(
                    org_id=str(org_id),
                    event_type=BillingEventType.OBSERVE_ADD,
                    amount=len(img_bytes),
                    properties={"source": "trace_image"},
                )
            )

        # Generate and return the public URL of the uploaded image
        url = get_object_url(bucket_name, object_key)

        return url
    except ValueError as e:
        raise e
    except Exception as e:
        logger.error(f"Error uploading image to S3: {str(e)}")
        traceback.print_exc()
        raise ValueError(get_error_message("ERROR_IMAGE_UPLOAD").format(str(e))) from e


def _communicate_ffmpeg(process, audio_bytes, timeout_seconds, operation):
    try:
        return process.communicate(input=audio_bytes, timeout=timeout_seconds)
    except subprocess.TimeoutExpired as e:
        process.kill()
        process.communicate()
        logger.warning(
            "ffmpeg process timed out",
            operation=operation,
            timeout_seconds=timeout_seconds,
        )
        raise TimeoutError(
            f"FFmpeg {operation} timed out after {timeout_seconds} seconds"
        ) from e


def detect_audio_format(audio_bytes):
    process = subprocess.Popen(
        ["ffmpeg", "-i", "-", "-f", "ffmetadata", "-"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    stdout, stderr = _communicate_ffmpeg(
        process,
        audio_bytes,
        _FFMPEG_DETECT_AUDIO_TIMEOUT_SECONDS,
        "audio format detection",
    )

    if process.returncode != 0:
        raise Exception(f"FFmpeg Error: {stderr.decode('utf-8')}")

    output = stderr.decode("utf-8")
    format_name = None
    for line in output.split("\n"):
        if "Input #0" in line:
            format_name = line.split(",")[1].strip().split(" ")[-1]
            break

    return format_name


def convert_to_mp3(audio_bytes):
    try:
        process = subprocess.Popen(
            [
                "ffmpeg",
                "-i",
                "-",
                "-f",
                "mp3",
                "-acodec",
                "libmp3lame",
                "-ab",
                "192k",
                "-",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        stdout, stderr = _communicate_ffmpeg(
            process,
            audio_bytes,
            _FFMPEG_CONVERT_AUDIO_TIMEOUT_SECONDS,
            "audio conversion",
        )

        if process.returncode != 0:
            raise Exception(f"FFmpeg Conversion Error: {stderr.decode('utf-8')}")

        return stdout, "mp3"
    except Exception as e:
        traceback.print_exc()
        raise ValueError(get_storage_error_message("UNABLE_TO_PROCESS_AUDIO")) from e


def upload_audio_to_s3_duration(
    audio_base64_str,
    bucket_name=os.getenv("MINIO_BUCKET_NAME"),
    object_key=None,
    duration_seconds=None,
    org_id=None,
):
    try:
        bucket_name = UPLOAD_BUCKET_NAME
        _generated_object_key = object_key is None
        audio_format = None
        supported_formats = {"mp3", "wav", "mpeg"}
        if audio_base64_str in (None, "", "None"):
            raise ValueError(get_storage_error_message("EMPTY_AUDIO_DATA"))
        if isinstance(audio_base64_str, dict):
            audio_array = audio_base64_str["array"]
            sampling_rate = audio_base64_str["sampling_rate"]
            with io.BytesIO() as buffer:
                sf.write(
                    buffer,
                    audio_array,
                    samplerate=sampling_rate,
                    format="WAV",
                    subtype="PCM_16",
                )
                audio_bytes = buffer.getvalue()

            if audio_base64_str["path"]:
                audio_format = audio_base64_str["path"].split(".")[-1]
            if not audio_format or audio_format not in supported_formats:
                audio_bytes, audio_format = convert_to_mp3(audio_bytes)

        else:
            if bucket_name in audio_base64_str:
                if not duration_seconds:
                    if is_valid_url(audio_base64_str):
                        audio_bytes = download_audio_from_url(audio_base64_str)

                        # Get duration using the new utility function
                        duration_seconds = get_audio_duration(audio_bytes)

                    else:
                        raise ValueError(get_storage_error_message("INVALID_URL"))
                return audio_base64_str, float(duration_seconds)

            # Check if the input is already a URL
            parsed_url = urlparse(audio_base64_str)
            if parsed_url.scheme in ("http", "https"):
                # Check if the provided URL is valid
                if is_valid_url(audio_base64_str):
                    audio_bytes = download_audio_from_url(audio_base64_str)
                else:
                    raise ValueError(get_storage_error_message("INVALID_URL"))
            else:
                try:
                    # If it's a data URI, extract the base64 part
                    if audio_base64_str.startswith("data:"):
                        audio_base64_str = audio_base64_str.split(",")[1]

                    # Decode the base64 string back to bytes
                    audio_bytes = base64.b64decode(audio_base64_str)

                except binascii.Error as e:
                    raise ValueError(
                        get_storage_error_message("CORRUPTED_AUDIO")
                    ) from e
                except Exception as e:
                    logger.exception(f"Error decoding audio: {e}")
                    raise ValueError(
                        get_storage_error_message("CORRUPTED_AUDIO")
                    ) from e

        try:
            container = av.open(BytesIO(audio_bytes))

            # Get duration in seconds
            if container.duration:
                # Container duration is in microseconds
                duration_seconds = container.duration / 1000000.0
            else:
                # Fallback to stream duration
                audio_stream = container.streams.audio[0]
                if audio_stream.duration and audio_stream.time_base:
                    duration_seconds = float(
                        audio_stream.duration * audio_stream.time_base
                    )
                else:
                    # Last resort: decode and calculate
                    total_frames = 0
                    for packet in container.demux(audio_stream):
                        for frame in packet.decode():
                            total_frames += frame.samples
                    duration_seconds = total_frames / float(audio_stream.rate)

            container.close()

        except Exception as e:
            logger.exception(f"Error decoding audio: {e}")
            raise ValueError(get_storage_error_message("CORRUPTED_AUDIO")) from e

        if not audio_format:
            audio_format = detect_audio_format(audio_bytes)

            if not audio_format or audio_format not in supported_formats:
                audio_bytes, audio_format = convert_to_mp3(audio_bytes)

        # Set bucket name based on environment

        minio_client = get_storage_client()
        ensure_bucket(minio_client, bucket_name)

        # Upload the audio bytes to S3
        content_type = _FORMAT_TO_MIME.get(audio_format, "audio/mpeg")

        if _generated_object_key:
            ext = _ext_from_mime(content_type) or audio_format or "mp3"
            object_key = f"tempcust/{uuid.uuid4()}.{ext}"

        minio_client.put_object(
            bucket_name=bucket_name,
            object_name=object_key,
            data=BytesIO(audio_bytes),
            length=len(audio_bytes),
            content_type=content_type,
        )

        # Generate and return the public URL of the uploaded audio
        url = get_object_url(bucket_name, object_key)

        if org_id:
            try:
                try:
                    from ee.usage.schemas.event_types import BillingEventType
                except ImportError:
                    BillingEventType = None
                try:
                    from ee.usage.schemas.events import UsageEvent
                except ImportError:
                    UsageEvent = None
                try:
                    from ee.usage.services.emitter import emit
                except ImportError:
                    emit = None

                emit(
                    UsageEvent(
                        org_id=str(org_id),
                        event_type=BillingEventType.OBSERVE_ADD,
                        amount=len(audio_bytes),
                        properties={"source": "dataset_audio"},
                    )
                )
            except ImportError:
                pass

        return url, duration_seconds
    except ValueError as e:
        raise e
    except Exception as e:
        logger.error(f"Error uploading audio to S3: {str(e)}")
        traceback.print_exc()
        raise ValueError(get_storage_error_message("UNABLE_TO_PROCESS_AUDIO")) from e


# # Example usage
# bucket_name = 'your-bucket-name'
# object_key = 'path/to/your/image.jpg'
# img_base64_str = to_byte_image(image)  # Assuming you have this function defined

# public_url = upload_image_to_s3(img_base64_str, bucket_name, object_key)
# if public_url:
#     print(f"Image public URL: {public_url}")

# def download_image_from_url(image_url, max_retries=3, timeout=10):
#     """Downloads an image from the provided URL with retries and error handling."""
#     headers = {
#         "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
#     }

#     for attempt in range(max_retries):
#         try:
#             response = requests.get(image_url, headers=headers, timeout=timeout)
#             print(f' ===== response : {response} ===== ')
#             if response.status_code == 200:
#                 # Check if the content is an image
#                 content_type = response.headers.get("Content-Type", "")
#                 print(f' ===== content_type : {content_type} ===== ')
#                 if "image" in content_type:
#                     print(f' ===== if "image" in content_type: ===== ')
#                     return response.content
#                 else:
#                     raise ValueError(f"Invalid content type: {content_type}")

#             elif 500 <= response.status_code < 600:  # Handle server errors
#                 print(f' ===== 500 <= response.status_code < 600: ===== ')
#                 time.sleep(2 ** attempt)  # Exponential backoff

#             else:
#                 raise ValueError(f"Failed to download image, Status Code: {response.status_code}")

#         except RequestException as e:
#             print(f"Attempt {attempt + 1} failed: {e}")
#             time.sleep(2 ** attempt)  # Exponential backoff

#     raise ValueError("ERROR_DOWNLOADING_IMAGE: Max retries exceeded")


def download_image_from_url(image_url, max_retries=5, timeout=20):
    """
    Downloads an image from the provided URL with retries and error handling.
    Tries to validate whether the downloaded bytes are actually an image using Pillow.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/91.0.4472.124 Safari/537.36"
        )
    }

    logger.info(f"[DEBUG] download_image_from_url called with URL: {image_url}")

    for attempt in range(max_retries):
        try:
            logger.info(
                f"[DEBUG] Attempt {attempt + 1}/{max_retries} for URL: {image_url}"
            )
            with requests.get(image_url, headers=headers, timeout=timeout) as response:
                logger.info(
                    f"[DEBUG] Response status: {response.status_code}, URL: {image_url}"
                )

                if response.status_code == 200:
                    # Attempt to verify if the response is an image by using Pillow
                    # rather than relying solely on the Content-Type header.
                    try:
                        img_bytes = response.content
                        # Try opening the image in-memory
                        with Image.open(io.BytesIO(img_bytes)) as img:
                            img.verify()  # verify the image is not corrupt
                        # If no exception is thrown by Pillow, then it's valid image data
                        return img_bytes
                    except Exception:
                        # If Pillow fails to open/verify the image, it's probably not a valid image
                        content_type = response.headers.get("Content-Type", "")
                        raise ValueError(  # noqa: B904
                            f"Invalid image data (Content-Type: {content_type})"
                        )

                elif 500 <= response.status_code < 600 or response.status_code == 429:
                    # Server error: wait and retry (exponential backoff)
                    time.sleep(2**attempt)
                else:
                    logger.error(
                        f"[DEBUG] Unexpected status code {response.status_code} for URL: {image_url}"
                    )
                    logger.error(f"[DEBUG] Response headers: {dict(response.headers)}")
                    raise ValueError(
                        f"Unable to process link. Status Code: {response.status_code}"
                    )

        except RequestException as e:
            logger.error(
                f"[DEBUG] Attempt {attempt + 1} failed with RequestException: {e}, URL: {image_url}"
            )
            time.sleep(2**attempt)  # Exponential backoff

    logger.error(f"[DEBUG] Max retries exceeded for URL: {image_url}")
    raise ValueError("ERROR_DOWNLOADING_IMAGE: Max retries exceeded")


def convert_image_from_url_to_base64(image_url, max_retries=5, timeout=120):
    for attempt in range(max_retries):
        try:
            response = requests.get(image_url, timeout=timeout)

            if response.status_code == 200:
                # Attempt to verify if the response is an image by using Pillow
                # rather than relying solely on the Content-Type header.
                try:
                    img_bytes = response.content
                    # Try opening the image in-memory
                    with Image.open(io.BytesIO(img_bytes)) as img:
                        img.verify()  # verify the image is not corrupt
                    content_type = response.headers.get("Content-Type", "image/jpeg")
                    encoded_string = base64.b64encode(img_bytes).decode("utf-8")
                    data_url = f"data:{content_type};base64,{encoded_string}"

                    return data_url
                except Exception:
                    # If Pillow fails to open/verify the image, it's probably not a valid image
                    content_type = response.headers.get("Content-Type", "")
                    raise ValueError(  # noqa: B904
                        f"Invalid image data (Content-Type: {content_type})"
                    )

            elif 500 <= response.status_code < 600:
                # Server error: wait and retry (exponential backoff)
                time.sleep(2**attempt)
            else:
                raise ValueError(
                    f"Unable to process link. Status Code: {response.status_code}"
                )

        except RequestException as e:
            logger.error(f"Attempt {attempt + 1} failed with error: {e}")
            time.sleep(2**attempt)  # Exponential backoff

    raise ValueError("ERROR_DOWNLOADING_IMAGE: Max retries exceeded")


def image_bytes_from_url_or_base64(img_str):
    """
    Opens an image from a URL or a base64-encoded string.

    Args:
        img_str (str): The input string, either a URL or a base64-encoded image.

    Returns:
        PIL.Image.Image: An opened PIL Image object.

    Raises:
        ValueError: If the input is an invalid URL or unsupported format.
    """
    parsed_url = urlparse(img_str)
    supported_formats = {"jpeg", "jpg", "png", "gif"}

    if parsed_url.scheme in ("http", "https"):
        # Handle as a URL
        if is_valid_url(img_str):
            img_bytes = download_image_from_url(img_str)
            return img_bytes
        else:
            raise ValueError(get_storage_error_message("INVALID_URL"))
    else:
        # Handle as a base64-encoded string
        if img_str.startswith("data:"):
            # Extract format from data URI
            try:
                format_detected = img_str.split(";")[0].split("/")[1]
                if format_detected not in supported_formats:
                    raise ValueError(
                        get_error_message("UNSUPPORTED_IMAGE_FORMAT")
                        + f" {format_detected}"
                    )
                # Extract base64 data
                img_str = img_str.split(",")[1]
            except IndexError as e:
                raise ValueError(get_error_message("UNSUPPORTED_IMAGE_FORMAT")) from e
        try:
            img_bytes = base64.b64decode(img_str)
            return img_bytes
        except Exception as e:
            raise ValueError(f"Failed to decode base64 string: {e}")  # noqa: B904


def delete_compare_folder(compare_id, bucket_name=os.getenv("MINIO_BUCKET_NAME")):
    # delete the compare folder from s3 bucket
    bucket_name = UPLOAD_BUCKET_NAME
    minio_client = get_storage_client()
    try:
        # if the folder exists in the bucket, delete it
        if minio_client.bucket_exists(bucket_name):
            # List all objects in the bucket with the specified prefix
            objects = minio_client.list_objects(
                bucket_name, prefix=f"compare/{compare_id}/", recursive=True
            )
            for obj in objects:
                minio_client.remove_object(bucket_name, obj.object_name)

        return True

    except Exception as e:
        logger.exception(f"Error deleting compare folder: {str(e)}")
        raise ValueError(get_error_message("ERROR_FOLDER_DELETE").format(str(e))) from e


def upload_compare_json_to_s3(
    compare_json, compare_id, page_name, bucket_name=os.getenv("MINIO_BUCKET_NAME")
):
    # upload the compare files which is json to s3 bucket
    bucket_name = UPLOAD_BUCKET_NAME
    minio_client = get_storage_client()
    ensure_bucket(minio_client, bucket_name)

    try:
        # compare_json is a dictionary, convert it to bytes
        file_bytes = json.dumps(compare_json).encode("utf-8")
        if not page_name:
            return None
        object_key = f"compare/{compare_id}/{page_name}"
        # Upload the file bytes to S3
        minio_client.put_object(
            bucket_name=bucket_name,
            object_name=object_key,
            data=BytesIO(file_bytes),  # Wrap bytes in BytesIO for streaming
            length=len(file_bytes),
            content_type="application/json",
        )

        url = get_object_url(bucket_name, object_key)
        return url

    except Exception as e:
        logger.exception(f"Error uploading file to S3: {str(e)}")
        raise ValueError(get_error_message("ERROR_FILE_UPLOAD").format(str(e))) from e


def download_json_from_s3(object_key, bucket_name=os.getenv("MINIO_BUCKET_NAME")):
    bucket_name = UPLOAD_BUCKET_NAME
    # Download the JSON file from S3 and return its content as a dictionary
    minio_client = get_storage_client()

    try:
        response = minio_client.get_object(bucket_name, object_key)
        json_data = response.read()
        return json.loads(json_data.decode("utf-8"))
    except Exception as e:
        logger.exception(f"Error downloading JSON from S3: {str(e)}")
        raise ValueError(get_error_message("ERROR_FILE_DOWNLOAD").format(str(e))) from e


def upload_file_to_s3(
    filepath=None,
    kb_id=None,
    file_id=None,
    bucket_name=os.getenv("MINIO_BUCKET_NAME"),
    file_bytes=None,
    file_name=None,
    org_id=None,
):
    """
    Upload a file to S3 for Knowledge Base.

    Can be called with either:
    - filepath: Path to local file (reads file from disk)
    - file_bytes + file_name: File content as bytes with filename (uploads directly from memory)

    Args:
        filepath: Path to local file (optional if file_bytes provided)
        kb_id: Knowledge Base ID
        file_id: File record ID
        bucket_name: S3 bucket name (defaults to UPLOAD_BUCKET_NAME)
        file_bytes: File content as bytes (optional if filepath provided)
        file_name: Original filename when using file_bytes

    Returns:
        str: The S3 URL of the uploaded file
    """
    bucket_name = UPLOAD_BUCKET_NAME

    minio_client = get_storage_client()
    ensure_bucket(minio_client, bucket_name)

    try:
        # Get file bytes and filename from either filepath or direct bytes
        if file_bytes is not None and file_name is not None:
            # Direct bytes upload (for K8s environments without shared filesystem)
            original_filename = file_name
        elif filepath is not None:
            # Read from local file
            with open(filepath, "rb") as file_obj:
                file_bytes = file_obj.read()
            original_filename = os.path.basename(filepath)
        else:
            raise ValueError(
                "Either filepath or (file_bytes + file_name) must be provided"
            )

        # Determine content type based on file extension
        extension = original_filename.split(".")[-1].lower()
        content_type_map = {
            "pdf": "application/pdf",
            "txt": "text/plain",
            "doc": "application/msword",
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }
        content_type = content_type_map.get(extension, "application/octet-stream")

        # Build object key
        s3_file_name = f"{file_id}.{extension}"
        object_key = f"knowledge-base/{kb_id}/{s3_file_name}"

        # Upload the file bytes to S3
        minio_client.put_object(
            bucket_name=bucket_name,
            object_name=object_key,
            data=BytesIO(file_bytes),
            length=len(file_bytes),
            content_type=content_type,
            part_size=10 * 1024 * 1024,
        )

        if org_id:
            try:
                from ee.usage.schemas.event_types import BillingEventType
            except ImportError:
                BillingEventType = None
            try:
                from ee.usage.schemas.events import UsageEvent
            except ImportError:
                UsageEvent = None
            try:
                from ee.usage.services.emitter import emit
            except ImportError:
                emit = None

            emit(
                UsageEvent(
                    org_id=str(org_id),
                    event_type=BillingEventType.KB_STORAGE,
                    amount=len(file_bytes),
                    properties={
                        "source": "kb_upload",
                        "kb_id": str(kb_id) if kb_id else None,
                    },
                )
            )

        # Generate and return the public URL of the uploaded file
        url = get_object_url(bucket_name, object_key)
        return url

    except Exception as e:
        logger.exception(f"Error uploading file to S3: {str(e)}")
        raise ValueError(get_error_message("ERROR_FILE_UPLOAD").format(str(e))) from e


def get_file_from_s3(kb_id, bucket_name=os.getenv("MINIO_BUCKET_NAME")):

    bucket_name = UPLOAD_BUCKET_NAME

    minio_client = get_storage_client()
    if not minio_client.bucket_exists(bucket_name):
        raise ValueError("Bucket does not exist")

    try:
        # List all objects in the knowledge-base/kb_id/ prefix
        object_prefix = f"knowledge-base/{kb_id}/"
        objects = minio_client.list_objects(
            bucket_name, prefix=object_prefix, recursive=True
        )

        file_urls = []

        for obj in objects:
            url = get_object_url(bucket_name, obj.object_name)
            file_urls.append(url)

        return file_urls
    except Exception as e:
        logger.exception(f"Error retrieving files from S3: {str(e)}")
        raise ValueError("Error retrieving files from S3")


def audio_bytes_from_url_or_base64(
    audio_input,
    min_duration_seconds: float | None = 1.0,
    pad_silence: bool = True,
    timeout: int = 60,
):
    """
    Opens an audio file from a URL, a base64-encoded string, or a dict payload,
    with validation for format and size.
    """
    audio_bytes = None
    supported_formats = {"mp3", "wav", "ogg", "m4a", "mpeg"}

    try:
        if isinstance(audio_input, dict):
            if audio_input.get("bytes") and isinstance(
                audio_input["bytes"], (bytes, bytearray)
            ):
                audio_bytes = bytes(audio_input["bytes"])
            elif audio_input.get("data"):
                audio_bytes = base64.b64decode(str(audio_input["data"]))
            elif audio_input.get("url"):
                audio_bytes = download_audio_from_url(
                    audio_url=str(audio_input["url"]),
                    min_duration_seconds=min_duration_seconds,
                    pad_silence=pad_silence,
                    timeout=timeout,
                )
        elif isinstance(audio_input, str):
            s = audio_input.strip()
            if s.startswith(("http://", "https://")):
                audio_bytes = download_audio_from_url(
                    audio_url=s,
                    min_duration_seconds=min_duration_seconds,
                    pad_silence=pad_silence,
                    timeout=timeout,
                )
            elif s.startswith("data:"):
                try:
                    header, encoded = s.split(",", 1)
                    format_detected = header.split(";")[0].split("/")[1]
                except (IndexError, ValueError) as e:
                    raise ValueError(
                        get_error_message("UNSUPPORTED_AUDIO_FORMAT")
                    ) from e
                if format_detected not in supported_formats:
                    logger.warning(
                        "unknown_audio_format_in_data_uri",
                        format=format_detected,
                    )
                audio_bytes = base64.b64decode(encoded)
            else:
                audio_bytes = base64.b64decode(s, validate=True)

    except binascii.Error as e:
        raise ValueError(get_error_message("UNSUPPORTED_AUDIO_FORMAT")) from e
    except ValueError:
        raise
    except Exception as e:
        logger.error("failed_to_process_audio_input", error=str(e))
        raise

    if not audio_bytes:
        raise ValueError("Failed to extract audio bytes from input")

    # Size check for non-URL inputs (download_audio_from_url has its own check)
    is_url = (
        isinstance(audio_input, str)
        and audio_input.startswith("http")
        or isinstance(audio_input, dict)
        and audio_input.get("url")
    )

    if not is_url and len(audio_bytes) > MAX_AUDIO_FILE_SIZE:
        raise ValueError(
            f"Audio file exceeds maximum size of {MAX_AUDIO_FILE_SIZE / (1024 * 1024):.1f}MB"
        )

    if min_duration_seconds and pad_silence:
        audio_bytes = _ensure_min_duration(audio_bytes, float(min_duration_seconds))

    return audio_bytes


def download_audio_from_url(
    audio_url,
    max_retries=5,
    timeout=200,
    min_duration_seconds: float | None = 1.0,
    pad_silence: bool = True,
):
    # PYTHONWARNINGS="error::AssertionError"
    """
    Downloads an audio file from the provided URL with retries and error handling.
    Processes audio data in memory and converts to MP3 format if needed.
    """
    # headers = {
    #     "User-Agent": (
    #         "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    #         "AppleWebKit/537.36 (KHTML, like Gecko) "
    #         "Chrome/91.0.4472.124 Safari/537.36"
    #     )
    # }

    for attempt in range(max_retries):
        audio_data = b""  # Reset audio_data for each attempt
        try:
            with requests.get(audio_url, timeout=timeout, stream=True) as response:
                if response.status_code == 200:
                    # Stream the content in chunks to handle large files and avoid IncompleteRead errors
                    try:
                        for chunk in response.iter_content(chunk_size=8192):
                            if chunk:
                                audio_data += chunk
                                # Check if file size exceeds the maximum allowed
                                if len(audio_data) > MAX_AUDIO_FILE_SIZE:
                                    raise ValueError(
                                        f"Audio file exceeds maximum size of {MAX_AUDIO_FILE_SIZE / (1024 * 1024):.1f}MB"
                                    )
                    except (ConnectionError, ChunkedEncodingError) as stream_error:
                        # Connection aborted during streaming - log and re-raise to trigger retry
                        logger.warning(
                            f"connection_error_during_streaming - Attempt {attempt + 1}, audio_url={audio_url}, error={stream_error}"
                        )
                        # Reset audio_data since streaming was incomplete
                        audio_data = b""
                        raise RequestException(
                            f"Connection error during streaming: {stream_error}"
                        ) from stream_error

                    # Check if the file is already in MP3 format
                    is_mp3 = False
                    try:
                        # Create a temporary file-like object in memory
                        audio_input = BytesIO(audio_data)
                        # Check if it's already an MP3 file
                        if (
                            audio_url.lower().endswith(".mp3")
                            or response.headers.get("Content-Type", "").lower()
                            == "audio/mpeg"
                        ):
                            # Verify it's a valid MP3 file
                            try:
                                audio_input.seek(0)  # Reset position

                                # Use PyAV to check format
                                container = av.open(audio_input)

                                # Check if format is MP3
                                is_mp3 = container.format.name == "mp3" or (
                                    container.streams.audio
                                    and container.streams.audio[0].codec_context.name
                                    in ["mp3", "mp3float"]
                                )

                                container.close()
                                audio_input.seek(0)  # Reset for later use

                                if is_mp3:
                                    logger.debug(
                                        "audio_already_mp3",
                                        audio_url=audio_url,
                                        content_type=response.headers.get(
                                            "Content-Type"
                                        ),
                                    )

                            except Exception:
                                is_mp3 = False
                    except Exception as e:
                        logger.error(f"Error checking audio format: {e}")
                        is_mp3 = False

                        if "audio_input" in locals():
                            audio_input.close()

                    # If it's already MP3, potentially validate duration
                    if is_mp3:
                        try:
                            if min_duration_seconds and pad_silence:
                                # ensure minimum duration by padding if needed
                                audio_input.seek(0)
                                audio_data = _ensure_min_duration(
                                    audio_input.read(), float(min_duration_seconds)
                                )
                        finally:
                            audio_input.close()
                        return audio_data

                    # Convert the audio to MP3 format
                    try:
                        # Reset the file pointer
                        # with gsub.patch():
                        # Use PyAV to convert to MP3
                        audio_input.seek(0)
                        audio_output = BytesIO()

                        # PyAV replacement for pydub
                        input_container = av.open(audio_input)
                        output_container = av.open(audio_output, mode="w", format="mp3")

                        # Get the first audio stream
                        input_stream = input_container.streams.audio[0]

                        # Create output stream - let PyAV handle the codec configuration
                        output_stream = output_container.add_stream(
                            "mp3", rate=input_stream.rate
                        )

                        # Decode and re-encode
                        for packet in input_container.demux(input_stream):
                            for frame in packet.decode():
                                for packet in output_stream.encode(frame):
                                    output_container.mux(packet)

                        # Flush encoder
                        for packet in output_stream.encode():
                            output_container.mux(packet)

                        # Close containers
                        output_container.close()
                        input_container.close()

                        # Get the converted data
                        converted_data = audio_output.getvalue()

                        if min_duration_seconds and pad_silence:
                            converted_data = _ensure_min_duration(
                                converted_data, float(min_duration_seconds)
                            )

                        audio_input.close()
                        audio_output.close()
                        return converted_data
                    except Exception as e:
                        logger.error(
                            f"Error converting audio (download_audio_from_url) : {e}"
                        )
                        # If conversion fails, return the original data
                        if audio_input:
                            audio_input.close()
                        if audio_output:
                            audio_output.close()

                        return audio_data

                elif response.status_code == 429:
                    logger.error(
                        f"Server error (status {response.status_code}). Retrying in {2**attempt} seconds..."
                    )
                    time.sleep(2**attempt)
                else:
                    raise ValueError(
                        f"Failed to download audio. Status Code: {response.status_code}"
                    )

        except RequestException as e:
            logger.exception(
                f"download_audio_retry - Attempt {attempt + 1}/{max_retries}, audio_url={audio_url}, error={e}"
            )
            # Don't sleep on the last attempt
            if attempt < max_retries - 1:
                time.sleep(2**attempt)

    raise ValueError("ERROR_DOWNLOADING_AUDIO: Max retries exceeded")


def _ensure_min_duration(audio_bytes: bytes, min_duration_seconds: float) -> bytes:
    """Pads audio with silence to ensure at least min_duration_seconds. Returns MP3 bytes."""
    import librosa  # Lazy load - pulls scipy/sklearn (~300MB)

    buf = None
    wav_buf = None
    y = None
    y_padded = None
    silence = None

    try:
        buf = BytesIO(audio_bytes)
        y, sr = librosa.load(buf, sr=None, mono=True)

        if y is None or sr is None:
            return audio_bytes

        duration = float(len(y)) / float(sr)
        if duration >= float(min_duration_seconds):
            return audio_bytes

        pad_needed = int(max(0.0, float(min_duration_seconds) - duration) * sr)
        if pad_needed <= 0:
            return audio_bytes

        silence = np.zeros(pad_needed, dtype=np.float32)
        y_padded = np.concatenate([y.astype(np.float32), silence])

        wav_buf = BytesIO()
        sf.write(wav_buf, y_padded, sr, format="WAV", subtype="PCM_16")
        wav_bytes = wav_buf.getvalue()

        # Explicitly delete large arrays before conversion to free memory
        del y, y_padded, silence

        mp3_bytes, _ = convert_to_mp3(wav_bytes)
        return mp3_bytes

    except Exception as e:
        logger.warning(f"_ensure_min_duration failed: {e}", exc_info=True)
        return audio_bytes

    finally:
        # Clean up all resources
        if buf:
            try:
                buf.close()
            except Exception:
                pass

        if wav_buf:
            try:
                wav_buf.close()
            except Exception:
                pass

        # Explicitly delete numpy arrays if they still exist
        try:
            if y is not None:
                del y
            if y_padded is not None:
                del y_padded
            if silence is not None:
                del silence
        except Exception:
            pass


def upload_audio_to_s3(
    audio_data, bucket_name=os.getenv("MINIO_BUCKET_NAME"), object_key=None, org_id=None
):
    try:
        # Defer object_key generation until we know the audio format so
        # the URL carries a .mp3/.wav/etc. suffix that frontend renderers
        # can detect.
        _generated_object_key = object_key is None

        bucket_name = UPLOAD_BUCKET_NAME

        if bucket_name in str(audio_data):
            return audio_data

        # Handle string representation of dictionary
        if (
            isinstance(audio_data, str)
            and audio_data.strip().startswith("{")
            and audio_data.strip().endswith("}")
        ):
            try:
                # Try to parse as JSON
                import json

                dict_data = json.loads(audio_data)
                if isinstance(dict_data, dict) and "bytes" in dict_data:
                    return upload_audio_to_s3(
                        dict_data, bucket_name, object_key, org_id
                    )
            except json.JSONDecodeError:
                try:
                    # Try to parse as Python literal
                    import ast

                    dict_data = ast.literal_eval(audio_data)
                    if isinstance(dict_data, dict) and "bytes" in dict_data:
                        return upload_audio_to_s3(
                            dict_data, bucket_name, object_key, org_id
                        )
                except (ValueError, SyntaxError):
                    pass

        # Handle dictionary input with bytes
        content_type = None
        if isinstance(audio_data, dict) and "bytes" in audio_data:
            audio_bytes = audio_data["bytes"]
        elif isinstance(audio_data, bytes):
            audio_bytes = audio_data
        else:
            # Check if the input is already a URL
            parsed_url = urlparse(str(audio_data))
            if parsed_url.scheme in ("http", "https"):
                # Check if the provided URL is valid
                if is_valid_url(str(audio_data)):
                    audio_bytes = download_audio_from_url(str(audio_data))
                    content_type = "audio/mpeg"  # Default content type for URLs
                else:
                    raise ValueError(get_error_message("INVALID_URL"))

            # Handle local file paths - simplified to only check exact path
            elif "." in str(audio_data) and os.path.splitext(str(audio_data))[
                1
            ].lower() in [".mp3", ".wav", ".ogg", ".m4a", ".aac", ".flac", ".wma"]:
                file_path = str(audio_data)

                # Check if file exists
                if not os.path.isfile(file_path):
                    raise ValueError(f"Audio file not found at path: {file_path}")

                try:
                    # Read the file directly
                    with open(file_path, "rb") as f:
                        audio_bytes = f.read()

                    # Determine content type based on extension
                    file_ext = os.path.splitext(file_path)[1].lower().lstrip(".")
                    content_type = _FORMAT_TO_MIME.get(file_ext, "audio/mpeg")

                except Exception as e:
                    raise ValueError(  # noqa: B904
                        f"Error reading audio file at {file_path}: {str(e)}"
                    )

            # Handle data URIs
            elif str(audio_data).startswith("data:"):
                try:
                    # First, detect the format more reliably
                    format_detected = str(audio_data).split(";")[0].split("/")[1]
                    if format_detected not in _FORMAT_TO_MIME:
                        raise ValueError(
                            f"{get_error_message('UNSUPPORTED_AUDIO_FORMAT')}: {format_detected}"
                        )
                    content_type = _FORMAT_TO_MIME[format_detected]

                    # Check specifically for base64 encoding in the URI
                    if ";base64," in str(audio_data):
                        # Extract the base64 part more reliably
                        base64_data = str(audio_data).split(";base64,")[1]
                        try:
                            # Use validate=True for better error detection
                            audio_bytes = base64.b64decode(base64_data, validate=True)
                        except Exception as e:
                            raise ValueError(  # noqa: B904
                                f"Failed to decode base64 data from URI: {str(e)}"
                            )
                    else:
                        # Handle non-base64 encoded data URIs if needed
                        raise ValueError(
                            get_error_message("UNSUPPORTED_AUDIO_FORMAT")
                            + ": Missing base64 encoding"
                        )

                except IndexError as e:
                    raise ValueError(
                        get_error_message("UNSUPPORTED_AUDIO_FORMAT")
                    ) from e
            else:
                try:
                    # As a last resort, try to decode as base64
                    audio_bytes = base64.b64decode(str(audio_data))
                    content_type = "audio/mpeg"  # Default content type
                except Exception as e:
                    raise ValueError(  # noqa: B904
                        f"Could not process audio data. If this is a file path, the file was not found: {str(e)}"
                    )

        # For bytes/dict inputs, detect format and resolve MIME type
        if content_type is None:
            detected_format = detect_audio_format(audio_bytes)
            content_type = (
                _FORMAT_TO_MIME.get(detected_format, "audio/mpeg")
                if detected_format
                else "audio/mpeg"
            )

        if _generated_object_key:
            ext = _ext_from_mime(content_type) or "mp3"
            object_key = f"tempcust/{uuid.uuid4()}.{ext}"

        minio_client = get_storage_client()
        ensure_bucket(minio_client, bucket_name)

        # Upload the audio bytes to S3
        with BytesIO(audio_bytes) as audio_buffer:
            minio_client.put_object(
                bucket_name=bucket_name,
                object_name=object_key,
                data=audio_buffer,
                length=len(audio_bytes),
                content_type=content_type,
            )

        if org_id:
            try:
                from ee.usage.schemas.event_types import BillingEventType
            except ImportError:
                BillingEventType = None
            try:
                from ee.usage.schemas.events import UsageEvent
            except ImportError:
                UsageEvent = None
            try:
                from ee.usage.services.emitter import emit
            except ImportError:
                emit = None

            emit(
                UsageEvent(
                    org_id=str(org_id),
                    event_type=BillingEventType.OBSERVE_ADD,
                    amount=len(audio_bytes),
                    properties={"source": "trace_audio"},
                )
            )

        # Generate and return the public URL of the uploaded audio
        url = get_object_url(bucket_name, object_key)

        return url

    except Exception as e:
        logger.exception(f"Error uploading audio to S3: {str(e)}")
        raise ValueError(get_error_message("ERROR_AUDIO_UPLOAD").format(str(e))) from e


# S3 multipart upload part size (must be >= 5 MB for S3)
_S3_STREAM_PART_SIZE = 10 * 1024 * 1024  # 10 MB


def upload_stream_to_s3(
    readable: io.RawIOBase | io.BufferedIOBase,
    object_key: str,
    content_type: str = "audio/mpeg",
    bucket_name: str | None = None,
) -> str:
    """Upload a streaming readable to S3 without loading the full payload in memory.

    Uses ``put_object`` with ``length=-1`` for streaming multipart upload.
    Only *_S3_STREAM_PART_SIZE* bytes are buffered at a time, making this
    safe for arbitrarily large files on memory-constrained workers.

    Args:
        readable: Any object with a ``read(size)`` method (file, pipe,
            HTTP response, ``io.BytesIO``, etc.).
        object_key: S3 object key (path inside the bucket).
        content_type: MIME type for the uploaded object.
        bucket_name: Target bucket. Defaults to ``UPLOAD_BUCKET_NAME``.

    Returns:
        The public URL of the uploaded object.
    """
    bucket_name = bucket_name or UPLOAD_BUCKET_NAME

    minio_client = get_storage_client()
    ensure_bucket(minio_client, bucket_name)

    minio_client.put_object(
        bucket_name=bucket_name,
        object_name=object_key,
        data=readable,
        length=-1,
        part_size=_S3_STREAM_PART_SIZE,
        content_type=content_type,
    )

    return get_object_url(bucket_name, object_key)


# Function to fetch an image from a URL and open it
def open_image_from_url(image_url, save_as=None):
    try:
        # Process image input
        img_bytes = image_bytes_from_url_or_base64(image_url)
        if img_bytes is None:
            raise ValueError("Image bytes for are None")

        img = BytesIO(img_bytes)
        return Image.open(img)

    except Exception as e:
        logger.error(f"Error opening image from URL: {e}")
        return None


def open_audio_from_url(audio_url):
    """
    Downloads and opens an audio file from a URL, returning the loaded audio data.
    Similar to open_image_from_url but specialized for audio processing using librosa.

    Args:
        audio_url (str): URL of the audio file to download

    Returns:
        tuple: (audio_waveform, sampling_rate) from librosa, or None if there's an error
    """
    import librosa  # Lazy load - pulls scipy/sklearn (~300MB)

    try:
        # Process audio input using the existing download_audio_from_url function
        audio_bytes = download_audio_from_url(audio_url)
        if audio_bytes is None:
            raise ValueError("Audio bytes are None")

        # Create a BytesIO object from the audio bytes
        audio_buffer = BytesIO(audio_bytes)

        # Load audio from the buffer
        audio_waveform, sampling_rate = librosa.load(audio_buffer)

        if audio_waveform is None:
            raise ValueError("Failed to load audio data with librosa")

        return audio_waveform, sampling_rate

    except Exception as e:
        logger.exception(f"Error opening audio from URL: {e}")
        return None


def upload_video_to_s3(
    video_data,
    bucket_name=os.getenv("MINIO_BUCKET_NAME"),
    object_key=None,
    thumbnail=False,
    org_id=None,
):
    try:
        _generated_object_key = object_key is None

        bucket_name = UPLOAD_BUCKET_NAME

        if bucket_name in str(video_data):
            return video_data

        if isinstance(video_data, bytes):
            video_bytes = video_data
            content_type = "video/mp4"
        elif is_valid_url(video_data):
            response = requests.get(video_data, stream=True, timeout=30)
            response.raise_for_status()
            video_bytes = response.content
            content_type = response.headers.get("Content-Type", "video/mp4")
        elif str(video_data).startswith("data:video"):
            header, encoded = str(video_data).split(",", 1)
            content_type = header.split(":")[1].split(";")[0]
            video_bytes = base64.b64decode(encoded)
        else:
            try:
                video_bytes = base64.b64decode(video_data)
                content_type = "video/mp4"
            except (binascii.Error, TypeError):
                raise ValueError("Invalid video data provided.")  # noqa: B904

        if _generated_object_key:
            ext = _ext_from_mime(content_type) or "mp4"
            object_key = f"tempcust/{uuid.uuid4()}.{ext}"

        minio_client = get_storage_client()
        ensure_bucket(minio_client, bucket_name)

        minio_client.put_object(
            bucket_name=bucket_name,
            object_name=object_key,
            data=BytesIO(video_bytes),
            length=len(video_bytes),
            content_type=content_type,
        )

        url = get_object_url(bucket_name, object_key)

        if thumbnail:
            try:
                thumbnail_bytes = extract_video_thumbnail(video_bytes)
                thumbnail_object_key = f"tempcust/{uuid.uuid4()}.jpg"

                minio_client.put_object(
                    bucket_name=bucket_name,
                    object_name=thumbnail_object_key,
                    data=BytesIO(thumbnail_bytes),
                    length=len(thumbnail_bytes),
                    content_type="image/jpeg",
                )

                if org_id:
                    try:
                        from ee.usage.schemas.event_types import BillingEventType
                    except ImportError:
                        BillingEventType = None
                    try:
                        from ee.usage.schemas.events import UsageEvent
                    except ImportError:
                        UsageEvent = None
                    try:
                        from ee.usage.services.emitter import emit
                    except ImportError:
                        emit = None

                    emit(
                        UsageEvent(
                            org_id=str(org_id),
                            event_type=BillingEventType.OBSERVE_ADD,
                            amount=len(video_bytes) + len(thumbnail_bytes),
                            properties={"source": "trace_video"},
                        )
                    )

                thumbnail_url = get_object_url(bucket_name, thumbnail_object_key)
                return url, thumbnail_url
            except Exception as e:
                logger.warning(f"Failed to extract thumbnail: {str(e)}")
                if org_id:
                    try:
                        from ee.usage.schemas.event_types import BillingEventType
                    except ImportError:
                        BillingEventType = None
                    try:
                        from ee.usage.schemas.events import UsageEvent
                    except ImportError:
                        UsageEvent = None
                    try:
                        from ee.usage.services.emitter import emit
                    except ImportError:
                        emit = None

                    emit(
                        UsageEvent(
                            org_id=str(org_id),
                            event_type=BillingEventType.OBSERVE_ADD,
                            amount=len(video_bytes),
                            properties={"source": "trace_video"},
                        )
                    )
                return url, None
        else:
            if org_id:
                try:
                    from ee.usage.schemas.event_types import BillingEventType
                except ImportError:
                    BillingEventType = None
                try:
                    from ee.usage.schemas.events import UsageEvent
                except ImportError:
                    UsageEvent = None
                try:
                    from ee.usage.services.emitter import emit
                except ImportError:
                    emit = None

                emit(
                    UsageEvent(
                        org_id=str(org_id),
                        event_type=BillingEventType.OBSERVE_ADD,
                        amount=len(video_bytes),
                        properties={"source": "trace_video"},
                    )
                )
            return url
    except Exception as e:
        logger.exception(f"Error uploading video to S3: {str(e)}")
        raise ValueError(get_error_message("ERROR_FILE_UPLOAD").format(str(e))) from e


def extract_video_thumbnail(video_bytes):
    """
    Extract a thumbnail from video bytes using ffmpeg.
    Returns the thumbnail as JPEG bytes.
    """
    try:
        # Create a temporary file for the video
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as temp_video:
            temp_video.write(video_bytes)
            temp_video_path = temp_video.name

        # Create a temporary file for the thumbnail with unique name
        thumbnail_path = os.path.join(
            tempfile.gettempdir(), f"thumb_{uuid.uuid4()}.jpg"
        )

        try:
            cmd = [
                "ffmpeg",
                "-i",
                temp_video_path,
                "-ss",
                "00:00:01",
                "-vframes",
                "1",
                "-vf",
                "scale=320:240",
                "-f",
                "image2",
                "-y",
                thumbnail_path,
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

            if result.returncode != 0:
                raise Exception(f"FFmpeg failed: {result.stderr}")

            with open(thumbnail_path, "rb") as f:
                thumbnail_bytes = f.read()

            return thumbnail_bytes

        finally:
            if os.path.exists(temp_video_path):
                os.unlink(temp_video_path)
            if os.path.exists(thumbnail_path):
                os.unlink(thumbnail_path)

    except Exception as e:
        logger.error(f"Error extracting thumbnail: {str(e)}")
        raise e
