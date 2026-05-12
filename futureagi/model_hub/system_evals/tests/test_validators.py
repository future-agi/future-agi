"""
Golden tests for system eval validators + the shared parse family.

Each test loads the corresponding YAML, materializes the embedded code body
in an isolated namespace, then exercises the validator directly. The goal
is to pin eval-body correctness, not test the sandbox dispatch path.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

YAML_DIR = Path(__file__).resolve().parent.parent / "function"


def _load_eval(name: str):
    """Load YAML, materialize the code body, return the evaluate callable."""
    path = YAML_DIR / f"{name}.yaml"
    code = yaml.safe_load(path.read_text())["config"]["code"]
    ns: dict = {}
    runner = __builtins__["exec"] if isinstance(__builtins__, dict) else getattr(__builtins__, "exec")
    runner(compile(code, str(path), "exec"), ns)
    return ns["evaluate"]


# ---------------------------------------------------------------------------
# Shared _parse family
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "eval_name", [
        "accuracy",
        "balanced_accuracy",
        "cohen_kappa",
        "f_beta_score",
        "matthews_correlation",
        "precision_score",
    ],
)
def test_empty_inputs_score_one(eval_name):
    ev = _load_eval(eval_name)
    kwargs = {}
    if eval_name in ("f_beta_score", "precision_score"):
        kwargs["positive_label"] = "yes"
    r = ev(None, None, None, None, **kwargs)
    assert r["score"] == 1.0, f"{eval_name}: empty/empty must score 1.0, got {r}"


@pytest.mark.parametrize(
    "eval_name", [
        "accuracy",
        "balanced_accuracy",
        "cohen_kappa",
        "f_beta_score",
        "matthews_correlation",
        "precision_score",
    ],
)
def test_whitespace_normalization(eval_name):
    ev = _load_eval(eval_name)
    # Pass output/expected positionally — kwargs would collide with the
    # signature's `output=` / `expected=` parameter names.
    extra = {}
    if eval_name in ("f_beta_score", "precision_score"):
        extra["positive_label"] = "yes"
    r1 = ev(None, ["yes", "no"], ["yes", "no"], None, **extra)
    r2 = ev(None, ["yes ", " no"], ["yes", "no"], None, **extra)
    assert r1["score"] == r2["score"], (
        f"{eval_name}: whitespace changed score "
        f"clean={r1['score']} vs ws={r2['score']}"
    )


def test_accuracy_basic():
    ev = _load_eval("accuracy")
    r = ev(None, ["a", "b", "c"], ["a", "b", "c"], None)
    assert r["score"] == 1.0
    r = ev(None, ["a", "b", "c"], ["a", "b", "d"], None)
    assert r["score"] == pytest.approx(2 / 3, rel=1e-3)


def test_log_loss_empty():
    ev = _load_eval("log_loss")
    r = ev(None, None, None, None)
    assert r["score"] == 1.0


# ---------------------------------------------------------------------------
# is_email
# ---------------------------------------------------------------------------


@pytest.fixture
def is_email():
    return _load_eval("is_email")


@pytest.mark.parametrize("addr", [
    "foo@example.com",
    "a.b@example.com",
    "first+tag@x.io",
    "user_123@sub.example.co.uk",
])
def test_is_email_accepts_valid(is_email, addr):
    assert is_email(None, addr, None, None, text=addr)["score"] == 1.0


@pytest.mark.parametrize("addr", [
    "a..b@x.com",
    ".a@x.com",
    "a.@x.com",
    "a@.x.com",
    "a@-x.com",
    "a@x-.com",
])
def test_is_email_rejects_rfc_violations(is_email, addr):
    assert is_email(None, addr, None, None, text=addr)["score"] == 0.0, addr


# ---------------------------------------------------------------------------
# is_html
# ---------------------------------------------------------------------------


@pytest.fixture
def is_html():
    return _load_eval("is_html")


def test_is_html_accepts_valid(is_html):
    assert is_html(None, "<p>hello</p>", None, None, text="<p>hello</p>")["score"] == 1.0
    assert is_html(None, "<b><i>x</i></b>", None, None, text="<b><i>x</i></b>")["score"] == 1.0


def test_is_html_rejects_orphan_close(is_html):
    assert is_html(None, "<p></p></p>", None, None, text="<p></p></p>")["score"] == 0.0


def test_is_html_rejects_mismatched_close(is_html):
    assert is_html(None, "<b></i></b>", None, None, text="<b></i></b>")["score"] == 0.0


# ---------------------------------------------------------------------------
# is_sql
# ---------------------------------------------------------------------------


@pytest.fixture
def is_sql():
    return _load_eval("is_sql")


def test_is_sql_accepts_real_statements(is_sql):
    for q in [
        "SELECT * FROM users",
        "INSERT INTO t VALUES (1)",
        "UPDATE t SET x = 1",
        "DELETE FROM t WHERE id = 1",
        "CREATE TABLE t (id INT)",
        "DROP TABLE t",
    ]:
        assert is_sql(None, q, None, None, text=q)["score"] == 1.0, q


def test_is_sql_rejects_prose(is_sql):
    bad = "DROP me like a hot potato"
    assert is_sql(None, bad, None, None, text=bad)["score"] == 0.0


def test_is_sql_rejects_bare_keyword(is_sql):
    assert is_sql(None, "SELECT", None, None, text="SELECT")["score"] == 0.0


def test_is_sql_comment_apostrophe_no_false_negative(is_sql):
    q = "SELECT * FROM t -- it's a comment"
    assert is_sql(None, q, None, None, text=q)["score"] == 1.0


# ---------------------------------------------------------------------------
# is_url
# ---------------------------------------------------------------------------


@pytest.fixture
def is_url():
    return _load_eval("is_url")


@pytest.mark.parametrize("u", [
    "https://example.com",
    "http://sub.example.co.uk/path?q=1",
    "mailto:user@example.com",
    "file:///etc/passwd",
    "tel:+15551234567",
])
def test_is_url_accepts_valid(is_url, u):
    assert is_url(None, u, None, None, text=u)["score"] == 1.0, u


@pytest.mark.parametrize("u", [
    "not a url",
    "http://",
    "http://.example.com",
    "http://-example.com",
    "http://example-.com",
])
def test_is_url_rejects_malformed(is_url, u):
    assert is_url(None, u, None, None, text=u)["score"] == 0.0, u


# ---------------------------------------------------------------------------
# is_xml
# ---------------------------------------------------------------------------


@pytest.fixture
def is_xml():
    return _load_eval("is_xml")


def test_is_xml_accepts_well_formed(is_xml):
    for x in ["<root><child/></root>", "<root>text</root>"]:
        assert is_xml(None, x, None, None, text=x)["score"] == 1.0, x


def test_is_xml_rejects_doctype(is_xml):
    payload = '<!DOCTYPE x><x/>'
    assert is_xml(None, payload, None, None, text=payload)["score"] == 0.0


def test_is_xml_rejects_billion_laughs(is_xml):
    payload = (
        '<!DOCTYPE lolz [<!ENTITY lol "lol">'
        '<!ENTITY lol2 "&lol;&lol;">]>'
        '<lolz>&lol2;</lolz>'
    )
    assert is_xml(None, payload, None, None, text=payload)["score"] == 0.0


# ---------------------------------------------------------------------------
# json_diff
# ---------------------------------------------------------------------------


def test_json_diff_identical_empty_dict_is_perfect():
    ev = _load_eval("json_diff")
    assert ev(None, "{}", "{}", None)["score"] == 1.0


def test_json_diff_identical_empty_list_is_perfect():
    ev = _load_eval("json_diff")
    assert ev(None, "[]", "[]", None)["score"] == 1.0


def test_json_diff_nested_empty_is_perfect():
    ev = _load_eval("json_diff")
    assert ev(None, '{"a":{}}', '{"a":{}}', None)["score"] == 1.0
    assert ev(None, '{"a":[]}', '{"a":[]}', None)["score"] == 1.0


# ---------------------------------------------------------------------------
# fleiss_kappa
# ---------------------------------------------------------------------------


def test_fleiss_kappa_score_always_in_unit_interval():
    import json
    ev = _load_eval("fleiss_kappa")
    for matrix in [
        [[5, 0], [5, 0], [5, 0]],
        [[3, 2], [2, 3], [3, 2]],
        [[5, 0], [7, 0]],
        [[5, 0], [5]],
    ]:
        r = ev(None, json.dumps(matrix), None, None)
        assert 0.0 <= r["score"] <= 1.0, f"{matrix} -> {r}"


def test_fleiss_kappa_rejects_inconsistent_rows():
    import json
    ev = _load_eval("fleiss_kappa")
    r = ev(None, json.dumps([[5, 0], [7, 0]]), None, None)
    assert r["score"] == 0.0
    assert "rater count" in r["reason"].lower() or "rows" in r["reason"].lower()


# ---------------------------------------------------------------------------
# match_error_rate / word_info_lost
# ---------------------------------------------------------------------------


def test_mer_insertion_does_not_zero_hits():
    ev = _load_eval("match_error_rate")
    r = ev(None, "the cat sat happily", "the cat sat", None,
           reference="the cat sat", hypothesis="the cat sat happily")
    assert "H=3" in r["reason"], r["reason"]


def test_wil_insertion_does_not_zero_hits():
    ev = _load_eval("word_info_lost")
    r = ev(None, "the cat sat happily", "the cat sat", None,
           reference="the cat sat", hypothesis="the cat sat happily")
    assert "H=3" in r["reason"], r["reason"]


# ---------------------------------------------------------------------------
# meteor_score
#
# METEOR moved out of the sandbox: heavy lifting runs in
# `evaluations.engine.preprocessing._preprocess_meteor` (backend has WordNet),
# and the eval body is now a thin reader of `_meteor_precomputed_score`. So
# the tests exercise the full preprocess → eval-body path.
# ---------------------------------------------------------------------------


def _meteor_full_path(reference, hypothesis):
    """Run the preprocessor + eval body the same way `run_eval_func` would."""
    from evaluations.engine.preprocessing import preprocess_inputs
    ev = _load_eval("meteor_score")
    inputs = {"reference": reference, "hypothesis": hypothesis}
    inputs = preprocess_inputs("meteor_score", inputs)
    return ev(None, hypothesis, reference, None, **inputs)


def test_meteor_body_without_preprocessing_reports_required():
    """Sanity: eval body alone (no preprocessor run) must surface a clear
    error, not silently zero-score."""
    ev = _load_eval("meteor_score")
    r = ev(None, "the cat sat", "the cat sat", None,
           reference="the cat sat", hypothesis="the cat sat")
    assert r["score"] == 0.0
    assert "preprocessing" in r["reason"].lower()


def test_meteor_perfect_match_high():
    r = _meteor_full_path("the cat sat on the mat", "the cat sat on the mat")
    assert r["score"] > 0.95


def test_meteor_reorder_lower_than_perfect():
    perfect = _meteor_full_path("the cat sat on the mat", "the cat sat on the mat")
    reordered = _meteor_full_path("the cat sat on the mat", "mat the on cat sat the")
    assert reordered["score"] < perfect["score"]
    assert reordered["score"] > 0


def test_meteor_both_empty_is_perfect():
    r = _meteor_full_path("", "")
    assert r["score"] == 1.0


def test_meteor_one_empty_reports_missing():
    r = _meteor_full_path("the cat sat", "")
    assert r["score"] == 0.0
    assert "missing" in r["reason"].lower()
