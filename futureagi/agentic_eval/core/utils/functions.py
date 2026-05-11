import ast
import base64
import io
import json
import mimetypes
import os
import re
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import urlparse

import filetype
import requests
from PIL import Image
from pydantic import BaseModel
from tenacity import retry, stop_after_attempt, wait_random_exponential

import structlog

logger = structlog.get_logger(__name__)

try:
    from ee.prompts.eval_prompts import (
        apply_fixes_prompt,
        combine_subcriterias_prompt,
        eval_prompt,
        eval_subcriteria_prompt,
        generate_correct_answer_prompt,
        generate_fixes_for_answer_prompt,
        qualitative_eval_parameter_prompt_v2,
        qualitative_eval_parameter_prompt_v3,
        score_prompt,
        summary_judgement_prompt,
        summary_judgement_prompt_ragrank,
        summary_judgement_prompt_ragrank_v2,
    )
except ImportError:
    apply_fixes_prompt = ""
    combine_subcriterias_prompt = ""
    eval_prompt = ""
    eval_subcriteria_prompt = ""
    generate_correct_answer_prompt = ""
    generate_fixes_for_answer_prompt = ""
    qualitative_eval_parameter_prompt_v2 = ""
    qualitative_eval_parameter_prompt_v3 = ""
    score_prompt = ""
    summary_judgement_prompt = ""
    summary_judgement_prompt_ragrank = ""
    summary_judgement_prompt_ragrank_v2 = ""

model_name = "anthropic"


def encode_image(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode("utf-8")


def get_summary_judgement(client, judgements, images=None):
    chat_conversation = ""
    for idx, judgement in enumerate(judgements):
        chat_conversation += f"""
        <criteria_{idx+1}> {judgement['criteria']} </criteria_{idx+1}> \n <judgement_{idx+1}>{judgement['judgment']}</judgement_{idx+1}>\n\n
    """

    prompt = summary_judgement_prompt.format(chat_conversation=chat_conversation)
    messages = [{"role": "user", "content": prompt}]
    return client._get_completion_content(messages)

def get_summary_judgement_ragrank(client, judgements ,images=None):
    query_doc_info = ""
    for idx, judgement in enumerate(judgements):
        query_prefix = f"<subquery_{idx+1}>"
        query_suffix = f"<subquery_{idx+1}>"
        doc_info = ""
        for doc_idx,docs in enumerate(judgement):
            doc_info = doc_info + "\n" + f"<doc_{doc_idx}>"+ docs["relevancy_explanation"] + f"<doc_{doc_idx}>" + "\n"
        query_doc_info = query_doc_info + query_prefix + doc_info + query_suffix + "\n"


    prompt = summary_judgement_prompt_ragrank.format(query_doc_info=query_doc_info)
    messages = [{"role": "user", "content": prompt}]
    return client._get_completion_content(messages)

def format_output(idx,subquery, documents):
    output = ""
    output += f"SUBQUERY_{idx} : {subquery}\n"
    output += f"SUBQUERY_{idx}-DOCUMENT RELEVANCY :\n\n"
    output += "{\n"
    for doc in documents:
        output += f'  "document no": {doc["document_no"]},\n'
        output += f'  "information gained": {doc["information_gained"]},\n'
        output += f'  "relevancy": {doc["relevancy"]},\n'
        output += f'  "relevancy_explanation": "{doc["relevancy_explanation"]}",\n'
        output += '  "relevancy of dependent subqueries": {},\n\n'
    output += "}\n"

    return output

def get_summary_judgement_ragrank_v2(client, judgements, question , context , subqueries, images=None):
    subquery_relevance_info = ""
    for idx, (subquery,judgement) in enumerate(zip(subqueries,judgements, strict=False)):
        try:
            subquery_relevance_info = subquery_relevance_info +format_output(idx,subquery["subquery"],judgement)
        except Exception:
            pass

    ranked_context =[f"Document{idx+1}: {doc} , Rank: {idx+1}" for idx,doc in enumerate(context)]
    prompt = summary_judgement_prompt_ragrank_v2.format(query = question , context = "\n".join(ranked_context) , subquery_relevance_info = subquery_relevance_info )
    messages = [{"role": "user", "content": prompt}]
    return client._get_completion_content(messages)

def expand_eval_instructions(client, eval_instructions, prompt=None):
    messages = [
        {
            "role": "user",
            "content": (
                eval_prompt.format(eval_instructions=eval_instructions)
                if not prompt
                else prompt
            ),
        }
    ]

    return client._get_completion_content(messages)


def calculate_score(client, judgment, criteria=None, prompt=None, only_score=False):
    messages = [
        {
            "role": "user",
            "content": (
                score_prompt.format(judgment=judgment.strip()) if not prompt else prompt
            ),
        }
    ]
    try:
        response = client._get_completion_content(messages)
        grade = json.loads(response)["summary"]
        score = {"good": 1, "very good": 2, "bad": -1, "very bad": -2}.get(
            grade.lower(), 0
        )
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse JSON response: {e}")
        score = 0
    except KeyError as e:
        logger.error(f"Missing 'summary' key in response: {e}")
        score = 0
    except Exception as e:
        logger.exception(f"Unexpected error in calculate_score: {e}")
        score = 0

    if only_score:
        return score
    return {"score": score, "judgment": judgment}


def encoded_images(image_paths):
    images = []
    for image_path in image_paths:
        encoded_image = encode_image(image_path)
        images.append(encoded_image)

    return images


def save_images(image_bytes, file_path):
    # Create an in-memory stream
    image_stream = io.BytesIO(image_bytes)
    # Create Pillow Image object
    img = Image.open(image_stream)
    # Save the image as JPEG
    img.save(file_path, "JPEG")
    return file_path


def normalize_val(old_range, new_range, old_value):
    return (old_value - old_range[0]) * (new_range[1] - new_range[0]) / (
        old_range[1] - old_range[0]
    ) + new_range[0]


def format_conversation(conversation):
    formatted_lines = []
    for message in conversation:
        role = "User" if message["role"] == "user" else "Model"
        content = message["content"]
        formatted_lines.append(f"{role}: {content}")

    return "\n".join(formatted_lines)


def execute_with_concurrent_future_pool(func, args_list, pool_size=50):
    """
    Executes a function with the given arguments using a thread pool.

    Args:
    - func: The function to execute.
    - args_list: A list of argument tuples to pass to the function.
    - pool_size: The number of threads in the thread pool (default: 50).

    Returns:
    - A list of results returned by the function.
    """
    results = []

    # Initialize the thread pool with the specified number of threads
    with ThreadPoolExecutor(max_workers=pool_size) as executor:
        # Submit tasks to the executor
        future_to_args = {executor.submit(func, *args): args for args in args_list}
        # Collect results as they complete
        for future in as_completed(future_to_args):
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                logger.exception(f"Error retrieving result: {e}")

    return results


def camel_or_snake_to_normal(text):
    # Convert camelCase to spaces
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    # Convert snake_case to spaces
    text = text.replace("_", " ")
    # Capitalize the first letter
    return text.lower()


def get_qualitative_eval_parameter_prompt_v2(llm_client, criteria, prompt=None):
    messages = [
        {
            "role": "user",
            "content": (
                qualitative_eval_parameter_prompt_v2.format(criteria=criteria)
                if not prompt
                else prompt
            ),
        },
    ]

    return llm_client._get_completion_content(messages)


def get_score_from_subcriterias(client, subcriteria, chat_history):
    messages = [
        {
            "role": "user",
            "content": eval_subcriteria_prompt.format(
                subcriteria=subcriteria, chat_history=chat_history
            ),
        }
    ]
    return json.loads(client._get_completion_content(messages))


def get_criteria_judegement_score(eval_instructions, results):
    if isinstance(eval_instructions[0], list):
        total_score = sum(
            result["score"] * weight[1]
            for result, weight in zip(results, eval_instructions, strict=False)
        )
        judgments = [
            {"criteria": instruction[0], "judgment": result["judgment"]}
            for instruction, result in zip(eval_instructions, results, strict=False)
        ]
    else:
        total_score = sum(result["score"] for result in results)
        judgments = [
            {"criteria": instruction, "judgment": result["judgment"]}
            for instruction, result in zip(eval_instructions, results, strict=False)
        ]

    return total_score, judgments

def get_criteria_judegement_score_ragrank(results):
    total_score = sum(result["score"] for result in results)
    judgments = [
        { "judgment": result["judgment"]}
        for result in results
    ]

    return total_score, judgments


def eval_instruction_process_data_format(data):
    # Regular expression patterns
    result = []
    for item in data:
        try:
            # Try to convert the string representation of a list to an actual list
            parsed_item = ast.literal_eval(item)

            # Check if the parsed item is a list with two elements
            if isinstance(parsed_item, list) and len(parsed_item) == 2:
                string_part = parsed_item[0]  # The string part
                number_part = float(
                    parsed_item[1]
                )  # Convert the number part to a float
                result.append([string_part, number_part])
            else:
                result.append(
                    item
                )  # If not a list of two elements, append the original item

        except (ValueError, SyntaxError):
            # If it's not a string representation of a list, append it as is
            result.append(item)

    return result


def generate_correct_answer(
    client, chat_history, criteria, evaluation, original_response
):
    messages = [
        {
            "role": "user",
            "content": generate_correct_answer_prompt.format(
                chat_history=chat_history,
                criteria=criteria,
                evaluation=evaluation,
                original_response=original_response,
            ),
        }
    ]

    return client._get_completion_content(messages)


def generate_summary(client, text, quality):
    if quality == "good":
        prompt = (
            f"Provide an accurate and concise summary of the following text:\n\n{text}"
        )
    elif quality == "bad":
        prompt = f"Provide a summary of the following text, but intentionally make it inaccurate or incomplete:\n\n{text}"
    else:
        prompt = f"Provide a summary of the following text with mixed quality - some parts accurate, some inaccurate:\n\n{text}"

    messages = [
        {
            "role": "user",
            "content": prompt,
        },
    ]

    return client._get_completion_content(messages)


def generate_fixes_for_answer(client, answer, evaluation, chat_history):
    messages = [
        {
            "role": "user",
            "content": generate_fixes_for_answer_prompt.format(
                answer=answer, evaluation=evaluation, chat_history=chat_history
            ),
        }
    ]

    return client._get_completion_content(messages)


def apply_fixes_to_answer(client, answer, fixes, chat_history, evaluation):
    messages = [
        {
            "role": "user",
            "content": apply_fixes_prompt.format(
                answer=answer,
                fixes=fixes,
                chat_history=chat_history,
                evaluation=evaluation,
            ),
        }
    ]
    return client._get_completion_content(messages)


def generate_combined_criteria(client, user_defined_criterias):
    # convert the criterias into a bulleted list string using numbers
    user_defined_criterias = "\n".join(
        [f"{i+1}. {criteria}" for i, criteria in enumerate(user_defined_criterias)]
    )
    messages = [
        {
            "role": "user",
            "content": qualitative_eval_parameter_prompt_v3.format(
                criterias=user_defined_criterias
            ),
        }
    ]
    response = client._get_completion_content(messages)
    return response


@retry(stop=stop_after_attempt(10), wait=wait_random_exponential(min=1, max=60))
def generate_combined_subcriterias(client, criterias, user_defined_metrics):
    # convert the criterias into a bulleted list string using numbers
    user_defined_metrics = "\n".join(
        [f"{i}. {criteria}" for i, criteria in enumerate(user_defined_metrics)]
    )
    all_criterias = []
    for criteria in criterias:
        for subcriteria, _ in criteria.values():
            all_criterias.append(subcriteria)
    all_criterias = "\n".join(
        [f"{i}. {criteria}" for i, criteria in enumerate(all_criterias)]
    )
    messages = [
        {
            "role": "user",
            "content": combine_subcriterias_prompt.format(
                criterias=all_criterias,
                user_defined_metrics=user_defined_metrics,
            ),
        }
    ]
    return json.loads(client._get_completion_content(messages))


def download_image_to_base64(url):
    # Download the image
    response = requests.get(url)

    # Check if the request was successful
    if response.status_code == 200:
        # Get the content type from the response header
        content_type = response.headers.get("Content-Type", "").lower()

        # Ensure the media type is 'image/jpeg'
        if content_type == "image/jpeg":
            # Encode the image content to base64
            image_base64 = base64.b64encode(response.content).decode("utf-8")

            return image_base64
        else:
            raise ValueError(
                f"Unexpected media type: {content_type}. Expected 'image/jpeg'."
            )
    else:
        raise Exception(
            f"Failed to download image. Status code: {response.status_code}"
        )

def download_audio_to_base64(url):
    response = requests.get(url)
    if response.status_code == 200:
        audio_base64 = base64.b64encode(response.content).decode("utf-8")
        return audio_base64
    else:
        raise Exception(f"Failed to download audio. Status code: {response.status_code}")


def parse_llm_str_response(
    json_str: str, output_schema: BaseModel, as_dict=True
) -> dict | BaseModel:
    json_candidates = re.findall(r"\{.*?\}", json_str, re.DOTALL)
    for candidate in json_candidates:
        try:
            parsed_json = json.loads(candidate)
            # Check if the parsed JSON matches the expected schema
            validated_data = output_schema(**parsed_json)
            if as_dict:
                return validated_data.dict()
            else:
                return validated_data
        except (json.JSONDecodeError, ValueError):
            continue
    # return empty if no json found
    return {} if as_dict else output_schema()

def is_uuid(v) -> bool:
    if not isinstance(v, str):
        return False
    try:
        uuid.UUID(v)
        return True
    except (ValueError, AttributeError, TypeError):
        return False


# Hosts that user-supplied URLs must never reach from the API server.
# Pre-checked before any HTTP request to bound the SSRF surface that
# `_classify_url_type` opens up for billing-classification accuracy.
_BLOCKED_HOST_PREFIXES = (
    "localhost",
    "127.",
    "10.",
    "169.254.",
    "192.168.",
    "0.0.0.0",
    "::1",
    "metadata.google.internal",
)
_BLOCKED_HOST_172 = tuple(f"172.{i}." for i in range(16, 32))  # 172.16-31.x.x (RFC1918)


def _host_is_blocked(host: str) -> bool:
    host = (host or "").lower().strip()
    if not host:
        return True
    return host.startswith(_BLOCKED_HOST_PREFIXES) or host.startswith(_BLOCKED_HOST_172)


def _classify_url_mime(mime: str) -> str | None:
    if not mime:
        return None
    mime = mime.lower()
    if mime.startswith("audio/"):
        return "audio"
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("application/pdf"):
        return "pdf"
    if mime.startswith("video/") or mime.startswith("application/"):
        return "file"
    return None


def _classify_url_type(item: str) -> str:
    """Classify a URL as audio / image / pdf / file / text for billing.

    Tries extension first (instant, no I/O). Then a bounded HTTP probe with
    a short timeout to cover paths like FAI's S3 (UUID names, no extension).
    Private / loopback / metadata hosts are blocked before the probe to
    contain the SSRF surface this introduces. Any failure falls back to
    'text' so billing accuracy never blocks the eval from running.
    """
    # Cheap extension check first — covers ~95% with zero network.
    path = urlparse(item).path
    guessed, _ = mimetypes.guess_type(path)
    cls = _classify_url_mime(guessed) if guessed else None
    if cls:
        return cls

    # Fall back to a bounded probe for extensionless URLs.
    host = urlparse(item).hostname or ""
    if _host_is_blocked(host):
        return "text"
    try:
        with requests.get(
            item,
            # (connect_timeout, read_timeout) — total wall-clock stays under
            # ~4s on unreachable hosts so PG never feels frozen.
            timeout=(2, 2),
            stream=True,
            allow_redirects=False,
            headers={"Range": "bytes=0-511"},
        ) as response:
            if response.status_code >= 400:
                return "file"
            header_mime = (response.headers.get("Content-Type", "") or "").split(";")[0].strip()
            cls = _classify_url_mime(header_mime)
            if cls:
                return cls
            # Magic-byte sniff on a small slice — filetype.guess needs ~262 bytes.
            head_bytes = response.raw.read(512, decode_content=True)
            kind = filetype.guess(head_bytes) if head_bytes else None
            if kind:
                cls = _classify_url_mime(kind.mime)
                if cls:
                    return cls
            return "file"
    except Exception:
        # Fetch timed out / connection refused / DNS failed / etc.
        # Billing defaults to text rather than block the eval.
        return "text"


def detect_input_type(input_item: Any) -> dict:
    """
    Detect the type of input (text, image, audio, file) based on content.

    Args:
        input_item: The input to analyze. Can be a single value, dict, or list.
                   For lists, detects if all items are of the same media type (e.g., all images).

    Returns:
        dict: A dictionary with the same structure as input (if input is dict)
              or a simple dict with type information (if input is not dict)
              Values will be replaced with detected types: 'text', 'image', 'images', 'audio', or 'file'
              Returns 'images' for lists of image URLs.
    """
    # Helper function to detect a single item's type
    def detect_single_item(item):
        if isinstance(item, str) and item.startswith('"') and item.endswith('"'):
            item = item[1:-1]  # Strip surrounding double quotes
        # logger.info(f' ----- DETECTING TYPE OF: {item} type: {type(item)} ----- ')
        try:
            # check for URLs first
            if isinstance(item, str) and (item.startswith(('http://', 'https://')) or (urlparse(item).scheme and urlparse(item).netloc)):
                logger.info(' ----- HANDLING URL First Condition----- ')
                return _classify_url_type(item)

            if isinstance(item, str) and item.strip().startswith('{') and item.strip().endswith('}'):
                logger.info(' ----- HANDLING STRING REPRESENTATION OF DICTIONARY Second Condition ----- ')
                try:
                    # Try to parse as JSON
                    dict_data = json.loads(item)
                    if isinstance(dict_data, dict) and 'bytes' in dict_data:
                        # If bytes is stored as a string representation of bytes
                        bytes_str = dict_data['bytes']
                        if isinstance(bytes_str, str) and bytes_str.startswith("b'") and "RIFF" in bytes_str and "WAVE" in bytes_str:
                            return 'audio'
                        # Return result of recursive call with the extracted dictionary
                        return detect_single_item(dict_data)
                except json.JSONDecodeError:
                    try:
                        # Try to parse as Python literal
                        dict_data = ast.literal_eval(item)
                        if isinstance(dict_data, dict) and 'bytes' in dict_data:
                            return detect_single_item(dict_data)
                    except (ValueError, SyntaxError):
                        pass

            # Direct bytes handling
            if isinstance(item, bytes):
                logger.info(' ----- HANDLING BYTES 3rd Condition ----- ')
                kind = filetype.guess(item)
                if kind is not None:
                    if kind.mime.startswith('audio/'):
                        return 'audio'
                    elif kind.mime.startswith('image/'):
                        return 'image'
                    elif kind.mime.startswith('application/pdf'):
                        return 'pdf'
                    else:
                        return 'file'

                # If no specific format detected, default to file
                return 'file'

            # Special case for dictionary with bytes and path
            elif isinstance(item, dict) and 'bytes' in item:
                logger.info(' ----- HANDLING DICTIONARY WITH BYTES AND PATH 4th Condition ----- ')
                bytes_data = item['bytes']
                if isinstance(bytes_data, bytes):
                    kind = filetype.guess(bytes_data)
                    if kind is not None:
                        if kind.mime.startswith('audio/'):
                            return 'audio'
                        elif kind.mime.startswith('image/'):
                            return 'image'
                        elif kind.mime.startswith('application/pdf'):
                            return 'pdf'
                        else:
                            return 'file'
                    return 'file'

            # Handle data URIs
            
            elif isinstance(item, str) and item.startswith('data:'):
                logger.info(' ----- HANDLING DATA URIS 5th Condition ----- ')
                mime_type = item.split(';')[0].split(':')[1].lower()
                if mime_type.startswith('audio/'):
                    return 'audio'
                elif mime_type.startswith('image/'):
                    return 'image'
                return 'text'

            elif isinstance(item, str) and is_uuid(item):
                logger.info(' ----- HANDLING UUID STRING Condition ----- ')
                return "knowledge_base"


            # Handle plain strings: now try to detect if the string is Base64-encoded audio.
            elif isinstance(item, str):
                try:
                    logger.info(' ----- HANDLING PLAIN STRINGS AS BASE64 6th Condition ----- ')
                    # First try to handle as URL
                    with requests.get(item, timeout=100) as response:
                        logger.info(f' ----- RESPONSE downloaded :{response.status_code} {response.content[:100]}----- ')
                        if response.status_code == 200:
                                content = response.content
                                kind = filetype.guess(content)
                                if kind is not None:
                                    if kind.mime.startswith('audio/'):
                                        return 'audio'
                                    elif kind.mime.startswith('image/'):
                                        return 'image'
                                    elif kind.mime.startswith('application/pdf'):
                                        return 'pdf'
                                    else:
                                        return 'file'
                                # FIX: If filetype detection fails but URL looks like audio/image/file, infer from URL
                                elif '/audio' in item.lower() or item.lower().endswith(('.mp3', '.wav', '.ogg', '.flac', '.m4a', '.aac')):
                                    logger.info(' ----- URL INFERRED AS AUDIO FROM PATH ----- ')
                                    return 'audio'
                                elif '/image' in item.lower() or item.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')):
                                    logger.info(' ----- URL INFERRED AS IMAGE FROM PATH ----- ')
                                    return 'image'
                        logger.info(' ----- DID NOT WORK AS URL ----- ')
                        # Try to handle as base64 encoded data
                        candidate_bytes = base64.b64decode(item, validate=True)
                        kind = filetype.guess(candidate_bytes)
                        if kind is not None:
                            if kind.mime.startswith('audio/'):
                                return 'audio'
                            elif kind.mime.startswith('image/'):
                                return 'image'
                            elif kind.mime.startswith('application/pdf'):
                                return 'pdf'
                            return 'file'
                        else:
                            return 'text'
                except Exception:
                    pass

                logger.info(' ----- HANDLING PLAIN STRINGS AS TEXT 7th Condition ----- ')
                return 'text'

            # Default fallback
            logger.info(' ----- DEFAULT FOR OTHER TYPES 8th Condition ----- ')
            return 'text'

        except ImportError:
            # Fallback if required libraries are not available
            bytes_data = None
            if isinstance(item, dict) and 'bytes' in item:
                bytes_data = item['bytes']
            elif isinstance(item, bytes):
                bytes_data = item

            if bytes_data is not None and isinstance(bytes_data, bytes):
                if bytes_data.startswith(b'RIFF') and b'WAVE' in bytes_data[0:12]:
                    return 'audio'
                elif bytes_data.startswith(b'ID3'):
                    return 'audio'
                elif any(bytes_data.startswith(bytes([0xFF, header])) for header in range(0xE0, 0xF0)):
                    return 'audio'
                elif bytes_data.startswith(b'OggS'):
                    return 'audio'
                elif bytes_data.startswith(b'fLaC'):
                    return 'audio'
                elif bytes_data.startswith(b'\xFF\xF1') or bytes_data.startswith(b'\xFF\xF9'):
                    return 'audio'
                elif b'ftypM4A' in bytes_data[0:32] or b'ftypaac' in bytes_data[0:32]:
                    return 'audio'
                return 'image'

            elif isinstance(item, str):
                if item.startswith(('http://', 'https://')):
                    url_path = urlparse(item).path.lower()
                    if any(url_path.endswith(ext) for ext in ['.mp3', '.wav', '.ogg', '.m4a', '.aac', '.flac', '.wma']):
                        return 'audio'
                    elif any(url_path.endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp']):
                        return 'image'
                    return 'text'
                elif item.startswith('data:'):
                    mime_type = item.split(';')[0].split(':')[1].lower()
                    if mime_type.startswith('audio/'):
                        return 'audio'
                    elif mime_type.startswith('image/'):
                        return 'image'
                    return 'text'
                elif '.' in item:
                    file_ext = os.path.splitext(item)[1].lower()
                    if file_ext in ['.mp3', '.wav', '.ogg', '.m4a', '.aac', '.flac', '.wma']:
                        return 'audio'
                    elif file_ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp']:
                        return 'image'
                return 'text'
            return 'text'
        except Exception as e:
            # If any error occurs during detection, default to text
            logger.error(f"Error detecting input type: {e}\n{traceback.format_exc()}")
            return 'text'

    try:
        # If input_item is a dictionary, process each value
        if isinstance(input_item, dict):
            result = {}
            for key, value in input_item.items():
                # If the value is a nested dictionary, recursively process it
                if isinstance(value, dict):
                    result[key] = detect_input_type(value)
                elif isinstance(value, list):
                    # Handle list values (e.g., multiple images in a column)
                    result[key] = detect_input_type(value).get("type", "text")
                else:
                    result[key] = detect_single_item(value)
            return result
        # Handle list inputs (e.g., JSON array of image URLs)
        elif isinstance(input_item, list):
            if not input_item:
                return {"type": "text"}
            # Detect type of each item in the list
            detected_types = []
            for item in input_item:
                item_type = detect_single_item(item)
                detected_types.append(item_type)
            # If all items are images, return 'images' (plural) to indicate multi-image
            if all(t == "image" for t in detected_types):
                return {"type": "images"}
            # If all items are the same type, return that type
            if len(set(detected_types)) == 1:
                return {"type": detected_types[0]}
            # Mixed types - default to text (caller should handle explicitly)
            logger.warning(
                "detect_input_type_mixed_list",
                detected_types=detected_types,
                message="List contains mixed types, defaulting to text"
            )
            return {"type": "text"}
        # Handle JSON array string (e.g., '["url1", "url2"]')
        elif isinstance(input_item, str):
            # Try to parse as JSON array
            try:
                parsed = json.loads(input_item)
                if isinstance(parsed, list) and len(parsed) > 0:
                    # Recursively detect type of the parsed list
                    return detect_input_type(parsed)
            except (json.JSONDecodeError, TypeError):
                pass
            # Not a JSON array, detect as single item
            return {"type": detect_single_item(input_item)}
        else:
            return {"type": detect_single_item(input_item)}
    except Exception as e:
        return {"type": "text", "error": str(e)}
