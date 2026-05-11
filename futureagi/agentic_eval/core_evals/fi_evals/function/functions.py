import json
import os
import re
import string
import subprocess
import tempfile
from typing import Any, List

import Levenshtein
import numpy as np
import requests
from jinja2 import Environment
from nltk.translate.bleu_score import SmoothingFunction, sentence_bleu
from rouge_score import rouge_scorer
from scipy.spatial.distance import cityblock, cosine, euclidean

from agentic_eval.core_evals.fi_evals.grounded.similarity import CosineSimilarity
from agentic_eval.core_evals.fi_utils.exceptions import NoOpenAiApiKeyException
from agentic_eval.core_evals.fi_utils.fi_code_execution import CodeExecution
from agentic_eval.core_evals.fi_utils.json import extract_json_path, validate_json
from agentic_eval.core_evals.fi_utils.logging import logger
from agentic_eval.core_evals.fi_utils.utils import PreserveUndefined
from agentic_eval.core_evals.keys.openai_api import OpenAiApiKey
from agentic_eval.core_evals.llm_services.openai_api import OpenAiService


def _standardize_url(url):
    """
    Generate a standardized URL by adding 'http://' if it's missing.

    Args:
        url (str): The input URL to be standardized.

    Returns:
        str: The standardized URL.
    """
    if url.startswith("http://") or url.startswith("https://"):
        return url
    else:
        return "http://" + url


def _preprocess_strings(keywords, text, case_sensitive):
    """
    Preprocess the keywords based on the case_sensitive flag.

    Args:
        keywords (str or List[str]): The keyword(s) to preprocess.
        case_sensitive (bool): Whether the preprocessing should be case-sensitive.

    Returns:
        List[str]: The preprocessed keywords.
    """
    # If keywords is a string, convert it to a list
    if isinstance(keywords, str):
        keywords = keywords.split(",")

    # Strip leading and spaces from the keywords
    keywords = [k.strip() for k in keywords]

    # If case_sensitive is False, convert all keywords and text to lowercase
    if not case_sensitive:
        keywords = [keyword.lower() for keyword in keywords]
        text = text.lower()

    return keywords, text


def regex(pattern, text, **kwargs):
    """
    Perform a regex search on the text and return a dictionary indicating whether the pattern was found.

    Args:
        pattern (str): The regex pattern to search for.
        text (str): The text string to search within.

    Returns:
        dict: A dictionary containing the result of the regex search and the reason for the result.
    """
    match = re.search(pattern, text)
    if match:
        return {"result": True, "reason": f"regex pattern {pattern} found in output"}
    else:
        return {
            "result": False,
            "reason": f"regex pattern {pattern} not found in output",
        }


def contains_any(keywords, text: str, case_sensitive=False, **kwargs):
    """
    Check if any of the provided keywords are present in the text.

    Args:
        keywords (str or List[str]): The keyword(s) to search for in the text.
        text (str): The text string to search within.
        case_sensitive (bool, optional): Whether the search should be case-sensitive. Defaults to False.

    Returns:
        dict: A dictionary containing the result of the search and the reason for the result.
    """
    keywords, text = _preprocess_strings(keywords, text, case_sensitive)
    found_keywords = []
    for keyword in keywords:
        if keyword in text:
            found_keywords.append(keyword)

    if found_keywords:
        result = True
        reason = "One or more keywords were found in output: " + ", ".join(
            found_keywords
        )
    else:
        result = False
        reason = "No keywords found in output"

    return {"result": result, "reason": reason}


def contains_all(keywords, text, case_sensitive=False, **kwargs):
    """
    Check if all the provided keywords are present in the text.

    Args:
        keywords (List[str]): The list of keywords to search for in the text.
        text (str): The text string to search within.
        case_sensitive (bool, optional): If True, the comparison is case-sensitive. Defaults to False.

    Returns:
        dict: A dictionary containing the result of the keyword search and the reason for the result.
    """
    keywords, text = _preprocess_strings(keywords, text, case_sensitive)
    missing_keywords = []
    for keyword in keywords:
        if keyword not in text:
            result = False
            missing_keywords.append(keyword)
    if (len(missing_keywords)) > 0:
        result = False
        reason = "keywords not found in output: " + ", ".join(missing_keywords)
    else:
        result = True
        reason = f"{len(keywords)}/{len(keywords)} keywords found in output"

    return {"result": result, "reason": reason}



def calculate_bleu(reference, hypothesis, **kwargs):
    """
    Calculate BLEU score between a reference and a hypothesis sentence.
    Args:
        reference (str): The reference sentence.
        hypothesis (str): The generated sentence.
    Returns:
        float: BLEU score (0 to 1).
    """
    reference_tokens = [reference.split()]
    hypothesis_tokens = hypothesis.split()
    smoothie = SmoothingFunction().method4
    score = sentence_bleu(reference_tokens, hypothesis_tokens, smoothing_function=smoothie)
    return {"result": score, "reason": f"BLEU score: {score}"}




def calculate_rouge(reference, hypothesis):
    """
    Calculate ROUGE-1, ROUGE-2, and ROUGE-L scores.
    Args:
        reference (str): The reference sentence.
        hypothesis (str): The generated sentence.
    Returns:
        dict: ROUGE scores (precision, recall, fmeasure for each metric).
    """

    scorer = rouge_scorer.RougeScorer(['rouge1', 'rouge2', 'rougeL'], use_stemmer=True)
    scores = scorer.score(reference, hypothesis)

    # Parse rouge1 score and store as string
    rouge1_score = f"ROUGE-1: P={scores['rouge1'].precision:.3f}, R={scores['rouge1'].recall:.3f}, F={scores['rouge1'].fmeasure:.3f}"
    score = f'{scores["rouge1"].fmeasure:.3f}'
    return {"result": score, "reason": f"ROUGE score: {rouge1_score}"}

def _pil_to_uint8_tensor(img, size: int = 299):
    """
    Convert PIL image -> uint8 tensor (1, 3, size, size) in [0,255].
    Requires torch and torchvision to be installed.
    """
    import numpy as np
    import torch
    from torchvision.transforms import functional as TF

    img = img.convert("RGB")
    img = TF.resize(img, [size, size], antialias=True)

    # PIL -> (H, W, 3) uint8 -> torch (3, H, W) uint8
    x = torch.from_numpy(np.array(img, dtype=np.uint8)).permute(2, 0, 1)
    return x.unsqueeze(0)  # (1, 3, H, W)


def _parse_image_list(images_input):
    """
    Parse image input which can be:
    - A JSON string containing a list of URLs
    - A list of URLs (strings)
    - A list of PIL Images

    Returns a list of PIL Images.
    """
    from PIL import Image
    from tfc.utils.storage import open_image_from_url

    # If it's a string, try to parse as JSON
    if isinstance(images_input, str):
        try:
            images_input = json.loads(images_input)
        except json.JSONDecodeError:
            # Maybe it's a single URL
            if images_input.startswith(("http://", "https://")):
                images_input = [images_input]
            else:
                raise ValueError(f"Cannot parse images input: {images_input}")

    if not isinstance(images_input, list):
        raise ValueError(f"Expected a list of images, got {type(images_input)}")

    pil_images = []
    for img in images_input:
        if isinstance(img, Image.Image):
            # Already a PIL Image
            pil_images.append(img)
        elif isinstance(img, str):
            # It's a URL or base64 string - download and convert
            pil_img = open_image_from_url(img)
            if pil_img is None:
                raise ValueError(f"Failed to load image from: {img}")
            pil_images.append(pil_img)
        else:
            raise ValueError(f"Unsupported image type: {type(img)}")

    return pil_images


def calculate_fid(
    real_images,
    fake_images,
    device: str | None = None,
    size: int = 299,
    batch_size: int = 32,
):
    """
    Compute FID (Frechet Inception Distance) between two lists of images.

    FID measures the similarity between two distributions of images. Lower scores
    indicate more similar distributions.

    Requirements:
      - At least 2 images in each list (torchmetrics needs covariance).
      - Inputs can be: PIL Images, URLs, or JSON strings containing URLs.
      - torch, torchvision, and torchmetrics must be installed.

    Args:
        real_images: list of PIL images, URLs, or JSON string of URLs representing the "real" distribution
        fake_images: list of PIL images, URLs, or JSON string of URLs representing the "fake/generated" distribution
        device: "cuda" or "cpu" (auto-detected if None)
        size: resize size for Inception input (default 299)
        batch_size: how many images to push per update() call

    Returns:
        dict: A dictionary containing the FID score and reason.
    """
    import traceback

    logger.debug("=" * 50)
    logger.debug("calculate_fid called!")
    logger.debug(f"real_images type: {type(real_images)}")
    logger.debug(f"fake_images type: {type(fake_images)}")
    logger.debug("=" * 50)

    # Lazy imports - only load torch when FID is actually used
    try:
        import torch
        from torchmetrics.image.fid import FrechetInceptionDistance
        logger.debug("torch imports successful")
    except ImportError as e:
        logger.error(f"torch import failed: {e}")
        logger.error(traceback.format_exc())
        return {
            "result": None,
            "reason": f"FID requires torch, torchvision, and torchmetrics: {e}"
        }

    # Parse and convert inputs to PIL Images
    try:
        real_images = _parse_image_list(real_images)
        logger.debug(f"Parsed {len(real_images)} real images")
    except Exception as e:
        logger.error(f"Failed to parse real_images: {e}")
        return {
            "result": None,
            "reason": f"Failed to parse real_images: {e}"
        }

    try:
        fake_images = _parse_image_list(fake_images)
        logger.debug(f"Parsed {len(fake_images)} fake images")
    except Exception as e:
        logger.error(f"Failed to parse fake_images: {e}")
        return {
            "result": None,
            "reason": f"Failed to parse fake_images: {e}"
        }

    if len(real_images) < 2 or len(fake_images) < 2:
        return {
            "result": None,
            "reason": "Need at least 2 images in each list to compute FID."
        }

    if device is None:
        device = "cuda" if torch.cuda.is_available() else "cpu"

    logger.debug(f"Using device: {device}")
    logger.debug(f"Creating FID metric with {len(real_images)} real and {len(fake_images)} fake images")

    fid = FrechetInceptionDistance(feature=2048).to(device)

    # Update real distribution in batches
    for i in range(0, len(real_images), batch_size):
        batch = real_images[i : i + batch_size]
        x = torch.cat([_pil_to_uint8_tensor(img, size=size) for img in batch], dim=0).to(device)
        fid.update(x, real=True)

    # Update fake distribution in batches
    for i in range(0, len(fake_images), batch_size):
        batch = fake_images[i : i + batch_size]
        x = torch.cat([_pil_to_uint8_tensor(img, size=size) for img in batch], dim=0).to(device)
        fid.update(x, real=False)
    try:
        score = float(fid.compute().detach().cpu())
    except Exception as e:
        logger.error(f"Failed to compute FID score: {e}")
        return {
            "result": None,
            "reason": f"Failed to compute FID score: {e}"
        }
    return {"result": score, "reason": f"FID score: {score:.3f}"}


def calculate_clip_score(
    images,
    text,
):
    """
    Compute CLIP Score between images and text prompts.

    CLIP Score measures how well images match their text descriptions. Higher scores
    indicate better alignment between images and text (range: 0-100).

    Uses the existing embeddings service which provides CLIP-based image-text embeddings.

    Requirements:
      - Images can be: a single PIL Image, URL, or a list of images/URLs, or JSON string containing URLs.
      - Text can be a single string or a list of strings (one per image).

    Args:
        images: single image (PIL Image or URL) or list of images, or JSON string of URLs
        text: string or list of strings describing the images

    Returns:
        dict: A dictionary containing the CLIP score and reason.
    """
    import traceback

    import numpy as np
    from PIL import Image

    from agentic_eval.core.embeddings.embedding_manager import model_manager

    logger.debug("=" * 50)
    logger.debug("calculate_clip_score called!")
    logger.debug(f"images type: {type(images)}")
    logger.debug(f"text type: {type(text)}")
    logger.debug("=" * 50)

    # Parse and convert inputs to list of PIL Images
    try:
        # Handle single image case (PIL Image or single URL string that's not JSON)
        if isinstance(images, Image.Image):
            images_list = [images]
        elif isinstance(images, str):
            # Try to parse as JSON first
            try:
                parsed = json.loads(images)
                if isinstance(parsed, list):
                    images_list = _parse_image_list(parsed)
                else:
                    # Single URL or path
                    images_list = _parse_image_list([images])
            except json.JSONDecodeError:
                # Single URL or path (not JSON)
                images_list = _parse_image_list([images])
        elif isinstance(images, list):
            images_list = _parse_image_list(images)
        else:
            # Try to parse as a single image
            images_list = _parse_image_list([images])

        logger.debug(f"Parsed {len(images_list)} images")
    except Exception as e:
        logger.error(f"Failed to parse images: {e}")
        logger.error(traceback.format_exc())
        return {
            "result": None,
            "reason": f"Failed to parse images: {e}"
        }

    # Parse text input
    try:
        if isinstance(text, str):
            # Try to parse as JSON list first
            try:
                parsed_text = json.loads(text)
                if isinstance(parsed_text, list):
                    text_list = parsed_text
                else:
                    text_list = [text]
            except json.JSONDecodeError:
                # Single text string - replicate for all images
                text_list = [text] * len(images_list)
        elif isinstance(text, list):
            text_list = text
        else:
            text_list = [str(text)] * len(images_list)

        # Ensure text list matches image count
        if len(text_list) == 1 and len(images_list) > 1:
            text_list = text_list * len(images_list)
        elif len(text_list) != len(images_list):
            return {
                "result": None,
                "reason": f"Number of text prompts ({len(text_list)}) must match number of images ({len(images_list)})"
            }
        logger.debug(f"Parsed {len(text_list)} text prompts")
    except Exception as e:
        logger.error(f"Failed to parse text: {e}")
        logger.error(traceback.format_exc())
        return {
            "result": None,
            "reason": f"Failed to parse text: {e}"
        }

    if len(images_list) < 1:
        return {
            "result": None,
            "reason": "Need at least 1 image to compute CLIP score."
        }

    try:
        # Get the image-text model (CLIP-based) from the embedding manager
        image_text_model = model_manager.image_text_model

        if image_text_model is None:
            return {
                "result": None,
                "reason": "Image-text embedding model is not available"
            }

        # Compute CLIP scores for each image-text pair
        scores = []
        for img, txt in zip(images_list, text_list):
            # Get image embedding
            image_embedding = image_text_model(img)
            # Get text embedding
            text_embedding = image_text_model(txt)

            if not image_embedding or not text_embedding:
                logger.warning("Failed to get embeddings for image-text pair")
                continue

            # Convert to numpy arrays
            image_emb = np.array(image_embedding)
            text_emb = np.array(text_embedding)

            # Compute cosine similarity
            similarity = 1.0 - cosine(image_emb, text_emb)

            # Scale to 0-100 range (like torchmetrics CLIPScore)
            # CLIP similarity is typically in [-1, 1], but with normalized embeddings it's [0, 1]
            # We scale to [0, 100] to match the expected CLIPScore range
            clip_score = similarity * 100

            scores.append(clip_score)

        if not scores:
            return {
                "result": None,
                "reason": "Failed to compute CLIP scores for any image-text pairs"
            }

        # Return mean score across all pairs
        mean_score = float(np.mean(scores))
        logger.debug(f"CLIP score computed: {mean_score}")

        return {"result": mean_score, "reason": f"CLIP score: {mean_score:.3f}"}
    except Exception as e:
        logger.error(f"Error computing CLIP score: {e}")
        logger.error(traceback.format_exc())
        return {
            "result": None,
            "reason": f"Error computing CLIP score: {e}"
        }


def _parse_reference_and_hypothesis(reference, hypothesis):
    """Parse inputs using the same logic as recall_score."""
    try:
        if isinstance(reference, str):
            # Try to parse as JSON first
            try:
                reference = json.loads(reference)
            except json.JSONDecodeError:
                # If JSON parsing fails, try to evaluate as Python literal

                try:
                    import ast
                    reference = ast.literal_eval(reference)
                except Exception:
                    if reference is not None and not isinstance(reference, list):
                        reference = (
                            list(reference)
                            if isinstance(reference, set | tuple)
                            else [reference]
                        )

        if isinstance(hypothesis, str):
            # Try to parse as JSON first
            try:
                hypothesis = json.loads(hypothesis)
            except json.JSONDecodeError:
                # If JSON parsing fails, try to evaluate as Python literal
                try:
                    import ast
                    hypothesis = ast.literal_eval(hypothesis)
                except Exception:
                    if hypothesis is not None and not isinstance(hypothesis, list):
                        hypothesis = (
                            list(hypothesis)
                            if isinstance(hypothesis, set | tuple)
                            else [hypothesis]
                        )

    except (json.JSONDecodeError, ValueError, SyntaxError):
        raise ValueError(
            "Invalid format. Expected List format of reference and hypothesis."
        )

    if not isinstance(reference, list) or not isinstance(hypothesis, list):
        raise ValueError(
            "Invalid format. Expected List format of reference and hypothesis."
        )

    return reference, hypothesis


def _parse_k(k, default):
    # Invalid or missing k falls back to full retrieved length (default).
    if k is None:
        return default
    try:
        k = int(k)
    except (TypeError, ValueError):
        return default
    if k <= 0:
        return default
    return k


def _validate_non_empty_ground_truth(hypothesis, metric_name: str) -> None:
    """Raise a clear user-facing error when ground truth is missing."""
    if len(hypothesis) == 0:
        raise ValueError(
            f"{metric_name} requires at least one ground-truth relevant item."
        )


def _validate_flat_retrieval_inputs(
    reference, hypothesis, metric_name: str
) -> tuple[list, list]:
    """Validate single-query retrieval inputs and reject nested structures.

    Retrieval @k metrics operate on one ranked list and one relevant list.
    Nested inputs (list-of-lists) should use MRR multi-query mode instead.
    """

    def _check_flat(items, field_name: str):
        for item in items:
            if isinstance(item, list | dict):
                raise ValueError(
                    f"{metric_name} expects a flat list for '{field_name}'. "
                    "Use MRR with list-of-lists for multi-query input."
                )

    _check_flat(reference, "reference")
    _check_flat(hypothesis, "hypothesis")
    return reference, hypothesis


def _safe_set(items, metric_name: str, field_name: str):
    """Build set with a clear validation error for unhashable elements."""
    try:
        return set(items)
    except TypeError as exc:
        raise ValueError(
            f"{metric_name} expects hashable values in '{field_name}' (for example, plain strings)."
        ) from exc


def recall_score(reference, hypothesis):
    """
    Calculates Recall = (# relevant retrieved) / (# relevant total)

    Parameters:
    - hypothesis (List or Set): Retrieved chunks
    - reference (List or Set): Ground truth relevant chunks

    Returns:
    - float: Recall score
    """
    reference, hypothesis = _parse_reference_and_hypothesis(reference, hypothesis)
    # Contract: hypothesis = retrieved ranked items, reference = ground truth.
    ground_truth, retrieved = _validate_flat_retrieval_inputs(
        reference, hypothesis, "Recall"
    )
    _validate_non_empty_ground_truth(ground_truth, "Recall")

    retrieved_set = _safe_set(retrieved, "Recall", "hypothesis")
    ground_truth_set = _safe_set(ground_truth, "Recall", "reference")

    true_positives = len(retrieved_set.intersection(ground_truth_set))
    total_relevant = len(ground_truth_set)

    score = true_positives / total_relevant
    return {"result": score, "reason": f"Recall score: {score}"}


def recall_at_k(reference, hypothesis, k=None):
    """Compute Retrieval Recall@k for one ranked list.

    Inputs:
    - hypothesis: ranked retrieved items (list, JSON list string, or list-like string)
    - reference: ground-truth relevant items (same accepted formats)
    - k: optional cutoff; if missing/invalid/non-positive, full retrieved length is used

    Formula:
    - Recall@k = |top_k(hypothesis) ∩ reference| / |reference|

    Behavior:
    - Raises ValueError when ground-truth list is empty (user-facing message).

    Returns:
    - {"result": float, "reason": str}
    """
    reference, hypothesis = _parse_reference_and_hypothesis(reference, hypothesis)
    # Contract: hypothesis = retrieved ranked items, reference = ground truth.
    ground_truth, retrieved = _validate_flat_retrieval_inputs(
        reference, hypothesis, "Recall@k"
    )
    _validate_non_empty_ground_truth(ground_truth, "Recall@k")
    k = _parse_k(k, len(retrieved) if len(retrieved) > 0 else 1)

    top_k = retrieved[:k]
    relevant_set = _safe_set(ground_truth, "Recall@k", "reference")
    score = len(
        _safe_set(top_k, "Recall@k", "hypothesis").intersection(relevant_set)
    ) / len(relevant_set)
    return {"result": score, "reason": f"Recall@{k}: {score}"}


def precision_at_k(reference, hypothesis, k=None):
    """Compute Retrieval Precision@k for one ranked list.

    Inputs:
    - hypothesis: ranked retrieved items (list, JSON list string, or list-like string)
    - reference: ground-truth relevant items (same accepted formats)
    - k: optional cutoff; if missing/invalid/non-positive, full retrieved length is used

    Formula:
    - Precision@k = |top_k(hypothesis) ∩ reference| / k

    Behavior:
    - Raises ValueError when ground-truth list is empty (user-facing message).
    - Returns 0.0 when top_k is empty after parsing.

    Returns:
    - {"result": float, "reason": str}
    """
    reference, hypothesis = _parse_reference_and_hypothesis(reference, hypothesis)
    # Contract: hypothesis = retrieved ranked items, reference = ground truth.
    ground_truth, retrieved = _validate_flat_retrieval_inputs(
        reference, hypothesis, "Precision@k"
    )
    _validate_non_empty_ground_truth(ground_truth, "Precision@k")
    k = _parse_k(k, len(retrieved) if len(retrieved) > 0 else 1)

    top_k = retrieved[:k]
    relevant_set = _safe_set(ground_truth, "Precision@k", "reference")
    if len(top_k) == 0:
        return {"result": 0.0, "reason": f"Precision@{k}: 0.0"}

    score = (
        len(_safe_set(top_k, "Precision@k", "hypothesis").intersection(relevant_set))
        / k
    )
    return {"result": score, "reason": f"Precision@{k}: {score}"}


def ndcg_at_k(reference, hypothesis, k=None):
    """Compute Retrieval NDCG@k with binary relevance.

    Inputs:
    - hypothesis: ranked retrieved items (list, JSON list string, or list-like string)
    - reference: ground-truth relevant items (same accepted formats)
    - k: optional cutoff; if missing/invalid/non-positive, full retrieved length is used

    Scoring:
    - Relevance is binary (item in reference => 1, else 0)
    - DCG uses log2 discount by rank position
    - NDCG@k = DCG@k / IDCG@k

    Behavior:
    - Raises ValueError when ground-truth list is empty (user-facing message).

    Returns:
    - {"result": float, "reason": str}
    """
    reference, hypothesis = _parse_reference_and_hypothesis(reference, hypothesis)
    # Contract: hypothesis = retrieved ranked items, reference = ground truth.
    ground_truth, retrieved = _validate_flat_retrieval_inputs(
        reference, hypothesis, "NDCG@k"
    )
    _validate_non_empty_ground_truth(ground_truth, "NDCG@k")
    k = _parse_k(k, len(retrieved) if len(retrieved) > 0 else 1)

    relevant_set = _safe_set(ground_truth, "NDCG@k", "reference")
    dcg = 0.0
    seen_relevant = set()
    for idx, item in enumerate(retrieved[:k], start=1):
        # Binary relevance should credit each relevant item at most once.
        # Repeated retrieval of the same relevant item must not increase DCG.
        if item in relevant_set and item not in seen_relevant:
            dcg += 1.0 / np.log2(idx + 1)
            seen_relevant.add(item)

    ideal_hits = min(len(relevant_set), k)
    idcg = sum(1.0 / np.log2(i + 1) for i in range(1, ideal_hits + 1))
    if idcg == 0:
        return {"result": 0.0, "reason": f"NDCG@{k}: 0.0"}

    score = dcg / idcg
    return {"result": score, "reason": f"NDCG@{k}: {score}"}


def _reciprocal_rank(reference, hypothesis):
    relevant_set = _safe_set(reference, "MRR", "reference")
    for idx, item in enumerate(hypothesis, start=1):
        if item in relevant_set:
            return 1.0 / idx

    return 0.0


def mean_reciprocal_rank(reference, hypothesis):
    """Compute Mean Reciprocal Rank (MRR).

    Supports two input modes:
    1) Single-query mode: hypothesis/reference are flat lists.
    2) Multi-query mode: hypothesis/reference are list-of-lists, where each
        element corresponds to one query.

    Scoring:
    - Reciprocal rank for each query is 1/rank of first relevant item, else 0.
    - MRR is the average reciprocal rank across queries.

    Behavior:
    - Raises ValueError when any ground-truth relevant list is empty.
    - In multi-query mode, both inputs must be list-of-lists of equal length.

    Returns:
    - {"result": float, "reason": str}
    """
    reference, hypothesis = _parse_reference_and_hypothesis(reference, hypothesis)
    # Contract: hypothesis = retrieved ranked items, reference = ground truth.
    ground_truth_all, retrieved_all = reference, hypothesis

    is_reference_nested = len(ground_truth_all) > 0 and all(
        isinstance(item, list | tuple | set) for item in ground_truth_all
    )
    is_hypothesis_nested = len(retrieved_all) > 0 and all(
        isinstance(item, list | tuple | set) for item in retrieved_all
    )

    # Multi-query mode: true MRR across query set
    if is_reference_nested or is_hypothesis_nested:
        if not (is_reference_nested and is_hypothesis_nested):
            raise ValueError(
                "For MRR multi-query mode, both ground-truth (reference) and retrieved (hypothesis) must be list-of-lists."
            )

        if len(ground_truth_all) != len(retrieved_all):
            raise ValueError(
                "For MRR multi-query mode, ground-truth (reference) and retrieved (hypothesis) must have equal number of queries."
            )

        if len(ground_truth_all) == 0:
            return {"result": 0.0, "reason": "MRR: 0.0"}

        rr_scores = []
        for idx, (relevant_items, retrieved_items) in enumerate(
            zip(ground_truth_all, retrieved_all), start=1
        ):
            relevant_list = (
                list(relevant_items)
                if isinstance(relevant_items, tuple | set)
                else list(relevant_items)
            )
            retrieved_list = (
                list(retrieved_items)
                if isinstance(retrieved_items, tuple | set)
                else list(retrieved_items)
            )
            if len(relevant_list) == 0:
                raise ValueError(
                    f"MRR requires at least one ground-truth relevant item for query {idx}."
                )
            rr_scores.append(_reciprocal_rank(relevant_list, retrieved_list))

        score = sum(rr_scores) / len(rr_scores)
        return {
            "result": score,
            "reason": f"MRR: {score} across {len(rr_scores)} queries",
        }

    # Single-query mode (MRR over one query == RR)
    ground_truth, retrieved = _validate_flat_retrieval_inputs(
        reference, hypothesis, "MRR"
    )
    _validate_non_empty_ground_truth(ground_truth, "MRR")
    score = _reciprocal_rank(ground_truth, retrieved)
    return {"result": score, "reason": f"MRR: {score}"}


def hit_rate(reference, hypothesis):
    """Compute retrieval Hit Rate.

    Hit Rate is the percentage of queries where at least one relevant item is
    retrieved.

    Supports two input modes:
    1) Single-query mode (flat lists): returns 1.0 if any hit exists, else 0.0.
    2) Multi-query mode (list-of-lists): returns mean hit indicator across
       queries.

    Ground-truth relevant list must be non-empty for each evaluated query.
    """
    reference, hypothesis = _parse_reference_and_hypothesis(reference, hypothesis)
    # Contract: hypothesis = retrieved ranked items, reference = ground truth.
    ground_truth_all, retrieved_all = reference, hypothesis

    is_reference_nested = len(ground_truth_all) > 0 and all(
        isinstance(item, list | tuple | set) for item in ground_truth_all
    )
    is_hypothesis_nested = len(retrieved_all) > 0 and all(
        isinstance(item, list | tuple | set) for item in retrieved_all
    )

    if is_reference_nested or is_hypothesis_nested:
        if not (is_reference_nested and is_hypothesis_nested):
            raise ValueError(
                "Hit Rate multi-query mode requires list-of-lists for both ground-truth (reference) and retrieved (hypothesis)."
            )

        if len(ground_truth_all) != len(retrieved_all):
            raise ValueError(
                "Hit Rate multi-query mode requires equal number of queries in ground-truth (reference) and retrieved (hypothesis)."
            )

        if len(ground_truth_all) == 0:
            return {"result": 0.0, "reason": "Hit Rate: 0.0"}

        hit_indicators = []
        for idx, (relevant_items, retrieved_items) in enumerate(
            zip(ground_truth_all, retrieved_all), start=1
        ):
            relevant_list = (
                list(relevant_items)
                if isinstance(relevant_items, tuple | set)
                else list(relevant_items)
            )
            retrieved_list = (
                list(retrieved_items)
                if isinstance(retrieved_items, tuple | set)
                else list(retrieved_items)
            )

            if len(relevant_list) == 0:
                raise ValueError(
                    f"Hit Rate requires at least one ground-truth relevant item for query {idx}."
                )

            hit_indicators.append(
                1.0
                if len(
                    _safe_set(retrieved_list, "Hit Rate", "hypothesis").intersection(
                        _safe_set(relevant_list, "Hit Rate", "reference")
                    )
                )
                > 0
                else 0.0
            )

        score = sum(hit_indicators) / len(hit_indicators)
        return {
            "result": score,
            "reason": f"Hit Rate: {score} across {len(hit_indicators)} queries",
        }

    ground_truth, retrieved = _validate_flat_retrieval_inputs(
        reference, hypothesis, "Hit Rate"
    )
    _validate_non_empty_ground_truth(ground_truth, "Hit Rate")

    score = (
        1.0
        if len(
            _safe_set(retrieved, "Hit Rate", "hypothesis").intersection(
                _safe_set(ground_truth, "Hit Rate", "reference")
            )
        )
        > 0
        else 0.0
    )
    return {"result": score, "reason": f"Hit Rate: {score}"}


def calculate_levenshtein_similarity(output, expected, case_insensitive=True, remove_punctuation=True):
    """
    Calculates the normalized Levenshtein similarity between two strings and provides a descriptive reason.
    If calculation fails, raises an exception.
    """
    def _preprocess(text):
        if not isinstance(text, str):
            text = str(text) if text is not None else ""
        if case_insensitive:
            text = text.lower()
        if remove_punctuation:
            text = text.translate(str.maketrans('', '', string.punctuation))
        return text

    try:
        pred_proc = _preprocess(output)
        ref_proc = _preprocess(expected)
        max_len = max(len(pred_proc), len(ref_proc), 1)
        distance = Levenshtein.distance(pred_proc, ref_proc)
        score = distance / max_len
        similarity = 1.0 - score
        reason = f"Levenshtein Distance: {distance} edits, Normalized Score: {score:.3f}, Similarity: {similarity:.3f}"
        return {"result": similarity, "reason": reason}
    except Exception as e:
        raise Exception(f"Error calculating Levenshtein distance: {e}")


def _to_number(value, name):
    """
    Convert value to number, returning (number, error_msg) tuple.

    Args:
        value: Value to convert (int, float, str, or None)
        name: Parameter name for error messages

    Returns:
        tuple: (float | None, str | None) - (number, error_message)
               Returns (value, None) on success, (None, error_msg) on failure
    """
    # Handle None and empty strings
    if value is None:
        return None, f"{name} is None"

    # Handle numeric types
    if isinstance(value, int | float):
        return float(value), None

    # Convert to string and strip whitespace
    value_str = str(value).strip()

    # Handle empty strings after stripping
    if not value_str:
        return None, f"{name} is empty"

    # Try to extract numeric value
    match = re.search(r'-?\d+\.?\d*', value_str)
    if match:
        try:
            return float(match.group()), None
        except (ValueError, OverflowError) as e:
            logger.error(f"DEBUG: Error converting {name} to number: {value} contains invalid numeric value: {e}")
            return None, f"{name} contains invalid numeric value: {e}"

    # No numeric value found    
    return None, f"No numeric value found in {name}: '{value_str}'"




def calculate_numeric_similarity(output: str, expected: str):
    """
    Calculates the absolute numeric difference between two values extracted from the input strings,
    and provides a descriptive reason string. If extraction fails, raises an exception.
    """

    pred_num, pred_error = _to_number(output, "output")
    ref_num, ref_error = _to_number(expected, "expected")

    # Check for extraction errors
    errors = []
    if pred_error:
        errors.append(pred_error)
    if ref_error:
        errors.append(ref_error)

    if errors:
        # Return failure result with clear error message
        return {
            "result": 0.0,  # or False, depending on desired failure representation
            "reason": f"Cannot calculate numeric similarity: {'; '.join(errors)}"
        }
    diff = abs(pred_num - ref_num)
    normalized_diff = diff / max(pred_num, ref_num, 1)
    similarity = 1.0 - normalized_diff
    reason = f"Numeric Diff: |{pred_num} - {ref_num}| = {diff}, Normalized Diff: {normalized_diff:.3f}, Similarity: {similarity:.3f}"
    return {
        "result": similarity,
        "reason": reason
    }


def calculate_embedding_similarity(output:str, expected: str, similarity_method="cosine", normalize=True, model_name="all-MiniLM-L6-v2"):
    """
    Calculates semantic similarity between two texts using sentence embeddings,
    and provides a descriptive reason string. If embedding or similarity computation fails,
    raises an exception.
    """
    from agentic_eval.core.embeddings.embedding_manager import model_manager
    model = model_manager.text_model
    emb1, emb2 = model([str(output)]), model([str(expected)])
    if similarity_method == "cosine":
        similarity = 1.0 - cosine(emb1, emb2)
        reason = f"Cosine Similarity: {similarity:.3f}"
    elif similarity_method == "euclidean":
        dist = euclidean(emb1, emb2)
        similarity = 1.0 / (1.0 + dist)
        reason = f"Euclidean Similarity: {similarity:.3f} (distance={dist:.3f})"
    elif similarity_method == "manhattan":
        dist = cityblock(emb1, emb2)
        similarity = 1.0 / (1.0 + dist)
        reason = f"Manhattan Similarity: {similarity:.3f} (distance={dist:.3f})"
    else:
        raise ValueError(f"Unsupported similarity method: {similarity_method}")
    return {
        "result": similarity,
        "reason": reason
    }


def calculate_semantic_list_contains(output:str, expected:str, case_insensitive=True, remove_punctuation=True, match_all=False, similarity_threshold=0.7, model_name="all-MiniLM-L6-v2"):
    """
    Checks if the output contains phrases semantically similar to the expected phrases,
    and provides a descriptive reason string. If embedding or similarity computation fails,
    raises an exception.
    """
    def _preprocess(text):
        if not isinstance(text, str):
            text = str(text)
        if case_insensitive:
            text = text.lower()
        if remove_punctuation:
            text = text.translate(str.maketrans('', '', string.punctuation))
        return text.strip()

    def _get_expected_phrases(expected):
        if expected is None:
            return []
        if isinstance(expected, str):
            if (expected.startswith('[') and expected.endswith(']')) or \
               (expected.startswith('{') and expected.endswith('}')):
                try:
                    parsed = json.loads(expected)
                    if isinstance(parsed, list):
                        return parsed
                    return [expected]
                except Exception:
                    return [expected]
            return [expected]
        elif isinstance(expected, list):
            return expected
        else:
            return [str(expected)]
    from agentic_eval.core.embeddings.embedding_manager import model_manager

    model = model_manager.text_model

    expected_phrases = _get_expected_phrases(expected)
    if not isinstance(output, str) or not output.strip():
        raise Exception("Empty output")
    if not expected_phrases:
        raise Exception("No expected text to match")

    output_proc = _preprocess(output)
    phrases_proc = [_preprocess(phrase) for phrase in expected_phrases]

    resp_embedding = model([output_proc])
    phrase_embeddings = [model([phrase]) for phrase in phrases_proc]

    matches = []
    similarities = {}
    for i, phrase in enumerate(expected_phrases):
        try:
            similarity = 1.0 - cosine(resp_embedding, phrase_embeddings[i])
        except Exception:
            raise Exception(f"Error calculating cosine similarity for phrase: {phrase}")
        matches.append(similarity >= similarity_threshold)
        similarities[phrase] = similarity

    result = all(matches) if match_all else any(matches)
    matched_count = sum(matches)
    total = len(matches)
    reason = (
        f"Matched {matched_count}/{total} phrases. "
        f"Similarities: {json.dumps(similarities, default=float)}. "
        f"Threshold: {similarity_threshold}, Match all: {match_all}"
    )
    return {
        "result": result,
        "reason": reason
    }


def contains(keyword, text, case_sensitive=False, **kwargs):
    """
    Check if the text contains a specific keyword.

    Args:
        keyword (str): The keyword to search for in the text.
        text (str): The text string to search within.
        case_sensitive (bool, optional): If True, the comparison is case-sensitive. Defaults to False.

    Returns:
        dict: A dictionary containing the result of the keyword search and the reason for the result.
    """
    if case_sensitive is False:
        text = text.lower()
        keyword = keyword.lower()
    if keyword not in text:
        result = False
        reason = "keyword not found in output: " + keyword
    else:
        result = True
        reason = f"keyword {keyword} found in output"

    return {"result": result, "reason": reason}


def contains_none(keywords, text, case_sensitive=False, **kwargs):
    """
    Check if none of the provided keywords are present in the text.

    Args:
        keywords (str or List[str]): The keyword(s) to search for in the text.
        text (str): The text string to search within.
        case_sensitive (bool, optional): If True, the comparison is case-sensitive. Defaults to False.

    Returns:
        dict: A dictionary containing the result of the check and the reason for the result.
    """
    keywords, text = _preprocess_strings(keywords, text, case_sensitive)
    found_keywords = []
    for keyword in keywords:
        if keyword in text:
            found_keywords.append(keyword)

    if found_keywords:
        result = False
        reason = "One or more keywords were found in output: " + ", ".join(
            found_keywords
        )
    else:
        result = True
        reason = "No keywords found in output"

    return {"result": result, "reason": reason}


def contains_json(text, **kwargs):
    """
    Check if the text contains valid JSON.

    Args:
        text (str): The text string to check for valid JSON.

    Returns:
        dict: A dictionary containing the result of the JSON check and the reason for the result.
    """
    trimmed_output = text.strip()
    pattern = (
        r'\{(?:\s*"(?:\\.|[^"\\])*"\s*:\s*(?:"(?:\\.|[^"\\])*"|[^{}\[\]:,]+)|[^{}]+)*\}'
    )
    matches = re.findall(pattern, trimmed_output)

    if matches:
        results = []
        errors = []
        for potential_json_string in matches:
            try:
                parsed_json = json.loads(potential_json_string)
                results.append({"json": parsed_json, "valid": True})
            except json.JSONDecodeError as e:
                errors.append(
                    {"json": potential_json_string, "valid": False, "error": str(e)}
                )
        if errors:
            return {
                "result": False,
                "reason": "Output contains a potential JSON but it is invalid",
                "matches": results,
                "errors": errors,
            }
        else:
            return {
                "result": True,
                "reason": "Output contains JSON",
                "matches": results,
            }
    else:
        return {"result": False, "reason": "Output does not contain JSON"}


def contains_email(text, **kwargs):
    """
    Check if the text contains an email address.

    Args:
        text (str): The text string to check for an email address.

    Returns:
        dict: A dictionary containing the result of the email address check and the reason for the result.
    """
    return regex(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", text)


def is_json(text, **kwargs):
    """
    Check if the text contains valid JSON.

    Args:
        text (str): The text string to check for valid JSON.

    Returns:
        dict: A dictionary containing the result of the JSON check and the reason for the result.
    """
    try:
        json.loads(text)
        result = True
    except json.JSONDecodeError:
        result = False
    if result:
        return {
            "result": True,
            "reason": "Output contains JSON",
        }
    else:
        return {
            "result": False,
            "reason": "Output does not contain JSON",
        }


def is_email(text, **kwargs):
    """
    Check if the text is a valid email address.

    Args:
        text (str): The text string to check for a valid email address.

    Returns:
        dict: A dictionary containing the result of the email address check and the reason for the result.
    """
    return regex(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$", text)


def contains_link(text, **kwargs):
    """
    Check if the text contains a link.

    Args:
        text (str): The text string to check for a link.

    Returns:
        dict: A dictionary containing the result of the link check and the reason for the result.
    """
    pattern = r"(?!.*@)(?:https?://)?(?:www\.)?\S+\.\S+"
    result = bool(re.search(pattern, text))
    if result:
        return {"result": True, "reason": "Link found in output"}
    else:
        return {"result": False, "reason": "No link found in output"}


def contains_valid_link(text, **kwargs):
    """
    Check if the text contains a valid link.

    Args:
        text (str): The text string to check for a valid link.

    Returns:
        dict: A dictionary containing the result of the link check and the reason for the result.
    """
    pattern = r"(?!.*@)(?:https?://)?(?:www\.)?\S+\.\S+"
    link_match = re.search(pattern=pattern, string=text)
    if link_match:
        matched_url = link_match.group()
        if matched_url:
            standardized_url = _standardize_url(matched_url)
            try:
                text = requests.head(standardized_url)
                if text.status_code == 200:
                    return {
                        "result": True,
                        "reason": f"link {matched_url} found in output and is valid",
                    }
                else:
                    return {
                        "result": False,
                        "reason": f"link {matched_url} found in output but is invalid",
                    }
            except:
                return {
                    "result": False,
                    "reason": f"link {matched_url} found in output but is invalid",
                }
    return {"result": False, "reason": "no link found in output"}


def no_invalid_links(text, **kwargs):
    """
    Check for invalid links in the text.

    Args:
        text (str): The text string to check for invalid links.

    Returns:
        dict: A dictionary containing the result of the link check and the reason for the result.
    """
    pattern = r"(?!.*@)(?:https?://)?(?:htp?://)?(?://?)?(?:http?://)?(?:www\.)?\S+\.\S+"
    link_match = re.search(pattern=pattern, string=text)
    if link_match:
        matched_url = link_match.group()
        if matched_url:
            standardized_url = _standardize_url(matched_url)
            try:
                text = requests.head(standardized_url)
                if text.status_code == 200:
                    return {
                        "result": True,
                        "reason": f"link {matched_url} found in output and is valid",
                    }
                else:
                    return {
                        "result": False,
                        "reason": f"link {matched_url} found in output but is invalid",
                    }
            except:
                return {
                    "result": False,
                    "reason": f"link {matched_url} found in output but is invalid",
                }
    return {"result": True, "reason": "no invalid link found in output"}


def api_call(
    url: str,
    response: str,
    query: str | None = None,
    context: str | None = None,
    expected_response: str | None = None,
    payload: dict | None = None,
    headers: dict | None = None,
):
    """
    Make an API call with payload to the specified URL.

    Args:
        url (str): The URL to make the API call to.
        text (str): The text to be added to the payload.
        query (Optional[str]): The query parameter to be added to the payload.
        context (Optional[str]): The context parameter to be added to the payload.
        expected_response (Optional[str]): The expected text parameter to be added to the payload.
        payload (dict, optional): The payload to be sent in the API call. Defaults to None.
        headers (dict, optional): The headers to be included in the API call. Defaults to None.

    Returns:
        dict: A dictionary containing the result and reason of the API call.
    """
    if payload is None:
        payload = {}
    if headers is None:
        headers = {}
    payload["response"] = response
    if query:
        payload["query"] = query
    if context:
        payload["context"] = context
    if expected_response:
        payload["expected_response"] = expected_response
    # Check the status code and set the reason accordingly
    try:
        api_response = requests.post(url, json=payload, headers=headers)
        if api_response.status_code == 200:
            # Success
            result = api_response.json().get("result")
            reason = api_response.json().get("reason")
        elif api_response.status_code == 400:
            # Bad Request
            result = False
            reason = "Bad Request: The server could not understand the request due to invalid syntax."
        elif api_response.status_code == 401:
            # Unauthorized
            result = False
            reason = "Unauthorized: Authentication is required and has failed or has not been provided."
        elif api_response.status_code == 500:
            # Internal Server Error
            result = False
            reason = (
                "Internal Server Error: The server encountered an unexpected condition."
            )
        else:
            # Other error codes
            result = False
            reason = f"An error occurred: {api_response.status_code}"
    except Exception as e:
        # Handle any exceptions that occur during the API call
        result = False
        reason = f"API Request Exception: {e}"

    return {"result": result, "reason": reason}


def equals(expected_text, text, case_sensitive=False, **kwargs):
    """
    Check if the text exactly matches the expected text.

    Args:
        expected_text (str): The expected text to compare against.
        text (str): The text to compare with the expected output.
        case_sensitive (bool, optional): If True, the comparison is case-sensitive. Defaults to False.

    Returns:
        dict: A dictionary containing the result and reason of the comparison.
    """
    if case_sensitive is False:
        text = text.lower()
        expected_text = expected_text.lower()
    if text == expected_text:
        result = True
        reason = "✅ Text exactly matches expected text"
    else:
        result = False
        reason = "output does not exactly match expected text"
    return {"result": result, "reason": reason}


def starts_with(substring, text, case_sensitive=False, **kwargs):
    """
    Check if the text starts with a specified substring.

    Args:
        substring (str): The substring to check for at the start of the text.
        text (str): The text string to check.
        case_sensitive (bool, optional): If True, the comparison is case-sensitive. Defaults to False.

    Returns:
        dict: A dictionary containing the result of the check and the reason for the result.
    """
    if case_sensitive is False:
        text = text.lower()
        substring = substring.lower()
    result = text.startswith(substring)
    if result is True:
        return {"result": result, "reason": "output starts with " + substring}
    else:
        return {"result": result, "reason": "output does not start with " + substring}


def ends_with(substring, text, case_sensitive=False, **kwargs):
    """
    Check if the text ends with a specified substring.

    Args:
        substring (str): The substring to check for at the end of the text.
        text (str): The text string to check.
        case_sensitive (bool, optional): If True, the comparison is case-sensitive. Defaults to False.

    Returns:
        dict: A dictionary containing the result of the check and the reason for the result.
    """
    if case_sensitive is False:
        text = text.lower()
        substring = substring.lower()
    result = text.endswith(substring)
    if result is True:
        return {"result": result, "reason": "output ends with " + substring}
    else:
        return {"result": result, "reason": "output does not end with " + substring}


def length_less_than(max_length, text, **kwargs):
    """
    Check if the length of the text is less than a specified maximum length.

    Args:
        max_length (int): The maximum length that the text should have.
        text (str): The text string to check the length of.

    Returns:
        dict: A dictionary containing the result of the length check and the reason for the result.
    """
    if len(text) < max_length:
        return {
            "result": True,
            "reason": f"output length is less than {max_length} characters",
        }
    else:
        return {
            "result": False,
            "reason": f"output length is greater than {max_length} characters",
        }


def length_greater_than(min_length, text, **kwargs):
    """
    Check if the length of the text is greater than a specified minimum length.

    Args:
        min_length (int): The minimum length that the text should have.
        text (str): The text string to check the length of.

    Returns:
        dict: A dictionary containing the result of the length check and the reason for the result.
    """
    if len(text) > min_length:
        return {
            "result": True,
            "reason": f"output length is greater than {min_length} characters",
        }
    else:
        return {
            "result": False,
            "reason": f"output length is less than {min_length} characters",
        }

def length_between(min_length, max_length, text, **kwargs):
    """
    Check if the length of the text is between a specified minimum and maximum length.

    Args:
        min_length (int): The minimum length that the text should have.
        max_length (int): The maximum length that the text should have.
        text (str): The text string to check the length of.

    Returns:
        dict: A dictionary containing the result of the length check and the reason for the result.
    """
    if min_length <= len(text) <= max_length:
        return {
            "result": True,
            "reason": f"output length is between {min_length} and {max_length} characters",
        }
    else:
        return {
            "result": False,
            "reason": f"output length is not between {min_length} and {max_length} characters",
        }

def one_line(text, **kwargs):
    """
    Check if the text is a single line.

    Args:
        text (str): The text string to check.

    Returns:
        dict: A dictionary containing the result of the check and the reason for the result.
    """
    if "\n" in text or len(text.splitlines()) > 1:
        return {"result": False, "reason": "output contains multiple lines"}
    else:
        return {"result": True, "reason": "output is a single line"}

def json_schema(
    actual_json: dict | str,
    **kwargs
) -> dict[str, Any]:
    """
    Check if the actual_json matched the schema definition.

    Args:
        actual_json (dict or str): The JSON string to check with the schema.
    """
    try:
        # Load the actual JSON data from the input
        actual_json = _load_json(actual_json)

        # Retrieve the schema from the provided keyword arguments
        schema = _get_schema(kwargs)
        if not schema:
            # Return failure if schema is not provided
            return {"result": False, "reason": "Schema not provided"}

        # Validate the actual JSON against the schema
        passed, reason = _validate_json_with_schema(actual_json, schema)
        if not passed:
            # Return failure if validation does not pass
            return {"result": False, "reason": reason}

        # Return success if validation passes
        return {"result": True, "reason": "JSON schema passed"}
    except Exception as e:
        # Log and raise any exceptions that occur during the process
        logger.error(f"Error occurred during JSON schema validation: {e}")
        raise e

def json_validation(
    actual_json: dict | str,
    expected_json: dict | str,
    **kwargs
) -> dict[str, Any]:
    """
    Check if the actual JSON and expected JSON match the validation rules.

    Args:
        actual_json (dict or str): The actual JSON string to compare against the expected JSON.
        expected_json (dict or str): The expected JSON string to compare against the actual JSON.
    """
    try:
        actual_json = _load_json(actual_json)
        expected_json = _load_json(expected_json)

        validations = kwargs.get("validations", [])
        if validations:
            for validation in validations:
                validation_passed, validation_reason = _apply_validation(actual_json, expected_json, validation)
                if not validation_passed:
                    return {"result": False, "reason": validation_reason}

        return {"result": True, "reason": "Json validation passed"}
    except Exception as e:
        logger.error(f"Error occurred during Json validation eval: {e}")
        raise e

def _bandit_check(code: str) -> str | None:
    """
    Run Bandit security check on the provided code.
    """
    with tempfile.NamedTemporaryFile(delete=False, suffix=".py") as temp_file:
        temp_file.write(code.encode('utf-8'))
        temp_file_path = temp_file.name
    try:
        result = subprocess.run(
            ["bandit", "-r", temp_file_path, "-f", "json", "-c", "bandit.yml"],
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            return json.dumps(result.stdout)
    finally:
        os.remove(temp_file_path)
    return None

def custom_code_eval(code, language=None, **kwargs):
    """
    Run custom code provided by the user in a sandboxed environment.

    Python code is executed via RestrictedPython with process-level isolation.
    JavaScript code is executed via Node.js subprocess with timeout.

    The user function can return:
      - bool: True = pass (1.0), False = fail (0.0)
      - float/int: Score between 0 and 1
      - dict: {"result": bool|float, "reason": "explanation"}

    Args:
        code (str): The custom code to run. Must define `evaluate()` or `main()`.
        language (str, optional): 'python' or 'javascript'. Auto-detected if not set.

    Returns:
        dict: {"result": float, "reason": str}
    """
    code_execution = CodeExecution(code=code, language=language)
    result = code_execution.execute(kwargs)

    status = result.get("status")
    if status == "skip":
        # The user's function returned None — treat as a failed/skipped eval
        # with a clear message instead of the misleading "Code eval error: None".
        return {"result": False, "reason": "Code eval: function returned None (no result produced)"}
    if status != "success":
        error_msg = result.get("data", "Unknown error in code eval")
        return {"result": False, "reason": f"Code eval error: {error_msg}"}

    data = result.get("data")

    # Handle dict return: {"result": ..., "reason": ...}
    if isinstance(data, dict):
        if "result" not in data:
            return {"result": False, "reason": "Dict return must include a 'result' key"}
        result_val = data["result"]
        reason = data.get("reason", "Custom code eval completed")
        if isinstance(result_val, bool):
            return {"result": float(result_val), "reason": reason}
        elif isinstance(result_val, (int, float)):
            return {"result": float(min(max(result_val, 0), 1)), "reason": reason}
        else:
            return {"result": float(bool(result_val)), "reason": reason}

    # Handle float/int return (score 0-1)
    if isinstance(data, (int, float)) and not isinstance(data, bool):
        score = float(min(max(data, 0), 1))
        return {"result": score, "reason": f"Custom code eval score: {score}"}

    # Handle bool return
    if isinstance(data, bool):
        return {
            "result": float(data),
            "reason": f"Custom code eval {'passed' if data else 'failed'}",
        }

    # Fallback: truthy check
    return {
        "result": float(bool(data)),
        "reason": f"Custom code eval {'passed' if data else 'failed'}",
    }


def _load_json(json_data: dict | str) -> dict:
    if isinstance(json_data, str):
        return json.loads(json_data)
    return json_data

def _get_schema(kwargs: dict[str, Any]) -> dict | None:
    schema = kwargs.get("schema")
    if schema and isinstance(schema, str):
        return json.loads(schema.replace("\n", "").replace("\t", ""))
    return schema

def _validate_json_with_schema(json_data: dict, schema: dict) -> tuple[bool, str]:
    return validate_json(json_data, schema)

def _apply_validation(actual_json: dict, expected_json: dict, validation: dict) -> tuple[bool, str]:
    validating_function = validation.get("validating_function")
    json_path = validation.get("json_path", "")
    actual_value = extract_json_path(actual_json, json_path)
    expected_value = extract_json_path(expected_json, json_path)

    if validating_function == "Equals":
        return _validate_equals(actual_value, expected_value, validation, json_path or "")
    elif validating_function == "Cosine Similarity":
        return _validate_cosine_similarity(actual_value, expected_value, validation, json_path or "")
    elif validating_function == "LLM Similarity":
        return _validate_llm_similarity(actual_value, expected_value, validation, json_path or "")
    else:
        error_message = f"Validation function {validating_function} not supported"
        logger.error(error_message)
        return False, error_message

def _validate_equals(actual_value: Any, expected_value: Any, validation: dict, json_path: str) -> tuple[bool, str]:
    case_sensitive = validation.get("case_sensitive", False)
    if not case_sensitive and isinstance(actual_value, str) and isinstance(expected_value, str):
        actual_value = str(actual_value).lower()
        expected_value = str(expected_value).lower()
    if actual_value != expected_value:
        error_message = f"JSON path {json_path} does not match expected value"
        logger.error(error_message)
        return False, error_message
    return True, ""

def _validate_cosine_similarity(actual_value: str, expected_value: str, validation: dict, json_path: str) -> tuple[bool, str]:
    threshold = validation.get("pass_threshold", 0.8)
    cosine_similarity = CosineSimilarity().compare(str(actual_value), str(expected_value))
    if cosine_similarity < threshold:
        error_message = f"Cosine similarity score of {round(cosine_similarity, 2)} for {json_path} is less than the threshold ({threshold})."
        logger.error(error_message)
        return False, error_message
    return True, ""

def _validate_llm_similarity(actual_value: str, expected_value: str, validation: dict, json_path: str) -> tuple[bool, str]:
    open_ai_api_key = validation.get("open_ai_api_key") or OpenAiApiKey.get_key() or os.environ.get("OPENAI_API_KEY")
    if not open_ai_api_key:
        raise NoOpenAiApiKeyException()

    OpenAiApiKey.set_key(open_ai_api_key)
    llm_service = OpenAiService(openai_api_key=open_ai_api_key)
    messages = _get_messages(validation, actual_value, expected_value)

    response = llm_service.json_completion(
        model=validation.get("model", "gpt-3.5-turbo"),
        messages=messages,
        temperature=0.0,
    )

    try:
        result = response["result"]
        explanation = response["explanation"]
        if bool(str(result).lower() == "fail"):
            error_message = f"LLM Similarity validation failed for {json_path}. Reason: {explanation}"
            logger.error(error_message)
            return False, error_message
        return True, ""
    except Exception:
        error_message = f"Error occurred during LLM similarity validation for {json_path}"
        logger.error(error_message)
        return False, error_message

def _get_messages(validation: dict, actual_value: Any, expected_value: Any) -> list:
    if validation.get("system_message") and validation.get("user_message"):
        env = Environment(
            variable_start_string='{{',
            variable_end_string='}}',
            undefined=PreserveUndefined
        )
        render_context = {"actual": actual_value, "expected": expected_value}
        system_message = env.from_string(validation.get("system_message")).render(render_context)
        user_message = env.from_string(validation.get("user_message")).render(render_context)
        return [
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_message},
        ]
    else:
        # Default messages
        system_message = """
        You are an expert at evaluating whether two given strings are similar or not. Consider semantic similarity also while evaluating.
        You MUST return a JSON object with the following fields:
        - result: Result must be either 'Pass' or 'Fail'.
        - explanation: An explanation of why the result is Pass or Fail.
        - score: Any matching score you have used to come to the result.
        """

        user_message = f"""
        Following are two strings:
        1. String 1: {actual_value}.
        2. String 2: {expected_value}.
        """

        return [
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_message},
        ]


def calculate_meteor(reference, hypothesis, **kwargs):
    """
    Compute METEOR score between reference and hypothesis.
    METEOR uses unigram matching with exact, stem, and synonym matching.

    Pure-Python implementation: exact match + basic Porter-style stemming.
    Score = F_mean * (1 - penalty), where penalty penalizes fragmentation.

    Args:
        reference (str): The reference text.
        hypothesis (str): The hypothesis/generated text.

    Returns:
        dict: {"result": float, "reason": str}
    """
    import re as _re

    def _simple_stem(word):
        """Basic suffix-stripping stemmer."""
        for suffix in ["ingly", "edly", "tion", "sion", "ment", "ness", "able", "ible",
                        "ing", "ous", "ful", "ive", "ize", "ise", "ent", "ant",
                        "ly", "ed", "er", "es", "al", "en", "s"]:
            if len(word) > len(suffix) + 2 and word.endswith(suffix):
                return word[: -len(suffix)]
        return word

    ref_str = str(reference).lower().strip()
    hyp_str = str(hypothesis).lower().strip()
    if not ref_str or not hyp_str:
        return {"result": 0.0, "reason": "Missing reference or hypothesis"}

    ref_tokens = ref_str.split()
    hyp_tokens = hyp_str.split()
    if not ref_tokens or not hyp_tokens:
        return {"result": 0.0, "reason": "Empty tokens after split"}

    # Stage 1: exact matches
    ref_matched = [False] * len(ref_tokens)
    hyp_matched = [False] * len(hyp_tokens)
    for i, ht in enumerate(hyp_tokens):
        for j, rt in enumerate(ref_tokens):
            if not ref_matched[j] and ht == rt:
                hyp_matched[i] = True
                ref_matched[j] = True
                break

    # Stage 2: stem matches (unmatched only)
    ref_stems = [_simple_stem(t) for t in ref_tokens]
    hyp_stems = [_simple_stem(t) for t in hyp_tokens]
    for i, hs in enumerate(hyp_stems):
        if hyp_matched[i]:
            continue
        for j, rs in enumerate(ref_stems):
            if not ref_matched[j] and hs == rs:
                hyp_matched[i] = True
                ref_matched[j] = True
                break

    matches = sum(hyp_matched)
    if matches == 0:
        return {"result": 0.0, "reason": "METEOR: 0.0 (no matches)"}

    precision = matches / len(hyp_tokens)
    recall = matches / len(ref_tokens)
    alpha = 0.9  # METEOR default: recall-weighted
    f_mean = (precision * recall) / (alpha * precision + (1 - alpha) * recall)

    # Fragmentation penalty: count chunks (contiguous matched sequences)
    chunks = 0
    prev_matched = False
    for m in hyp_matched:
        if m and not prev_matched:
            chunks += 1
        prev_matched = m

    penalty = 0.5 * (chunks / matches) ** 3 if matches > 0 else 0
    score = f_mean * (1 - penalty)
    score = max(0.0, min(1.0, score))
    return {"result": score, "reason": f"METEOR: {score:.4f} (P={precision:.3f}, R={recall:.3f}, chunks={chunks})"}


def calculate_gleu(reference, hypothesis, **kwargs):
    """
    Compute Google BLEU (GLEU) score.
    GLEU = min(precision, recall) over all n-grams (1-4).
    More balanced than BLEU for sentence-level evaluation.

    Args:
        reference (str): The reference text.
        hypothesis (str): The hypothesis/generated text.

    Returns:
        dict: {"result": float, "reason": str}
    """
    import math
    from collections import Counter

    ref_str = str(reference).lower().strip()
    hyp_str = str(hypothesis).lower().strip()
    if not ref_str or not hyp_str:
        return {"result": 0.0, "reason": "Missing reference or hypothesis"}

    ref_tokens = ref_str.split()
    hyp_tokens = hyp_str.split()
    if not ref_tokens or not hyp_tokens:
        return {"result": 0.0, "reason": "Empty tokens"}

    def _ngrams(tokens, n):
        return [tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)]

    max_n = min(4, len(ref_tokens), len(hyp_tokens))
    if max_n == 0:
        return {"result": 0.0, "reason": "GLEU: 0.0 (tokens too short for n-grams)"}

    log_gleu_prec = 0.0
    for n in range(1, max_n + 1):
        ref_ng = Counter(_ngrams(ref_tokens, n))
        hyp_ng = Counter(_ngrams(hyp_tokens, n))
        total_ref = sum(ref_ng.values())
        total_hyp = sum(hyp_ng.values())
        if total_hyp == 0 or total_ref == 0:
            return {"result": 0.0, "reason": f"GLEU: 0.0 (no {n}-grams)"}
        clipped = sum(min(hyp_ng[ng], ref_ng[ng]) for ng in hyp_ng)
        precision = clipped / total_hyp
        recall = clipped / total_ref
        gleu_n = min(precision, recall)
        if gleu_n == 0:
            return {"result": 0.0, "reason": f"GLEU: 0.0 (zero {n}-gram overlap)"}
        log_gleu_prec += math.log(gleu_n)

    score = math.exp(log_gleu_prec / max_n)
    score = max(0.0, min(1.0, score))
    return {"result": score, "reason": f"GLEU: {score:.4f}"}


def calculate_chrf(reference, hypothesis, n=6, beta=2.0, **kwargs):
    """
    Compute ChrF score (character n-gram F-score).
    More robust than BLEU for morphologically rich languages.

    Args:
        reference (str): The reference text.
        hypothesis (str): The hypothesis/generated text.
        n (int): Maximum character n-gram order (default 6).
        beta (float): Recall weight (default 2.0, recall twice as important as precision).

    Returns:
        dict: {"result": float, "reason": str}
    """
    from collections import Counter

    ref_str = str(reference).strip()
    hyp_str = str(hypothesis).strip()
    if not ref_str or not hyp_str:
        return {"result": 0.0, "reason": "Missing reference or hypothesis"}

    def _char_ngrams(text, order):
        return Counter(text[i : i + order] for i in range(len(text) - order + 1))

    total_precision = 0.0
    total_recall = 0.0
    count = 0

    for order in range(1, n + 1):
        ref_ng = _char_ngrams(ref_str, order)
        hyp_ng = _char_ngrams(hyp_str, order)
        total_ref = sum(ref_ng.values())
        total_hyp = sum(hyp_ng.values())
        if total_hyp == 0 or total_ref == 0:
            continue
        clipped = sum(min(hyp_ng[ng], ref_ng[ng]) for ng in hyp_ng)
        total_precision += clipped / total_hyp
        total_recall += clipped / total_ref
        count += 1

    if count == 0:
        return {"result": 0.0, "reason": "ChrF: 0.0 (no character n-grams)"}

    avg_prec = total_precision / count
    avg_rec = total_recall / count

    if avg_prec + avg_rec == 0:
        return {"result": 0.0, "reason": "ChrF: 0.0"}

    beta_sq = beta ** 2
    score = (1 + beta_sq) * avg_prec * avg_rec / (beta_sq * avg_prec + avg_rec)
    score = max(0.0, min(1.0, score))
    return {"result": score, "reason": f"ChrF{n}: {score:.4f} (P={avg_prec:.3f}, R={avg_rec:.3f})"}


def calculate_f1_score(output, expected, case_insensitive=True, **kwargs):
    """
    Compute token-level F1 score between output and expected text.
    Treats both texts as bags of tokens and computes precision, recall, and F1.

    Args:
        output (str): The generated text.
        expected (str): The expected/reference text.
        case_insensitive (bool): Whether to lowercase before comparing.

    Returns:
        dict: {"result": float, "reason": str}
    """
    out_str = str(output).strip()
    exp_str = str(expected).strip()
    if case_insensitive:
        out_str = out_str.lower()
        exp_str = exp_str.lower()

    out_tokens = out_str.split()
    exp_tokens = exp_str.split()

    if not out_tokens and not exp_tokens:
        return {"result": 1.0, "reason": "F1: 1.0 (both empty)"}
    if not out_tokens or not exp_tokens:
        return {"result": 0.0, "reason": "F1: 0.0 (one side is empty)"}

    from collections import Counter
    out_counts = Counter(out_tokens)
    exp_counts = Counter(exp_tokens)
    overlap = sum((out_counts & exp_counts).values())

    if overlap == 0:
        return {"result": 0.0, "reason": "F1: 0.0 (no token overlap)"}

    precision = overlap / len(out_tokens)
    recall = overlap / len(exp_tokens)
    f1 = 2 * precision * recall / (precision + recall)
    return {"result": f1, "reason": f"F1: {f1:.4f} (P={precision:.3f}, R={recall:.3f})"}


def calculate_jaccard_similarity(output, expected, case_insensitive=True, **kwargs):
    """
    Compute Jaccard similarity between two texts.
    Jaccard = |intersection| / |union| of token sets.

    Args:
        output (str): The generated text.
        expected (str): The expected/reference text.
        case_insensitive (bool): Whether to lowercase before comparing.

    Returns:
        dict: {"result": float, "reason": str}
    """
    out_str = str(output).strip()
    exp_str = str(expected).strip()
    if case_insensitive:
        out_str = out_str.lower()
        exp_str = exp_str.lower()

    out_set = set(out_str.split())
    exp_set = set(exp_str.split())

    if not out_set and not exp_set:
        return {"result": 1.0, "reason": "Jaccard: 1.0 (both empty)"}

    union = out_set | exp_set
    intersection = out_set & exp_set

    if not union:
        return {"result": 0.0, "reason": "Jaccard: 0.0"}

    score = len(intersection) / len(union)
    return {"result": score, "reason": f"Jaccard: {score:.4f} (|intersection|={len(intersection)}, |union|={len(union)})"}


def calculate_jaro_winkler_similarity(output, expected, case_insensitive=True, prefix_weight=0.1, **kwargs):
    """
    Compute Jaro-Winkler similarity between two strings.
    Particularly effective for short strings (names, labels).

    Args:
        output (str): The generated text.
        expected (str): The expected/reference text.
        case_insensitive (bool): Whether to lowercase before comparing.
        prefix_weight (float): Winkler prefix scaling factor (default 0.1, max 0.25).

    Returns:
        dict: {"result": float, "reason": str}
    """
    s1 = str(output).strip()
    s2 = str(expected).strip()
    if case_insensitive:
        s1 = s1.lower()
        s2 = s2.lower()

    if s1 == s2:
        return {"result": 1.0, "reason": "Jaro-Winkler: 1.0 (exact match)"}
    if not s1 or not s2:
        return {"result": 0.0, "reason": "Jaro-Winkler: 0.0 (empty string)"}

    # Jaro distance
    len1, len2 = len(s1), len(s2)
    match_distance = max(len1, len2) // 2 - 1
    if match_distance < 0:
        match_distance = 0

    s1_matches = [False] * len1
    s2_matches = [False] * len2
    matches = 0
    transpositions = 0

    for i in range(len1):
        start = max(0, i - match_distance)
        end = min(i + match_distance + 1, len2)
        for j in range(start, end):
            if s2_matches[j] or s1[i] != s2[j]:
                continue
            s1_matches[i] = True
            s2_matches[j] = True
            matches += 1
            break

    if matches == 0:
        return {"result": 0.0, "reason": "Jaro-Winkler: 0.0 (no matching characters)"}

    k = 0
    for i in range(len1):
        if not s1_matches[i]:
            continue
        while not s2_matches[k]:
            k += 1
        if s1[i] != s2[k]:
            transpositions += 1
        k += 1

    jaro = (matches / len1 + matches / len2 + (matches - transpositions / 2) / matches) / 3

    # Winkler modification: boost for common prefix
    prefix = 0
    for i in range(min(4, len1, len2)):
        if s1[i] == s2[i]:
            prefix += 1
        else:
            break

    pw = min(prefix_weight, 0.25)
    score = jaro + prefix * pw * (1 - jaro)
    score = max(0.0, min(1.0, score))
    return {"result": score, "reason": f"Jaro-Winkler: {score:.4f} (Jaro={jaro:.4f}, prefix={prefix})"}


def calculate_hamming_similarity(output, expected, case_insensitive=True, **kwargs):
    """
    Compute Hamming similarity between two strings.
    Counts matching character positions, normalized by the longer string.
    Pads shorter string with nulls for unequal lengths.

    Args:
        output (str): The generated text.
        expected (str): The expected/reference text.
        case_insensitive (bool): Whether to lowercase before comparing.

    Returns:
        dict: {"result": float, "reason": str}
    """
    s1 = str(output).strip()
    s2 = str(expected).strip()
    if case_insensitive:
        s1 = s1.lower()
        s2 = s2.lower()

    if not s1 and not s2:
        return {"result": 1.0, "reason": "Hamming: 1.0 (both empty)"}

    max_len = max(len(s1), len(s2))
    s1_padded = s1.ljust(max_len, '\0')
    s2_padded = s2.ljust(max_len, '\0')

    mismatches = sum(c1 != c2 for c1, c2 in zip(s1_padded, s2_padded))
    similarity = 1.0 - (mismatches / max_len)
    return {"result": similarity, "reason": f"Hamming: {similarity:.4f} ({mismatches} mismatches out of {max_len} chars)"}


def calculate_fuzzy_match(output, expected, case_insensitive=True, **kwargs):
    """
    Compute fuzzy string matching score using SequenceMatcher.
    Returns a similarity ratio between 0 and 1.

    Args:
        output (str): The generated text.
        expected (str): The expected/reference text.
        case_insensitive (bool): Whether to lowercase before comparing.

    Returns:
        dict: {"result": float, "reason": str}
    """
    from difflib import SequenceMatcher

    s1 = str(output).strip()
    s2 = str(expected).strip()
    if case_insensitive:
        s1 = s1.lower()
        s2 = s2.lower()

    if not s1 and not s2:
        return {"result": 1.0, "reason": "Fuzzy Match: 1.0 (both empty)"}

    ratio = SequenceMatcher(None, s1, s2).ratio()
    return {"result": ratio, "reason": f"Fuzzy Match: {ratio:.4f}"}


def is_xml(text, **kwargs):
    """
    Validate if text is well-formed XML.

    Args:
        text (str): The text to validate.

    Returns:
        dict: {"result": bool, "reason": str}
    """
    import xml.etree.ElementTree as ET

    text = str(text).strip()
    if not text:
        return {"result": False, "reason": "Empty text is not valid XML"}
    try:
        ET.fromstring(text)
        return {"result": True, "reason": "Text is valid XML"}
    except ET.ParseError as e:
        return {"result": False, "reason": f"Text is not valid XML: {e}"}


def is_sql(text, **kwargs):
    """
    Validate if text appears to be syntactically valid SQL.
    Uses basic structural validation: SQL keywords, balanced parentheses,
    and no obvious syntax errors.

    Args:
        text (str): The text to validate.

    Returns:
        dict: {"result": bool, "reason": str}
    """
    text = str(text).strip()
    if not text:
        return {"result": False, "reason": "Empty text is not valid SQL"}

    sql_upper = text.upper().strip().rstrip(";").strip()

    # Check for common SQL statement starters
    sql_starters = [
        "SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP",
        "WITH", "EXPLAIN", "MERGE", "REPLACE", "TRUNCATE", "GRANT", "REVOKE",
        "BEGIN", "COMMIT", "ROLLBACK", "SET", "SHOW", "DESCRIBE", "USE",
    ]
    starts_with_sql = any(sql_upper.startswith(kw) for kw in sql_starters)
    if not starts_with_sql:
        return {"result": False, "reason": "Text does not start with a recognized SQL keyword"}

    # Check balanced parentheses
    depth = 0
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if depth < 0:
            return {"result": False, "reason": "Unbalanced parentheses in SQL (extra closing)"}
    if depth != 0:
        return {"result": False, "reason": "Unbalanced parentheses in SQL (unclosed)"}

    # Check balanced quotes
    for quote_char in ["'", '"']:
        count = text.count(quote_char) - text.count(f"\\{quote_char}")
        if count % 2 != 0:
            return {"result": False, "reason": f"Unbalanced {quote_char} quotes in SQL"}

    return {"result": True, "reason": "Text appears to be valid SQL"}


def is_url(text, **kwargs):
    """
    Validate if text is a properly formatted URL.

    Args:
        text (str): The text to validate.

    Returns:
        dict: {"result": bool, "reason": str}
    """
    from urllib.parse import urlparse

    text = str(text).strip()
    if not text:
        return {"result": False, "reason": "Empty text is not a valid URL"}

    try:
        parsed = urlparse(text)
        if parsed.scheme in ("http", "https", "ftp", "ftps", "ssh", "mailto") and parsed.netloc:
            return {"result": True, "reason": f"Valid URL with scheme={parsed.scheme}"}
        elif parsed.scheme and parsed.netloc:
            return {"result": True, "reason": f"Valid URL with scheme={parsed.scheme}"}
        else:
            return {"result": False, "reason": "URL missing scheme or netloc"}
    except Exception as e:
        return {"result": False, "reason": f"Not a valid URL: {e}"}


def word_count_in_range(text, min_words=None, max_words=None, **kwargs):
    """
    Check if the word count of text falls within a specified range.

    Args:
        text (str): The text to check.
        min_words (int, optional): Minimum word count (inclusive).
        max_words (int, optional): Maximum word count (inclusive).

    Returns:
        dict: {"result": bool, "reason": str}
    """
    text = str(text).strip()
    words = text.split()
    count = len(words)

    if min_words is not None and max_words is not None:
        min_w = int(min_words)
        max_w = int(max_words)
        if min_w <= count <= max_w:
            return {"result": True, "reason": f"Word count {count} is within range [{min_w}, {max_w}]"}
        else:
            return {"result": False, "reason": f"Word count {count} is outside range [{min_w}, {max_w}]"}
    elif min_words is not None:
        min_w = int(min_words)
        if count >= min_w:
            return {"result": True, "reason": f"Word count {count} >= {min_w}"}
        else:
            return {"result": False, "reason": f"Word count {count} < {min_w}"}
    elif max_words is not None:
        max_w = int(max_words)
        if count <= max_w:
            return {"result": True, "reason": f"Word count {count} <= {max_w}"}
        else:
            return {"result": False, "reason": f"Word count {count} > {max_w}"}
    else:
        return {"result": True, "reason": f"Word count: {count} (no constraints specified)"}


def calculate_readability_score(text, **kwargs):
    """
    Compute Flesch-Kincaid readability scores.
    Returns a normalized score (0-1) based on Flesch Reading Ease (0-100 scale mapped to 0-1).

    Args:
        text (str): The text to evaluate.

    Returns:
        dict: {"result": float, "reason": str}
    """
    import re as _re

    text = str(text).strip()
    if not text:
        return {"result": 0.0, "reason": "Empty text"}

    # Count sentences
    sentences = _re.split(r'[.!?]+', text)
    sentences = [s.strip() for s in sentences if s.strip()]
    num_sentences = max(len(sentences), 1)

    # Count words
    words = text.split()
    num_words = len(words)
    if num_words == 0:
        return {"result": 0.0, "reason": "No words found"}

    # Count syllables (approximate)
    def _count_syllables(word):
        word = word.lower().strip(".,!?;:'\"()-")
        if not word:
            return 1
        count = 0
        vowels = "aeiouy"
        prev_vowel = False
        for char in word:
            is_vowel = char in vowels
            if is_vowel and not prev_vowel:
                count += 1
            prev_vowel = is_vowel
        if word.endswith("e") and count > 1:
            count -= 1
        return max(count, 1)

    total_syllables = sum(_count_syllables(w) for w in words)

    # Flesch Reading Ease = 206.835 - 1.015*(words/sentences) - 84.6*(syllables/words)
    fre = 206.835 - 1.015 * (num_words / num_sentences) - 84.6 * (total_syllables / num_words)
    fre = max(0.0, min(100.0, fre))

    # Flesch-Kincaid Grade Level
    fkgl = 0.39 * (num_words / num_sentences) + 11.8 * (total_syllables / num_words) - 15.59
    fkgl = max(0.0, fkgl)

    # Normalize FRE to 0-1 (higher = more readable)
    score = fre / 100.0
    return {
        "result": score,
        "reason": f"Readability: Flesch Reading Ease={fre:.1f}/100, Grade Level={fkgl:.1f} ({num_words} words, {num_sentences} sentences, {total_syllables} syllables)",
    }


def sentence_count(text, min_sentences=None, max_sentences=None, **kwargs):
    """
    Count sentences in text and optionally validate against a range.

    Args:
        text (str): The text to check.
        min_sentences (int, optional): Minimum sentence count.
        max_sentences (int, optional): Maximum sentence count.

    Returns:
        dict: {"result": bool or float, "reason": str}
    """
    import re as _re

    text = str(text).strip()
    if not text:
        count = 0
    else:
        sentences = _re.split(r'(?<=[.!?])\s+', text)
        count = len([s for s in sentences if s.strip()])

    if min_sentences is not None and max_sentences is not None:
        min_s = int(min_sentences)
        max_s = int(max_sentences)
        in_range = min_s <= count <= max_s
        return {
            "result": in_range,
            "reason": f"Sentence count {count} {'is' if in_range else 'is not'} within [{min_s}, {max_s}]",
        }
    elif min_sentences is not None:
        min_s = int(min_sentences)
        passed = count >= min_s
        reason = f"Sentence count {count} >= {min_s}" if passed else f"Sentence count {count} < {min_s}"
        return {"result": passed, "reason": reason}
    elif max_sentences is not None:
        max_s = int(max_sentences)
        passed = count <= max_s
        reason = f"Sentence count {count} <= {max_s}" if passed else f"Sentence count {count} > {max_s}"
        return {"result": passed, "reason": reason}
    else:
        return {"result": True, "reason": f"Sentence count: {count}"}


def tool_call_accuracy(output, expected, **kwargs):
    """
    Evaluate accuracy of tool/function calls by comparing actual vs expected calls.
    Compares function names and arguments.

    Accepts JSON strings or dicts/lists of tool calls in the format:
    [{"name": "func_name", "arguments": {...}}, ...]
    or
    [{"function": {"name": "func_name", "arguments": {...}}}, ...]

    Args:
        output: Actual tool calls (JSON string, dict, or list).
        expected: Expected tool calls (JSON string, dict, or list).

    Returns:
        dict: {"result": float, "reason": str}
    """
    def _parse_calls(val):
        if val is None:
            return []
        if isinstance(val, str):
            try:
                val = json.loads(val)
            except (json.JSONDecodeError, ValueError):
                return []
        if isinstance(val, dict):
            val = [val]
        if not isinstance(val, list):
            return []

        calls = []
        for item in val:
            if not isinstance(item, dict):
                continue
            # Handle OpenAI format: {"function": {"name": ..., "arguments": ...}}
            if "function" in item and isinstance(item["function"], dict):
                func = item["function"]
                calls.append({
                    "name": func.get("name", ""),
                    "arguments": func.get("arguments", {}),
                })
            else:
                calls.append({
                    "name": item.get("name", ""),
                    "arguments": item.get("arguments", {}),
                })
        return calls

    def _normalize_args(args):
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (json.JSONDecodeError, ValueError):
                pass
        return args

    actual_calls = _parse_calls(output)
    expected_calls = _parse_calls(expected)

    if not expected_calls:
        if not actual_calls:
            return {"result": 1.0, "reason": "No tool calls expected or made"}
        return {"result": 0.0, "reason": f"No tool calls expected but {len(actual_calls)} were made"}

    if not actual_calls:
        return {"result": 0.0, "reason": f"{len(expected_calls)} tool calls expected but none were made"}

    # Match actual calls to expected calls (greedy matching)
    matched = 0
    name_matches = 0
    used_expected = set()

    for actual in actual_calls:
        best_match = -1
        best_score = -1
        for j, exp in enumerate(expected_calls):
            if j in used_expected:
                continue
            if actual["name"] == exp["name"]:
                actual_args = _normalize_args(actual.get("arguments", {}))
                exp_args = _normalize_args(exp.get("arguments", {}))
                if actual_args == exp_args:
                    score = 2  # full match
                else:
                    score = 1  # name match only
                if score > best_score:
                    best_score = score
                    best_match = j

        if best_match >= 0:
            used_expected.add(best_match)
            if best_score == 2:
                matched += 1
                name_matches += 1
            else:
                name_matches += 1

    total = max(len(expected_calls), len(actual_calls))
    # Score: full matches count 1.0, name-only matches count 0.5
    partial_score = (matched + (name_matches - matched) * 0.5) / total
    score = max(0.0, min(1.0, partial_score))

    return {
        "result": score,
        "reason": f"Tool Call Accuracy: {score:.3f} ({matched}/{len(expected_calls)} exact matches, {name_matches} name matches, {len(actual_calls)} actual vs {len(expected_calls)} expected)",
    }


def calculate_ssim(output, expected, **kwargs):
    """
    Compute Structural Similarity Index (SSIM) between two images.
    Compares luminance, contrast, and structure.

    Args:
        output: Output image (PIL Image, file path, or base64 string).
        expected: Reference image (same formats).

    Returns:
        dict: {"result": float (0-1), "reason": str}
    """
    try:
        from PIL import Image
        import io
        import base64

        def _load_image(img):
            if isinstance(img, Image.Image):
                return img.convert("L")
            if isinstance(img, str):
                if os.path.isfile(img):
                    return Image.open(img).convert("L")
                try:
                    data = base64.b64decode(img)
                    return Image.open(io.BytesIO(data)).convert("L")
                except Exception:
                    pass
            if isinstance(img, bytes):
                return Image.open(io.BytesIO(img)).convert("L")
            raise ValueError(f"Cannot load image from {type(img)}")

        img1 = _load_image(output)
        img2 = _load_image(expected)

        # Resize to match
        if img1.size != img2.size:
            img2 = img2.resize(img1.size, Image.BILINEAR)

        arr1 = np.array(img1, dtype=np.float64)
        arr2 = np.array(img2, dtype=np.float64)

        C1 = (0.01 * 255) ** 2
        C2 = (0.03 * 255) ** 2

        mu1 = arr1.mean()
        mu2 = arr2.mean()
        sigma1_sq = arr1.var()
        sigma2_sq = arr2.var()
        sigma12 = ((arr1 - mu1) * (arr2 - mu2)).mean()

        ssim_val = ((2 * mu1 * mu2 + C1) * (2 * sigma12 + C2)) / (
            (mu1 ** 2 + mu2 ** 2 + C1) * (sigma1_sq + sigma2_sq + C2)
        )
        ssim_val = max(0.0, min(1.0, float(ssim_val)))
        return {"result": ssim_val, "reason": f"SSIM: {ssim_val:.4f}"}
    except Exception as e:
        return {"result": 0.0, "reason": f"SSIM error: {e}"}


def calculate_psnr(output, expected, **kwargs):
    """
    Compute Peak Signal-to-Noise Ratio (PSNR) between two images.
    Higher values indicate more similar images. Returns normalized 0-1 score.

    Args:
        output: Output image (PIL Image, file path, or base64 string).
        expected: Reference image (same formats).

    Returns:
        dict: {"result": float (0-1), "reason": str}
    """
    try:
        from PIL import Image
        import io
        import base64

        def _load_image(img):
            if isinstance(img, Image.Image):
                return img.convert("RGB")
            if isinstance(img, str):
                if os.path.isfile(img):
                    return Image.open(img).convert("RGB")
                try:
                    data = base64.b64decode(img)
                    return Image.open(io.BytesIO(data)).convert("RGB")
                except Exception:
                    pass
            if isinstance(img, bytes):
                return Image.open(io.BytesIO(img)).convert("RGB")
            raise ValueError(f"Cannot load image from {type(img)}")

        img1 = _load_image(output)
        img2 = _load_image(expected)

        if img1.size != img2.size:
            img2 = img2.resize(img1.size, Image.BILINEAR)

        arr1 = np.array(img1, dtype=np.float64)
        arr2 = np.array(img2, dtype=np.float64)

        mse = np.mean((arr1 - arr2) ** 2)
        if mse == 0:
            return {"result": 1.0, "reason": "PSNR: inf (identical images), score=1.0"}

        psnr_db = 10 * np.log10(255.0 ** 2 / mse)
        # Normalize: PSNR typically 20-50 dB for decent images. Map to 0-1.
        score = max(0.0, min(1.0, psnr_db / 50.0))
        return {"result": score, "reason": f"PSNR: {psnr_db:.2f} dB, normalized score={score:.4f}"}
    except Exception as e:
        return {"result": 0.0, "reason": f"PSNR error: {e}"}


def image_properties(text, expected_width=None, expected_height=None, max_file_size_kb=None,
                     expected_format=None, min_width=None, min_height=None, **kwargs):
    """
    Validate image properties: dimensions, format, file size.

    Args:
        text: Image file path or base64 string.
        expected_width/height: Exact expected dimensions.
        min_width/min_height: Minimum dimensions.
        max_file_size_kb: Maximum file size in KB.
        expected_format: Expected format (JPEG, PNG, etc).

    Returns:
        dict: {"result": bool, "reason": str}
    """
    try:
        from PIL import Image
        import io
        import base64

        img_data = None
        if isinstance(text, str) and os.path.isfile(text):
            file_size = os.path.getsize(text)
            img = Image.open(text)
        else:
            data = base64.b64decode(str(text))
            file_size = len(data)
            img = Image.open(io.BytesIO(data))

        width, height = img.size
        fmt = img.format or "unknown"
        issues = []

        if expected_width is not None and width != int(expected_width):
            issues.append(f"width {width} != expected {expected_width}")
        if expected_height is not None and height != int(expected_height):
            issues.append(f"height {height} != expected {expected_height}")
        if min_width is not None and width < int(min_width):
            issues.append(f"width {width} < min {min_width}")
        if min_height is not None and height < int(min_height):
            issues.append(f"height {height} < min {min_height}")
        if max_file_size_kb is not None and file_size > int(max_file_size_kb) * 1024:
            issues.append(f"file size {file_size/1024:.1f}KB > max {max_file_size_kb}KB")
        if expected_format is not None and fmt.upper() != str(expected_format).upper():
            issues.append(f"format {fmt} != expected {expected_format}")

        if issues:
            return {"result": False, "reason": f"Image validation failed: {'; '.join(issues)}"}
        return {"result": True, "reason": f"Image OK: {width}x{height}, {fmt}, {file_size/1024:.1f}KB"}
    except Exception as e:
        return {"result": False, "reason": f"Image properties error: {e}"}


def _levenshtein_distance_list(ref, hyp):
    """Compute Levenshtein edit distance between two lists of tokens."""
    n, m = len(ref), len(hyp)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dp[i][0] = i
    for j in range(m + 1):
        dp[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = 0 if ref[i - 1] == hyp[j - 1] else 1
            dp[i][j] = min(dp[i - 1][j] + 1, dp[i][j - 1] + 1, dp[i - 1][j - 1] + cost)
    return dp[n][m]


def calculate_word_error_rate(reference, hypothesis, **kwargs):
    """
    Compute Word Error Rate (WER) for ASR/STT evaluation.
    WER = (S + D + I) / N where S=substitutions, D=deletions, I=insertions, N=reference words.
    Returns 1-WER as score (higher=better).

    Args:
        reference (str): The reference/ground truth transcription.
        hypothesis (str): The ASR/STT output transcription.

    Returns:
        dict: {"result": float (0-1), "reason": str}
    """
    ref_words = str(reference).strip().lower().split()
    hyp_words = str(hypothesis).strip().lower().split()

    if not ref_words and not hyp_words:
        return {"result": 1.0, "reason": "WER: 0.0% (both empty)"}
    if not ref_words:
        return {"result": 0.0, "reason": f"WER: 100%+ ({len(hyp_words)} insertions, empty reference)"}

    distance = _levenshtein_distance_list(ref_words, hyp_words)
    wer = distance / len(ref_words)
    score = max(0.0, 1.0 - wer)
    return {"result": score, "reason": f"WER: {wer*100:.1f}% ({distance} edits / {len(ref_words)} ref words), score={score:.4f}"}


def calculate_character_error_rate(reference, hypothesis, **kwargs):
    """
    Compute Character Error Rate (CER) for ASR/OCR evaluation.
    CER = edit_distance(ref_chars, hyp_chars) / len(ref_chars).
    Returns 1-CER as score (higher=better).

    Args:
        reference (str): The reference/ground truth text.
        hypothesis (str): The output text.

    Returns:
        dict: {"result": float (0-1), "reason": str}
    """
    ref_chars = list(str(reference).strip().lower())
    hyp_chars = list(str(hypothesis).strip().lower())

    if not ref_chars and not hyp_chars:
        return {"result": 1.0, "reason": "CER: 0.0% (both empty)"}
    if not ref_chars:
        return {"result": 0.0, "reason": f"CER: 100%+ ({len(hyp_chars)} insertions, empty reference)"}

    distance = Levenshtein.distance("".join(ref_chars), "".join(hyp_chars))
    cer = distance / len(ref_chars)
    score = max(0.0, 1.0 - cer)
    return {"result": score, "reason": f"CER: {cer*100:.1f}% ({distance} edits / {len(ref_chars)} ref chars), score={score:.4f}"}


def syntax_validation(text, language="python", **kwargs):
    """
    Validate code syntax without executing it.
    Supports Python (via ast.parse) and JSON.

    Args:
        text (str): The code to validate.
        language (str): Language to validate ('python', 'json').

    Returns:
        dict: {"result": bool, "reason": str}
    """
    import ast

    text = str(text).strip()
    if not text:
        return {"result": False, "reason": "Empty code"}

    lang = str(language).lower()

    if lang == "python":
        try:
            ast.parse(text)
            return {"result": True, "reason": "Valid Python syntax"}
        except SyntaxError as e:
            return {"result": False, "reason": f"Python syntax error at line {e.lineno}: {e.msg}"}
    elif lang == "json":
        try:
            json.loads(text)
            return {"result": True, "reason": "Valid JSON syntax"}
        except json.JSONDecodeError as e:
            return {"result": False, "reason": f"JSON syntax error: {e}"}
    elif lang == "javascript" or lang == "js":
        # Basic JS syntax checks
        depth_paren = 0
        depth_brace = 0
        depth_bracket = 0
        for ch in text:
            if ch == "(": depth_paren += 1
            elif ch == ")": depth_paren -= 1
            elif ch == "{": depth_brace += 1
            elif ch == "}": depth_brace -= 1
            elif ch == "[": depth_bracket += 1
            elif ch == "]": depth_bracket -= 1
            if depth_paren < 0 or depth_brace < 0 or depth_bracket < 0:
                return {"result": False, "reason": "Unbalanced brackets in JavaScript"}
        if depth_paren != 0 or depth_brace != 0 or depth_bracket != 0:
            return {"result": False, "reason": "Unbalanced brackets in JavaScript"}
        return {"result": True, "reason": "JavaScript basic syntax checks passed"}
    else:
        return {"result": False, "reason": f"Unsupported language: {language}"}


def code_complexity(text, **kwargs):
    """
    Compute cyclomatic complexity of Python code using AST analysis.
    Counts decision points (if, elif, for, while, except, with, and, or, assert).
    Returns normalized score (lower complexity = higher score).

    Args:
        text (str): Python code to analyze.

    Returns:
        dict: {"result": float (0-1), "reason": str}
    """
    import ast

    text = str(text).strip()
    if not text:
        return {"result": 0.0, "reason": "Empty code"}

    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        return {"result": 0.0, "reason": f"Cannot parse code: {e}"}

    complexity = 1  # Base complexity
    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.IfExp)):
            complexity += 1
        elif isinstance(node, ast.For):
            complexity += 1
        elif isinstance(node, ast.While):
            complexity += 1
        elif isinstance(node, ast.ExceptHandler):
            complexity += 1
        elif isinstance(node, ast.With):
            complexity += 1
        elif isinstance(node, ast.Assert):
            complexity += 1
        elif isinstance(node, ast.BoolOp):
            complexity += len(node.values) - 1

    # Normalize: complexity 1-5 = excellent (1.0-0.8), 6-10 = good (0.7-0.5),
    # 11-20 = moderate (0.4-0.2), 20+ = high (0.1-0.0)
    if complexity <= 5:
        score = 1.0 - (complexity - 1) * 0.05
    elif complexity <= 10:
        score = 0.75 - (complexity - 6) * 0.05
    elif complexity <= 20:
        score = 0.5 - (complexity - 11) * 0.03
    else:
        score = max(0.0, 0.2 - (complexity - 21) * 0.01)

    return {"result": score, "reason": f"Cyclomatic complexity: {complexity}, score={score:.3f}"}


def calculate_code_bleu(reference, hypothesis, **kwargs):
    """
    Compute CodeBLEU - a code-aware BLEU variant.
    Combines standard BLEU with keyword matching for code-specific tokens.

    Args:
        reference (str): Reference code.
        hypothesis (str): Generated code.

    Returns:
        dict: {"result": float (0-1), "reason": str}
    """
    import math
    from collections import Counter

    ref_str = str(reference).strip()
    hyp_str = str(hypothesis).strip()
    if not ref_str or not hyp_str:
        return {"result": 0.0, "reason": "Missing reference or hypothesis"}

    ref_tokens = ref_str.split()
    hyp_tokens = hyp_str.split()

    if not hyp_tokens:
        return {"result": 0.0, "reason": "Empty hypothesis"}

    # Standard BLEU component
    def _ngrams(tokens, n):
        return [tuple(tokens[i:i+n]) for i in range(len(tokens) - n + 1)]

    weights = [0.25, 0.25, 0.25, 0.25]
    log_precisions = []
    for n in range(1, 5):
        ref_ng = Counter(_ngrams(ref_tokens, n))
        hyp_ng = Counter(_ngrams(hyp_tokens, n))
        total = sum(hyp_ng.values())
        if total == 0:
            precision = 1e-9
        else:
            clipped = sum(min(hyp_ng[ng], ref_ng.get(ng, 0)) for ng in hyp_ng)
            precision = (clipped + 1) / (total + 1)
        log_precisions.append(math.log(precision))

    bp = 1.0 if len(hyp_tokens) >= len(ref_tokens) else math.exp(1 - len(ref_tokens) / max(len(hyp_tokens), 1))
    bleu = bp * math.exp(sum(w * lp for w, lp in zip(weights, log_precisions)))

    # Keyword match component
    code_keywords = {
        "def", "class", "return", "if", "else", "elif", "for", "while", "try",
        "except", "finally", "with", "import", "from", "as", "in", "not", "and",
        "or", "is", "None", "True", "False", "lambda", "yield", "async", "await",
        "function", "const", "let", "var", "=>", "===", "!==", "typeof", "instanceof",
        "SELECT", "FROM", "WHERE", "JOIN", "INSERT", "UPDATE", "DELETE", "CREATE",
    }
    ref_kw = set(t for t in ref_tokens if t in code_keywords)
    hyp_kw = set(t for t in hyp_tokens if t in code_keywords)
    if ref_kw:
        kw_score = len(ref_kw & hyp_kw) / len(ref_kw)
    else:
        kw_score = 1.0

    # Combined score (weighted average)
    score = 0.7 * bleu + 0.3 * kw_score
    score = max(0.0, min(1.0, score))
    return {"result": score, "reason": f"CodeBLEU: {score:.4f} (BLEU={bleu:.4f}, keyword={kw_score:.4f})"}


def calculate_accuracy(output, expected, **kwargs):
    """
    Compute classification accuracy. Compares predicted labels against expected labels.
    Accepts single values or lists/JSON arrays.

    Args:
        output: Predicted label(s) (string, number, or JSON array).
        expected: Expected label(s) (same formats).

    Returns:
        dict: {"result": float (0-1), "reason": str}
    """
    def _parse(val):
        if val is None:
            return []
        if isinstance(val, list):
            return [str(v) for v in val]
        if isinstance(val, str):
            try:
                parsed = json.loads(val)
                if isinstance(parsed, list):
                    return [str(v) for v in parsed]
            except (json.JSONDecodeError, ValueError):
                pass
            return [val.strip()]
        return [str(val)]

    preds = _parse(output)
    labels = _parse(expected)

    if len(preds) != len(labels):
        return {"result": 0.0, "reason": f"Length mismatch: {len(preds)} predictions vs {len(labels)} labels"}

    if not labels:
        return {"result": 1.0, "reason": "Accuracy: 1.0 (both empty)"}

    correct = sum(1 for p, l in zip(preds, labels) if p.lower() == l.lower())
    accuracy = correct / len(labels)
    return {"result": accuracy, "reason": f"Accuracy: {accuracy:.4f} ({correct}/{len(labels)} correct)"}


def calculate_precision_score(output, expected, positive_label=None, **kwargs):
    """
    Compute precision for binary/multiclass classification.
    Precision = TP / (TP + FP).

    Args:
        output: Predicted label(s).
        expected: Expected label(s).
        positive_label: The label considered as positive (default: auto-detect).

    Returns:
        dict: {"result": float (0-1), "reason": str}
    """
    def _parse(val):
        if val is None:
            return []
        if isinstance(val, list):
            return [str(v).lower() for v in val]
        if isinstance(val, str):
            try:
                parsed = json.loads(val)
                if isinstance(parsed, list):
                    return [str(v).lower() for v in parsed]
            except (json.JSONDecodeError, ValueError):
                pass
            return [val.strip().lower()]
        return [str(val).lower()]

    preds = _parse(output)
    labels = _parse(expected)

    if len(preds) != len(labels) or not labels:
        return {"result": 0.0, "reason": f"Invalid input: {len(preds)} preds vs {len(labels)} labels"}

    if positive_label is not None:
        pos = str(positive_label).lower()
    else:
        unique = set(labels)
        pos = sorted(unique)[0] if unique else ""

    tp = sum(1 for p, l in zip(preds, labels) if p == pos and l == pos)
    fp = sum(1 for p, l in zip(preds, labels) if p == pos and l != pos)

    if tp + fp == 0:
        return {"result": 0.0, "reason": f"Precision: 0.0 (no positive predictions for label '{pos}')"}

    precision = tp / (tp + fp)
    return {"result": precision, "reason": f"Precision: {precision:.4f} (TP={tp}, FP={fp}, positive='{pos}')"}


def calculate_cohen_kappa(output, expected, **kwargs):
    """
    Compute Cohen's Kappa coefficient for inter-rater agreement.
    Accounts for agreement occurring by chance. Range: -1 to 1.
    Normalized to 0-1 for scoring (kappa mapped to (kappa+1)/2).

    Args:
        output: Predicted label(s).
        expected: Expected label(s).

    Returns:
        dict: {"result": float (0-1), "reason": str}
    """
    def _parse(val):
        if val is None:
            return []
        if isinstance(val, list):
            return [str(v).lower() for v in val]
        if isinstance(val, str):
            try:
                parsed = json.loads(val)
                if isinstance(parsed, list):
                    return [str(v).lower() for v in parsed]
            except (json.JSONDecodeError, ValueError):
                pass
            return [val.strip().lower()]
        return [str(val).lower()]

    preds = _parse(output)
    labels = _parse(expected)

    if len(preds) != len(labels) or not labels:
        return {"result": 0.0, "reason": "Invalid input for Cohen's Kappa"}

    n = len(labels)
    categories = sorted(set(labels) | set(preds))
    if len(categories) < 2:
        # Perfect agreement or single class
        observed_agreement = sum(1 for p, l in zip(preds, labels) if p == l) / n
        return {"result": observed_agreement, "reason": f"Cohen's Kappa: N/A (single class), agreement={observed_agreement:.4f}"}

    # Observed agreement
    po = sum(1 for p, l in zip(preds, labels) if p == l) / n

    # Expected agreement
    pe = 0.0
    for cat in categories:
        p_freq = sum(1 for p in preds if p == cat) / n
        l_freq = sum(1 for l in labels if l == cat) / n
        pe += p_freq * l_freq

    if pe >= 1.0:
        kappa = 1.0 if po == 1.0 else 0.0
    else:
        kappa = (po - pe) / (1 - pe)

    # Normalize kappa from [-1, 1] to [0, 1]
    score = (kappa + 1) / 2
    return {"result": score, "reason": f"Cohen's Kappa: {kappa:.4f} (po={po:.4f}, pe={pe:.4f}), normalized={score:.4f}"}


def calculate_matthews_correlation(output, expected, **kwargs):
    """
    Compute Matthews Correlation Coefficient (MCC).
    Balanced metric even for imbalanced classes. Range: -1 to 1.
    Normalized to 0-1 for scoring.

    Args:
        output: Predicted label(s).
        expected: Expected label(s).

    Returns:
        dict: {"result": float (0-1), "reason": str}
    """
    def _parse(val):
        if val is None:
            return []
        if isinstance(val, list):
            return [str(v).lower() for v in val]
        if isinstance(val, str):
            try:
                parsed = json.loads(val)
                if isinstance(parsed, list):
                    return [str(v).lower() for v in parsed]
            except (json.JSONDecodeError, ValueError):
                pass
            return [val.strip().lower()]
        return [str(val).lower()]

    preds = _parse(output)
    labels = _parse(expected)

    if len(preds) != len(labels) or not labels:
        return {"result": 0.0, "reason": "Invalid input for MCC"}

    categories = sorted(set(labels) | set(preds))

    if len(categories) == 2:
        # Binary MCC
        pos = categories[0]
        tp = sum(1 for p, l in zip(preds, labels) if p == pos and l == pos)
        tn = sum(1 for p, l in zip(preds, labels) if p != pos and l != pos)
        fp = sum(1 for p, l in zip(preds, labels) if p == pos and l != pos)
        fn = sum(1 for p, l in zip(preds, labels) if p != pos and l == pos)

        denom = ((tp + fp) * (tp + fn) * (tn + fp) * (tn + fn)) ** 0.5
        if denom == 0:
            mcc = 0.0
        else:
            mcc = (tp * tn - fp * fn) / denom
    else:
        # Multiclass MCC (using confusion matrix)
        n = len(labels)
        correct = sum(1 for p, l in zip(preds, labels) if p == l)
        mcc = (correct / n - 1 / len(categories)) / (1 - 1 / len(categories)) if len(categories) > 1 else 0.0

    # Normalize from [-1, 1] to [0, 1]
    score = (mcc + 1) / 2
    return {"result": score, "reason": f"MCC: {mcc:.4f}, normalized={score:.4f}"}


def json_diff(output, expected, **kwargs):
    """
    Deep structural comparison between two JSON objects.
    Returns a score based on matching keys and values at all levels.

    Args:
        output: Actual JSON (string or dict/list).
        expected: Expected JSON (string or dict/list).

    Returns:
        dict: {"result": float (0-1), "reason": str}
    """
    def _parse_json(val):
        if isinstance(val, str):
            return json.loads(val)
        return val

    def _compare(a, b, path=""):
        matches = 0
        total = 0

        if type(a) != type(b):
            return 0, 1

        if isinstance(a, dict):
            all_keys = set(list(a.keys()) + list(b.keys()))
            for key in all_keys:
                total += 1
                if key in a and key in b:
                    sub_m, sub_t = _compare(a[key], b[key], f"{path}.{key}")
                    matches += sub_m
                    total += sub_t - 1  # Already counted the key
            return matches, max(total, 1)
        elif isinstance(a, list):
            max_len = max(len(a), len(b))
            for i in range(max_len):
                total += 1
                if i < len(a) and i < len(b):
                    sub_m, sub_t = _compare(a[i], b[i], f"{path}[{i}]")
                    matches += sub_m
                    total += sub_t - 1
            return matches, max(total, 1)
        else:
            return (1 if a == b else 0), 1

    try:
        actual = _parse_json(output)
        exp = _parse_json(expected)
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        return {"result": 0.0, "reason": f"JSON parse error: {e}"}

    matches, total = _compare(actual, exp)
    score = matches / total if total > 0 else 1.0
    return {"result": score, "reason": f"JSON Diff: {score:.4f} ({matches}/{total} matching nodes)"}


def is_html(text, **kwargs):
    """
    Validate if text contains well-formed HTML.

    Args:
        text (str): The text to validate.

    Returns:
        dict: {"result": bool, "reason": str}
    """
    from html.parser import HTMLParser

    text = str(text).strip()
    if not text:
        return {"result": False, "reason": "Empty text is not valid HTML"}

    class _HtmlValidator(HTMLParser):
        def __init__(self):
            super().__init__()
            self.errors = []
            self.tag_stack = []
            self.has_tags = False

        def handle_starttag(self, tag, attrs):
            self.has_tags = True
            void_elements = {"area", "base", "br", "col", "embed", "hr", "img", "input",
                             "link", "meta", "param", "source", "track", "wbr"}
            if tag.lower() not in void_elements:
                self.tag_stack.append(tag.lower())

        def handle_endtag(self, tag):
            if self.tag_stack and self.tag_stack[-1] == tag.lower():
                self.tag_stack.pop()

        def handle_data(self, data):
            pass

    try:
        validator = _HtmlValidator()
        validator.feed(text)
        if not validator.has_tags:
            return {"result": False, "reason": "Text contains no HTML tags"}
        if validator.tag_stack:
            return {"result": False, "reason": f"Unclosed HTML tags: {', '.join(validator.tag_stack)}"}
        return {"result": True, "reason": "Text is valid HTML"}
    except Exception as e:
        return {"result": False, "reason": f"HTML parse error: {e}"}


def calculate_translation_edit_rate(reference, hypothesis, **kwargs):
    """
    Compute Translation Edit Rate (TER).
    TER = min edits (insertions, deletions, substitutions, shifts) / reference length.
    Returns 1-TER as score (higher=better).

    Args:
        reference (str): Reference translation.
        hypothesis (str): System translation.

    Returns:
        dict: {"result": float (0-1), "reason": str}
    """
    ref_tokens = str(reference).strip().lower().split()
    hyp_tokens = str(hypothesis).strip().lower().split()

    if not ref_tokens and not hyp_tokens:
        return {"result": 1.0, "reason": "TER: 0.0 (both empty)"}
    if not ref_tokens:
        return {"result": 0.0, "reason": f"TER: {len(hyp_tokens)/1:.1f} (empty reference)"}

    # Standard edit distance
    edit_dist = _levenshtein_distance_list(ref_tokens, hyp_tokens)
    ter = edit_dist / len(ref_tokens)
    score = max(0.0, min(1.0, 1.0 - ter))
    return {"result": score, "reason": f"TER: {ter:.4f} ({edit_dist} edits / {len(ref_tokens)} ref words), score={score:.4f}"}


def trajectory_match(output, expected, mode="strict", **kwargs):
    """
    Validate agent action/tool call sequences.

    Modes:
    - strict: Same actions in same order
    - unordered: Same actions, any order
    - subset: Expected is subset of actual
    - superset: Actual is subset of expected

    Args:
        output: Actual trajectory (JSON list of action names/dicts).
        expected: Expected trajectory (same format).
        mode: Matching mode (strict/unordered/subset/superset).

    Returns:
        dict: {"result": float (0-1), "reason": str}
    """
    def _parse_actions(val):
        if val is None:
            return []
        if isinstance(val, str):
            try:
                val = json.loads(val)
            except (json.JSONDecodeError, ValueError):
                return [v.strip() for v in val.split(",") if v.strip()]
        if isinstance(val, list):
            result = []
            for item in val:
                if isinstance(item, dict):
                    result.append(item.get("name", item.get("action", str(item))))
                else:
                    result.append(str(item))
            return result
        return [str(val)]

    actual = _parse_actions(output)
    exp = _parse_actions(expected)

    if not exp:
        return {"result": 1.0 if not actual else 0.0, "reason": f"No expected actions, {'none made' if not actual else f'{len(actual)} made'}"}

    mode = str(mode).lower()

    if mode == "strict":
        if actual == exp:
            return {"result": 1.0, "reason": f"Trajectory strict match: {len(exp)} actions matched in order"}
        # Partial score based on longest common prefix
        common = 0
        for a, e in zip(actual, exp):
            if a == e:
                common += 1
            else:
                break
        score = common / max(len(exp), len(actual))
        return {"result": score, "reason": f"Trajectory strict: {common}/{len(exp)} prefix match, score={score:.4f}"}

    elif mode == "unordered":
        actual_set = set(actual)
        exp_set = set(exp)
        intersection = actual_set & exp_set
        union = actual_set | exp_set
        score = len(intersection) / len(union) if union else 1.0
        return {"result": score, "reason": f"Trajectory unordered: {len(intersection)}/{len(union)} matched"}

    elif mode == "subset":
        exp_set = set(exp)
        actual_set = set(actual)
        if exp_set.issubset(actual_set):
            return {"result": 1.0, "reason": f"All {len(exp_set)} expected actions found in actual"}
        missing = exp_set - actual_set
        score = 1.0 - len(missing) / len(exp_set)
        return {"result": score, "reason": f"Subset: {len(exp_set) - len(missing)}/{len(exp_set)} expected found, missing: {missing}"}

    elif mode == "superset":
        actual_set = set(actual)
        exp_set = set(exp)
        if actual_set.issubset(exp_set):
            return {"result": 1.0, "reason": f"All {len(actual_set)} actual actions within expected"}
        extra = actual_set - exp_set
        score = 1.0 - len(extra) / len(actual_set)
        return {"result": score, "reason": f"Superset: {len(extra)} unexpected actions: {extra}"}

    return {"result": 0.0, "reason": f"Unknown trajectory match mode: {mode}"}


def step_count(output, min_steps=None, max_steps=None, expected_steps=None, **kwargs):
    """
    Count and validate the number of steps/actions in an agent trajectory.

    Args:
        output: Agent trajectory (JSON list of steps/actions, or comma-separated string).
        min_steps: Minimum acceptable step count.
        max_steps: Maximum acceptable step count.
        expected_steps: Exact expected step count.

    Returns:
        dict: {"result": bool, "reason": str}
    """
    def _parse(val):
        if val is None:
            return []
        if isinstance(val, str):
            try:
                val = json.loads(val)
            except (json.JSONDecodeError, ValueError):
                return [v.strip() for v in val.split(",") if v.strip()]
        if isinstance(val, list):
            return val
        return [val]

    steps = _parse(output)
    count = len(steps)

    if expected_steps is not None:
        expected = int(expected_steps)
        passed = count == expected
        return {"result": passed, "reason": f"Step count {count} {'==' if passed else '!='} expected {expected}"}

    if min_steps is not None and max_steps is not None:
        min_s, max_s = int(min_steps), int(max_steps)
        passed = min_s <= count <= max_s
        return {"result": passed, "reason": f"Step count {count} {'within' if passed else 'outside'} [{min_s}, {max_s}]"}
    elif min_steps is not None:
        min_s = int(min_steps)
        passed = count >= min_s
        reason = f"Step count {count} >= {min_s}" if passed else f"Step count {count} < {min_s}"
        return {"result": passed, "reason": reason}
    elif max_steps is not None:
        max_s = int(max_steps)
        passed = count <= max_s
        reason = f"Step count {count} <= {max_s}" if passed else f"Step count {count} > {max_s}"
        return {"result": passed, "reason": reason}

    return {"result": True, "reason": f"Step count: {count}"}


def regex_pii_detection(text, detect_types=None, **kwargs):
    """
    Detect PII (Personally Identifiable Information) using regex patterns.
    Detects: SSN, credit card numbers, phone numbers, email addresses, IP addresses.

    Args:
        text (str): Text to scan for PII.
        detect_types (list, optional): Types to detect. Default: all.
            Options: ssn, credit_card, phone, email, ip_address

    Returns:
        dict: {"result": bool (True=no PII found), "reason": str}
    """
    text = str(text)
    if not text.strip():
        return {"result": True, "reason": "Empty text, no PII found"}

    patterns = {
        "ssn": (r"\b\d{3}-\d{2}-\d{4}\b", "SSN"),
        "credit_card": (r"\b(?:\d{4}[-\s]?){3}\d{4}\b", "Credit Card"),
        "phone": (r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b", "Phone Number"),
        "email": (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", "Email Address"),
        "ip_address": (r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b", "IP Address"),
    }

    if detect_types:
        if isinstance(detect_types, str):
            try:
                detect_types = json.loads(detect_types)
            except (json.JSONDecodeError, ValueError):
                detect_types = [t.strip() for t in detect_types.split(",")]
        active_patterns = {k: v for k, v in patterns.items() if k in detect_types}
    else:
        active_patterns = patterns

    found = []
    for pii_type, (pattern, label) in active_patterns.items():
        matches = re.findall(pattern, text)
        if matches:
            found.append(f"{label}: {len(matches)} found")

    if found:
        return {"result": False, "reason": f"PII detected: {'; '.join(found)}"}
    return {"result": True, "reason": f"No PII detected (checked: {', '.join(active_patterns.keys())})"}


def _parse_number_list(val):
    """Parse a value into a list of floats."""
    if val is None:
        return []
    if isinstance(val, list):
        return [float(v) for v in val]
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            if isinstance(parsed, list):
                return [float(v) for v in parsed]
        except (json.JSONDecodeError, ValueError):
            pass
        return [float(v.strip()) for v in val.split(",") if v.strip()]
    return [float(val)]


def calculate_pearson_correlation(output, expected, **kwargs):
    """Compute Pearson correlation coefficient between two sets of values."""
    x = _parse_number_list(output)
    y = _parse_number_list(expected)
    if len(x) != len(y) or len(x) < 2:
        return {"result": 0.0, "reason": f"Invalid input: {len(x)} vs {len(y)} values (need >=2)"}
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    ss_x = sum((xi - mx) ** 2 for xi in x)
    ss_y = sum((yi - my) ** 2 for yi in y)
    if ss_x == 0 or ss_y == 0:
        return {"result": 0.0, "reason": "Zero variance in one or both inputs"}
    r = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y)) / (ss_x * ss_y) ** 0.5
    score = (r + 1) / 2  # Normalize from [-1,1] to [0,1]
    return {"result": score, "reason": f"Pearson r={r:.4f}, normalized={score:.4f}"}


def calculate_spearman_correlation(output, expected, **kwargs):
    """Compute Spearman rank correlation coefficient."""
    x = _parse_number_list(output)
    y = _parse_number_list(expected)
    if len(x) != len(y) or len(x) < 2:
        return {"result": 0.0, "reason": f"Invalid input: {len(x)} vs {len(y)} values"}
    n = len(x)

    def _rank(vals):
        indexed = sorted(enumerate(vals), key=lambda t: t[1])
        ranks = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j < n - 1 and indexed[j + 1][1] == indexed[i][1]:
                j += 1
            avg_rank = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                ranks[indexed[k][0]] = avg_rank
            i = j + 1
        return ranks

    rx, ry = _rank(x), _rank(y)
    d_sq = sum((rxi - ryi) ** 2 for rxi, ryi in zip(rx, ry))
    rho = 1 - (6 * d_sq) / (n * (n ** 2 - 1))
    score = (rho + 1) / 2
    return {"result": score, "reason": f"Spearman rho={rho:.4f}, normalized={score:.4f}"}


def calculate_r2_score(output, expected, **kwargs):
    """Compute R-squared (coefficient of determination)."""
    y_pred = _parse_number_list(output)
    y_true = _parse_number_list(expected)
    if len(y_pred) != len(y_true) or len(y_true) < 2:
        return {"result": 0.0, "reason": "Invalid input"}
    mean_true = sum(y_true) / len(y_true)
    ss_res = sum((yt - yp) ** 2 for yt, yp in zip(y_true, y_pred))
    ss_tot = sum((yt - mean_true) ** 2 for yt in y_true)
    if ss_tot == 0:
        return {"result": 1.0 if ss_res == 0 else 0.0, "reason": "Zero variance in target"}
    r2 = 1 - ss_res / ss_tot
    score = max(0.0, min(1.0, (r2 + 1) / 2))  # R2 can be negative, normalize
    return {"result": score, "reason": f"R2={r2:.4f}, normalized={score:.4f}"}


def calculate_rmse(output, expected, **kwargs):
    """Compute Root Mean Squared Error. Returns 1/(1+RMSE) as score."""
    y_pred = _parse_number_list(output)
    y_true = _parse_number_list(expected)
    if len(y_pred) != len(y_true) or not y_true:
        return {"result": 0.0, "reason": "Invalid input"}
    mse = sum((yt - yp) ** 2 for yt, yp in zip(y_true, y_pred)) / len(y_true)
    rmse = mse ** 0.5
    score = 1.0 / (1.0 + rmse)
    return {"result": score, "reason": f"RMSE={rmse:.4f}, score={score:.4f}"}


def calculate_balanced_accuracy(output, expected, **kwargs):
    """Compute balanced accuracy (average recall per class)."""
    def _parse(val):
        if isinstance(val, list):
            return [str(v).lower() for v in val]
        if isinstance(val, str):
            try:
                parsed = json.loads(val)
                if isinstance(parsed, list):
                    return [str(v).lower() for v in parsed]
            except (json.JSONDecodeError, ValueError):
                pass
            return [val.strip().lower()]
        return [str(val).lower()]
    preds = _parse(output)
    labels = _parse(expected)
    if len(preds) != len(labels) or not labels:
        return {"result": 0.0, "reason": "Invalid input"}
    classes = sorted(set(labels))
    recalls = []
    for cls in classes:
        tp = sum(1 for p, l in zip(preds, labels) if l == cls and p == cls)
        total = sum(1 for l in labels if l == cls)
        recalls.append(tp / total if total > 0 else 0.0)
    ba = sum(recalls) / len(recalls) if recalls else 0.0
    return {"result": ba, "reason": f"Balanced Accuracy: {ba:.4f} (avg recall across {len(classes)} classes)"}


def calculate_f_beta_score(output, expected, beta=1.0, positive_label=None, **kwargs):
    """Compute F-beta score with configurable beta."""
    def _parse(val):
        if isinstance(val, list):
            return [str(v).lower() for v in val]
        if isinstance(val, str):
            try:
                parsed = json.loads(val)
                if isinstance(parsed, list):
                    return [str(v).lower() for v in parsed]
            except (json.JSONDecodeError, ValueError):
                pass
            return [val.strip().lower()]
        return [str(val).lower()]
    preds = _parse(output)
    labels = _parse(expected)
    if len(preds) != len(labels) or not labels:
        return {"result": 0.0, "reason": "Invalid input"}
    beta = float(beta)
    pos = str(positive_label).lower() if positive_label else sorted(set(labels))[0]
    tp = sum(1 for p, l in zip(preds, labels) if p == pos and l == pos)
    fp = sum(1 for p, l in zip(preds, labels) if p == pos and l != pos)
    fn = sum(1 for p, l in zip(preds, labels) if p != pos and l == pos)
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    if precision + recall == 0:
        return {"result": 0.0, "reason": f"F{beta}: 0.0 (no TP)"}
    b2 = beta ** 2
    fb = (1 + b2) * precision * recall / (b2 * precision + recall)
    return {"result": fb, "reason": f"F{beta}: {fb:.4f} (P={precision:.4f}, R={recall:.4f})"}


def calculate_log_loss(output, expected, **kwargs):
    """Compute log loss (cross-entropy). Returns 1/(1+logloss) as score."""
    import math
    y_pred = _parse_number_list(output)
    y_true = _parse_number_list(expected)
    if len(y_pred) != len(y_true) or not y_true:
        return {"result": 0.0, "reason": "Invalid input"}
    eps = 1e-15
    loss = -sum(yt * math.log(max(min(yp, 1 - eps), eps)) + (1 - yt) * math.log(max(min(1 - yp, 1 - eps), eps))
               for yt, yp in zip(y_true, y_pred)) / len(y_true)
    score = 1.0 / (1.0 + loss)
    return {"result": score, "reason": f"Log Loss={loss:.4f}, score={score:.4f}"}


def calculate_mean_average_precision(reference, hypothesis, **kwargs):
    """Compute Mean Average Precision for retrieval."""
    reference, hypothesis = _parse_reference_and_hypothesis(reference, hypothesis)
    ground_truth, retrieved = reference, hypothesis
    is_nested_ref = len(ground_truth) > 0 and all(isinstance(i, (list, tuple, set)) for i in ground_truth)
    is_nested_hyp = len(retrieved) > 0 and all(isinstance(i, (list, tuple, set)) for i in retrieved)
    if is_nested_ref and is_nested_hyp:
        if len(ground_truth) != len(retrieved):
            raise ValueError("MAP requires equal number of queries")
        aps = []
        for gt, ret in zip(ground_truth, retrieved):
            gt_set = set(str(x) for x in gt)
            hits = 0
            sum_prec = 0.0
            for rank, item in enumerate(ret, 1):
                if str(item) in gt_set:
                    hits += 1
                    sum_prec += hits / rank
            ap = sum_prec / len(gt_set) if gt_set else 0.0
            aps.append(ap)
        score = sum(aps) / len(aps) if aps else 0.0
        return {"result": score, "reason": f"MAP: {score:.4f} across {len(aps)} queries"}
    # Single query
    gt_set = set(str(x) for x in ground_truth)
    if not gt_set:
        return {"result": 0.0, "reason": "Empty ground truth"}
    hits = 0
    sum_prec = 0.0
    for rank, item in enumerate(retrieved, 1):
        if str(item) in gt_set:
            hits += 1
            sum_prec += hits / rank
    ap = sum_prec / len(gt_set)
    return {"result": ap, "reason": f"AP: {ap:.4f} ({hits} relevant in {len(retrieved)} retrieved)"}


def calculate_squad_score(output, expected, **kwargs):
    """Compute SQuAD-style exact match + token F1 for QA evaluation."""
    import re as _re
    from collections import Counter

    def _normalize(text):
        text = str(text).lower().strip()
        text = _re.sub(r'\b(a|an|the)\b', ' ', text)
        text = _re.sub(r'[^\w\s]', '', text)
        return ' '.join(text.split())

    pred = _normalize(output)
    gold = _normalize(expected)
    em = 1.0 if pred == gold else 0.0
    pred_tokens = pred.split()
    gold_tokens = gold.split()
    if not pred_tokens or not gold_tokens:
        f1 = 1.0 if pred == gold else 0.0
    else:
        common = sum((Counter(pred_tokens) & Counter(gold_tokens)).values())
        if common == 0:
            f1 = 0.0
        else:
            p = common / len(pred_tokens)
            r = common / len(gold_tokens)
            f1 = 2 * p * r / (p + r)
    score = (em + f1) / 2
    return {"result": score, "reason": f"SQuAD: EM={em:.0f}, F1={f1:.4f}, combined={score:.4f}"}


def calculate_match_error_rate(reference, hypothesis, **kwargs):
    """Compute Match Error Rate (MER) for speech. MER = edits / (hits + edits)."""
    ref_words = str(reference).strip().lower().split()
    hyp_words = str(hypothesis).strip().lower().split()
    if not ref_words and not hyp_words:
        return {"result": 1.0, "reason": "MER: 0.0% (both empty)"}
    if not ref_words:
        return {"result": 0.0, "reason": "Empty reference"}
    distance = _levenshtein_distance_list(ref_words, hyp_words)
    hits = len(ref_words) - distance if distance <= len(ref_words) else 0
    mer = distance / (hits + distance) if (hits + distance) > 0 else 1.0
    score = max(0.0, 1.0 - mer)
    return {"result": score, "reason": f"MER: {mer*100:.1f}%, score={score:.4f}"}


def calculate_word_info_lost(reference, hypothesis, **kwargs):
    """Compute Word Information Lost (WIL). WIL = 1 - (hits/ref_len * hits/hyp_len)."""
    ref_words = str(reference).strip().lower().split()
    hyp_words = str(hypothesis).strip().lower().split()
    if not ref_words and not hyp_words:
        return {"result": 1.0, "reason": "WIL: 0.0 (both empty)"}
    if not ref_words or not hyp_words:
        return {"result": 0.0, "reason": "WIL: 1.0 (one side empty)"}
    distance = _levenshtein_distance_list(ref_words, hyp_words)
    hits = max(0, len(ref_words) - distance)
    wil = 1.0 - (hits / len(ref_words)) * (hits / len(hyp_words)) if len(ref_words) > 0 and len(hyp_words) > 0 else 1.0
    score = max(0.0, 1.0 - wil)
    return {"result": score, "reason": f"WIL: {wil:.4f}, score={score:.4f}"}


def calculate_word_info_preserved(reference, hypothesis, **kwargs):
    """Compute Word Information Preserved (WIP). WIP = hits/ref_len * hits/hyp_len."""
    ref_words = str(reference).strip().lower().split()
    hyp_words = str(hypothesis).strip().lower().split()
    if not ref_words and not hyp_words:
        return {"result": 1.0, "reason": "WIP: 1.0 (both empty)"}
    if not ref_words or not hyp_words:
        return {"result": 0.0, "reason": "WIP: 0.0 (one side empty)"}
    distance = _levenshtein_distance_list(ref_words, hyp_words)
    hits = max(0, len(ref_words) - distance)
    wip = (hits / len(ref_words)) * (hits / len(hyp_words))
    return {"result": wip, "reason": f"WIP: {wip:.4f}"}


def non_llm_context_precision(output, expected, **kwargs):
    """Non-LLM context precision: what fraction of retrieved contexts match reference contexts."""
    def _parse(val):
        if isinstance(val, str):
            try:
                return json.loads(val)
            except (json.JSONDecodeError, ValueError):
                return [v.strip() for v in val.split("\n") if v.strip()]
        if isinstance(val, list):
            return val
        return [str(val)]
    retrieved = _parse(output)
    reference = _parse(expected)
    if not retrieved:
        return {"result": 0.0, "reason": "No retrieved contexts"}
    ref_set = set(str(r).lower().strip() for r in reference)
    hits = sum(1 for ctx in retrieved if str(ctx).lower().strip() in ref_set)
    precision = hits / len(retrieved)
    return {"result": precision, "reason": f"Context Precision: {precision:.4f} ({hits}/{len(retrieved)} relevant)"}


def non_llm_context_recall(output, expected, **kwargs):
    """Non-LLM context recall: what fraction of reference contexts were retrieved."""
    def _parse(val):
        if isinstance(val, str):
            try:
                return json.loads(val)
            except (json.JSONDecodeError, ValueError):
                return [v.strip() for v in val.split("\n") if v.strip()]
        if isinstance(val, list):
            return val
        return [str(val)]
    retrieved = _parse(output)
    reference = _parse(expected)
    if not reference:
        return {"result": 1.0 if not retrieved else 0.0, "reason": "No reference contexts"}
    ret_set = set(str(r).lower().strip() for r in retrieved)
    hits = sum(1 for ctx in reference if str(ctx).lower().strip() in ret_set)
    recall = hits / len(reference)
    return {"result": recall, "reason": f"Context Recall: {recall:.4f} ({hits}/{len(reference)} found)"}


def calculate_distinct_n(text, n=1, **kwargs):
    """Compute Distinct-N: ratio of unique n-grams to total n-grams. Measures text diversity."""
    text = str(text).strip().lower()
    tokens = text.split()
    if len(tokens) < n:
        return {"result": 0.0, "reason": f"Text too short for {n}-grams"}
    ngrams = [tuple(tokens[i:i + n]) for i in range(len(tokens) - n + 1)]
    if not ngrams:
        return {"result": 0.0, "reason": "No n-grams"}
    score = len(set(ngrams)) / len(ngrams)
    return {"result": score, "reason": f"Distinct-{n}: {score:.4f} ({len(set(ngrams))} unique / {len(ngrams)} total)"}


def calculate_type_token_ratio(text, **kwargs):
    """Compute Type-Token Ratio: unique tokens / total tokens."""
    text = str(text).strip().lower()
    tokens = text.split()
    if not tokens:
        return {"result": 0.0, "reason": "Empty text"}
    score = len(set(tokens)) / len(tokens)
    return {"result": score, "reason": f"TTR: {score:.4f} ({len(set(tokens))} types / {len(tokens)} tokens)"}


def calculate_repetition_rate(text, n=3, **kwargs):
    """Compute repetition rate: fraction of n-grams that are repeated. Lower = less repetitive."""
    text = str(text).strip().lower()
    tokens = text.split()
    n = int(n)
    if len(tokens) < n:
        return {"result": 1.0, "reason": "Text too short"}
    ngrams = [tuple(tokens[i:i + n]) for i in range(len(tokens) - n + 1)]
    from collections import Counter
    counts = Counter(ngrams)
    repeated = sum(c - 1 for c in counts.values() if c > 1)
    rate = repeated / len(ngrams) if ngrams else 0.0
    score = max(0.0, 1.0 - rate)  # Higher = less repetitive = better
    return {"result": score, "reason": f"Repetition Rate: {rate:.4f} (score={score:.4f}, {repeated} repeated {n}-grams)"}


def is_refusal(text, **kwargs):
    """Detect if LLM output is a refusal to answer."""
    text = str(text).strip().lower()
    if not text:
        return {"result": True, "reason": "Empty response (likely refusal)"}
    refusal_patterns = [
        "i cannot", "i can't", "i'm unable to", "i am unable to",
        "i'm not able to", "i am not able to", "i will not", "i won't",
        "i do not", "i don't", "i refuse", "as an ai", "as a language model",
        "i'm sorry, but i can't", "i apologize, but i cannot",
        "it is not appropriate", "it's not appropriate",
        "i'm not comfortable", "i am not comfortable",
        "against my guidelines", "against my programming",
        "i must decline", "i have to decline",
    ]
    for pattern in refusal_patterns:
        if pattern in text:
            return {"result": True, "reason": f"Refusal detected: '{pattern}'"}
    return {"result": False, "reason": "No refusal detected"}


def latency_check(text, max_latency_ms=None, **kwargs):
    """Check if response latency is within acceptable bounds."""
    try:
        latency = float(str(text).strip())
    except (ValueError, TypeError):
        return {"result": False, "reason": f"Cannot parse latency value: {text}"}
    if max_latency_ms is not None:
        max_ms = float(max_latency_ms)
        if latency <= max_ms:
            return {"result": True, "reason": f"Latency {latency:.1f}ms <= {max_ms:.1f}ms limit"}
        return {"result": False, "reason": f"Latency {latency:.1f}ms > {max_ms:.1f}ms limit"}
    return {"result": True, "reason": f"Latency: {latency:.1f}ms"}


def calculate_fleiss_kappa(output, expected=None, **kwargs):
    """Compute Fleiss' Kappa for multi-rater agreement. Input: matrix of ratings as JSON."""
    try:
        if isinstance(output, str):
            matrix = json.loads(output)
        else:
            matrix = output
    except (json.JSONDecodeError, ValueError, TypeError):
        return {"result": 0.0, "reason": "Cannot parse ratings matrix"}

    if not isinstance(matrix, list) or not matrix:
        return {"result": 0.0, "reason": "Empty or invalid matrix"}

    n = len(matrix)  # subjects
    k = len(matrix[0]) if matrix else 0  # categories
    N_raters = sum(matrix[0]) if matrix else 0  # raters per subject

    if n == 0 or k == 0 or N_raters == 0:
        return {"result": 0.0, "reason": "Invalid matrix dimensions"}

    # P_i for each subject
    p_i_list = []
    for row in matrix:
        s = sum(r * (r - 1) for r in row)
        p_i_list.append(s / (N_raters * (N_raters - 1)) if N_raters > 1 else 0)
    P_bar = sum(p_i_list) / n

    # P_j for each category
    p_j_list = []
    for j in range(k):
        total = sum(matrix[i][j] for i in range(n))
        p_j_list.append(total / (n * N_raters))
    P_e_bar = sum(pj ** 2 for pj in p_j_list)

    if P_e_bar >= 1.0:
        kappa = 1.0 if P_bar == 1.0 else 0.0
    else:
        kappa = (P_bar - P_e_bar) / (1 - P_e_bar)

    score = (kappa + 1) / 2
    return {"result": score, "reason": f"Fleiss' Kappa: {kappa:.4f}, normalized={score:.4f}"}


"""
A dictionary containing the available operations and their corresponding functions.
"""
operations = {
    "Regex": regex,
    "ContainsAny": contains_any,
    "ContainsAll": contains_all,
    "Contains": contains,
    "ContainsNone": contains_none,
    "ContainsJson": contains_json,
    "ContainsEmail": contains_email,
    "IsJson": is_json,
    "IsEmail": is_email,
    "NoInvalidLinks": no_invalid_links,
    "ContainsLink": contains_link,
    "ContainsValidLink": contains_valid_link,
    "Equals": equals,
    "StartsWith": starts_with,
    "EndsWith": ends_with,
    "LengthLessThan": length_less_than,
    "LengthGreaterThan": length_greater_than,
    "LengthBetween": length_between,
    "ApiCall": api_call,
    "OneLine": one_line,
    "JsonSchema": json_schema,
    "JsonValidation": json_validation,
    "CustomCodeEval": custom_code_eval,
    "BleuScore": calculate_bleu,
    "RougeScore": calculate_rouge,
    "FidScore": calculate_fid,
    "ClipScore": calculate_clip_score,
    "RecallScore": recall_score,
    "RecallAtK": recall_at_k,
    "PrecisionAtK": precision_at_k,
    "NdcgAtK": ndcg_at_k,
    "Mrr": mean_reciprocal_rank,
    "HitRate": hit_rate,
    "LevenshteinSimilarity": calculate_levenshtein_similarity,
    "NumericSimilarity": calculate_numeric_similarity,
    "EmbeddingSimilarity": calculate_embedding_similarity,
    "SemanticListContains": calculate_semantic_list_contains,
    "MeteorScore": calculate_meteor,
    "GleuScore": calculate_gleu,
    "ChrfScore": calculate_chrf,
    "F1Score": calculate_f1_score,
    "JaccardSimilarity": calculate_jaccard_similarity,
    "JaroWinklerSimilarity": calculate_jaro_winkler_similarity,
    "HammingSimilarity": calculate_hamming_similarity,
    "FuzzyMatch": calculate_fuzzy_match,
    "IsXml": is_xml,
    "IsSql": is_sql,
    "IsUrl": is_url,
    "WordCountInRange": word_count_in_range,
    "ReadabilityScore": calculate_readability_score,
    "SentenceCount": sentence_count,
    "ToolCallAccuracy": tool_call_accuracy,
    "Ssim": calculate_ssim,
    "Psnr": calculate_psnr,
    "ImageProperties": image_properties,
    "WordErrorRate": calculate_word_error_rate,
    "CharacterErrorRate": calculate_character_error_rate,
    "SyntaxValidation": syntax_validation,
    "CodeComplexity": code_complexity,
    "CodeBleu": calculate_code_bleu,
    "Accuracy": calculate_accuracy,
    "PrecisionScore": calculate_precision_score,
    "CohenKappa": calculate_cohen_kappa,
    "MatthewsCorrelation": calculate_matthews_correlation,
    "JsonDiff": json_diff,
    "IsHtml": is_html,
    "TranslationEditRate": calculate_translation_edit_rate,
    "TrajectoryMatch": trajectory_match,
    "StepCount": step_count,
    "RegexPiiDetection": regex_pii_detection,
    "PearsonCorrelation": calculate_pearson_correlation,
    "SpearmanCorrelation": calculate_spearman_correlation,
    "R2Score": calculate_r2_score,
    "Rmse": calculate_rmse,
    "BalancedAccuracy": calculate_balanced_accuracy,
    "FBetaScore": calculate_f_beta_score,
    "LogLoss": calculate_log_loss,
    "MeanAveragePrecision": calculate_mean_average_precision,
    "SquadScore": calculate_squad_score,
    "MatchErrorRate": calculate_match_error_rate,
    "WordInfoLost": calculate_word_info_lost,
    "WordInfoPreserved": calculate_word_info_preserved,
    "NonLlmContextPrecision": non_llm_context_precision,
    "NonLlmContextRecall": non_llm_context_recall,
    "DistinctN": calculate_distinct_n,
    "TypeTokenRatio": calculate_type_token_ratio,
    "RepetitionRate": calculate_repetition_rate,
    "IsRefusal": is_refusal,
    "LatencyCheck": latency_check,
    "FleissKappa": calculate_fleiss_kappa,
}
