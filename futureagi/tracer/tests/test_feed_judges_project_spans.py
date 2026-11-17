"""An issue's evaluation reads keep only its own project's rows.

A trace id can be held by projects of several organizations (a replay, a
re-import, a shared provider account). An evaluation row names a trace id and
a span id. The issue views read evaluation rows by trace id: the judge reason
and score of each trace card, the per-trace average score, the sidebar's
evaluation list, the Trends tab's score chart, and the average score in the
Traces tab's summary bar, on the Trends tab and in the sidebar's metadata.
Each read must keep only the rows of the issue's own project: a row with an
eval config when the config is the project's, and a row with no config when
its span is one the project holds on that trace. The score chart reads rows
with an eval config only.

These cases pin the change in the next commit.

Organization B owns the issue. Organization A holds the shared trace id too,
with its own span, eval configs and evaluation rows, and one trace of its own.
Each project also has a row with no eval config on its own span. Postgres runs
for real. The ClickHouse span read is a stand-in that holds each project's
spans; the feed's other ClickHouse reads are stubbed.
"""

import uuid
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.db.models import Q
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models.organization import Organization
from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from model_hub.models.evals_metric import EvalTemplate
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.observation_span import EvalLogger, EvalTargetType
from tracer.models.project import Project
from tracer.models.trace_error_analysis import (
    ClusterSource,
    ErrorClusterTraces,
    TraceErrorGroup,
)
from tracer.queries import feed
from tracer.views.feed.linear_issue_view import CreateLinearIssueView

pytestmark = pytest.mark.django_db

# The sidebar's metadata read itself; the issue fixture puts a stand-in in its
# place for the cases that do not read the metadata.
_SIDEBAR_AI_METADATA = feed._fetch_sidebar_ai_metadata

# Held by both organizations, each with its own span.
SHARED_TRACE = str(uuid.uuid4())
# Held by B only; a member of B's issue.
OWN_TRACE = str(uuid.uuid4())
# Held by A only; not a member of B's issue.
A_ONLY_TRACE = str(uuid.uuid4())
# A session of B's project; the member of B's session issue.
B_SESSION = str(uuid.uuid4())
# The latest trace of that session; it carries no evaluation row.
B_SESSION_TRACE = str(uuid.uuid4())

B_CONFIG = "B refund check"
B_REASON = "B: the agent promised a refund outside the window"
B_PLAIN_REASON = "B: a check with no config flagged the reply"
B_OWN_REASON = "B: the agent skipped the greeting"
A_CONFIG = "A private rubric"
A_VERDICT_CONFIG = "A private verdict"
A_REASON = "A: the evaluator text of another organization"
A_PLAIN_REASON = "A: a check with no config on another organization's span"
A_ONLY_REASON = "A: the reasoning on a trace only A holds"
A_ONLY_PLAIN_REASON = "A: a check with no config on a trace only A holds"
A_VERDICT = "A-VERDICT"
A_TEXTS = (
    A_REASON,
    A_PLAIN_REASON,
    A_ONLY_REASON,
    A_ONLY_PLAIN_REASON,
    A_VERDICT,
    A_CONFIG,
    A_VERDICT_CONFIG,
)


class _Spans:
    """``get_reader()`` stand-in for the span read: each project's spans by id,
    scoped by ``project_id`` like the real read. Counts its reads and keeps
    the span ids each read asked for."""

    def __init__(self, spans):
        self.spans = spans
        self.reads = 0
        self.asked = []
        self.fail = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def list_by_ids(self, span_ids, *, project_id=None, **_):
        self.reads += 1
        self.asked.append(set(span_ids))
        if self.fail:
            raise RuntimeError("clickhouse is down")
        return [
            span
            for span in self.spans
            if span.id in span_ids
            and (not project_id or span.project_id == str(project_id))
        ]


def _project(organization, workspace, name):
    return Project.objects.create(
        name=name,
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )


def _config(project, organization, workspace, name):
    template = EvalTemplate.objects.create(
        name=f"{name} template",
        description="A test evaluation template",
        organization=organization,
        workspace=workspace,
        config={"type": "pass_fail", "criteria": "Test criteria"},
    )
    return CustomEvalConfig.objects.create(
        name=name,
        project=project,
        eval_template=template,
        config={"threshold": 0.8},
        mapping={"input": "input", "output": "output"},
        filters={},
    )


def _eval(config, trace_id, span_id, *, reason=None, score=None, verdict=None):
    # The trace and span live only in ClickHouse; the row keeps their raw ids.
    # No eval task: the live work-item unique indexes skip the row.
    return EvalLogger.objects.create(
        trace_id=trace_id,
        observation_span_id=span_id,
        custom_eval_config=config,
        eval_explanation=reason,
        output_float=score,
        output_str=verdict,
    )


def _span(project, trace_id, span_id):
    return SimpleNamespace(id=span_id, project_id=str(project.id), trace_id=trace_id)


def _members_without_ch(trace_ids, *_args, **_kwargs):
    return {str(t): feed._CHTraceShim(str(t), None) for t in trace_ids}


@pytest.fixture
def issue(organization, workspace, user):
    """B's scanner issue over the shared trace and B's own trace. A's rows on
    the shared trace score lower than B's, so a read by trace id alone picks
    A's reasoning and pulls B's average down."""
    b_project = _project(organization, workspace, "xorg-b")
    a_org = Organization.objects.create(name=f"Org A {uuid.uuid4().hex[:8]}")
    a_workspace = Workspace.no_workspace_objects.create(
        name="Org A Workspace",
        organization=a_org,
        is_default=True,
        is_active=True,
        created_by=user,
    )
    a_project = _project(a_org, a_workspace, "xorg-a")

    b_config = _config(b_project, organization, workspace, B_CONFIG)
    a_config = _config(a_project, a_org, a_workspace, A_CONFIG)
    a_verdict_config = _config(a_project, a_org, a_workspace, A_VERDICT_CONFIG)

    _eval(b_config, SHARED_TRACE, "span-b-shared", reason=B_REASON, score=0.6)
    _eval(None, SHARED_TRACE, "span-b-shared", reason=B_PLAIN_REASON, score=0.5)
    _eval(b_config, OWN_TRACE, "span-b-own", reason=B_OWN_REASON, score=0.3)
    _eval(a_config, SHARED_TRACE, "span-a-shared", reason=A_REASON, score=0.1)
    _eval(None, SHARED_TRACE, "span-a-shared", reason=A_PLAIN_REASON, score=0.05)
    _eval(a_verdict_config, SHARED_TRACE, "span-a-shared", verdict=A_VERDICT)
    _eval(a_config, A_ONLY_TRACE, "span-a-only", reason=A_ONLY_REASON, score=0.2)
    _eval(None, A_ONLY_TRACE, "span-a-only", reason=A_ONLY_PLAIN_REASON, score=0.15)

    cluster = TraceErrorGroup.objects.create(
        project=b_project,
        cluster_id="XORG-B-1",
        error_type="tool_failure",
        issue_group="Tool Failures",
        title="Tool call failed",
        source=ClusterSource.SCANNER,
    )
    ErrorClusterTraces.objects.create(cluster=cluster, trace_id=SHARED_TRACE)
    ErrorClusterTraces.objects.create(cluster=cluster, trace_id=OWN_TRACE)

    spans = _Spans(
        [
            _span(b_project, SHARED_TRACE, "span-b-shared"),
            _span(b_project, OWN_TRACE, "span-b-own"),
            _span(a_project, SHARED_TRACE, "span-a-shared"),
            _span(a_project, A_ONLY_TRACE, "span-a-only"),
        ]
    )
    with (
        patch("tracer.services.clickhouse.v2.get_reader", return_value=spans),
        patch.object(feed, "_resolve_member_traces", side_effect=_members_without_ch),
        patch.object(feed, "_get_trace_totals_batch", return_value={}),
        patch.object(feed, "_get_trace_costs_batch", return_value={}),
        patch.object(feed, "_get_root_spans_batch", return_value={}),
        patch.object(feed, "_fetch_events_over_time", return_value=[]),
        patch.object(
            feed, "_fetch_pattern_summary", return_value=feed.PatternSummary()
        ),
        patch.object(feed, "_fetch_success_trace_pass_reel", return_value=[]),
        patch.object(feed, "_cluster_highlight_terms", return_value=[]),
        patch.object(
            feed,
            "_fetch_sidebar_ai_metadata",
            return_value=feed.SidebarAIMetadata(),
        ),
    ):
        yield SimpleNamespace(
            cluster=cluster,
            b_project=b_project,
            b_project_id=str(b_project.id),
            a_project=a_project,
            a_project_id=str(a_project.id),
            b_config=b_config,
            a_config=a_config,
            spans=spans,
        )


# ---------------------------------------------------------------------------
# Issue views
# ---------------------------------------------------------------------------


def _overview_cards(issue):
    overview = feed.get_overview(issue.cluster.cluster_id, [issue.b_project_id])
    return {card.id: card for card in overview.representative_traces}


def test_overview_shared_trace_shows_only_the_issue_projects_judge(issue):
    card = _overview_cards(issue)[SHARED_TRACE]

    # B's lowest row on its own span: the config-less 0.5, not A's 0.05 or 0.1.
    assert card.evidence.judge_reason == B_PLAIN_REASON
    assert card.evidence.score == pytest.approx(0.5)
    # B's two scored rows on the shared trace: 0.6 and 0.5.
    assert card.summary.eval_score == pytest.approx(0.55)
    assert card.evidence.judge_reason not in A_TEXTS


def test_overview_trace_no_other_organization_holds_is_unchanged(issue):
    card = _overview_cards(issue)[OWN_TRACE]

    assert card.evidence.judge_reason == B_OWN_REASON
    assert card.evidence.score == pytest.approx(0.3)
    assert card.summary.eval_score == pytest.approx(0.3)


def _traces_tab_scores(issue):
    tab = feed.get_traces_tab(issue.cluster.cluster_id, [issue.b_project_id])
    return {row.id: row.score for row in tab.traces}


def test_traces_tab_shared_trace_averages_only_the_issue_projects_scores(issue):
    assert _traces_tab_scores(issue)[SHARED_TRACE] == pytest.approx(0.55)


def test_traces_tab_trace_no_other_organization_holds_is_unchanged(issue):
    assert _traces_tab_scores(issue)[OWN_TRACE] == pytest.approx(0.3)


def test_sidebar_lists_only_the_issue_projects_evaluations(issue):
    sidebar = feed.get_sidebar(issue.cluster.cluster_id, [issue.b_project_id])

    assert [row.label for row in sidebar.evaluations] == [B_CONFIG]
    assert [row.value for row in sidebar.evaluations] == [None]
    # B's two config rows: 0.6 on the shared trace and 0.3 on its own.
    assert sidebar.evaluations[0].score == pytest.approx(0.45)


def test_sidebar_for_a_trace_no_other_organization_holds_is_unchanged(issue):
    sidebar = feed.get_sidebar(
        issue.cluster.cluster_id, [issue.b_project_id], trace_id=OWN_TRACE
    )

    assert [row.label for row in sidebar.evaluations] == [B_CONFIG]
    assert sidebar.evaluations[0].score == pytest.approx(0.3)


# ---------------------------------------------------------------------------
# The four reads, called for one project
# ---------------------------------------------------------------------------


def test_reads_for_the_issue_project_carry_none_of_the_other_rows(issue):
    project_id = issue.b_project_id
    trace_ids = [SHARED_TRACE, OWN_TRACE]

    # An empty evaluator filter, as scanner and Omega issues read.
    assert feed._trace_judges_batch(trace_ids, project_id) == {
        SHARED_TRACE: (B_PLAIN_REASON, pytest.approx(0.5)),
        OWN_TRACE: (B_OWN_REASON, pytest.approx(0.3)),
    }
    assert feed.trace_judge(SHARED_TRACE, project_id) == (
        B_PLAIN_REASON,
        pytest.approx(0.5),
    )
    assert feed._get_trace_scores_batch(trace_ids, project_id) == {
        SHARED_TRACE: pytest.approx(0.55),
        OWN_TRACE: pytest.approx(0.3),
    }
    evaluations = feed._fetch_sidebar_evaluations(trace_ids, project_id)
    assert [row.label for row in evaluations] == [B_CONFIG]
    assert A_VERDICT not in [row.value for row in evaluations]


def test_reads_for_the_other_project_keep_its_own_rows(issue):
    project_id = issue.a_project_id

    # A's lowest row on its own span: its config-less 0.05.
    assert feed.trace_judge(SHARED_TRACE, project_id) == (
        A_PLAIN_REASON,
        pytest.approx(0.05),
    )
    # A's scored rows on the shared trace: 0.1 and 0.05.
    assert feed._get_trace_scores_batch([SHARED_TRACE], project_id) == {
        SHARED_TRACE: pytest.approx(0.075)
    }
    evaluations = feed._fetch_sidebar_evaluations([SHARED_TRACE], project_id)
    assert sorted(row.label for row in evaluations) == [A_CONFIG, A_VERDICT_CONFIG]


def test_a_trace_read_for_a_project_that_does_not_hold_it_is_empty(issue):
    project_id = issue.b_project_id

    assert feed.trace_judge(A_ONLY_TRACE, project_id) == (None, None)
    assert feed._trace_judges_batch([A_ONLY_TRACE], project_id) == {}
    assert feed._get_trace_scores_batch([A_ONLY_TRACE], project_id) == {}
    assert feed._fetch_sidebar_evaluations([A_ONLY_TRACE], project_id) == []


def test_an_evaluator_filter_narrows_and_never_widens(issue):
    project_id = issue.b_project_id
    trace_ids = [SHARED_TRACE, OWN_TRACE]

    # Naming another organization's evaluator does not admit its rows.
    a_only = Q(custom_eval_config_id=issue.a_config.id)
    assert feed._trace_judges_batch(trace_ids, project_id, a_only) == {}
    b_only = Q(custom_eval_config_id=issue.b_config.id)
    assert feed._trace_judges_batch(trace_ids, project_id, b_only) == {
        SHARED_TRACE: (B_REASON, pytest.approx(0.6)),
        OWN_TRACE: (B_OWN_REASON, pytest.approx(0.3)),
    }
    # Both filters leave no row without a config, so no span is read.
    assert issue.spans.reads == 0


def test_rows_with_the_projects_config_need_no_span_read(issue):
    project_id = issue.b_project_id

    assert feed._trace_judges_batch([OWN_TRACE], project_id) == {
        OWN_TRACE: (B_OWN_REASON, pytest.approx(0.3))
    }
    assert feed._get_trace_scores_batch([OWN_TRACE], project_id) == {
        OWN_TRACE: pytest.approx(0.3)
    }
    assert issue.spans.reads == 0


def test_each_read_asks_once_for_the_spans_of_rows_without_a_config(issue):
    project_id = issue.b_project_id
    trace_ids = [SHARED_TRACE, OWN_TRACE]
    configless = {"span-b-shared", "span-a-shared"}

    feed._trace_judges_batch(trace_ids, project_id)
    assert issue.spans.reads == 1
    feed.trace_judge(SHARED_TRACE, project_id)
    assert issue.spans.reads == 2
    feed._get_trace_scores_batch(trace_ids, project_id)
    assert issue.spans.reads == 3
    assert issue.spans.asked == [configless, configless, configless]
    # The sidebar decides by the eval config's project and reads no span.
    feed._fetch_sidebar_evaluations(trace_ids, project_id)
    assert issue.spans.reads == 3


def test_no_evaluation_rows_means_no_span_read(issue):
    unseen = [str(uuid.uuid4())]

    assert feed._trace_judges_batch(unseen, issue.b_project_id) == {}
    assert feed._get_trace_scores_batch(unseen, issue.b_project_id) == {}
    assert issue.spans.reads == 0


def test_a_failed_span_read_keeps_no_row_without_a_config(issue):
    issue.spans.fail = True
    project_id = issue.b_project_id
    trace_ids = [SHARED_TRACE, OWN_TRACE]

    # The project's config rows still count; no config-less row does.
    assert feed._trace_judges_batch(trace_ids, project_id) == {
        SHARED_TRACE: (B_REASON, pytest.approx(0.6)),
        OWN_TRACE: (B_OWN_REASON, pytest.approx(0.3)),
    }
    assert feed.trace_judge(SHARED_TRACE, project_id) == (
        B_REASON,
        pytest.approx(0.6),
    )
    assert feed._get_trace_scores_batch(trace_ids, project_id) == {
        SHARED_TRACE: pytest.approx(0.6),
        OWN_TRACE: pytest.approx(0.3),
    }


# ---------------------------------------------------------------------------
# Other issues of B's project over the fixture's traces
# ---------------------------------------------------------------------------


def _b_issue(issue, cluster_id, trace_ids, **fields):
    """Another issue of B's project over some of the fixture's traces."""
    fields.setdefault("source", ClusterSource.SCANNER)
    cluster = TraceErrorGroup.objects.create(
        project=issue.b_project,
        cluster_id=cluster_id,
        error_type="tool_failure",
        issue_group="Tool Failures",
        title="Tool call failed",
        **fields,
    )
    for trace_id in trace_ids:
        ErrorClusterTraces.objects.create(cluster=cluster, trace_id=trace_id)
    return cluster


def _b_eval_issue(issue):
    """B's eval issue for its own evaluator, over both of B's member traces."""
    return _b_issue(
        issue,
        "XORG-B-EVAL-2",
        [SHARED_TRACE, OWN_TRACE],
        source=ClusterSource.EVAL,
        eval_target_type=EvalTargetType.SPAN,
        eval_config=issue.b_config,
    )


def _b_rep_traces(session_ids, *_args):
    """Each session's latest trace, as the ClickHouse session read gives it."""
    return {str(s): B_SESSION_TRACE for s in session_ids if str(s) == B_SESSION}


@pytest.fixture
def session_issue(issue):
    """B's session issue: one session member that B's evaluator scored 0.8.
    The session's latest trace carries no evaluation row."""
    cluster = _b_issue(
        issue,
        "XORG-B-SESSION-1",
        [],
        source=ClusterSource.EVAL,
        eval_target_type=EvalTargetType.SESSION,
    )
    ErrorClusterTraces.objects.create(cluster=cluster, trace_session_id=B_SESSION)
    EvalLogger.objects.create(
        trace_session_id=B_SESSION,
        target_type=EvalTargetType.SESSION,
        custom_eval_config=issue.b_config,
        output_float=0.8,
    )
    with patch.object(feed, "_session_rep_trace_map", side_effect=_b_rep_traces):
        yield cluster


# ---------------------------------------------------------------------------
# Trends tab score chart
# ---------------------------------------------------------------------------


def _score_trends(issue, cluster=None):
    return feed._fetch_score_trends(
        cluster or issue.cluster, 14, project_id=issue.b_project_id
    )


def test_score_trends_list_only_the_issue_projects_configs(issue):
    trends = _score_trends(issue)

    assert [trend.label for trend in trends] == [B_CONFIG]
    # B's two rows with a config: 0.6 on the shared trace and 0.3 on its own.
    assert trends[0].current == pytest.approx(0.45)
    # The chart decides by the eval config's project and reads no span.
    assert issue.spans.reads == 0


def test_score_trend_takes_no_score_from_a_same_named_config_elsewhere(issue):
    a_project = issue.a_project
    organization, workspace = a_project.organization, a_project.workspace
    same_name = _config(a_project, organization, workspace, B_CONFIG)
    _eval(same_name, SHARED_TRACE, "span-a-shared", score=0.9)

    trends = _score_trends(issue)

    assert [trend.label for trend in trends] == [B_CONFIG]
    # B's two rows alone; the 0.9 row is not this project's config.
    assert trends[0].current == pytest.approx(0.45)


def test_score_trends_with_no_project_keep_no_row(issue):
    assert feed._fetch_score_trends(issue.cluster, 14, project_id=None) == []


def test_trends_tab_lists_only_the_issue_projects_configs(issue):
    """The Trends tab read for B's issue. Its other sections are stubbed; the
    score chart reads Postgres for real."""
    with (
        patch.object(feed, "_fetch_trend_metrics", return_value=[]),
        patch.object(feed, "_fetch_events_over_time_with_passing", return_value=[]),
    ):
        tab = feed.get_trends_tab(issue.cluster.cluster_id, [issue.b_project_id])

    assert [trend.label for trend in tab.score_trends] == [B_CONFIG]
    assert tab.score_trends[0].current == pytest.approx(0.45)


def test_score_trends_on_a_trace_no_other_organization_holds_are_unchanged(issue):
    cluster = _b_issue(issue, "XORG-B-2", [OWN_TRACE])

    trends = _score_trends(issue, cluster)

    assert [trend.label for trend in trends] == [B_CONFIG]
    assert trends[0].current == pytest.approx(0.3)


def test_score_trends_of_an_eval_issue_are_unchanged(issue):
    trends = _score_trends(issue, _b_eval_issue(issue))

    assert [trend.label for trend in trends] == [B_CONFIG]
    assert trends[0].current == pytest.approx(0.45)


def test_score_trends_of_a_session_member_are_unchanged(issue, session_issue):
    trends = _score_trends(issue, session_issue)

    assert [trend.label for trend in trends] == [B_CONFIG]
    assert trends[0].current == pytest.approx(0.8)


# ---------------------------------------------------------------------------
# Average scores: Traces tab summary bar, Trends tab card, sidebar metadata
# ---------------------------------------------------------------------------


def _summary_average(issue, cluster=None):
    """The average score in the Traces tab's summary bar."""
    cluster = cluster or issue.cluster
    tab = feed.get_traces_tab(cluster.cluster_id, [issue.b_project_id])
    return tab.aggregates.avg_score


def _window_score(issue, cluster=None):
    """The Trends tab's average score card for the last 14 days. The card's
    ClickHouse-backed neighbours are stubbed."""
    cluster = cluster or issue.cluster
    with (
        patch.object(feed, "_users_affected_in_window", return_value=0),
        patch.object(feed, "_project_scope_total", return_value=10),
    ):
        metrics = feed._fetch_trend_metrics(
            cluster.cluster_id, issue.b_project_id, days=14
        )
    return next(m for m in metrics if m.label == "Avg eval score")


def _sidebar_eval_score(issue, cluster=None, trace_id=None):
    """The eval score in the sidebar's metadata, read through the sidebar. The
    metadata's latest-trace read is stubbed and its model read finds no LLM
    span."""
    cluster = cluster or issue.cluster
    reader = MagicMock()
    reader.first_span_by_type.return_value = None
    with (
        patch.object(feed, "_fetch_sidebar_ai_metadata", _SIDEBAR_AI_METADATA),
        patch.object(feed, "_fetch_latest_trace_id_batch", return_value={}),
        patch.object(feed, "get_reader", return_value=nullcontext(reader)),
    ):
        sidebar = feed.get_sidebar(
            cluster.cluster_id, [issue.b_project_id], trace_id=trace_id
        )
    return sidebar.ai_metadata.eval_score


def test_summary_average_counts_only_the_issue_projects_rows(issue):
    # B's three scored rows: 0.6 and 0.5 on the shared trace, 0.3 on its own.
    # A's 0.1 and 0.05 on the shared trace would pull it down to 0.31.
    assert _summary_average(issue) == pytest.approx(0.4667)


def test_window_score_counts_only_the_issue_projects_rows(issue):
    score = _window_score(issue)

    assert score.value == "0.47"
    # Nothing was scored in the window before: no change to show.
    assert score.delta == 0.0


def test_sidebar_eval_score_counts_only_the_issue_projects_rows(issue):
    assert _sidebar_eval_score(issue) == pytest.approx(0.4667)
    # The picked trace alone: B's 0.6 and 0.5.
    assert _sidebar_eval_score(issue, trace_id=SHARED_TRACE) == pytest.approx(0.55)


def test_averages_on_a_trace_no_other_organization_holds_are_unchanged(issue):
    cluster = _b_issue(issue, "XORG-B-2", [OWN_TRACE])

    assert _summary_average(issue, cluster) == pytest.approx(0.3)
    assert _window_score(issue, cluster).value == "0.30"
    assert _sidebar_eval_score(issue, cluster) == pytest.approx(0.3)
    assert _sidebar_eval_score(issue, trace_id=OWN_TRACE) == pytest.approx(0.3)


def test_averages_of_an_eval_issue_are_unchanged(issue):
    cluster = _b_eval_issue(issue)

    # Its own evaluator's rows: 0.6 on the shared trace and 0.3 on B's own.
    assert _summary_average(issue, cluster) == pytest.approx(0.45)
    assert _window_score(issue, cluster).value == "0.45"
    assert _sidebar_eval_score(issue, cluster) == pytest.approx(0.45)


def test_averages_of_a_session_member_are_unchanged(issue, session_issue):
    # No trace row is scored, so each average is the session's own 0.8.
    assert _summary_average(issue, session_issue) == pytest.approx(0.8)
    assert _window_score(issue, session_issue).value == "0.80"
    assert _sidebar_eval_score(issue, session_issue) == pytest.approx(0.8)


# ---------------------------------------------------------------------------
# The average read, called for one project
# ---------------------------------------------------------------------------


def test_average_read_for_a_project_carries_none_of_the_other_rows(issue):
    b_id, a_id = issue.b_project_id, issue.a_project_id

    # B's rows: 0.6 and 0.5 on the shared trace, 0.3 on its own.
    both = feed._avg_eval_score([SHARED_TRACE, OWN_TRACE], b_id)
    assert both == pytest.approx(1.4 / 3)
    assert feed._avg_eval_score([SHARED_TRACE], b_id) == pytest.approx(0.55)
    # A's rows on the shared trace: 0.1 and 0.05.
    assert feed._avg_eval_score([SHARED_TRACE], a_id) == pytest.approx(0.075)
    # A trace the project does not hold has nothing to average.
    assert feed._avg_eval_score([A_ONLY_TRACE], b_id) is None


def test_average_read_evaluator_filter_narrows_and_never_widens(issue):
    b_id = issue.b_project_id
    trace_ids = [SHARED_TRACE, OWN_TRACE]

    # Naming another organization's evaluator does not admit its rows.
    a_only = Q(custom_eval_config_id=issue.a_config.id)
    assert feed._avg_eval_score(trace_ids, b_id, a_only) is None
    b_only = Q(custom_eval_config_id=issue.b_config.id)
    assert feed._avg_eval_score(trace_ids, b_id, b_only) == pytest.approx(0.45)
    # Both filters leave no row without a config, so no span is read.
    assert issue.spans.reads == 0


def test_average_read_asks_once_for_the_spans_of_rows_without_a_config(issue):
    feed._avg_eval_score([SHARED_TRACE, OWN_TRACE], issue.b_project_id)

    assert issue.spans.reads == 1
    assert issue.spans.asked == [{"span-b-shared", "span-a-shared"}]


def test_average_read_of_rows_with_the_projects_config_needs_no_span_read(issue):
    b_id = issue.b_project_id

    assert feed._avg_eval_score([OWN_TRACE], b_id) == pytest.approx(0.3)
    assert issue.spans.reads == 0


def test_average_read_with_a_failed_span_read_keeps_no_row_without_a_config(issue):
    issue.spans.fail = True

    average = feed._avg_eval_score([SHARED_TRACE, OWN_TRACE], issue.b_project_id)

    # The project's config rows still count: 0.6 and 0.3.
    assert average == pytest.approx(0.45)


def test_average_read_with_no_project_keeps_no_row(issue):
    trace_ids = [SHARED_TRACE, OWN_TRACE]

    assert feed._avg_eval_score(trace_ids, None) is None
    assert feed._avg_eval_score(trace_ids, "") is None
    assert issue.spans.reads == 0


# ---------------------------------------------------------------------------
# Linear issue route
# ---------------------------------------------------------------------------


def test_linear_issue_for_an_eval_issue_on_a_shared_trace_quotes_its_own_reasoning(
    issue, organization, user, monkeypatch
):
    """B's eval issue names the trace id A holds too. The ticket quotes B's
    evaluator and none of A's text. Nothing leaves the process."""
    monkeypatch.setattr("tfc.ee_gating.check_ee_feature", lambda *a, **k: None)
    eval_issue = TraceErrorGroup.objects.create(
        project=issue.b_project,
        cluster_id="XORG-B-EVAL-1",
        error_type="eval",
        issue_group=B_CONFIG,
        title="Refund check failed",
        source=ClusterSource.EVAL,
        eval_target_type=EvalTargetType.SPAN,
        eval_config=issue.b_config,
    )
    ErrorClusterTraces.objects.create(cluster=eval_issue, trace_id=SHARED_TRACE)
    created = {
        "url": "https://linear.app/example/issue/TEAM-123",
        "identifier": "TEAM-123",
        "title": "Refund check failed",
    }
    cluster_id = eval_issue.cluster_id
    with (
        patch(
            "tracer.views.feed.linear_issue_view.resolve_requested_project_ids",
            return_value=[issue.b_project_id],
        ),
        patch(
            "integrations.models.integration_connection."
            "IntegrationConnection.objects.filter"
        ) as connections,
        patch(
            "tracer.views.feed.linear_issue_view.CredentialManager.decrypt",
            return_value={"token": "mock"},
        ),
        patch(
            "integrations.services.linear_service.LinearService.create_issue",
            return_value=created,
        ) as create_issue,
    ):
        newest = connections.return_value.exclude.return_value.order_by.return_value
        newest.first.return_value = SimpleNamespace(encrypted_credentials="mock")
        request = APIRequestFactory().post(
            f"/tracer/feed/issues/{cluster_id}/create-linear-issue/",
            {"team_id": "team-local-test", "trace_id": SHARED_TRACE},
            format="json",
        )
        request.organization = organization
        force_authenticate(request, user=user)
        response = CreateLinearIssueView.as_view()(request, cluster_id=cluster_id)

    assert response.status_code == 200
    description = create_issue.call_args.kwargs["description"]
    assert "## Evaluator reasoning — sampled trace (0.60/1.00)" in description
    assert B_REASON in description
    for text in A_TEXTS:
        assert text not in description
