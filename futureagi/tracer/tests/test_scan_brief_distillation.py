"""The scanner clustering must embed distilled phrases, not raw briefs.

``ClusterableIssue.embedding_text`` already prefers ``distilled``, but nothing
sets it unless ``cluster_issues`` asks for it — an unwired distiller leaves the
dataclass contract satisfied and the feed just as fragmented. Replayed over one
project's 1,277 real production issues through the real assignment loop, at the
production threshold and category partition:

    raw briefs   226 feed entries, 131 singletons (58%), top 10 cover 45%
    distilled     52 feed entries,  26 singletons (50%), top 10 cover 88%

The eval clustering path has done this since it shipped
(``distill_eval_failure_phrases`` in tracer/utils/eval_clustering.py); these pin
the scanner path to the same contract.
"""

import uuid
from unittest.mock import patch

import pytest

from tracer.types.scan_types import ClusterableIssue
from tracer.utils.trace_scanner import cluster_issues


def _issue(brief: str, category: str = "Language-only") -> ClusterableIssue:
    return ClusterableIssue(
        issue_id=str(uuid.uuid4()),
        trace_id=str(uuid.uuid4()),
        project_id="p1",
        category=category,
        group="Tool Failures",
        fix_layer="Tools",
        brief=brief,
        confidence="high",
    )


RAW = [
    "Stated NVDA price without calling the quote tool",
    "Reported MSFT valuation without invoking the lookup tool",
]
CANON = "agent states stock price without calling data tools"


@pytest.fixture
def issues():
    return [_issue(b) for b in RAW]


def _run(issues, distiller):
    """Drive cluster_issues far enough to capture what it embedded."""
    captured = {}

    def _embed(texts):
        captured["texts"] = list(texts)
        return [[1.0, 0.0] for _ in texts]

    with patch("tracer.utils.trace_scanner.get_unclustered_issues", return_value=issues), \
         patch("tracer.utils.trace_scanner.distill_scan_briefs", side_effect=distiller), \
         patch("tracer.utils.trace_scanner.embed_texts", side_effect=_embed), \
         patch("tracer.utils.trace_scanner.find_nearest_centroid", return_value=None), \
         patch("tracer.utils.trace_scanner.create_cluster", return_value="C1"):
        cluster_issues("p1")
    return captured.get("texts")


def test_embeds_the_distilled_phrase_not_the_raw_brief(issues):
    def distiller(items):
        for i in items:
            i.distilled = CANON
        return items

    assert _run(issues, distiller) == [CANON, CANON]


def test_two_briefs_differing_only_by_ticker_embed_identically(issues):
    """One bug, two tickers. Raw briefs are distinct strings and seed two
    clusters; that is the 96%-distinct-briefs fragmentation in miniature."""
    def distiller(items):
        for i in items:
            i.distilled = CANON
        return items

    embedded = _run(issues, distiller)
    assert issues[0].brief != issues[1].brief
    assert len(set(embedded)) == 1


def test_falls_back_to_raw_briefs_when_the_distiller_is_a_no_op(issues):
    """OSS has no distiller and a gateway batch can fail. Clustering must still
    run on the raw briefs rather than embed empty strings."""
    assert _run(issues, lambda items: items) == RAW
