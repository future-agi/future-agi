"""Reference-value tests for the pure-Python/embedding metric functions in
``functions.py``.

These are the metrics that need no LLM judge and no network access, so they
run anywhere (including OSS-only installs without ``ee/``). Retrieval and
count-based metrics (Recall@k, Precision@k, NDCG@k, MRR, Hit Rate, MAP,
F1, WER, CER, non-LLM context precision/recall) are checked against
hand-derived expected values computed independently from the production
code path. BLEU/ROUGE intentionally delegate to nltk/rouge_score, so those
tests assert the wrapper actually calls through rather than re-deriving the
library's own math.
"""

import math

import pytest

from agentic_eval.core_evals.fi_evals.function.functions import (
    calculate_bleu,
    calculate_character_error_rate,
    calculate_f1_score,
    calculate_gleu,
    calculate_mean_average_precision,
    calculate_rouge,
    calculate_word_error_rate,
    hit_rate,
    mean_reciprocal_rank,
    ndcg_at_k,
    non_llm_context_precision,
    non_llm_context_recall,
    precision_at_k,
    recall_at_k,
    recall_score,
)


# ---------------------------------------------------------------------------
# BLEU / ROUGE — thin wrappers over nltk / rouge_score; verify delegation.
# ---------------------------------------------------------------------------


def test_bleu_identical_sentences_scores_near_one():
    result = calculate_bleu("the cat sat on the mat", "the cat sat on the mat")
    assert result["result"] == pytest.approx(1.0, abs=1e-6)


def test_bleu_unrelated_sentences_scores_low():
    identical = calculate_bleu("the cat sat on the mat", "the cat sat on the mat")
    unrelated = calculate_bleu("the cat sat on the mat", "quantum entanglement theory")
    assert unrelated["result"] < identical["result"]


def test_rouge_identical_sentences_scores_one():
    result = calculate_rouge("the cat sat on the mat", "the cat sat on the mat")
    assert float(result["result"]) == pytest.approx(1.0, abs=1e-3)


def test_rouge_returns_rouge1_fmeasure_as_string():
    # calculate_rouge reports ROUGE-1 F-measure formatted to 3 decimals.
    result = calculate_rouge("a b c d", "a b")
    # precision=2/2=1.0, recall=2/4=0.5, f = 2*1*0.5/1.5 = 0.6667
    assert float(result["result"]) == pytest.approx(0.667, abs=1e-3)


# ---------------------------------------------------------------------------
# F1Score — token-overlap F1 between two texts.
# ---------------------------------------------------------------------------


def test_f1_identical_texts():
    result = calculate_f1_score("the cat sat", "the cat sat")
    assert result["result"] == pytest.approx(1.0)


def test_f1_partial_overlap():
    # out=[the,cat,sat] (3), exp=[the,dog,sat,on,the,mat] (6)
    # overlap (multiset intersection) = the:1, sat:1 -> 2
    # precision = 2/3, recall = 2/6, f1 = 2PR/(P+R) = 4/9
    result = calculate_f1_score("the cat sat", "the dog sat on the mat")
    assert result["result"] == pytest.approx(4 / 9)


def test_f1_no_overlap_is_zero():
    result = calculate_f1_score("foo bar", "baz qux")
    assert result["result"] == 0.0


def test_f1_both_empty_is_one():
    result = calculate_f1_score("", "")
    assert result["result"] == 1.0


def test_f1_one_side_empty_is_zero():
    result = calculate_f1_score("", "some text")
    assert result["result"] == 0.0


# ---------------------------------------------------------------------------
# Retrieval metrics — Recall@k, Precision@k, NDCG@k, MRR, Hit Rate, MAP.
# Shared fixture: 3 relevant docs, a 5-item ranked retrieval with 2 hits.
# ---------------------------------------------------------------------------

GROUND_TRUTH = ["doc1", "doc2", "doc3"]
RETRIEVED = ["doc5", "doc1", "doc7", "doc2", "doc9"]  # hits at rank 2 and 4


def test_recall_score_counts_all_hits_regardless_of_rank():
    # {doc1, doc2} retrieved out of 3 relevant -> 2/3
    result = recall_score(GROUND_TRUTH, RETRIEVED)
    assert result["result"] == pytest.approx(2 / 3)


def test_recall_at_k_respects_cutoff():
    # top_3 = [doc5, doc1, doc7] -> only doc1 is relevant -> 1/3
    result = recall_at_k(GROUND_TRUTH, RETRIEVED, k=3)
    assert result["result"] == pytest.approx(1 / 3)


def test_recall_at_k_full_list_when_k_omitted():
    result = recall_at_k(GROUND_TRUTH, RETRIEVED)
    assert result["result"] == pytest.approx(2 / 3)


def test_precision_at_k():
    # top_3 = [doc5, doc1, doc7] -> 1 hit / k=3
    result = precision_at_k(GROUND_TRUTH, RETRIEVED, k=3)
    assert result["result"] == pytest.approx(1 / 3)


def test_precision_at_k_empty_top_k_is_zero():
    result = precision_at_k(GROUND_TRUTH, [], k=3)
    assert result["result"] == 0.0


def test_recall_and_precision_at_k_raise_on_empty_ground_truth():
    with pytest.raises(ValueError):
        recall_at_k([], RETRIEVED, k=3)
    with pytest.raises(ValueError):
        precision_at_k([], RETRIEVED, k=3)


def test_ndcg_at_k_matches_independent_log2_discount_formula():
    # Relevant hits at 1-indexed ranks 2 (doc1) and 4 (doc2); rank 9 for
    # doc3 doesn't exist within the 5-item list, so it never contributes.
    hit_ranks = [2, 4]
    dcg = sum(1.0 / math.log2(rank + 1) for rank in hit_ranks)
    ideal_hits = min(len(GROUND_TRUTH), 5)  # k=5 (full list), 3 relevant docs
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, ideal_hits + 1))
    expected = dcg / idcg

    result = ndcg_at_k(GROUND_TRUTH, RETRIEVED, k=5)
    assert result["result"] == pytest.approx(expected)


def test_ndcg_at_k_does_not_double_count_repeated_relevant_items():
    # doc1 appears twice in the ranked list; binary relevance must credit
    # it once, at its first (best) rank, not twice.
    hypothesis = ["doc1", "doc1", "doc9"]
    reference = ["doc1"]
    dcg = 1.0 / math.log2(1 + 1)  # single credit at rank 1
    idcg = 1.0 / math.log2(1 + 1)  # 1 relevant doc, ideal rank 1
    result = ndcg_at_k(reference, hypothesis, k=3)
    assert result["result"] == pytest.approx(dcg / idcg)
    assert result["result"] == pytest.approx(1.0)


def test_mean_reciprocal_rank_single_query():
    # First relevant item ("doc1") is at 1-indexed rank 3.
    result = mean_reciprocal_rank(["doc1", "doc2"], ["doc5", "doc7", "doc1", "doc2"])
    assert result["result"] == pytest.approx(1 / 3)


def test_mean_reciprocal_rank_no_hit_is_zero():
    result = mean_reciprocal_rank(["doc1"], ["doc5", "doc7"])
    assert result["result"] == 0.0


def test_mean_reciprocal_rank_multi_query_averages_reciprocal_ranks():
    reference = [["a"], ["b"], ["c"]]
    hypothesis = [["x", "a", "y"], ["b", "z"], ["x", "y", "z"]]
    # RR: 1/2 (a at rank 2), 1/1 (b at rank 1), 0 (c never retrieved)
    expected = (0.5 + 1.0 + 0.0) / 3
    result = mean_reciprocal_rank(reference, hypothesis)
    assert result["result"] == pytest.approx(expected)


def test_hit_rate_single_query():
    assert hit_rate(["doc1", "doc2"], ["doc5", "doc7"])["result"] == 0.0
    assert hit_rate(["doc1", "doc2"], ["doc5", "doc1"])["result"] == 1.0


def test_hit_rate_multi_query_is_mean_of_indicators():
    reference = [["a"], ["b"], ["c"]]
    hypothesis = [["x", "a"], ["b", "z"], ["x", "y"]]
    # hits: 1 (a found), 1 (b found), 0 (c not found) -> mean 2/3
    result = hit_rate(reference, hypothesis)
    assert result["result"] == pytest.approx(2 / 3)


def test_mean_average_precision_single_query():
    # Hits at ranks 2 (doc1) and 4 (doc2): AP = (1/2 + 2/4) / 3
    result = calculate_mean_average_precision(GROUND_TRUTH, RETRIEVED)
    assert result["result"] == pytest.approx((0.5 + 0.5) / 3)


def test_mean_average_precision_multi_query():
    reference = [["a", "b"], ["c"]]
    hypothesis = [["a", "x", "b"], ["x", "c"]]
    # q1: hits at rank 1 (a) and rank 3 (b) -> AP = (1/1 + 2/3) / 2 = 5/6
    # q2: hit at rank 2 (c) -> AP = (1/2) / 1 = 1/2
    expected = ((1 / 1 + 2 / 3) / 2 + (1 / 2) / 1) / 2
    result = calculate_mean_average_precision(reference, hypothesis)
    assert result["result"] == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Non-LLM context precision / recall (RAG-specific, no judge required).
# ---------------------------------------------------------------------------


def test_non_llm_context_precision():
    retrieved = ["ctx_a", "ctx_b", "ctx_c", "ctx_d"]
    reference = ["ctx_b", "ctx_d", "ctx_x"]
    result = non_llm_context_precision(retrieved, reference)
    assert result["result"] == pytest.approx(2 / 4)


def test_non_llm_context_recall():
    retrieved = ["ctx_a", "ctx_b", "ctx_c", "ctx_d"]
    reference = ["ctx_b", "ctx_d", "ctx_x"]
    result = non_llm_context_recall(retrieved, reference)
    assert result["result"] == pytest.approx(2 / 3)


def test_non_llm_context_precision_recall_are_case_insensitive():
    retrieved = ["Context A", "context b"]
    reference = ["CONTEXT A"]
    precision = non_llm_context_precision(retrieved, reference)
    recall = non_llm_context_recall(retrieved, reference)
    assert precision["result"] == pytest.approx(1 / 2)
    assert recall["result"] == pytest.approx(1.0)


def test_non_llm_context_precision_empty_retrieved_is_zero():
    assert non_llm_context_precision([], ["ctx_a"])["result"] == 0.0


def test_non_llm_context_recall_empty_reference_and_retrieved_is_one():
    assert non_llm_context_recall([], [])["result"] == 1.0


def test_non_llm_context_recall_empty_reference_nonempty_retrieved_is_zero():
    assert non_llm_context_recall(["ctx_a"], [])["result"] == 0.0


# ---------------------------------------------------------------------------
# WER / CER — Levenshtein-based error rates for ASR/OCR-style evaluation.
# ---------------------------------------------------------------------------


def test_word_error_rate_identical_is_perfect_score():
    result = calculate_word_error_rate("the cat sat", "the cat sat")
    assert result["result"] == pytest.approx(1.0)


def test_word_error_rate_single_deletion():
    # ["a","b","c","d"] vs ["a","b","c"]: one deletion -> WER = 1/4
    result = calculate_word_error_rate("a b c d", "a b c")
    assert result["result"] == pytest.approx(1 - 1 / 4)


def test_word_error_rate_both_empty_is_perfect_score():
    result = calculate_word_error_rate("", "")
    assert result["result"] == 1.0


def test_word_error_rate_empty_reference_is_zero():
    result = calculate_word_error_rate("", "some hypothesis text")
    assert result["result"] == 0.0


def test_character_error_rate_single_substitution():
    # "cat" vs "cot": 1 character substitution -> CER = 1/3
    result = calculate_character_error_rate("cat", "cot")
    assert result["result"] == pytest.approx(1 - 1 / 3)


def test_character_error_rate_identical_is_perfect_score():
    result = calculate_character_error_rate("cat", "cat")
    assert result["result"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# GLEU — characterization tests for CURRENT behavior.
#
# calculate_gleu takes the geometric mean of per-n-gram-order min(precision,
# recall) for n in 1..min(4, len), and returns 0.0 the instant ANY single
# order has zero clipped overlap (functions.py, ``if gleu_n == 0: return``).
# Standard Google GLEU (Wu et al., 2016) instead pools n-gram matches/totals
# across ALL orders before taking a single min(precision, recall) over the
# pooled counts, so a realistic short paraphrase with no matching 4-gram
# still gets credit from its 1-3-gram overlap. These tests pin the current,
# non-standard behavior rather than "fix" it silently, since the scoring
# change would be user-visible; the divergence is flagged for a follow-up.
# ---------------------------------------------------------------------------


def test_gleu_identical_sentences_scores_near_one():
    result = calculate_gleu("the cat sat on the mat", "the cat sat on the mat")
    assert result["result"] == pytest.approx(1.0, abs=1e-6)


def test_gleu_short_paraphrase_collapses_to_zero_current_behavior():
    # A realistic near-paraphrase: substantial 1-3-gram overlap (~0.57,
    # 0.33, 0.20) but no shared 4-gram. Standard GLEU would report a
    # positive score reflecting that partial overlap; the current
    # implementation reports exactly 0.0 because 4-grams have zero overlap.
    reference = "the cat is sitting on the mat"
    hypothesis = "a cat sits on the mat"
    result = calculate_gleu(reference, hypothesis)
    assert result["result"] == 0.0


def test_gleu_completely_unrelated_sentences_also_scores_zero():
    reference = "the cat sat on the mat"
    hypothesis = "quantum entanglement theory"
    result = calculate_gleu(reference, hypothesis)
    assert result["result"] == 0.0
