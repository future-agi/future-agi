"""Deterministic lexical diagnostics; none of these establish semantic support.

The catalog runs standalone copies in the code-eval sandbox. Contract tests pin
both implementations, including configurable thresholds and input encodings.
"""

import json
import re
from collections import Counter


def lexical_contexts(value):
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return []
        try:
            parsed = json.loads(value)
        except ValueError:
            parsed = None
        if isinstance(parsed, list):
            return lexical_contexts(parsed)
        return [line.strip() for line in value.splitlines() if line.strip()]
    return [str(value).strip()]


def lexical_tokens(text):
    return Counter(re.findall(r"\b\w+\b", text.lower()))


def overlap_threshold(value):
    try:
        threshold = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(
            "min_token_overlap must be a number between 0 and 1"
        ) from error
    if not 0 <= threshold <= 1:
        raise ValueError("min_token_overlap must be a number between 0 and 1")
    return threshold


def calculate_context_precision_score(
    hypothesis=None, reference=None, k=None, min_token_overlap=0.6, **kwargs
):
    """Average precision over lexical matches in the evaluated top-k chunks.

    A match has token precision OR recall >= the threshold, or text containment.
    The denominator is matched retrieved chunks, not all reference chunks. Use
    the existing mean_average_precision evaluator for exact-item retrieval AP.
    """
    threshold = overlap_threshold(min_token_overlap)
    retrieved = lexical_contexts(
        hypothesis if hypothesis is not None else kwargs.get("output")
    )
    refs = lexical_contexts(
        reference if reference is not None else kwargs.get("expected")
    )
    if not refs or not retrieved:
        return {
            "result": float(not refs and not retrieved),
            "reason": "Empty lexical context input",
        }
    if k is not None:
        try:
            retrieved = retrieved[: max(1, int(k))]
        except (ValueError, TypeError):
            pass
    hits = 0
    total = 0.0
    for rank, chunk in enumerate(retrieved, 1):
        chunk_tokens = lexical_tokens(chunk)
        if not chunk_tokens:
            continue
        for ref in refs:
            ref_tokens = lexical_tokens(ref)
            if not ref_tokens:
                continue
            overlap = sum((chunk_tokens & ref_tokens).values())
            if (
                overlap / sum(chunk_tokens.values()) >= threshold
                or overlap / sum(ref_tokens.values()) >= threshold
                or ref.lower() in chunk.lower()
                or chunk.lower() in ref.lower()
            ):
                hits += 1
                total += hits / rank
                break
    score = total / hits if hits else 0.0
    return {
        "result": score,
        "reason": f"Lexical context precision: {score:.4f} ({hits}/{len(retrieved)} matching chunks); not semantic relevance",
    }


def calculate_context_recall_score(
    hypothesis=None, reference=None, min_token_overlap=0.7, **kwargs
):
    """Fraction of references with lexical coverage in any one retrieved chunk."""
    threshold = overlap_threshold(min_token_overlap)
    retrieved = lexical_contexts(
        hypothesis if hypothesis is not None else kwargs.get("output")
    )
    refs = lexical_contexts(
        reference if reference is not None else kwargs.get("expected")
    )
    if not refs or not retrieved:
        return {
            "result": float(not refs and not retrieved),
            "reason": "Empty lexical context input",
        }
    hits = 0
    for ref in refs:
        ref_tokens = lexical_tokens(ref)
        if not ref_tokens or any(
            sum((ref_tokens & lexical_tokens(chunk)).values())
            / sum(ref_tokens.values())
            >= threshold
            or ref.lower() in chunk.lower()
            for chunk in retrieved
            if lexical_tokens(chunk)
        ):
            hits += 1
    score = hits / len(refs)
    return {
        "result": score,
        "reason": f"Lexical context recall: {score:.4f} ({hits}/{len(refs)} matching references); not factual coverage",
    }


def calculate_faithfulness_score(output=None, context=None, **kwargs):
    """Sentence token-overlap diagnostic, not entailment or hallucination detection.

    Contradictions can score 1.0. Use the existing groundedness model judge when
    the question is whether an answer is semantically supported by its context.
    """
    out_text = "\n".join(lexical_contexts(output))
    ctx_text = "\n".join(lexical_contexts(context))
    if not out_text or not ctx_text:
        return {
            "result": float(not out_text),
            "reason": "Empty lexical overlap input; not semantic support",
        }
    sentences = [
        part.strip()
        for part in re.split(r"(?<=[.!?])\s+|\n+", out_text)
        if len(part.strip()) > 3
    ] or [out_text]
    context_tokens = lexical_tokens(ctx_text)
    hits = 0
    for sentence in sentences:
        tokens = lexical_tokens(sentence)
        if (
            not tokens
            or sum((tokens & context_tokens).values()) / sum(tokens.values()) >= 0.7
            or sentence.lower() in ctx_text.lower()
        ):
            hits += 1
    score = hits / len(sentences)
    return {
        "result": score,
        "reason": f"Lexical sentence overlap: {score:.4f} ({hits}/{len(sentences)} matching sentences); not semantic support",
    }


def calculate_noise_sensitivity(output=None, expected=None, context=None, **kwargs):
    """Return 1 minus the output-token ratio found only in non-reference context.

    Higher is better, like other catalog scores. The raw sensitivity in the
    reason is a lexical diagnostic, not evidence that context caused an answer.
    """
    output_tokens = lexical_tokens("\n".join(lexical_contexts(output)))
    expected_tokens = lexical_tokens("\n".join(lexical_contexts(expected)))
    context_tokens = lexical_tokens("\n".join(lexical_contexts(context)))
    total = sum(output_tokens.values())
    noise = sum(
        min(count, context_tokens[token])
        for token, count in output_tokens.items()
        if token not in expected_tokens
    )
    sensitivity = noise / total if total else 0.0
    return {
        "result": 1.0 - sensitivity,
        "reason": f"Lexical noise sensitivity={sensitivity:.4f} ({noise}/{total} output tokens in context but absent from reference); score=1-sensitivity, not causal influence",
    }
