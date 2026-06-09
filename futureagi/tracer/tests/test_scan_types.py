"""
Embedding-input contract for ClusterableIssue.

The clustering pipeline embeds `embedding_text` per issue and assigns to
clusters by cosine distance. Key moments are trace-specific verbatim
quotes that dominated the embedding and fragmented same-issue findings
across many singleton clusters. We embed the brief alone — the issue
described, not the surrounding trace text.
"""

from tracer.types.scan_types import ClusterableIssue


def _issue(brief: str, key_moments: list[str] | None = None) -> ClusterableIssue:
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
