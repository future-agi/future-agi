"""
Python code for all system code-based evaluations.

Each function is the `code` field stored in the EvalTemplate config.
The function signature matches the eval's required_keys.

Code evals return:
  - bool → Pass/Fail
  - float → score (0.0-1.0)
  - dict → {"result": bool/float, "reason": str}
"""

# =============================================================================
# String / Content Checks
# =============================================================================

CONTAINS = '''def evaluate(text, keyword="", case_sensitive=False, **kwargs):
    """Check if text contains the keyword."""
    if not keyword:
        return {"result": False, "reason": "No keyword configured"}
    if not case_sensitive:
        return keyword.lower() in str(text).lower()
    return keyword in str(text)
'''

CONTAINS_ALL = '''def evaluate(text, keywords=None, case_sensitive=False, **kwargs):
    """Check if text contains ALL keywords."""
    if not keywords:
        return {"result": False, "reason": "No keywords configured"}
    t = str(text) if case_sensitive else str(text).lower()
    return all((k if case_sensitive else k.lower()) in t for k in keywords)
'''

CONTAINS_ANY = '''def evaluate(text, keywords=None, case_sensitive=False, **kwargs):
    """Check if text contains ANY of the keywords."""
    if not keywords:
        return {"result": False, "reason": "No keywords configured"}
    t = str(text) if case_sensitive else str(text).lower()
    return any((k if case_sensitive else k.lower()) in t for k in keywords)
'''

CONTAINS_NONE = '''def evaluate(text, keywords=None, case_sensitive=False, **kwargs):
    """Check if text contains NONE of the keywords."""
    if not keywords:
        return True
    t = str(text) if case_sensitive else str(text).lower()
    return not any((k if case_sensitive else k.lower()) in t for k in keywords)
'''

EQUALS = '''def evaluate(text, expected_text, **kwargs):
    """Check if text exactly equals expected text (trimmed)."""
    return str(text).strip() == str(expected_text).strip()
'''

STARTS_WITH = '''def evaluate(text, prefix="", **kwargs):
    """Check if text starts with the given prefix."""
    return str(text).startswith(str(prefix))
'''

ENDS_WITH = '''def evaluate(text, suffix="", **kwargs):
    """Check if text ends with the given suffix."""
    return str(text).endswith(str(suffix))
'''

REGEX = '''import re
def evaluate(text, pattern="", **kwargs):
    """Check if text matches the regex pattern."""
    if not pattern:
        return {"result": False, "reason": "No pattern configured"}
    return bool(re.search(pattern, str(text)))
'''

ONE_LINE = '''def evaluate(text, **kwargs):
    """Check if text is a single line (no newlines)."""
    return "\\n" not in str(text).strip()
'''

LENGTH_LESS_THAN = '''def evaluate(text, **kwargs):
    """Check if text length is less than max_length."""
    max_length = int(kwargs.get("max_length", 0) or 0)
    if max_length <= 0:
        return {"result": False, "reason": "No max_length configured"}
    return len(str(text)) < max_length
'''

LENGTH_GREATER_THAN = '''def evaluate(text, **kwargs):
    """Check if text length is greater than min_length."""
    min_length = int(kwargs.get("min_length", 0) or 0)
    if min_length <= 0:
        return {"result": False, "reason": "No min_length configured"}
    return len(str(text)) > min_length
'''

LENGTH_BETWEEN = '''def evaluate(text, **kwargs):
    """Check if text length is between min and max."""
    min_length = int(kwargs.get("min_length", 0) or 0)
    max_length = int(kwargs.get("max_length", 0) or 0)
    if max_length <= 0:
        return {"result": False, "reason": "No min_length/max_length configured"}
    length = len(str(text))
    return min_length <= length <= max_length
'''

IS_JSON = '''import json
def evaluate(text, **kwargs):
    """Check if text is valid JSON."""
    if isinstance(text, (dict, list)):
        return True
    try:
        json.loads(str(text))
        return True
    except (json.JSONDecodeError, TypeError):
        return False
'''

IS_EMAIL = '''import re
def evaluate(text, **kwargs):
    """Check if text is a valid email address."""
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$"
    return bool(re.match(pattern, str(text).strip()))
'''

CONTAINS_VALID_LINK = """import re
import urllib.request
def evaluate(text, **kwargs):
    \"\"\"Check if text contains at least one valid (reachable) URL.\"\"\"
    urls = re.findall(r'https?://[^\\\\s<>\"\\']+', str(text))
    for url in urls:
        try:
            req = urllib.request.Request(url, method="HEAD")
            urllib.request.urlopen(req, timeout=5)
            return True
        except Exception:
            continue
    return False
"""

NO_INVALID_LINKS = """import re
import urllib.request
def evaluate(text, **kwargs):
    \"\"\"Check that all URLs in the text are reachable.\"\"\"
    urls = re.findall(r'https?://[^\\\\s<>\"\\']+', str(text))
    if not urls:
        return True
    for url in urls:
        try:
            req = urllib.request.Request(url, method="HEAD")
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            return {"result": False, "reason": "Invalid URL: " + url}
    return True
"""

JSON_SCHEMA_VALIDATION = '''import json
def evaluate(actual_json, expected_json, **kwargs):
    """Validate JSON structure against expected schema/structure."""
    try:
        actual = json.loads(actual_json) if isinstance(actual_json, str) else actual_json
        expected = json.loads(expected_json) if isinstance(expected_json, str) else expected_json

        def check_keys(actual, expected, path=""):
            if isinstance(expected, dict):
                if not isinstance(actual, dict):
                    return False, f"Expected dict at {path}, got {type(actual).__name__}"
                for key in expected:
                    if key not in actual:
                        return False, f"Missing key: {path}.{key}"
                    ok, msg = check_keys(actual[key], expected[key], f"{path}.{key}")
                    if not ok:
                        return False, msg
            elif isinstance(expected, list) and expected:
                if not isinstance(actual, list):
                    return False, f"Expected list at {path}, got {type(actual).__name__}"
                if actual:
                    ok, msg = check_keys(actual[0], expected[0], f"{path}[0]")
                    if not ok:
                        return False, msg
            return True, "Valid"

        ok, msg = check_keys(actual, expected)
        return {"result": ok, "reason": msg}
    except Exception as e:
        return {"result": False, "reason": str(e)}
'''

API_CALL = '''import json
def evaluate(response, **kwargs):
    """Check if the response is a valid API response."""
    try:
        if isinstance(response, str):
            data = json.loads(response)
        else:
            data = response
        if isinstance(data, dict):
            return True
        return {"result": False, "reason": "Response is not a JSON object"}
    except (json.JSONDecodeError, TypeError):
        return {"result": False, "reason": "Response is not valid JSON"}
'''

CUSTOM_CODE_EVALUATION = '''def evaluate(**kwargs):
    """Placeholder for custom user code. Replace with your evaluation logic."""
    return {"result": True, "reason": "Custom code placeholder - implement your logic"}
'''

# =============================================================================
# Similarity & Scoring (NLP metrics)
# =============================================================================

BLEU_SCORE = '''def evaluate(reference, hypothesis, **kwargs):
    """Calculate BLEU score between reference and hypothesis."""
    from collections import Counter
    import math

    ref_tokens = str(reference).lower().split()
    hyp_tokens = str(hypothesis).lower().split()

    if not hyp_tokens:
        return 0.0

    # Unigram precision
    ref_counts = Counter(ref_tokens)
    hyp_counts = Counter(hyp_tokens)
    clipped = sum(min(hyp_counts[w], ref_counts.get(w, 0)) for w in hyp_counts)
    precision = clipped / len(hyp_tokens) if hyp_tokens else 0

    # Brevity penalty
    bp = math.exp(1 - len(ref_tokens) / len(hyp_tokens)) if len(hyp_tokens) < len(ref_tokens) else 1.0

    return round(bp * precision, 4)
'''

ROUGE_SCORE = '''def evaluate(reference, hypothesis, **kwargs):
    """Calculate ROUGE-L score (longest common subsequence)."""
    ref_tokens = str(reference).lower().split()
    hyp_tokens = str(hypothesis).lower().split()

    if not ref_tokens or not hyp_tokens:
        return 0.0

    # LCS length
    m, n = len(ref_tokens), len(hyp_tokens)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if ref_tokens[i-1] == hyp_tokens[j-1]:
                dp[i][j] = dp[i-1][j-1] + 1
            else:
                dp[i][j] = max(dp[i-1][j], dp[i][j-1])

    lcs = dp[m][n]
    precision = lcs / n if n else 0
    recall = lcs / m if m else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0

    return round(f1, 4)
'''

RECALL_SCORE = '''def evaluate(hypothesis, reference, **kwargs):
    """Calculate recall: proportion of reference items found in hypothesis."""
    ref_set = set(str(reference).lower().split())
    hyp_set = set(str(hypothesis).lower().split())
    if not ref_set:
        return 1.0
    recall = len(ref_set & hyp_set) / len(ref_set)
    return round(recall, 4)
'''

PRECISION_AT_K = '''def evaluate(hypothesis, reference, **kwargs):
    """Precision@K: proportion of top-K retrieved items that are relevant."""
    import json
    k = int(kwargs.get("k", 0)) if kwargs.get("k") else None
    try:
        hyp = json.loads(hypothesis) if isinstance(hypothesis, str) else hypothesis
        ref = json.loads(reference) if isinstance(reference, str) else reference
    except (json.JSONDecodeError, TypeError):
        hyp = str(hypothesis).split(",")
        ref = str(reference).split(",")

    hyp = [str(x).strip().lower() for x in (hyp if isinstance(hyp, list) else [hyp])]
    ref = set(str(x).strip().lower() for x in (ref if isinstance(ref, list) else [ref]))

    if k is None or k <= 0:
        k = len(hyp) or 1
    top_k = hyp[:k]
    if not top_k:
        return 0.0
    relevant = sum(1 for item in top_k if item in ref)
    return round(relevant / len(top_k), 4)
'''

RECALL_AT_K = '''def evaluate(hypothesis, reference, **kwargs):
    """Recall@K: proportion of relevant items found in top-K."""
    import json
    k = int(kwargs.get("k", 0)) if kwargs.get("k") else None
    try:
        hyp = json.loads(hypothesis) if isinstance(hypothesis, str) else hypothesis
        ref = json.loads(reference) if isinstance(reference, str) else reference
    except (json.JSONDecodeError, TypeError):
        hyp = str(hypothesis).split(",")
        ref = str(reference).split(",")

    hyp = [str(x).strip().lower() for x in (hyp if isinstance(hyp, list) else [hyp])]
    ref = set(str(x).strip().lower() for x in (ref if isinstance(ref, list) else [ref]))

    if k is None or k <= 0:
        k = len(hyp) or 1
    top_k = hyp[:k]
    if not ref:
        return 1.0
    found = sum(1 for item in top_k if item in ref)
    return round(found / len(ref), 4)
'''

HIT_RATE = '''def evaluate(hypothesis, reference, **kwargs):
    """Hit Rate: 1.0 if any relevant item is in the retrieved list, else 0.0."""
    import json
    try:
        hyp = json.loads(hypothesis) if isinstance(hypothesis, str) else hypothesis
        ref = json.loads(reference) if isinstance(reference, str) else reference
    except (json.JSONDecodeError, TypeError):
        hyp = str(hypothesis).split(",")
        ref = str(reference).split(",")

    hyp_set = set(str(x).strip().lower() for x in (hyp if isinstance(hyp, list) else [hyp]))
    ref_set = set(str(x).strip().lower() for x in (ref if isinstance(ref, list) else [ref]))

    return 1.0 if hyp_set & ref_set else 0.0
'''

MRR = '''def evaluate(hypothesis, reference, **kwargs):
    """Mean Reciprocal Rank: 1/rank of the first relevant item."""
    import json
    try:
        hyp = json.loads(hypothesis) if isinstance(hypothesis, str) else hypothesis
        ref = json.loads(reference) if isinstance(reference, str) else reference
    except (json.JSONDecodeError, TypeError):
        hyp = str(hypothesis).split(",")
        ref = str(reference).split(",")

    hyp_list = [str(x).strip().lower() for x in (hyp if isinstance(hyp, list) else [hyp])]
    ref_set = set(str(x).strip().lower() for x in (ref if isinstance(ref, list) else [ref]))

    for i, item in enumerate(hyp_list):
        if item in ref_set:
            return round(1.0 / (i + 1), 4)
    return 0.0
'''

NDCG_AT_K = '''import math
import json
def evaluate(hypothesis, reference, **kwargs):
    """NDCG@K: Normalized Discounted Cumulative Gain."""
    k = int(kwargs.get("k", 0)) if kwargs.get("k") else None
    try:
        hyp = json.loads(hypothesis) if isinstance(hypothesis, str) else hypothesis
        ref = json.loads(reference) if isinstance(reference, str) else reference
    except (json.JSONDecodeError, TypeError):
        hyp = str(hypothesis).split(",")
        ref = str(reference).split(",")

    hyp_list = [str(x).strip().lower() for x in (hyp if isinstance(hyp, list) else [hyp])]
    ref_set = set(str(x).strip().lower() for x in (ref if isinstance(ref, list) else [ref]))

    if k is None or k <= 0:
        k = len(hyp_list) or 1
    top_k = hyp_list[:k]

    dcg = sum((1.0 / math.log2(i + 2)) for i, item in enumerate(top_k) if item in ref_set)
    ideal_count = min(len(ref_set), k)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_count))

    if idcg == 0:
        return 0.0
    return round(dcg / idcg, 4)
'''

LEVENSHTEIN_SIMILARITY = '''def evaluate(output, expected, **kwargs):
    """Normalized Levenshtein similarity (1 - edit_distance/max_len)."""
    s1, s2 = str(output), str(expected)
    if s1 == s2:
        return 1.0
    m, n = len(s1), len(s2)
    if not m or not n:
        return 0.0

    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev = dp[0]
        dp[0] = i
        for j in range(1, n + 1):
            temp = dp[j]
            if s1[i-1] == s2[j-1]:
                dp[j] = prev
            else:
                dp[j] = 1 + min(prev, dp[j], dp[j-1])
            prev = temp

    distance = dp[n]
    return round(1.0 - distance / max(m, n), 4)
'''

NUMERIC_SIMILARITY = '''def evaluate(output, expected, **kwargs):
    """Normalized similarity between numbers (1 - |a-b|/max(|a|,|b|))."""
    import re
    def extract_number(text):
        nums = re.findall(r"-?\\d+\\.?\\d*", str(text))
        return float(nums[0]) if nums else None

    a = extract_number(output)
    b = extract_number(expected)

    if a is None or b is None:
        return 0.0
    if a == b:
        return 1.0

    max_abs = max(abs(a), abs(b))
    if max_abs == 0:
        return 1.0
    return round(max(0, 1.0 - abs(a - b) / max_abs), 4)
'''

EMBEDDING_SIMILARITY = '''def evaluate(output, expected, **kwargs):
    """Semantic similarity using bag-of-words cosine (no external deps)."""
    from collections import Counter
    import math

    def tokenize(text):
        return str(text).lower().split()

    tokens_a = tokenize(output)
    tokens_b = tokenize(expected)

    if not tokens_a or not tokens_b:
        return 0.0

    counter_a = Counter(tokens_a)
    counter_b = Counter(tokens_b)
    all_words = set(counter_a.keys()) | set(counter_b.keys())

    dot = sum(counter_a.get(w, 0) * counter_b.get(w, 0) for w in all_words)
    mag_a = math.sqrt(sum(v**2 for v in counter_a.values()))
    mag_b = math.sqrt(sum(v**2 for v in counter_b.values()))

    if mag_a == 0 or mag_b == 0:
        return 0.0
    return round(dot / (mag_a * mag_b), 4)
'''

SEMANTIC_LIST_CONTAINS = '''def evaluate(output, expected, **kwargs):
    """Check semantic presence of expected phrases in output."""
    output_lower = str(output).lower()
    expected_items = str(expected).lower().split(",") if isinstance(expected, str) else [str(x).lower() for x in expected]
    expected_items = [x.strip() for x in expected_items if x.strip()]

    if not expected_items:
        return 1.0

    found = sum(1 for item in expected_items if item in output_lower)
    return round(found / len(expected_items), 4)
'''

ANSWER_SIMILARITY = '''def evaluate(expected_response, response, **kwargs):
    """Calculate similarity between expected and actual response."""
    from collections import Counter
    import math

    tokens_a = str(expected_response).lower().split()
    tokens_b = str(response).lower().split()

    if not tokens_a or not tokens_b:
        return 0.0

    counter_a = Counter(tokens_a)
    counter_b = Counter(tokens_b)
    all_words = set(counter_a.keys()) | set(counter_b.keys())

    dot = sum(counter_a.get(w, 0) * counter_b.get(w, 0) for w in all_words)
    mag_a = math.sqrt(sum(v**2 for v in counter_a.values()))
    mag_b = math.sqrt(sum(v**2 for v in counter_b.values()))

    if mag_a == 0 or mag_b == 0:
        return 0.0
    return round(dot / (mag_a * mag_b), 4)
'''

# =============================================================================
# Multimodal code evals
#
# These receive PRE-COMPUTED embeddings/features from the engine's
# preprocessing layer. The sandbox code only does the math.
#
# The engine preprocessor (in evaluations/engine/preprocessing.py):
#   - Downloads images from URLs
#   - Computes CLIP/Inception embeddings via the serving client
#   - Passes vectors to the sandbox as JSON lists
# =============================================================================

CLIP_SCORE = '''import json
import math

def evaluate(images, text, **kwargs):
    """Compute CLIP score from pre-computed image and text embeddings.

    If raw URLs/text are passed (no preprocessing), returns instructions.
    If embeddings are passed (after preprocessing), computes cosine similarity.
    """
    # Check if embeddings were pre-computed by the engine
    image_embeddings = kwargs.get("_image_embeddings")
    text_embeddings = kwargs.get("_text_embeddings")

    if image_embeddings and text_embeddings:
        # Parse if JSON strings
        if isinstance(image_embeddings, str):
            image_embeddings = json.loads(image_embeddings)
        if isinstance(text_embeddings, str):
            text_embeddings = json.loads(text_embeddings)

        # Ensure list of lists
        if image_embeddings and not isinstance(image_embeddings[0], list):
            image_embeddings = [image_embeddings]
        if text_embeddings and not isinstance(text_embeddings[0], list):
            text_embeddings = [text_embeddings]

        scores = []
        for img_emb, txt_emb in zip(image_embeddings, text_embeddings):
            # Cosine similarity
            dot = sum(a * b for a, b in zip(img_emb, txt_emb))
            mag_img = math.sqrt(sum(a * a for a in img_emb))
            mag_txt = math.sqrt(sum(b * b for b in txt_emb))
            if mag_img == 0 or mag_txt == 0:
                scores.append(0.0)
            else:
                scores.append(dot / (mag_img * mag_txt))

        avg_score = sum(scores) / len(scores) if scores else 0.0
        # Normalize to 0-1 range (cosine similarity is already -1 to 1, clamp to 0-1)
        normalized = max(0.0, min(avg_score, 1.0))
        return {"result": round(normalized, 4), "reason": f"CLIP score: {normalized:.4f} (cosine similarity, {len(scores)} pairs)"}

    # Fallback: no preprocessing available, try using numpy if installed
    try:
        import numpy as np
        # If embeddings aren't pre-computed, we can't compute CLIP score in sandbox
        return {"result": None, "reason": "CLIP score requires image/text embeddings. Enable preprocessing in eval config or use the Agent evaluator."}
    except ImportError:
        return {"result": None, "reason": "CLIP score requires numpy. Enable preprocessing or use Agent evaluator."}
'''

FID_SCORE = '''import json
import math

def evaluate(real_images, fake_images, **kwargs):
    """Compute FID score from pre-computed Inception features.

    Expects _real_features and _fake_features in kwargs (pre-computed by engine).
    Each is a list of feature vectors (2048-dim from Inception v3).
    FID = ||mu_r - mu_f||^2 + Tr(C_r + C_f - 2*(C_r @ C_f)^0.5)
    """
    # Check if FID was pre-computed entirely by the engine
    precomputed = kwargs.get("_fid_precomputed_score")
    if precomputed is not None:
        return {"result": round(float(precomputed), 4), "reason": f"FID score: {float(precomputed):.3f} (lower is better)"}

    real_features = kwargs.get("_real_features")
    fake_features = kwargs.get("_fake_features")

    if real_features and fake_features:
        if isinstance(real_features, str):
            real_features = json.loads(real_features)
        if isinstance(fake_features, str):
            fake_features = json.loads(fake_features)

        try:
            import numpy as np

            real = np.array(real_features, dtype=np.float64)
            fake = np.array(fake_features, dtype=np.float64)

            if real.shape[0] < 2 or fake.shape[0] < 2:
                return {"result": None, "reason": "Need at least 2 samples per distribution for FID"}

            # Compute means
            mu_r = np.mean(real, axis=0)
            mu_f = np.mean(fake, axis=0)

            # Compute covariances
            sigma_r = np.cov(real, rowvar=False)
            sigma_f = np.cov(fake, rowvar=False)

            # Squared difference of means
            diff = mu_r - mu_f
            mean_diff_sq = np.dot(diff, diff)

            # Product of covariances
            covmean_sq = sigma_r @ sigma_f

            # Matrix square root via eigendecomposition
            eigenvalues, eigenvectors = np.linalg.eigh(covmean_sq)
            eigenvalues = np.maximum(eigenvalues, 0)  # Clip negative eigenvalues
            sqrt_eigenvalues = np.sqrt(eigenvalues)
            covmean = eigenvectors @ np.diag(sqrt_eigenvalues) @ eigenvectors.T

            fid = mean_diff_sq + np.trace(sigma_r + sigma_f - 2 * covmean)
            fid = float(max(0, fid))  # FID should be non-negative

            return {"result": round(fid, 4), "reason": f"FID score: {fid:.3f} (lower is better)"}

        except Exception as e:
            return {"result": None, "reason": f"FID computation error: {str(e)}"}

    return {"result": None, "reason": "FID requires pre-computed Inception features. Enable preprocessing in eval config or use the Agent evaluator."}
'''


DEAD_AIR_DETECTION = '''def evaluate(input, output, expected, context, **kwargs):
    """Detect dead air (silence) in an audio conversation.

    Audio decoding + RMS / silence-gap computation runs in the API-server
    preprocessor (librosa is not in the sandbox allowlist and the sandbox
    has no network access). This body just reads pre-computed numbers and
    applies user-tunable thresholds.

    Kwargs from preprocessor:
        _dead_air_percentage: float, % of audio below the silence threshold
        _dead_air_max_gap_ms: float, longest single silent run in ms
        _dead_air_duration_sec: float, total audio duration
        _dead_air_error: str, populated if preprocessing failed

    User-tunable kwargs (see eval template config):
        dead_air_threshold: float, max acceptable % of dead air (default 20.0)
        gap_threshold_ms: float, max acceptable single silence gap in ms (default 3000)
    """
    err = kwargs.get("_dead_air_error")
    if err:
        return {"score": 0.0, "reason": f"Dead air analysis unavailable: {err}"}

    dead_air_pct = kwargs.get("_dead_air_percentage")
    max_gap_ms = kwargs.get("_dead_air_max_gap_ms")
    if dead_air_pct is None or max_gap_ms is None:
        return {"score": 0.0, "reason": "Dead air analysis unavailable: preprocessing did not run"}

    try:
        dead_air_threshold = float(kwargs.get("dead_air_threshold", 20.0))
    except (TypeError, ValueError):
        dead_air_threshold = 20.0
    try:
        gap_threshold_ms = float(kwargs.get("gap_threshold_ms", 3000.0))
    except (TypeError, ValueError):
        gap_threshold_ms = 3000.0

    dead_air_passed = dead_air_pct <= dead_air_threshold
    gap_passed = max_gap_ms <= gap_threshold_ms
    passed = dead_air_passed and gap_passed

    reason = (
        f"Dead air: {dead_air_pct:.1f}% "
        f"({'pass' if dead_air_passed else 'fail'}, threshold {dead_air_threshold:.1f}%). "
        f"Max silence gap: {max_gap_ms:.0f}ms "
        f"({'pass' if gap_passed else 'fail'}, threshold {gap_threshold_ms:.0f}ms)."
    )
    return {"score": 1.0 if passed else 0.0, "reason": reason}
'''


# =============================================================================
# Registry: eval_name → code string
# =============================================================================

CODE_REGISTRY = {
    "contains": CONTAINS,
    "contains_all": CONTAINS_ALL,
    "contains_any": CONTAINS_ANY,
    "contains_none": CONTAINS_NONE,
    "equals": EQUALS,
    "starts_with": STARTS_WITH,
    "ends_with": ENDS_WITH,
    "regex": REGEX,
    "one_line": ONE_LINE,
    "length_less_than": LENGTH_LESS_THAN,
    "length_greater_than": LENGTH_GREATER_THAN,
    "length_between": LENGTH_BETWEEN,
    "is_json": IS_JSON,
    "is_email": IS_EMAIL,
    "contains_valid_link": CONTAINS_VALID_LINK,
    "no_invalid_links": NO_INVALID_LINKS,
    "json_scheme_validation": JSON_SCHEMA_VALIDATION,
    "api_call": API_CALL,
    "custom_code_evaluation": CUSTOM_CODE_EVALUATION,
    "bleu_score": BLEU_SCORE,
    "rouge_score": ROUGE_SCORE,
    "recall_score": RECALL_SCORE,
    "precision_at_k": PRECISION_AT_K,
    "recall_at_k": RECALL_AT_K,
    "hit_rate": HIT_RATE,
    "mrr": MRR,
    "ndcg_at_k": NDCG_AT_K,
    "levenshtein_similarity": LEVENSHTEIN_SIMILARITY,
    "numeric_similarity": NUMERIC_SIMILARITY,
    "embedding_similarity": EMBEDDING_SIMILARITY,
    "semantic_list_contains": SEMANTIC_LIST_CONTAINS,
    "answer_similarity": ANSWER_SIMILARITY,
    "clip_score": CLIP_SCORE,
    "fid_score": FID_SCORE,
    "dead_air_detection": DEAD_AIR_DETECTION,
}
