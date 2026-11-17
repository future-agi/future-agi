"""Unit tests for the Linear issue description builder and connection lookup.

The view itself (POST /tracer/feed/issues/{cluster_id}/create-linear-issue/)
is exercised end-to-end elsewhere; this file pins the description shape so
Linear tickets keep landing with a backlink to the cluster and — when a
cluster RCA has run — its synthesis, fix, confidence, and (UUID-only)
evidence traces inline. When a trace is supplied, the issue's own
evaluator's reasoning for that sampled trace is appended best-effort.

Both Linear endpoints read the connection of the organization the caller
has selected, the one Settings > Integrations shows as connected. A request
without an organization reads no connection: the account's own organization
never chooses one.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.db.models import Q
from django.test import SimpleTestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from accounts.models.organization import Organization
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.observation_span import EvalLogger, EvalTargetType
from tracer.models.trace_error_analysis import ClusterSource, TraceErrorGroup
from tracer.views.feed.linear_issue_view import (
    CreateLinearIssueView,
    LinearTeamsView,
    _build_issue_description,
    _cluster_url,
)

# ---------------------------------------------------------------------------
# _cluster_url helper
# ---------------------------------------------------------------------------


@override_settings(APP_BASE_URL="https://app.futureagi.com")
class TestClusterUrl(SimpleTestCase):
    def test_builds_url_from_app_url_and_scheme(self):
        url = _cluster_url("E-ABC123")
        assert url == "https://app.futureagi.com/dashboard/error-feed/E-ABC123"


@override_settings(APP_BASE_URL="http://localhost:3000")
class TestClusterUrlOnALoopbackInstall(SimpleTestCase):
    def test_keeps_http_for_localhost(self):
        assert _cluster_url("E-ABC123") == (
            "http://localhost:3000/dashboard/error-feed/E-ABC123"
        )


@override_settings(APP_BASE_URL="")
class TestClusterUrlWithoutAppUrl(SimpleTestCase):
    def test_returns_empty_when_app_url_unset(self):
        # No APP_URL configured (some envs); helper returns "" so the
        # description builder can fall back to a non-link mention.
        assert _cluster_url("E-ABC123") == ""


# ---------------------------------------------------------------------------
# _build_issue_description
# ---------------------------------------------------------------------------

CONFIG_ID = uuid.uuid4()
PROJECT_ID = uuid.uuid4()


def _cluster(
    cluster_id="E-CAFEBABE",
    priority="high",
    unique_traces=12,
    issue_group="timeout",
    rca_synthesis=None,
    rca_fix=None,
    rca_confidence=None,
    rca_evidence_trace_ids=None,
    source=ClusterSource.SCANNER,
    eval_target_type=None,
    eval_config_id=None,
):
    """Cluster fixture with every field _build_issue_description reads."""
    return SimpleNamespace(
        cluster_id=cluster_id,
        priority=priority,
        unique_traces=unique_traces,
        issue_group=issue_group,
        rca_synthesis=rca_synthesis,
        rca_fix=rca_fix,
        rca_confidence=rca_confidence,
        rca_evidence_trace_ids=rca_evidence_trace_ids,
        source=source,
        eval_target_type=eval_target_type,
        eval_config_id=eval_config_id,
        project_id=PROJECT_ID,
    )


def _eval_cluster(cluster_id="E-1", target_type=EvalTargetType.TRACE):
    """An eval issue: one eval config failing at one unit."""
    return _cluster(
        cluster_id,
        source=ClusterSource.EVAL,
        eval_target_type=target_type,
        eval_config_id=CONFIG_ID,
    )


def _own_evaluator(target_type):
    return Q(target_type=target_type, custom_eval_config_id=CONFIG_ID)


@override_settings(APP_BASE_URL="https://app.futureagi.com")
class TestBuildIssueDescriptionBacklink(SimpleTestCase):
    """The backlink must always be the first line — it's the only piece
    of context that lets a Linear assignee actually find the cluster."""

    def test_backlink_is_first_line(self):
        body = _build_issue_description(_cluster("E-1"), trace_id=None)
        first = body.splitlines()[0]
        assert "[View in Future AGI](" in first
        assert "/dashboard/error-feed/E-1" in first
        assert "`E-1`" in first

    def test_no_rca_sections_when_cluster_has_no_synthesis(self):
        body = _build_issue_description(_cluster("E-1"), trace_id=None)
        assert "## Root cause analysis" not in body
        assert "## Recommended fix" not in body
        assert "Confidence:" not in body
        assert "Evidence traces:" not in body


@override_settings(APP_BASE_URL="")
class TestBuildIssueDescriptionWithoutAppUrl(SimpleTestCase):
    def test_falls_back_to_plain_cluster_mention(self):
        body = _build_issue_description(_cluster("E-1"), trace_id=None)
        first = body.splitlines()[0]
        # No URL → no markdown link, but cluster_id still mentioned.
        assert "[View in Future AGI](" not in body
        assert "`E-1`" in first


@override_settings(APP_BASE_URL="https://app.futureagi.com")
class TestBuildIssueDescriptionRca(SimpleTestCase):
    def test_synthesis_fix_and_confidence_rendered(self):
        cluster = _cluster(
            "E-1",
            rca_synthesis="Upstream returns null on timeout, unchecked.",
            rca_fix="Guard response.data before use.",
            rca_confidence="high",
        )
        body = _build_issue_description(cluster, trace_id=None)

        assert "## Root cause analysis" in body
        assert "Upstream returns null on timeout, unchecked." in body
        assert "## Recommended fix" in body
        assert "Guard response.data before use." in body
        assert "Confidence: **high**" in body

    def test_fix_omitted_without_rca_fix(self):
        cluster = _cluster("E-1", rca_synthesis="Synthesis only.")
        body = _build_issue_description(cluster, trace_id=None)

        assert "## Root cause analysis" in body
        assert "## Recommended fix" not in body
        assert "Confidence:" not in body

    def test_evidence_uuid_filter_drops_aliases_and_caps_at_five(self):
        # Pre-alias-fix runs persisted LLM-facing labels (T01, Sp01) mixed
        # in with real trace UUIDs; only the UUIDs are meaningful outside
        # the run, and at most five should ship.
        real = [str(uuid.uuid4()) for _ in range(6)]
        cluster = _cluster(
            "E-1",
            rca_synthesis="Synthesis.",
            rca_evidence_trace_ids=[
                "T01",
                real[0],
                "Sp01",
                real[1],
                real[2],
                real[3],
                real[4],
                real[5],
            ],
        )
        body = _build_issue_description(cluster, trace_id=None)

        assert "Evidence traces:" in body
        # Aliases never appear.
        assert "T01" not in body
        assert "Sp01" not in body
        # Capped at five — the first five UUIDs ship, the sixth does not.
        for tid in real[:5]:
            assert tid in body
        assert real[5] not in body


@override_settings(APP_BASE_URL="https://app.futureagi.com")
class TestBuildIssueDescriptionTraceJudge(SimpleTestCase):
    @patch("tracer.views.feed.linear_issue_view._trace_judges_batch")
    def test_evaluator_reasoning_rendered_with_score(self, mock_judges):
        trace_id = uuid.uuid4()
        mock_judges.return_value = {
            str(trace_id): ("Output drifted from the rubric.", 0.42)
        }

        body = _build_issue_description(_eval_cluster("E-1"), trace_id=trace_id)

        mock_judges.assert_called_once_with(
            [str(trace_id)], str(PROJECT_ID), _own_evaluator(EvalTargetType.TRACE)
        )
        assert "## Evaluator reasoning — sampled trace (0.42/1.00)" in body
        assert "Output drifted from the rubric." in body

    @patch("tracer.views.feed.linear_issue_view._trace_judges_batch")
    def test_no_score_suffix_when_score_is_none(self, mock_judges):
        trace_id = uuid.uuid4()
        mock_judges.return_value = {str(trace_id): ("Reasoning without a score.", None)}

        body = _build_issue_description(_eval_cluster("E-1"), trace_id=trace_id)

        assert "## Evaluator reasoning — sampled trace" in body
        assert "/1.00" not in body
        assert "Reasoning without a score." in body

    @patch("tracer.views.feed.linear_issue_view._trace_judges_batch")
    def test_judge_raising_is_best_effort(self, mock_judges):
        # The lookup failing must not abort ticket creation: the
        # description is still built (backlink present), the evaluator
        # section is dropped, and the exception does not propagate.
        mock_judges.side_effect = RuntimeError("CH outage")

        body = _build_issue_description(_eval_cluster("E-1"), trace_id=uuid.uuid4())

        assert "[View in Future AGI](" in body
        assert "## Evaluator reasoning" not in body

    @patch("tracer.views.feed.linear_issue_view._trace_judges_batch")
    def test_no_judge_call_when_trace_id_is_none(self, mock_judges):
        body = _build_issue_description(_eval_cluster("E-1"), trace_id=None)

        # No trace selected → skip the evaluator lookup entirely.
        mock_judges.assert_not_called()
        assert "[View in Future AGI](" in body
        assert "## Evaluator reasoning" not in body


@override_settings(APP_BASE_URL="https://app.futureagi.com")
class TestBuildIssueDescriptionIssueEvaluator(SimpleTestCase):
    """The ticket quotes the issue's own evaluator, never another eval that
    happened to score the same trace lower."""

    @patch("tracer.views.feed.linear_issue_view._session_judges_batch")
    @patch("tracer.views.feed.linear_issue_view._trace_judges_batch")
    def test_scanner_issue_quotes_no_evaluator(self, mock_traces, mock_sessions):
        body = _build_issue_description(_cluster("S-1"), trace_id=uuid.uuid4())

        mock_traces.assert_not_called()
        mock_sessions.assert_not_called()
        assert "## Evaluator reasoning" not in body

    @patch("tracer.views.feed.linear_issue_view._trace_judges_batch")
    @patch("tracer.views.feed.linear_issue_view._session_judges_batch")
    @patch("tracer.views.feed.linear_issue_view.get_reader")
    def test_session_issue_quotes_its_evaluator_on_the_trace_session(
        self, mock_reader, mock_sessions, mock_traces
    ):
        trace_id, session_id = str(uuid.uuid4()), str(uuid.uuid4())
        cluster = _eval_cluster("E-2", EvalTargetType.SESSION)
        reader = mock_reader.return_value.__enter__.return_value
        reader.trace_session_ids_by_trace_ids.return_value = {trace_id: session_id}
        mock_sessions.return_value = {
            session_id: ("The caller was never greeted.", 0.2)
        }

        body = _build_issue_description(cluster, trace_id=uuid.UUID(trace_id))

        reader.trace_session_ids_by_trace_ids.assert_called_once_with(
            [trace_id], [str(cluster.project_id)]
        )
        mock_sessions.assert_called_once_with(
            [session_id], _own_evaluator(EvalTargetType.SESSION)
        )
        mock_traces.assert_not_called()
        assert "## Evaluator reasoning — sampled trace (0.20/1.00)" in body
        assert "The caller was never greeted." in body

    @patch("tracer.views.feed.linear_issue_view._session_judges_batch")
    @patch("tracer.views.feed.linear_issue_view.get_reader")
    def test_session_issue_without_a_session_quotes_nothing(
        self, mock_reader, mock_sessions
    ):
        reader = mock_reader.return_value.__enter__.return_value
        reader.trace_session_ids_by_trace_ids.return_value = {}

        body = _build_issue_description(
            _eval_cluster("E-3", EvalTargetType.SESSION), trace_id=uuid.uuid4()
        )

        mock_sessions.assert_not_called()
        assert "## Evaluator reasoning" not in body


def _eval_row(cfg, span, score, explanation):
    # Trace-target rows carry the root span too; EvalLogger.save() enforces it.
    return EvalLogger.objects.create(
        trace=span.trace,
        observation_span=span,
        custom_eval_config=cfg,
        target_type=EvalTargetType.TRACE,
        output_float=score,
        eval_explanation=explanation,
        # Distinct per row: two live evals on one (task, unit, config) collide
        # on the live work-item unique index.
        eval_task_id=f"et-{EvalLogger.objects.count() + 1}",
    )


@pytest.fixture
def two_evals_on_one_trace(
    project, observation_span, custom_eval_config, eval_template
):
    """Tone fails the trace at 0.40; Hallucination fails it harder at 0.00."""
    hallucination = CustomEvalConfig.objects.create(
        name="Hallucination",
        project=project,
        eval_template=eval_template,
        config={"threshold": 0.8},
        mapping={"input": "input", "output": "output"},
        filters={},
    )
    _eval_row(custom_eval_config, observation_span, 0.4, "The reply was curt.")
    _eval_row(hallucination, observation_span, 0.0, "It invented a refund policy.")
    return custom_eval_config


@pytest.mark.django_db
def test_eval_issue_ticket_quotes_its_own_evaluator(
    project, trace, two_evals_on_one_trace
):
    tone = two_evals_on_one_trace
    cluster = TraceErrorGroup.objects.create(
        project=project,
        cluster_id="E-TONE-1",
        error_type="eval",
        issue_group=tone.name,
        source=ClusterSource.EVAL,
        eval_target_type=EvalTargetType.TRACE,
        eval_config=tone,
    )

    body = _build_issue_description(cluster, trace_id=trace.id)

    assert "## Evaluator reasoning — sampled trace (0.40/1.00)" in body
    assert "The reply was curt." in body
    assert "It invented a refund policy." not in body


@pytest.mark.django_db
def test_scanner_issue_ticket_quotes_no_evaluator(
    project, trace, two_evals_on_one_trace
):
    cluster = TraceErrorGroup.objects.create(
        project=project,
        cluster_id="S-TOOL-1",
        error_type="tool_failure",
        issue_group="Tool Failures",
        source=ClusterSource.SCANNER,
    )

    body = _build_issue_description(cluster, trace_id=trace.id)

    assert "## Evaluator reasoning" not in body
    assert "It invented a refund policy." not in body


# ---------------------------------------------------------------------------
# Connection lookup: the selected organization
# ---------------------------------------------------------------------------

_CONNECTIONS = (
    "integrations.models.integration_connection.IntegrationConnection.objects.filter"
)


@pytest.fixture
def _error_feed_entitled(monkeypatch):
    monkeypatch.setattr("tfc.ee_gating.check_ee_feature", lambda *a, **k: None)


@pytest.mark.django_db
@pytest.mark.usefixtures("_error_feed_entitled")
def test_linear_teams_reads_the_selected_organization(user):
    # The user's account still points at its original organization; the
    # caller is working in another one, where Linear is connected.
    selected = Organization.objects.create(name="Selected Organization")
    request = APIRequestFactory().get("/tracer/feed/integrations/linear/teams/")
    request.organization = selected
    force_authenticate(request, user=user)

    with patch(_CONNECTIONS) as connections:
        connections.return_value.exclude.return_value.order_by.return_value.first.return_value = None
        response = LinearTeamsView.as_view()(request)

    assert response.status_code == 200
    assert connections.call_args.kwargs["organization"] == selected
    assert user.organization != selected


@pytest.mark.django_db
@pytest.mark.usefixtures("_error_feed_entitled")
def test_create_linear_issue_reads_the_selected_organization(
    user, organization, observe_project
):
    cluster = TraceErrorGroup.objects.create(
        project=observe_project,
        cluster_id="E-ORG-1",
        error_type="eval",
        title="Tone",
    )
    # Joined first, not selected now: the legacy account organization.
    user.organization = Organization.objects.create(name="Original Organization")
    request = APIRequestFactory().post(
        f"/tracer/feed/issues/{cluster.cluster_id}/create-linear-issue/",
        {"team_id": "team-local-test"},
        format="json",
    )
    request.organization = organization
    force_authenticate(request, user=user)

    with patch(_CONNECTIONS) as connections:
        connections.return_value.exclude.return_value.order_by.return_value.first.return_value = None
        response = CreateLinearIssueView.as_view()(
            request, cluster_id=cluster.cluster_id
        )

    # No connection in the selected organization either: refused, but the
    # lookup asked the selected organization, not the account's original one.
    assert response.status_code == 400
    assert connections.call_args.kwargs["organization"] == organization


# ---------------------------------------------------------------------------
# Connection lookup: a request without an organization
# ---------------------------------------------------------------------------

_DECRYPT = "tracer.views.feed.linear_issue_view.CredentialManager.decrypt"
_LINEAR_SERVICE = "integrations.services.linear_service.LinearService"
_PROJECT_SCOPE = "tracer.views.feed.linear_issue_view.resolve_requested_project_ids"
_STORED_BLOB = b"placeholder-blob"
_CREDENTIALS = {"api_key": "placeholder"}
_TEAMS = [{"id": "team-placeholder", "name": "Placeholder Team", "key": "TEAM"}]
_ISSUE = {
    "url": "https://linear.app/example/issue/TEAM-1",
    "identifier": "TEAM-1",
    "title": "Placeholder issue",
}


def _linear_connections(held_by):
    """Stand in for the connection query: only ``held_by`` has a connection."""
    connection = SimpleNamespace(encrypted_credentials=_STORED_BLOB)

    def lookup(*args, **kwargs):
        found = connection if kwargs.get("organization") == held_by else None
        chain = MagicMock()
        chain.exclude.return_value.order_by.return_value.first.return_value = found
        return chain

    return lookup


def _organizations_asked(connections):
    return [call.kwargs.get("organization") for call in connections.call_args_list]


def _teams_request(user, organization=None):
    request = APIRequestFactory().get("/tracer/feed/integrations/linear/teams/")
    if organization is not None:
        request.organization = organization
    force_authenticate(request, user=user)
    return request


def _create_issue_request(user, cluster, organization=None):
    request = APIRequestFactory().post(
        f"/tracer/feed/issues/{cluster.cluster_id}/create-linear-issue/",
        {"team_id": "team-placeholder"},
        format="json",
    )
    if organization is not None:
        request.organization = organization
    force_authenticate(request, user=user)
    return request


def _feed_issue(project, cluster_id):
    return TraceErrorGroup.objects.create(
        project=project,
        cluster_id=cluster_id,
        error_type="eval",
        title="Tone",
    )


@pytest.mark.django_db
@pytest.mark.usefixtures("_error_feed_entitled")
def test_linear_teams_without_a_request_organization_reads_no_connection(user):
    # The account's own organization holds a Linear connection, but no
    # organization was resolved onto the request.
    account_organization = user.organization
    no_linear = Organization.objects.create(name="Organization Without Linear")
    lookup = _linear_connections(held_by=account_organization)

    with (
        patch(_CONNECTIONS, side_effect=lookup) as connections,
        patch(_DECRYPT, return_value=_CREDENTIALS) as decrypt,
        patch(_LINEAR_SERVICE) as linear,
    ):
        linear.return_value.get_teams.return_value = _TEAMS
        not_connected = LinearTeamsView.as_view()(_teams_request(user, no_linear))
        response = LinearTeamsView.as_view()(_teams_request(user))

    assert not_connected.data["result"] == {"connected": False, "teams": []}
    # The same answer an organization without a connection gets.
    assert (response.status_code, response.data) == (
        not_connected.status_code,
        not_connected.data,
    )
    assert account_organization not in _organizations_asked(connections)
    decrypt.assert_not_called()
    # No request to Linear is made.
    linear.assert_not_called()


@pytest.mark.django_db
@pytest.mark.usefixtures("_error_feed_entitled")
def test_linear_teams_list_the_teams_of_the_request_organization(user):
    # The request carries the organization that holds the connection.
    organization = user.organization
    lookup = _linear_connections(held_by=organization)

    with (
        patch(_CONNECTIONS, side_effect=lookup) as connections,
        patch(_DECRYPT, return_value=_CREDENTIALS) as decrypt,
        patch(_LINEAR_SERVICE) as linear,
    ):
        linear.return_value.get_teams.return_value = _TEAMS
        response = LinearTeamsView.as_view()(_teams_request(user, organization))

    assert response.status_code == 200
    assert response.data["result"] == {"connected": True, "teams": _TEAMS}
    assert connections.call_args.kwargs["organization"] == organization
    decrypt.assert_called_once_with(_STORED_BLOB)
    linear.return_value.get_teams.assert_called_once_with(_CREDENTIALS)


@pytest.mark.django_db
@pytest.mark.usefixtures("_error_feed_entitled")
def test_create_linear_issue_without_a_request_organization_reads_no_connection(
    user, observe_project
):
    cluster = _feed_issue(observe_project, "E-LINEAR-A")
    # The account's own organization holds a Linear connection, but no
    # organization was resolved onto the request.
    account_organization = user.organization
    no_linear = Organization.objects.create(name="Organization Without Linear")
    lookup = _linear_connections(held_by=account_organization)

    # The issue is in scope for both requests, so each one reaches the
    # connection lookup; only the organization on the request differs.
    with (
        patch(_PROJECT_SCOPE, return_value=[str(cluster.project_id)]),
        patch(_CONNECTIONS, side_effect=lookup) as connections,
        patch(_DECRYPT, return_value=_CREDENTIALS) as decrypt,
        patch(_LINEAR_SERVICE) as linear,
    ):
        linear.return_value.create_issue.return_value = _ISSUE
        not_connected = CreateLinearIssueView.as_view()(
            _create_issue_request(user, cluster, no_linear),
            cluster_id=cluster.cluster_id,
        )
        response = CreateLinearIssueView.as_view()(
            _create_issue_request(user, cluster), cluster_id=cluster.cluster_id
        )

    assert not_connected.status_code == 400
    # The same refusal an organization without a connection gets.
    assert (response.status_code, response.data) == (
        not_connected.status_code,
        not_connected.data,
    )
    assert account_organization not in _organizations_asked(connections)
    decrypt.assert_not_called()
    # No request to Linear is made, and the issue stays unlinked.
    linear.assert_not_called()
    cluster.refresh_from_db()
    assert not cluster.external_issue_url
    assert not cluster.external_issue_id


@pytest.mark.django_db
@pytest.mark.usefixtures("_error_feed_entitled")
def test_create_linear_issue_files_with_the_request_organization_connection(
    user, observe_project
):
    cluster = _feed_issue(observe_project, "E-LINEAR-B")
    # The request carries the organization that holds the connection.
    organization = user.organization
    lookup = _linear_connections(held_by=organization)

    with (
        patch(_CONNECTIONS, side_effect=lookup) as connections,
        patch(_DECRYPT, return_value=_CREDENTIALS) as decrypt,
        patch(_LINEAR_SERVICE) as linear,
    ):
        linear.return_value.create_issue.return_value = _ISSUE
        response = CreateLinearIssueView.as_view()(
            _create_issue_request(user, cluster, organization),
            cluster_id=cluster.cluster_id,
        )

    assert response.status_code == 200
    assert response.data["result"] == {
        "issue_id": _ISSUE["identifier"],
        "issue_url": _ISSUE["url"],
        "issue_title": _ISSUE["title"],
    }
    assert connections.call_args.kwargs["organization"] == organization
    decrypt.assert_called_once_with(_STORED_BLOB)
    create_issue = linear.return_value.create_issue
    create_issue.assert_called_once()
    assert create_issue.call_args.kwargs["credentials"] == _CREDENTIALS
    assert create_issue.call_args.kwargs["team_id"] == "team-placeholder"
    cluster.refresh_from_db()
    assert (cluster.external_issue_url, cluster.external_issue_id) == (
        _ISSUE["url"],
        _ISSUE["identifier"],
    )
