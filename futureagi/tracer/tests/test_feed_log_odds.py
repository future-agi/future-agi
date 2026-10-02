"""Pure-unit pins for ``_log_odds_distinctive`` (stateless, no DB).

The NULLS-LAST ordering fix in ``trace_judge``/``_*_judges_batch`` is a
DB-backed guarantee over real ``EvalLogger`` rows, so it's tested there, not here.
"""

from tracer.queries.feed import _log_odds_distinctive

# Two well-separated failure topics vs two unrelated baseline topics. Reps are
# kept high (20) because the informative Dirichlet prior (a0 = |V|) still
# regularizes small-N corpora on purpose — a perfectly-separated bigram needs
# ~8 reps to clear the default min_z (1.96), so a handful of docs would yield []
# and mask the behavior we want to pin.
_FAIL_DOCS = ["database connection timeout error"] * 20 + [
    "payment gateway declined card"
] * 20
_BASE_DOCS = ["request finished successfully fast"] * 20 + [
    "user opened settings page"
] * 20


def test_surfaces_distinctive_fail_bigrams_at_default_threshold():
    """Default-arg call (mirrors the production call path) returns only
    fail-side n-grams, each with positive z and zero baseline document
    frequency."""
    out = _log_odds_distinctive(_FAIL_DOCS, _BASE_DOCS)

    assert out, "expected distinctive fail-side n-grams at the default threshold"
    for term, z, df_fail, df_base in out:
        assert " " in term  # bigrams/trigrams only
        assert z > 0  # distinctive *to* fail, not to baseline
        assert df_fail > 0 and df_base == 0  # appears in fail docs, never baseline


def test_longer_phrase_wins_on_z_tie():
    """Correlated n-grams tie on z; the longer phrase sorts first so the
    readable evidence phrase ("connection timeout error") beats its fragment
    ("connection timeout")."""
    terms = [term for term, *_ in _log_odds_distinctive(_FAIL_DOCS, _BASE_DOCS)]
    assert "connection timeout error" in terms
    assert "connection timeout" in terms
    assert terms.index("connection timeout error") < terms.index("connection timeout")


def test_empty_inputs_return_empty():
    assert _log_odds_distinctive([], _BASE_DOCS) == []
    assert _log_odds_distinctive(_FAIL_DOCS, []) == []
