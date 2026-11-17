"""The RCA agent's evaluation tools read only its own project's rows.

An evaluation row names no project, and spans of two organizations can carry one
trace id. On such a trace the agent of one of them ("home" below) reads, through
list(eval_results), read(eval_result) and aggregate(eval_metric), only the rows
on the spans its project holds on the cluster's traces:

  - the other organization's row, its metric name and its count stay out, and
    read(eval_result) answers not found for it, by alias and by raw UUID;
  - home's rows on the shared trace and on a trace only home holds still list,
    read and count;
  - a session cluster's session-level rows still show, even when the span id
    read fails;
  - one batched, project-scoped ClickHouse read serves every tool of a run;
  - when home holds no span of the cluster's traces, or ClickHouse fails, no
    trace-level row is read and one warning is logged.

The agent is built as a run builds it, with the gateway client and the schema
preload stubbed. ``_SpansTable`` stands in for ClickHouse at the client the span
reads share and at the span reader's session read. The set-up follows the shared
trace of test_selectors.py (two organizations, one trace id, a span and an
evaluation row of each), written out here so that this file stands alone.
"""

import re
import uuid
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.utils import timezone

from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from ee.agenthub.cluster_rca.agent import ClusterAnalysisAgent
from model_hub.models.ai_model import AIModel
from model_hub.models.evals_metric import EvalTemplate
from tfc.middleware.workspace_context import clear_workspace_context
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.observation_span import EvalLogger, EvalTargetType
from tracer.models.project import Project
from tracer.models.trace_error_analysis import (
    ClusterSource,
    ErrorClusterTraces,
    TraceErrorGroup,
)

_AGENT = "ee.agenthub.cluster_rca.agent"
_ANALYTICS = "tracer.services.clickhouse.cluster_rca_spans._analytics"

# The parts of a read of span ids by trace that the stand-in looks for.
_SPAN_ID_COLUMN = re.compile(r"SELECT\s+(?:DISTINCT\s+)?toString\(id\)\s+AS\s+(\w+)")
_FROM_SPANS = re.compile(r"\bFROM\s+spans\b")
_TRACE_IDS = re.compile(r"trace_id\)?\s+IN\s+%\((\w+)\)s")
_PROJECT = re.compile(r"project_id\s*=\s*%\((\w+)\)s")


class _SpansTable:
    """ClickHouse stand-in holding the ``spans`` rows of the cases below.

    ``execute_ch_query`` answers a read of the span ids of some traces the way
    ClickHouse would: the rows of the trace ids the query names and, when the
    query names a project, only that project's rows. It refuses any other query,
    and with ``fail`` set it raises, as a read does while ClickHouse is down.
    Each read is kept in ``reads`` as (project id or None, trace ids).

    ``session_trace_ids`` is the span reader's session read: the traces of one
    session's spans in one project.
    """

    def __init__(self):
        self.rows = []
        self.reads = []
        self.fail = False

    def add(self, project, trace_id, span_id, session_id=None):
        self.rows.append(
            SimpleNamespace(
                project_id=str(project.id),
                trace_id=trace_id,
                span_id=span_id,
                session_id=session_id,
            )
        )

    def drop(self, project):
        self.rows = [row for row in self.rows if row.project_id != str(project.id)]

    def execute_ch_query(self, query, params=None, **_options):
        params = params or {}
        column = _SPAN_ID_COLUMN.search(query)
        traces = _TRACE_IDS.search(query)
        if not (column and traces and _FROM_SPANS.search(query)):
            raise AssertionError(f"not a read of span ids by trace: {query}")
        scope = _PROJECT.search(query)
        project_id = str(params[scope.group(1)]) if scope else None
        trace_ids = {str(trace_id) for trace_id in params[traces.group(1)]}
        self.reads.append((project_id, trace_ids))
        if self.fail:
            raise ConnectionError("ClickHouse is unavailable")
        span_ids = sorted(
            {
                row.span_id
                for row in self.rows
                if row.trace_id in trace_ids and project_id in (None, row.project_id)
            }
        )
        name = column.group(1)
        return SimpleNamespace(columns=[name], data=[{name: s} for s in span_ids])

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def session_trace_ids(self, project_id, session_id):
        wanted = (str(project_id), str(session_id))
        return sorted(
            {
                row.trace_id
                for row in self.rows
                if (row.project_id, row.session_id) == wanted
            }
        )


def _project(user, name):
    org = Organization.objects.create(name=f"{name} Org")
    ws = Workspace.objects.create(
        name=f"{name} WS",
        organization=org,
        is_default=True,
        is_active=True,
        created_by=user,
    )
    return Project.objects.create(
        name=f"{name} Project",
        organization=org,
        workspace=ws,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )


def _metric(project, name):
    """An evaluation config of ``project`` named ``name``: a row's metric."""
    template = EvalTemplate.objects.create(
        name=f"{name} template",
        description="",
        organization=project.organization,
        workspace=project.workspace,
        config={"output": "score"},
    )
    return CustomEvalConfig.objects.create(
        name=name,
        project=project,
        eval_template=template,
        config={},
        mapping={},
        filters={},
    )


def _cluster(project, label, target):
    now = timezone.now()
    return TraceErrorGroup.objects.create(
        project=project,
        cluster_id=label,
        error_type=f"{label}-err",
        source=ClusterSource.EVAL,
        eval_target_type=target,
        title=f"{label} issue",
        first_seen=now,
        last_seen=now,
        error_count=1,
        unique_traces=1,
    )


def _span_row(trace_id, span_id, metric, explanation):
    return EvalLogger.objects.create(
        trace_id=trace_id,
        observation_span_id=span_id,
        target_type=EvalTargetType.SPAN,
        custom_eval_config=metric,
        output_float=0.5,
        eval_explanation=explanation,
    )


def _session_row(session_id, metric, explanation):
    return EvalLogger.objects.create(
        trace_session_id=session_id,
        target_type=EvalTargetType.SESSION,
        custom_eval_config=metric,
        output_bool=False,
        eval_explanation=explanation,
    )


@pytest.fixture
def shared(db):
    """Home's cluster of two traces: one whose id a span of the other
    organization also carries, and one only home holds.

    Each span has one evaluation row, under a metric of its own project. The
    workspace context is cleared, as in a run.
    """
    user = User.objects.create_user(
        email=f"rca-{uuid.uuid4().hex[:8]}@futureagi.com", password="x", name="Rca"
    )
    home, foreign = _project(user, "Home"), _project(user, "Foreign")
    shared_trace, home_trace = str(uuid.uuid4()), str(uuid.uuid4())
    table = _SpansTable()
    table.add(foreign, shared_trace, "foreign-span")
    table.add(home, shared_trace, "home-span")
    table.add(home, home_trace, "home-only-span")
    home_metric = _metric(home, "home-metric")
    foreign_metric = _metric(foreign, "foreign-metric")
    cluster = _cluster(home, "E-SHARED", EvalTargetType.SPAN)
    for trace_id in (shared_trace, home_trace):
        ErrorClusterTraces.objects.create(cluster=cluster, trace_id=trace_id)
    seeded = SimpleNamespace(
        home=home,
        cluster=cluster,
        table=table,
        traces={shared_trace, home_trace},
        home_metric=home_metric,
        foreign_eval=_span_row(
            shared_trace, "foreign-span", foreign_metric, "foreign explanation"
        ),
        home_eval=_span_row(shared_trace, "home-span", home_metric, "home explanation"),
        home_only_eval=_span_row(
            home_trace, "home-only-span", home_metric, "home-only explanation"
        ),
    )
    clear_workspace_context()
    return seeded


@pytest.fixture
def session_cluster(shared):
    """A session cluster of home's: one session, whose one trace has a span of
    home's, and a session-level evaluation row on the session."""
    session_id, trace_id = str(uuid.uuid4()), str(uuid.uuid4())
    shared.table.add(shared.home, trace_id, "home-session-span", session_id=session_id)
    cluster = _cluster(shared.home, "S-SESSION", EvalTargetType.SESSION)
    ErrorClusterTraces.objects.create(cluster=cluster, trace_session_id=session_id)
    return SimpleNamespace(
        cluster=cluster,
        row=_session_row(session_id, shared.home_metric, "session explanation"),
    )


@contextmanager
def _agent(seeded, cluster=None):
    """Home's RCA agent on ``cluster`` (by default home's shared-trace cluster),
    built as a run builds it, with ``seeded.table`` as ClickHouse."""
    cluster = cluster or seeded.cluster
    with (
        patch(f"{_AGENT}.get_gateway_client", return_value=object()),
        patch(f"{_AGENT}.build_project_schema_context", return_value=""),
        patch(f"{_AGENT}.get_reader", return_value=seeded.table),
        patch(_ANALYTICS, seeded.table),
    ):
        yield ClusterAnalysisAgent(
            cluster_id=str(cluster.id), project_id=str(seeded.home.id)
        )


def _listed(agent):
    """list(eval_results): the payload, and the ids of the rows it lists."""
    out = agent._tool_list("eval_results", {"cluster_id": agent.cluster_id})
    return out, [agent._resolve_alias(item["id"]) for item in out["items"]]


def _counted(agent):
    """aggregate(eval_metric): rows per metric name, and their total."""
    out = agent._tool_aggregate(
        metric="trace_count",
        filter={"cluster_id": agent.cluster_id},
        group_by="eval_metric",
    )
    return {bucket["key"]: bucket["count"] for bucket in out["buckets"]}, out["total"]


def _read(agent, ref):
    """read(eval_result), by alias or by raw UUID."""
    return agent._tool_read("eval_result", ref)


def _not_found(out):
    return out.get("is_error") is True and out.get("code") == "not_found"


@pytest.mark.django_db
class TestTheOtherOrganizationsRowsStayOut:
    """Home's agent, on a trace id a span of the other organization carries."""

    def test_list_shows_none_of_its_rows_metrics_or_counts(self, shared):
        with _agent(shared) as agent:
            out, ids = _listed(agent)

        assert str(shared.foreign_eval.id) not in ids
        assert "foreign-metric" not in {item["metric"] for item in out["items"]}
        assert not any(
            "foreign" in (item["explanation_snippet"] or "") for item in out["items"]
        )
        assert out["total_count"] == 2

    def test_aggregate_counts_none_of_its_rows(self, shared):
        with _agent(shared) as agent:
            counts, total = _counted(agent)

        assert "foreign-metric" not in counts
        assert total == 2

    def test_read_by_alias_answers_not_found(self, shared):
        with _agent(shared) as agent:
            alias = agent._mint_alias("eval_result", str(shared.foreign_eval.id))
            out = _read(agent, alias)

        assert _not_found(out)
        assert "foreign explanation" not in str(out)

    def test_read_by_raw_uuid_answers_not_found(self, shared):
        with _agent(shared) as agent:
            out = _read(agent, str(shared.foreign_eval.id))

        assert _not_found(out)
        assert "foreign explanation" not in str(out)


@pytest.mark.django_db
class TestOwnRowsStillShow:
    """Home's rows on the shared trace and on the trace only home holds."""

    def test_own_rows_still_list(self, shared):
        with _agent(shared) as agent:
            out, ids = _listed(agent)

        assert {str(shared.home_eval.id), str(shared.home_only_eval.id)} <= set(ids)
        assert "home-metric" in {item["metric"] for item in out["items"]}

    def test_own_rows_still_count(self, shared):
        with _agent(shared) as agent:
            counts, _ = _counted(agent)

        assert counts["home-metric"] == 2

    def test_own_row_still_reads_by_alias_and_by_raw_uuid(self, shared):
        with _agent(shared) as agent:
            out, ids = _listed(agent)
            alias = out["items"][ids.index(str(shared.home_eval.id))]["id"]
            by_alias = _read(agent, alias)
            by_uuid = _read(agent, str(shared.home_eval.id))

        for read in (by_alias, by_uuid):
            assert read["_uuid"] == str(shared.home_eval.id)
            assert read["eval_explanation"] == "home explanation"


@pytest.mark.django_db
class TestSessionClusterRowsStillShow:
    """A session-level row carries no trace and no span: it shows through the
    cluster's session."""

    def _assert_the_session_row_shows(self, shared, session_cluster):
        with _agent(shared, session_cluster.cluster) as agent:
            out, ids = _listed(agent)
            counts, total = _counted(agent)
            read = _read(agent, out["items"][0]["id"]) if out["items"] else {}

        assert ids == [str(session_cluster.row.id)]
        assert (counts, total) == ({"home-metric": 1}, 1)
        assert read.get("eval_explanation") == "session explanation"

    def test_session_level_rows_list_read_and_count(self, shared, session_cluster):
        self._assert_the_session_row_shows(shared, session_cluster)

    def test_session_level_rows_show_when_the_span_id_read_fails(
        self, shared, session_cluster
    ):
        shared.table.fail = True

        self._assert_the_session_row_shows(shared, session_cluster)


@pytest.mark.django_db
class TestOneSpanReadPerRun:
    def test_one_project_scoped_read_serves_every_tool(self, shared):
        with _agent(shared) as agent:
            _listed(agent)
            _counted(agent)
            _read(agent, str(shared.home_eval.id))
            _listed(agent)

        assert shared.table.reads == [(str(shared.home.id), shared.traces)]


@pytest.mark.django_db
class TestNoSpanIdsNoTraceLevelRows:
    """Without span ids of home's, no trace-level row is read, and the agent
    says so once."""

    def _assert_no_trace_level_row(self, shared):
        with _agent(shared) as agent, patch(f"{_AGENT}.logger") as log:
            out, _ = _listed(agent)
            counts, total = _counted(agent)
            read = _read(agent, str(shared.home_eval.id))

        assert (out["items"], out["total_count"]) == ([], 0)
        assert (counts, total) == ({}, 0)
        assert _not_found(read)
        assert log.warning.call_count == 1
        return log

    def test_no_span_of_this_project_reads_no_trace_level_row(self, shared):
        shared.table.drop(shared.home)

        self._assert_no_trace_level_row(shared)

    def test_a_clickhouse_error_reads_no_trace_level_row_and_warns_once(self, shared):
        shared.table.fail = True

        log = self._assert_no_trace_level_row(shared)

        assert "ClickHouse is unavailable" in str(log.warning.call_args)
        assert len(shared.table.reads) == 1
