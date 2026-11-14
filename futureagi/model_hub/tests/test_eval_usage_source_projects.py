"""TH-4804: Usage lists a trace-attached eval log by the source it ran on.

Owner rule (13 Nov 2026): "the eval should be attached to the source where the
eval was ran, not the eval config". The Usage page used to list a trace log
only while its trace's project held a *current* config of this eval, so a log
whose config later moved to another project, or was deleted, vanished from
Usage. A log that recorded its project is now listed by that project, never
from a project the caller cannot read. An older log recorded no project, so
nothing can name its source: it keeps the rule Usage had before (the trace
held by exactly one project running the eval), limited to readable projects.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from inspect import unwrap
from types import SimpleNamespace

import pytest
from django.db.models import Q

from accounts.models import Organization
from accounts.models.workspace import Workspace
from conftest import _ch_test_native_client, _ch_test_native_port
from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import EvalTemplate
from model_hub.selectors import eval_usage
from model_hub.serializers.contracts import EvalUsageQuerySerializer
from model_hub.services import eval_log_source_navigation
from model_hub.views import separate_evals
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.project import Project
from tracer.services import exact_aggregation_cache
from tracer.services.clickhouse import trace_project_scope
from tracer.services.clickhouse.client import ClickHouseClient
from tracer.services.exact_aggregation_cache import publish_exact_snapshot

pytest.importorskip("ee.usage.models.usage", reason="requires ee/")
pytestmark = [pytest.mark.django_db, pytest.mark.integration]

USAGE_QUERY = {"page": 0, "page_size": 25, "period": "30d", "refresh": True}


@pytest.fixture
def clickhouse_usage(settings, monkeypatch):
    """Usage and trace tables on the test ClickHouse that the Usage view reads."""

    settings.EVAL_USAGE_CLICKHOUSE_ENABLED = True
    monkeypatch.setattr(separate_evals, "is_clickhouse_enabled", lambda: True)
    suffix = uuid.uuid4().hex[:10]
    usage_table = f"_test_usage_source_{suffix}"
    trace_table = f"_test_usage_source_trace_{suffix}"
    with _ch_test_native_client() as admin:
        admin.execute(
            f"""
            CREATE TABLE {usage_table} (
                id Int64,
                log_id UUID,
                organization_id UUID,
                workspace_id Nullable(UUID),
                source_id String,
                status String,
                config String,
                -- The production column (tracer/services/clickhouse/schema.py).
                eval_trace_id String MATERIALIZED
                    JSONExtractString(JSONExtractString(config), 'trace_id'),
                deleted UInt8,
                created_at DateTime64(6, 'UTC'),
                _peerdb_is_deleted UInt8,
                _peerdb_version Int64
            ) ENGINE = MergeTree
            ORDER BY id
            """
        )
        admin.execute(
            f"""
            CREATE TABLE {trace_table} (
                id UUID, project_id UUID, is_deleted UInt8, _version UInt64
            ) ENGINE = ReplacingMergeTree(_version, is_deleted)
            ORDER BY (project_id, id)
            """
        )
        read_client = ClickHouseClient(
            host=os.environ.get("CH25_HOST", "127.0.0.1"),
            port=_ch_test_native_port().port,
            database="default",
        )
        monkeypatch.setattr(eval_usage, "_USAGE_TABLE", usage_table)
        monkeypatch.setattr(trace_project_scope, "_TRACE_TABLE", trace_table)
        monkeypatch.setattr(eval_usage, "get_clickhouse_client", lambda: read_client)

        def insert(*, logs=(), traces=()):
            if traces:
                admin.execute(f"INSERT INTO {trace_table} VALUES", list(traces))
            if logs:
                admin.execute(
                    f"INSERT INTO {usage_table} (id, log_id, organization_id,"
                    " workspace_id, source_id, status, config, deleted,"
                    " created_at, _peerdb_is_deleted, _peerdb_version) VALUES",
                    list(logs),
                )

        try:
            yield insert
        finally:
            read_client.close()
            admin.execute(f"DROP TABLE IF EXISTS {trace_table}")
            admin.execute(f"DROP TABLE IF EXISTS {usage_table}")


@pytest.fixture
def template(organization, workspace):
    return EvalTemplate.no_workspace_objects.create(
        name=f"usage-source-{uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={"output": "Pass/Fail", "eval_type_id": "AgentEvaluator"},
        eval_tags=["llm"],
        criteria="Check {{response}}",
        model="turing_large",
        visible_ui=True,
    )


def _project(organization, workspace, name, **fields):
    return Project.no_workspace_objects.create(
        name=name,
        organization=organization,
        workspace=workspace,
        model_type="GenerativeLLM",
        trace_type="observe",
        **fields,
    )


def _config(project, template, name):
    return CustomEvalConfig.objects.create(
        name=name,
        project=project,
        eval_template=template,
        config={},
        mapping={"output": "output"},
        filters={},
    )


class _Ledger:
    """Builds usage rows the way PeerDB mirrors ``usage_apicalllog``."""

    def __init__(self, organization, workspace, template):
        self.organization = organization
        self.workspace = workspace
        self.template = template
        self.now = datetime.now(UTC).replace(microsecond=0)
        self.next_id = 0

    def row(
        self,
        *,
        trace_id=None,
        config_id=None,
        project_id=None,
        double_encoded=False,
        workspace=None,
    ):
        self.next_id += 1
        config = {
            "source": "tracer",
            "output": {"output": "Passed"},
        }
        if trace_id is not None:
            config.update(
                trace_id=str(trace_id),
                span_id=uuid.uuid4().hex[:16],
                custom_eval_config_id=str(config_id),
            )
        if project_id is not None:
            # Logs written since TH-4804 record the project the eval ran in.
            config["project_id"] = str(project_id)
        encoded = json.dumps(config)
        log_id = uuid.uuid4()
        return log_id, (
            self.next_id,
            log_id,
            self.organization.id,
            (workspace or self.workspace).id,
            str(self.template.id),
            "success",
            json.dumps(encoded) if double_encoded else encoded,
            0,
            self.now - timedelta(hours=self.next_id),
            0,
            1,
        )


def _usage(template, organization, workspace):
    """The payload the exact-aggregation worker publishes for a 30D read."""

    query = EvalUsageQuerySerializer(data=USAGE_QUERY)
    query.is_valid(raise_exception=True)
    request = SimpleNamespace(
        validated_query_data=query.validated_data,
        organization=organization,
        workspace=workspace,
        user=SimpleNamespace(organization=organization),
        _exact_aggregation_worker=True,
    )
    response = unwrap(separate_evals.EvalUsageStatsView.get)(
        separate_evals.EvalUsageStatsView(), request, template.id
    )
    assert response.status_code == 200, response.data
    return response.data["result"]


def _listed(result):
    return {row["row_id"] for row in result["table"]}


def test_logs_stay_listed_after_their_config_moves_or_is_deleted(
    organization, workspace, template, clickhouse_usage
):
    source = _project(organization, workspace, "Where the evals ran")
    elsewhere = _project(organization, workspace, "Config moved here")
    moved = _config(source, template, "moved later")
    gone = _config(source, template, "deleted later")
    ledger = _Ledger(organization, workspace, template)
    moved_trace, gone_trace, stamped_trace = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    older_moved, older_moved_row = ledger.row(trace_id=moved_trace, config_id=moved.id)
    older_gone, older_gone_row = ledger.row(
        trace_id=gone_trace, config_id=gone.id, double_encoded=True
    )
    stamped, stamped_row = ledger.row(
        trace_id=stamped_trace,
        config_id=moved.id,
        project_id=source.id,
        double_encoded=True,
    )
    playground, playground_row = ledger.row()
    clickhouse_usage(
        traces=[
            (moved_trace, source.id, 0, 1),
            (gone_trace, source.id, 0, 1),
            (stamped_trace, source.id, 0, 1),
        ],
        logs=[older_moved_row, older_gone_row, stamped_row, playground_row],
    )
    # After the runs: one config moves to another project, the other is gone.
    CustomEvalConfig.all_objects.filter(id=moved.id).update(project=elsewhere)
    CustomEvalConfig.all_objects.filter(id=gone.id).delete()
    assert not CustomEvalConfig.all_objects.filter(project=source).exists()

    result = _usage(template, organization, workspace)

    # The recorded log stays listed by its source. The older logs keep the
    # rule from before sources were recorded: no project running this eval
    # holds their traces any more, so they are not listed (as on dev).
    assert _listed(result) == {str(stamped), str(playground)}
    assert {str(older_moved), str(older_gone)}.isdisjoint(_listed(result))
    assert result["logs"]["total"] == 2
    assert result["stats"]["runs_period"] == 2


def test_logs_from_projects_the_caller_cannot_read_never_appear(
    organization, user, workspace, template, clickhouse_usage
):
    source = _project(organization, workspace, "Readable source")
    deleted = _project(organization, workspace, "Deleted later")
    other_workspace = Workspace.no_workspace_objects.create(
        name=str(uuid.uuid4()),
        organization=organization,
        created_by=user,
        is_default=False,
        is_active=True,
    )
    walled = _project(organization, other_workspace, "Other workspace")
    foreign = _project(
        Organization.objects.create(name="Unreadable organization"),
        None,
        "Other organization",
    )
    # Every project has a live config of this eval, so the old config-based
    # scope would have offered each of them.
    configs = {
        project.id: _config(project, template, project.name)
        for project in (source, deleted, walled)
    }
    ledger = _Ledger(organization, workspace, template)
    readable_trace = uuid.uuid4()
    deleted_trace = uuid.uuid4()
    walled_trace = uuid.uuid4()
    foreign_trace = uuid.uuid4()
    # The same trace id also lives in the readable project: the recorded
    # (unreadable) project decides, not the readable copy.
    copied_trace = uuid.uuid4()

    visible, visible_row = ledger.row(
        trace_id=readable_trace, config_id=configs[source.id].id
    )
    _, deleted_row = ledger.row(
        trace_id=deleted_trace, config_id=configs[deleted.id].id
    )
    _, walled_row = ledger.row(trace_id=walled_trace, config_id=configs[walled.id].id)
    _, foreign_row = ledger.row(trace_id=foreign_trace, config_id=configs[source.id].id)
    _, stamped_foreign_row = ledger.row(
        trace_id=copied_trace,
        config_id=configs[source.id].id,
        project_id=foreign.id,
    )
    clickhouse_usage(
        traces=[
            (readable_trace, source.id, 0, 1),
            (deleted_trace, deleted.id, 0, 1),
            (walled_trace, walled.id, 0, 1),
            (foreign_trace, foreign.id, 0, 1),
            (copied_trace, foreign.id, 0, 1),
            (copied_trace, source.id, 0, 1),
        ],
        logs=[visible_row, deleted_row, walled_row, foreign_row, stamped_foreign_row],
    )
    Project.no_workspace_objects.filter(id=deleted.id).update(deleted=True)

    result = _usage(template, organization, workspace)

    assert _listed(result) == {str(visible)}
    assert result["logs"]["total"] == 1


def test_a_recorded_project_with_an_escaped_key_still_decides(
    organization, workspace, template, clickhouse_usage
):
    """JSON may escape any key character; the recorded project still decides.

    The log recorded a project the caller cannot read (another organization)
    and a readable project holds a copy of its trace id, so it must stay
    hidden, exactly as with a plainly written key."""

    readable = _project(organization, workspace, "Readable copy")
    foreign = _project(
        Organization.objects.create(name="Unreadable organization"),
        None,
        "Other organization",
    )
    config = _config(readable, template, "cfg")
    ledger = _Ledger(organization, workspace, template)
    trace_id = uuid.uuid4()
    _, row = ledger.row(trace_id=trace_id, config_id=config.id, project_id=foreign.id)
    escaped = row[6].replace('"project_id"', '"\\u0070roject_id"')
    assert escaped != row[6]
    row = (*row[:6], escaped, *row[7:])
    clickhouse_usage(
        traces=[(trace_id, foreign.id, 0, 1), (trace_id, readable.id, 0, 1)],
        logs=[row],
    )

    assert _listed(_usage(template, organization, workspace)) == set()


def test_a_trace_tombstoned_in_its_project_is_not_listed(
    organization, workspace, template, clickhouse_usage
):
    source = _project(organization, workspace, "Source")
    replay = _project(organization, workspace, "Replay")
    config = _config(source, template, "cfg")
    ledger = _Ledger(organization, workspace, template)
    trace_id = uuid.uuid4()
    stamped, stamped_row = ledger.row(
        trace_id=trace_id, config_id=config.id, project_id=source.id
    )
    older, older_row = ledger.row(trace_id=trace_id, config_id=config.id)
    clickhouse_usage(
        traces=[
            (trace_id, source.id, 0, 1),
            (trace_id, source.id, 1, 2),
            (trace_id, replay.id, 0, 1),
        ],
        logs=[stamped_row, older_row],
    )

    # The recorded project no longer holds the trace live. The older log is
    # not attributed to the live replay either: the only project running the
    # eval holds the trace tombstoned (the rule from before sources were
    # recorded), so neither log is listed.
    assert _listed(_usage(template, organization, workspace)) == set()


def test_past_the_project_bound_falls_back_to_projects_running_the_eval(
    organization, user, workspace, template, clickhouse_usage, monkeypatch
):
    """Past MAX_SOURCE_PROJECTS the scope narrows to the readable projects
    that run this eval now: still never an unreadable project. Older logs
    keep their rule (the trace held by exactly one of those projects)."""

    monkeypatch.setattr(eval_log_source_navigation, "MAX_SOURCE_PROJECTS", 1)
    warnings = []
    monkeypatch.setattr(
        separate_evals.logger,
        "warning",
        lambda event, **fields: warnings.append((event, fields)),
    )
    configured = _project(organization, workspace, "Runs this eval")
    unconfigured = _project(organization, workspace, "Another readable project")
    other_workspace = Workspace.no_workspace_objects.create(
        name=str(uuid.uuid4()),
        organization=organization,
        created_by=user,
        is_default=False,
        is_active=True,
    )
    walled = _project(organization, other_workspace, "Unreadable, runs this eval")
    config = _config(configured, template, "cfg")
    walled_config = _config(walled, template, "walled cfg")
    ledger = _Ledger(organization, workspace, template)
    trace_id, elsewhere_trace, walled_trace = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    older, row = ledger.row(trace_id=trace_id, config_id=config.id)
    stamped, stamped_row = ledger.row(
        trace_id=trace_id, config_id=config.id, project_id=configured.id
    )
    _, elsewhere_row = ledger.row(trace_id=elsewhere_trace, config_id=config.id)
    _, walled_row = ledger.row(trace_id=walled_trace, config_id=walled_config.id)
    clickhouse_usage(
        traces=[
            (trace_id, configured.id, 0, 1),
            (elsewhere_trace, unconfigured.id, 0, 1),
            (walled_trace, walled.id, 0, 1),
        ],
        logs=[row, stamped_row, elsewhere_row, walled_row],
    )

    assert _listed(_usage(template, organization, workspace)) == {
        str(older),
        str(stamped),
    }
    assert [event for event, _ in warnings] == [
        "eval_usage.source_project_scope_too_large",
    ]
    assert warnings[0][1]["template_id"] == str(template.id)


@pytest.mark.parametrize("double_encoded", [False, True], ids=["object", "string"])
def test_both_config_encodings_are_listed_by_their_trace_source(
    organization, user, workspace, template, clickhouse_usage, double_encoded
):
    """Configs are stored as a JSON object or as a JSON string holding it.

    ``eval_trace_id`` is the production MATERIALIZED column, so each encoding
    takes the same trace-attached path: listed while a readable project holds
    the trace, hidden when that project was deleted or is in a workspace the
    caller cannot read, for older and for stamped logs alike.
    """

    readable = _project(organization, workspace, "Readable")
    deleted = _project(organization, workspace, "Deleted later")
    other_workspace = Workspace.no_workspace_objects.create(
        name=str(uuid.uuid4()),
        organization=organization,
        created_by=user,
        is_default=False,
        is_active=True,
    )
    walled = _project(organization, other_workspace, "Other workspace")
    config = _config(readable, template, "cfg")
    ledger = _Ledger(organization, workspace, template)
    traces = {project.id: uuid.uuid4() for project in (readable, deleted, walled)}
    listed = set()
    rows = []
    for project in (readable, deleted, walled):
        for stamped in (False, True):
            log_id, row = ledger.row(
                trace_id=traces[project.id],
                config_id=config.id,
                project_id=project.id if stamped else None,
                double_encoded=double_encoded,
            )
            rows.append(row)
            if project is readable:
                listed.add(str(log_id))
    clickhouse_usage(
        traces=[(trace, project_id, 0, 1) for project_id, trace in traces.items()],
        logs=rows,
    )
    Project.no_workspace_objects.filter(id=deleted.id).update(deleted=True)

    result = _usage(template, organization, workspace)

    assert _listed(result) == listed
    assert result["logs"]["total"] == 2


def test_a_snapshot_listed_by_eval_config_is_never_served(
    auth_client, organization, workspace, template, settings, monkeypatch
):
    """A Usage snapshot cached under the config-based rule omits logs whose
    config moved or was deleted; the current request identity must not
    resolve to it."""

    settings.EVAL_USAGE_CLICKHOUSE_ENABLED = True
    monkeypatch.setattr(separate_evals, "is_clickhouse_enabled", lambda: True)
    config_rule_identity = {
        "organization_id": str(organization.id),
        "workspace_id": str(workspace.id),
        "template_id": str(template.id),
        "page": 0,
        "page_size": 25,
        "period": "30d",
        "start_date": None,
        "end_date": None,
        "runs": "success",
    }
    publish_exact_snapshot(
        "eval-usage",
        config_rule_identity,
        {
            "template_id": str(template.id),
            "stats": {"runs_period": 7, "success_count": 7, "error_count": 0},
            "query_complete": True,
            "query_status": "complete",
            "query_sampled": False,
        },
    )
    cache_only = exact_aggregation_cache.read_or_schedule_exact_snapshot
    monkeypatch.setattr(
        separate_evals,
        "read_or_schedule_exact_snapshot",
        lambda namespace, identity, **kwargs: cache_only(
            namespace, identity, **{**kwargs, "schedule_on_miss": False}
        ),
    )

    response = auth_client.get(
        f"/model-hub/eval-templates/{template.id}/usage/",
        {"page": 0, "page_size": 25, "period": "30d"},
    )

    assert response.status_code == 200, response.content
    result = response.json()["result"]
    assert result["query_status"] == "pending"
    assert result["stats"]["runs_period"] == 0


def _cache_only_usage(auth_client, template, monkeypatch):
    """GET the Usage page serving the exact-snapshot cache only; return the
    response payload and the cache identity the view asked for."""
    asked = []
    cache_only = exact_aggregation_cache.read_or_schedule_exact_snapshot

    def read(namespace, identity, **kwargs):
        asked.append(identity)
        return cache_only(namespace, identity, **{**kwargs, "schedule_on_miss": False})

    monkeypatch.setattr(separate_evals, "read_or_schedule_exact_snapshot", read)
    monkeypatch.setattr(
        separate_evals, "eval_usage_snapshot_is_stale", lambda **kwargs: False
    )
    response = auth_client.get(
        f"/model-hub/eval-templates/{template.id}/usage/",
        {"page": 0, "page_size": 25, "period": "30d"},
    )
    assert response.status_code == 200, response.content
    return response.json()["result"], asked[-1]


def _cached_payload(template, stamp=None):
    payload = {
        "template_id": str(template.id),
        "stats": {"runs_period": 7, "success_count": 7, "error_count": 0},
        "query_complete": True,
        "query_status": "complete",
        "query_sampled": False,
    }
    if stamp is not None:
        payload["trace_logs_by"] = stamp
    return payload


@pytest.mark.parametrize(
    "stamp",
    [
        None,
        "source_project",
        "source_project_unique_in_org",
        separate_evals.EVAL_USAGE_TRACE_LOGS_BY,
    ],
    ids=["older-worker", "previous-rule", "org-wide-rule", "current"],
)
def test_a_snapshot_an_older_worker_cached_under_the_new_identity_is_a_miss(
    auth_client, organization, workspace, template, settings, monkeypatch, stamp
):
    """During a rolling deploy an older worker can take a refresh job for the
    current identity and cache a payload listed by eval config, or by an
    earlier rule for older logs ("source_project" listed them by any readable
    project holding the trace; "source_project_unique_in_org" read other
    workspaces' traces). Only a payload that names the current listing rule
    is served."""

    settings.EVAL_USAGE_CLICKHOUSE_ENABLED = True
    monkeypatch.setattr(separate_evals, "is_clickhouse_enabled", lambda: True)
    _, identity = _cache_only_usage(auth_client, template, monkeypatch)
    assert identity["trace_logs_by"] == separate_evals.EVAL_USAGE_TRACE_LOGS_BY
    publish_exact_snapshot("eval-usage", identity, _cached_payload(template, stamp))

    result, _ = _cache_only_usage(auth_client, template, monkeypatch)
    if stamp == separate_evals.EVAL_USAGE_TRACE_LOGS_BY:
        assert result["query_status"] == "complete"
        assert result["stats"]["runs_period"] == 7
    else:
        assert result["query_status"] == "pending"
        assert result["stats"]["runs_period"] == 0


@pytest.mark.parametrize(
    "change,served",
    [
        ("deleted", False),
        ("moved", False),
        ("added", False),
        ("config_moved", False),
        ("added_in_another_workspace", True),
    ],
)
def test_a_snapshot_is_served_only_for_the_scope_it_listed_by(
    auth_client,
    organization,
    user,
    workspace,
    template,
    settings,
    monkeypatch,
    change,
    served,
):
    """Which logs Usage lists depends on the projects the caller may read
    and, for older logs, on the projects running this eval. A snapshot
    computed before either changed is a miss, not a stale list. A project in
    a workspace the caller cannot read changes neither, so it is not."""

    settings.EVAL_USAGE_CLICKHOUSE_ENABLED = True
    monkeypatch.setattr(separate_evals, "is_clickhouse_enabled", lambda: True)
    source = _project(organization, workspace, "Original source")
    other = _project(organization, workspace, "Another readable project")
    config = _config(source, template, "cfg")
    _, identity = _cache_only_usage(auth_client, template, monkeypatch)
    publish_exact_snapshot(
        "eval-usage",
        identity,
        _cached_payload(template, separate_evals.EVAL_USAGE_TRACE_LOGS_BY),
    )
    first, _ = _cache_only_usage(auth_client, template, monkeypatch)
    assert first["query_status"] == "complete"

    other_workspace = Workspace.no_workspace_objects.create(
        name=str(uuid.uuid4()),
        organization=organization,
        created_by=user,
        is_default=False,
        is_active=True,
    )
    if change == "deleted":
        Project.no_workspace_objects.filter(id=source.id).update(deleted=True)
    elif change == "moved":
        Project.no_workspace_objects.filter(id=source.id).update(
            workspace=other_workspace
        )
    elif change == "added":
        _project(organization, workspace, "New readable project")
    elif change == "config_moved":
        CustomEvalConfig.all_objects.filter(id=config.id).update(project=other)
    else:
        _project(organization, other_workspace, "Replay target elsewhere")

    result, changed = _cache_only_usage(auth_client, template, monkeypatch)
    if served:
        assert result["query_status"] == "complete"
        assert changed["source_scope"] == identity["source_scope"]
    else:
        assert result["query_status"] == "pending"
        assert result["stats"]["runs_period"] == 0
        assert changed["source_scope"] != identity["source_scope"]


def test_every_computed_usage_payload_names_its_listing_rule(
    organization, workspace, template, clickhouse_usage
):
    assert _usage(template, organization, workspace)["trace_logs_by"] == (
        "recorded_source_project"
    )


def _older_log_scenarios(organization, user, workspace, template):
    """Older logs (no recorded project) in every shape the gate named.

    Returns the projects, the ledger rows and the trace rows, and the log ids
    GitHub dev (6cd28b4a1) lists for this seed: ``latest_live_trace_projects_sql``
    over the projects holding a config of the eval in this workspace, a trace
    held (live or tombstoned) by exactly one of them and live there. Every
    configured project here is readable, so "limited to readable projects"
    removes nothing and dev's rows are the expected rows exactly.
    """
    a = _project(organization, workspace, "A runs the eval")
    b = _project(organization, workspace, "B runs the eval")
    c = _project(organization, workspace, "C readable, no config")
    moved_from = _project(organization, workspace, "Config moved away")
    hidden_workspace = Workspace.no_workspace_objects.create(
        name=str(uuid.uuid4()),
        organization=organization,
        created_by=user,
        is_default=False,
        is_active=True,
    )
    hidden = _project(organization, hidden_workspace, "Inaccessible workspace")
    configs = {
        project.id: _config(project, template, project.name)
        for project in (a, b, moved_from, hidden)
    }
    ledger = _Ledger(organization, workspace, template)
    names = (
        "only_a",
        "in_a_and_b",
        "tombstoned_a_live_b",
        "tombstoned_a",
        "a_and_unconfigured_c",
        "only_unconfigured_c",
        "only_moved_from",
        "a_and_hidden",
        "no_trace_anywhere",
    )
    traces = {name: uuid.uuid4() for name in names}
    logs, rows = {}, []
    for name in names:
        log_id, row = ledger.row(trace_id=traces[name], config_id=configs[a.id].id)
        logs[name] = str(log_id)
        rows.append(row)
    t = traces
    trace_rows = [
        (t["only_a"], a.id, 0, 1),
        (t["in_a_and_b"], a.id, 0, 1),
        (t["in_a_and_b"], b.id, 0, 1),
        (t["tombstoned_a_live_b"], a.id, 0, 1),
        (t["tombstoned_a_live_b"], a.id, 1, 2),
        (t["tombstoned_a_live_b"], b.id, 0, 1),
        (t["tombstoned_a"], a.id, 0, 1),
        (t["tombstoned_a"], a.id, 1, 2),
        (t["a_and_unconfigured_c"], a.id, 0, 1),
        (t["a_and_unconfigured_c"], c.id, 0, 1),
        (t["only_unconfigured_c"], c.id, 0, 1),
        (t["only_moved_from"], moved_from.id, 0, 1),
        (t["a_and_hidden"], a.id, 0, 1),
    ]
    dev_rows = {
        logs[name] for name in ("only_a", "a_and_unconfigured_c", "a_and_hidden")
    }
    return SimpleNamespace(
        a=a,
        hidden=hidden,
        moved_config=configs[moved_from.id],
        b=b,
        logs=logs,
        rows=rows,
        trace_rows=trace_rows,
        traces=traces,
        dev_rows=dev_rows,
    )


def test_older_logs_are_listed_exactly_as_on_dev(
    organization, user, workspace, template, clickhouse_usage
):
    """TH-4804 r9: an older log is listed by dev's rule, limited to readable
    projects, with no new rule of its own. In ``tombstoned_a_live_b`` (A holds
    the original tombstoned, B a live replay, and both still run the eval) the
    log is not listed, and a trace held only by a readable project without a
    config is not listed either, exactly as on dev. When A no longer runs the
    eval and B does, the replay in B is listed, as on dev: for older logs only
    navigation is closed, never the dev listing. ``a_and_hidden`` is listed,
    as on dev, whatever the inaccessible workspace holds."""
    seed = _older_log_scenarios(organization, user, workspace, template)
    # The moved-away config: its project no longer runs the eval.
    CustomEvalConfig.all_objects.filter(id=seed.moved_config.id).update(project=seed.b)
    clickhouse_usage(traces=seed.trace_rows, logs=seed.rows)

    result = _usage(template, organization, workspace)

    assert _listed(result) == seed.dev_rows
    assert result["logs"]["total"] == len(seed.dev_rows)


def test_older_logs_ignore_a_same_id_trace_in_an_inaccessible_workspace(
    organization, user, workspace, template, clickhouse_usage, monkeypatch
):
    """The gate's cross-workspace case for Usage: a same-id trace in a
    workspace the caller cannot access changes neither the listed rows nor any
    other part of the payload, and no ClickHouse read binds a project outside
    the caller's readable set."""
    seed = _older_log_scenarios(organization, user, workspace, template)
    CustomEvalConfig.all_objects.filter(id=seed.moved_config.id).update(project=seed.b)
    clickhouse_usage(traces=seed.trace_rows, logs=seed.rows)
    bound = []
    original_client = eval_usage.get_clickhouse_client

    def recording_client():
        client = original_client()
        execute_read = client.execute_read

        def record(query, params=None, **kwargs):
            bound.append(params or {})
            return execute_read(query, params, **kwargs)

        client.execute_read = record
        return client

    monkeypatch.setattr(eval_usage, "get_clickhouse_client", recording_client)

    def comparable(payload):
        return {k: v for k, v in payload.items() if k != "query_completed_at"}

    before = _usage(template, organization, workspace)
    # Same trace ids, now also held (live, and tombstoned) in the
    # inaccessible workspace.
    clickhouse_usage(
        traces=[
            *((trace, seed.hidden.id, 0, 3) for trace in seed.traces.values()),
            (seed.traces["only_a"], seed.hidden.id, 1, 4),
        ]
    )
    after = _usage(template, organization, workspace)

    assert comparable(after) == comparable(before)
    assert _listed(after) == seed.dev_rows
    # The caller's readable scope: live projects of the organization in this
    # workspace or in none (readable_source_projects).
    readable = {
        str(project.id)
        for project in Project.no_workspace_objects.filter(
            Q(workspace=workspace) | Q(workspace__isnull=True),
            organization=organization,
            deleted=False,
        )
    }
    projects_bound = {
        str(project_id)
        for params in bound
        for key in ("project_ids", "configured_project_ids")
        for project_id in params.get(key, ())
    }
    assert bound and projects_bound <= readable
    assert str(seed.hidden.id) not in projects_bound
    assert not any(
        key in params
        for params in bound
        for key in ("outside_project_ids", "trace_scan_project_ids")
    )


def test_older_logs_with_no_readable_project_running_the_eval_are_not_listed(
    organization, workspace, template, clickhouse_usage
):
    """With no readable project holding a config of the eval, dev bound a
    placeholder project id and its LEFT JOIN default matched it, listing every
    trace-attached log whatever its trace's project. Limited to readable
    projects, none is listed; a recorded readable source still is."""
    source = _project(organization, workspace, "Source, config deleted")
    gone = _config(source, template, "deleted later")
    ledger = _Ledger(organization, workspace, template)
    trace_id = uuid.uuid4()
    older, row = ledger.row(trace_id=trace_id, config_id=gone.id)
    stamped, stamped_row = ledger.row(
        trace_id=trace_id, config_id=gone.id, project_id=source.id
    )
    clickhouse_usage(traces=[(trace_id, source.id, 0, 1)], logs=[row, stamped_row])
    CustomEvalConfig.all_objects.filter(id=gone.id).delete()

    assert _listed(_usage(template, organization, workspace)) == {str(stamped)}


def test_a_trace_copy_in_another_organization_changes_nothing(
    organization, workspace, template, clickhouse_usage
):
    """A copy of the trace in another organization is never read."""
    source = _project(organization, workspace, "Source")
    foreign = _project(
        Organization.objects.create(name="Colliding organization"),
        None,
        "Other organization",
    )
    config = _config(source, template, "cfg")
    ledger = _Ledger(organization, workspace, template)
    trace_id = uuid.uuid4()
    older, row = ledger.row(trace_id=trace_id, config_id=config.id)
    clickhouse_usage(
        traces=[(trace_id, source.id, 0, 1), (trace_id, foreign.id, 0, 1)],
        logs=[row],
    )

    assert _listed(_usage(template, organization, workspace)) == {str(older)}
