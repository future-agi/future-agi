"""SDK ↔ platform parity for the deterministic evaluators.

Every deterministic eval exists TWICE in this repo, and the two copies are served
to different users:

* the ``ai-evaluation`` SDK runs the native Python in
  ``agentic_eval/.../function/functions.py`` (dispatched via the ``operations``
  registry), and
* the hosted platform runs the embedded ``code`` body inside the seeded
  ``model_hub/system_evals/function/<name>.yaml`` — the seeder hardwires every
  system eval to ``eval_type_id = "CustomCodeEval"``
  (``seed_system_evals.py``), so the YAML body is what actually executes in the
  product, not the native function.

When those two implementations disagree, the SAME eval on the SAME input returns
a different score depending on whether the caller used the SDK or the platform.
This module executes both implementations of every shared eval on a shared input
battery and asserts they agree, with an explicit, documented ledger of the
divergences that exist today (``KNOWN_DIVERGENCES``).

The ledger is a ratchet:

* a new disagreement (an agreeing pair drifts apart) fails
  ``test_no_undocumented_divergence`` with the offending eval named, and
* fixing one side of a known divergence (so the pair now agrees) also fails it,
  demanding the entry be removed.

Either way, any change to SDK/platform parity has to be acknowledged in code
review. Nothing here needs a database, network, or the sandbox — the YAML body
is exec'd directly, exactly as ``system_evals/tests/test_validators.py`` does.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agentic_eval.core_evals.fi_evals.eval_type import FunctionEvalTypeId
from agentic_eval.core_evals.fi_evals.function.functions import operations

# The YAML evals live under model_hub. This file is at
# futureagi/agentic_eval/core_evals/fi_evals/function/tests/, so the backend root
# (futureagi/) is five parents up.
_BACKEND_ROOT = Path(__file__).resolve().parents[5]
YAML_DIR = _BACKEND_ROOT / "model_hub" / "system_evals" / "function"

# Numeric agreement tolerance. ROUGE's SDK copy returns a 3-dp *string*, so a
# 3e-4 gap is a real divergence, not float noise — keep the tolerance well below.
TOL = 1e-6


# ---------------------------------------------------------------------------
# Loading both implementations
# ---------------------------------------------------------------------------
def _load_yaml_eval(name: str):
    """Materialize the YAML eval's embedded code body and return ``evaluate``.

    Mirrors ``model_hub/system_evals/tests/test_validators.py`` — no sandbox, no
    Django, just exec the source string in a fresh namespace.
    """
    code = yaml.safe_load((YAML_DIR / f"{name}.yaml").read_text())["config"]["code"]
    ns: dict = {}
    exec(compile(code, str(YAML_DIR / f"{name}.yaml"), "exec"), ns)  # noqa: S102
    return ns["evaluate"]


def _call_yaml(evaluate, inputs: dict):
    """Call a YAML ``evaluate`` with its four positional params + eval inputs."""
    kwargs = {"input": None, "output": None, "expected": None, "context": None}
    kwargs.update(inputs)
    return evaluate(**kwargs)


def _payload(ret) -> float | None:
    """Normalize either return shape to a float (bool → 1.0/0.0).

    functions.py returns ``{"result": ...}``; the YAML returns ``{"score": ...}``.
    A pass is ``True``/``1.0`` on both sides.
    """
    value = ret.get("result", ret.get("score")) if isinstance(ret, dict) else ret
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Input batteries — data-driven by each eval's required_keys, plus explicit
# fixtures for the evals that also need config params.
# ---------------------------------------------------------------------------
_B_OUTPUT_EXPECTED = [
    {"output": "cat", "expected": "cat"},
    {"output": "cat", "expected": "dog"},
    {"output": "the quick brown fox", "expected": "the quick brown fox"},
    {"output": "the quick brown fox", "expected": "a slow green turtle"},
    {"output": "a,a,b", "expected": "a,b,b"},
    {"output": "hello world", "expected": "hello there"},
]
_B_REFERENCE_HYPOTHESIS = [
    {"reference": "the cat sat on the mat", "hypothesis": "the cat sat on the mat"},
    {"reference": "the cat sat on the mat", "hypothesis": "the cat is on the mat"},
    {"reference": "the cat sat on the mat", "hypothesis": "on the mat the cat sat"},
    {"reference": "hello world", "hypothesis": "hello there"},
    {"reference": "a b c d", "hypothesis": "b a c d"},
]
_B_HYPOTHESIS_REFERENCE = [
    {"hypothesis": ["a", "b", "c"], "reference": ["a", "b"]},
    {"hypothesis": ["a", "a", "c"], "reference": ["a"]},
    {"hypothesis": ["x", "y", "a"], "reference": ["a"]},
    {"hypothesis": ["a", "b"], "reference": ["b", "a"]},
    {"hypothesis": ["z"], "reference": ["a"]},
]
_B_TEXT = [
    {"text": "hello world"},
    {"text": '{"a": 1}'},
    {"text": "SELECT * FROM t"},
    {"text": "Dropbox is a file sharing service."},
    {"text": "I can't help with that."},
    {"text": "https://example.com"},
    {"text": "user@example.com"},
    {"text": "<html><body>hi</body></html>"},
    {"text": "The answer is 42. It is final."},
    {"text": ""},
]
_B_NUMERIC = [
    {"output": "1,2,3,4,5", "expected": "1,2,3,4,5"},
    {"output": "1,2,3,4,5", "expected": "5,4,3,2,1"},
    {"output": "1,2,2,3,3", "expected": "1,2,1,2,3"},
    {"output": "0.1,0.9,0.2,0.8", "expected": "0,1,0,1"},
    {"output": "2,2,3,3,2", "expected": "2,3,2,2,2"},
]

# Evals whose (output, expected) values must be numeric lists.
_NUMERIC = {
    "log_loss",
    "pearson_correlation",
    "r2_score",
    "rmse",
    "spearman_correlation",
}

# Evals that need config params (or non-standard input key names) alongside data.
_CONFIG: dict[str, list[dict]] = {
    "contains": [
        {"text": "the cat sat", "keyword": "cat"},
        {"text": "the dog ran", "keyword": "cat"},
    ],
    "contains_any": [
        {"text": "the cat sat", "keywords": "cat,dog"},
        {"text": "nothing", "keywords": "cat,dog"},
    ],
    "contains_all": [
        {"text": "cat and dog", "keywords": "cat,dog"},
        {"text": "only cat", "keywords": "cat,dog"},
    ],
    "contains_none": [
        {"text": "the cat sat", "keywords": "cat,dog"},
        {"text": "nothing here", "keywords": "cat,dog"},
    ],
    "starts_with": [
        {"text": "hello world", "substring": "hello"},
        {"text": "hello world", "substring": "world"},
    ],
    "ends_with": [
        {"text": "hello world", "substring": "world"},
        {"text": "hello world", "substring": "hello"},
    ],
    "regex": [
        {"text": "abc123", "pattern": r"\d+"},
        {"text": "abc", "pattern": r"\d+"},
    ],
    "length_less_than": [
        {"text": "short", "max_length": 10},
        {"text": "this text is quite long", "max_length": 10},
    ],
    "length_greater_than": [
        {"text": "this is long enough", "min_length": 5},
        {"text": "hi", "min_length": 5},
    ],
    "length_between": [
        {"text": "medium text", "min_length": 5, "max_length": 20},
        {"text": "x", "min_length": 5, "max_length": 20},
    ],
    "word_count_in_range": [
        {"text": "one two three", "min_words": 1, "max_words": 5},
        {"text": "a b c d e f g", "min_words": 1, "max_words": 5},
    ],
    "step_count": [
        {"output": "step 1\nstep 2\nstep 3", "min_steps": 1, "max_steps": 5}
    ],
    "fleiss_kappa": [
        {"output": "[[5,0],[0,5],[3,2]]"},
        {"output": "[[2,0],[0,3],[3,0],[0,3]]"},
    ],
    "equals": [
        {"text": "cat", "expected_text": "cat"},
        {"text": "cat", "expected_text": "dog"},
    ],
}

_SHAPE_BATTERY = {
    ("output", "expected"): _B_OUTPUT_EXPECTED,
    ("reference", "hypothesis"): _B_REFERENCE_HYPOTHESIS,
    ("hypothesis", "reference"): _B_HYPOTHESIS_REFERENCE,
    ("text",): _B_TEXT,
}

# Evals that cannot be compared here, with the reason (surfaced, never silent).
_EXCLUDED: dict[str, str] = {
    "api_call": "makes a network request",
    "clip_score": "needs image tensors + preprocessing",
    "fid_score": "needs image sets + preprocessing",
    "image_properties": "needs an image + preprocessing",
    "psnr": "needs images + preprocessing",
    "ssim": "needs images + preprocessing",
    "dead_air_detection": "needs audio input",
    "embedding_similarity": "needs an embedding model download",
    "semantic_list_contains": "needs an embedding model download",
    "meteor_score": "platform body reads a precomputed preprocessing value",
    "custom_code_evaluation": "user-supplied template, no fixed body",
}


# ---------------------------------------------------------------------------
# The ledger: SDK/platform divergences that exist on this branch today.
# `correct` records which side matches the metric's definition / a reference
# implementation, or "convention" when both are defensible but differ.
# ---------------------------------------------------------------------------
KNOWN_DIVERGENCES: dict[str, dict] = {
    "fleiss_kappa": {
        "correct": "platform",
        "note": "SDK returns 17.0 on ragged rater counts (kappa is bounded [0,1]); "
        "the YAML rejects ragged input and clamps. Tracked in #1610.",
    },
    "is_sql": {
        "correct": "platform",
        "note": "SDK prefix-matches SQL keywords, so prose like 'Dropbox is a file "
        "sharing service.' scores as valid SQL; the YAML wants a real keyword. #1610.",
    },
    "rouge_score": {
        "correct": "platform",
        "note": "SDK returns ROUGE as a 3-dp *string* ('0.833'), losing precision and "
        "type; the YAML returns the float 0.8333. #1610.",
    },
    "ndcg_at_k": {
        "correct": "sdk",
        "note": "YAML credits a repeated relevant item at every rank, so NDCG exceeds "
        "1.0 (1.63 on ['a','a','c'] vs ['a']); the SDK dedups. Mirror of the MAP fix.",
    },
    "precision_at_k": {
        "correct": "convention",
        "note": "On duplicate retrievals the YAML counts relevant *positions* (2/3) "
        "while the SDK counts distinct relevant docs (1/3). Undocumented either way.",
    },
    "precision_score": {
        "correct": "convention",
        "note": "SDK auto-infers the positive label (matches sklearn macro = 1.0); the "
        "YAML requires an explicit positive_label and fails closed to 0.0 without one.",
    },
    "f_beta_score": {
        "correct": "convention",
        "note": "Same positive_label contract split as precision_score: SDK infers, "
        "YAML requires it and returns 0.0 when absent.",
    },
    "sentence_count": {
        "correct": "convention",
        "note": "With no min/max bounds the SDK returns the count as a pass; the YAML "
        "fails closed ('both min_sentences and max_sentences must be provided').",
    },
    "one_line": {
        "correct": "convention",
        "note": "On empty text the SDK reports a single line (pass); the YAML fails it "
        "('Text is empty').",
    },
    "bleu_score": {
        "correct": "convention",
        "note": "Different BLEU smoothing/weighting: SDK ≈ nltk smoothing1 (0.20 on "
        "'a b c d' vs 'b a c d'), YAML is a BLEU-4 variant (0.54). Not #1345.",
    },
}


# ---------------------------------------------------------------------------
# Build the comparable set once, at import time.
# ---------------------------------------------------------------------------
def _battery_for(name: str, required_keys: tuple) -> list[dict] | None:
    if name in _NUMERIC:
        return _B_NUMERIC
    if name in _CONFIG:
        return _CONFIG[name]
    return _SHAPE_BATTERY.get(required_keys)


def _discover():
    """Return (comparable, uncovered); comparable maps name -> (op_key, battery)."""
    name_to_op = {m.name.lower(): m.value for m in FunctionEvalTypeId}
    comparable: dict[str, tuple] = {}
    uncovered: dict[str, str] = {}
    for path in sorted(YAML_DIR.glob("*.yaml")):
        spec = yaml.safe_load(path.read_text())
        name = spec.get("name")
        if name in _EXCLUDED:
            continue
        op_key = name_to_op.get(name)
        if not op_key or op_key not in operations:
            uncovered[name] = "no functions.py operations counterpart"
            continue
        required_keys = tuple((spec.get("config") or {}).get("required_keys") or [])
        battery = _battery_for(name, required_keys)
        if battery is None:
            uncovered[name] = f"no input battery for required_keys={required_keys}"
            continue
        comparable[name] = (op_key, battery)
    return comparable, uncovered


_COMPARABLE, _UNCOVERED = _discover()


def _max_divergence(name: str) -> tuple[float, dict | None]:
    """Largest |SDK - platform| score gap across the eval's battery."""
    op_key, battery = _COMPARABLE[name]
    sdk_fn = operations[op_key]
    yaml_fn = _load_yaml_eval(name)
    worst, worst_case = 0.0, None
    for inputs in battery:
        sdk = _payload(sdk_fn(**inputs))
        platform = _payload(_call_yaml(yaml_fn, inputs))
        if sdk is None or platform is None:
            continue
        gap = abs(sdk - platform)
        if gap > worst:
            worst, worst_case = gap, {
                "inputs": inputs,
                "sdk": sdk,
                "platform": platform,
            }
    return worst, worst_case


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def _agree_ids():
    return sorted(n for n in _COMPARABLE if n not in KNOWN_DIVERGENCES)


@pytest.mark.parametrize("name", _agree_ids())
def test_sdk_matches_platform(name):
    """The SDK and platform implementations must return the same score."""
    worst, case = _max_divergence(name)
    assert worst < TOL, (
        f"SDK/platform divergence in '{name}': {case}. "
        f"If this is intentional, add it to KNOWN_DIVERGENCES with a rationale."
    )


@pytest.mark.parametrize("name", sorted(KNOWN_DIVERGENCES))
def test_known_divergence_still_present(name):
    """Pin each documented divergence so a one-sided fix is noticed here too.

    When a fix makes the pair agree, this fails and the ledger entry must be
    removed — that is the intended signal, not a flake.
    """
    if name not in _COMPARABLE:
        pytest.skip(f"'{name}' is not currently comparable")
    worst, _ = _max_divergence(name)
    assert worst >= TOL, (
        f"'{name}' no longer diverges between SDK and platform — remove it from "
        f"KNOWN_DIVERGENCES (ledger entry: {KNOWN_DIVERGENCES[name]['note']})."
    )


def test_no_undocumented_divergence():
    """The ratchet: the set of diverging evals must equal the ledger exactly."""
    observed = {n for n in _COMPARABLE if _max_divergence(n)[0] >= TOL}
    ledgered = set(KNOWN_DIVERGENCES)
    new = observed - ledgered
    fixed = ledgered - observed
    assert not new, (
        f"New SDK/platform divergence(s): {sorted(new)}. The same eval returns "
        f"different scores from the SDK and the platform. Fix one side or document it."
    )
    assert not fixed, (
        f"These evals no longer diverge: {sorted(fixed)}. Remove them from "
        f"KNOWN_DIVERGENCES."
    )


def test_coverage_is_accounted_for(capsys):
    """No silent gaps: every YAML function eval is compared, excluded, or uncovered."""
    all_yaml = {p.stem for p in YAML_DIR.glob("*.yaml")}
    compared = set(_COMPARABLE)
    excluded = set(_EXCLUDED)
    uncovered = set(_UNCOVERED)
    accounted = compared | excluded | uncovered
    missing = all_yaml - accounted
    assert (
        not missing
    ), f"Unaccounted-for evals (neither compared nor logged): {sorted(missing)}"

    with capsys.disabled():
        print(
            f"\n[parity] {len(all_yaml)} YAML function evals: "
            f"{len(compared)} compared "
            f"({len(compared) - len(KNOWN_DIVERGENCES)} agree, "
            f"{len(KNOWN_DIVERGENCES)} known-divergent), "
            f"{len(excluded)} excluded, {len(uncovered)} uncovered."
        )
        if uncovered:
            print(f"[parity] uncovered: {sorted(uncovered)}")


def test_something_was_compared():
    """Guard against the harness silently degrading to zero coverage."""
    assert (
        len(_COMPARABLE) >= 60
    ), f"Only {len(_COMPARABLE)} evals comparable; expected 60+."
