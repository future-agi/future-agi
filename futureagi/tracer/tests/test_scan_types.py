"""
Embedding-input contract for ClusterableIssue.

The clustering pipeline embeds `embedding_text` per issue and assigns to
clusters by cosine distance. Key moments are trace-specific verbatim
quotes that dominated the embedding and fragmented same-issue findings
across many singleton clusters. We embed the brief alone — the issue
described, not the surrounding trace text.

The brief carries the same disease in milder form: it names this trace's
ticker, client and feature. Across one project's 1,277 real issues, 1,229
briefs (96%) were distinct strings describing far fewer distinct bugs. So
the brief is what users READ, and the distilled phrase is what we EMBED.
"""

from tracer.types.scan_types import ClusterableIssue


def _issue(
    brief: str,
    key_moments: list[str] | None = None,
    distilled: str | None = None,
) -> ClusterableIssue:
    return ClusterableIssue(
        issue_id="i1",
        trace_id="t1",
        project_id="p1",
        category="reasoning",
        group="g1",
        fix_layer="prompt",
        brief=brief,
        confidence="high",
        key_moments_text=key_moments or [],
        distilled=distilled,
    )


def test_embedding_text_is_brief_only():
    issue = _issue(
        brief="Agent ignored the user's stated currency preference.",
        key_moments=["user said USD", "agent replied in EUR"],
    )
    assert issue.embedding_text == "Agent ignored the user's stated currency preference."


def test_embedding_text_ignores_empty_key_moments():
    issue = _issue(brief="Tool output contradicted the final answer.")
    assert issue.embedding_text == "Tool output contradicted the final answer."


def test_embedding_text_same_brief_clusters_across_traces():
    """Two issues with identical briefs but different per-trace key moments
    must produce identical embedding inputs — that's the whole point."""
    brief = "Agent hallucinated a non-existent API endpoint."
    a = _issue(brief, key_moments=["called /v1/foo", "got 404"])
    b = _issue(brief, key_moments=["called /v2/bar", "got 404"])
    assert a.embedding_text == b.embedding_text


def test_distilled_phrase_is_what_gets_embedded():
    issue = _issue(
        brief="Stated NVDA gain of $5,014.68 for client Arjun Malhotra without a tool call",
        distilled="agent states portfolio figures without calling data tools",
    )
    assert issue.embedding_text == "agent states portfolio figures without calling data tools"


def test_distillation_collapses_briefs_that_differ_only_by_entity():
    """The fragmentation this exists to fix: one bug, two tickers, two clusters."""
    a = _issue(
        brief="Stated NVDA price without calling the quote tool",
        distilled="agent states stock price without calling data tools",
    )
    b = _issue(
        brief="Reported MSFT valuation without invoking the lookup tool",
        distilled="agent states stock price without calling data tools",
    )
    assert a.brief != b.brief
    assert a.embedding_text == b.embedding_text


def test_falls_back_to_the_brief_when_distillation_is_unavailable():
    """OSS has no distiller and a batch can fail; clustering must still run on
    the raw brief rather than embed an empty string."""
    issue = _issue(brief="Tool output contradicted the final answer.", distilled=None)
    assert issue.embedding_text == "Tool output contradicted the final answer."
