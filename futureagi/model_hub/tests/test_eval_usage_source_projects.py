"""TH-4804: Usage lists a trace-attached eval log by the source it ran on.

Owner rule (13 Nov 2026): "the eval should be attached to the source where the
eval was ran, not the eval config". The Usage page used to list a trace log
only while its trace's project held a *current* config of this eval, so a log
whose config later moved to another project, or was deleted, vanished from
Usage (and its View trace became unreachable). A log is now listed by its
recorded project, or for older logs by the readable project that holds its
trace, and never from a project the caller cannot read.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from inspect import unwrap
from types import SimpleNamespace

import pytest

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

    assert _listed(result) == {
        str(older_moved),
        str(older_gone),
        str(stamped),
        str(playground),
    }
    assert result["logs"]["total"] == 4
    assert result["stats"]["runs_period"] == 4


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

    # The recorded project no longer holds the trace live. The older log's
    # trace is still live in another readable project, so it stays listed.
    assert _listed(_usage(template, organization, workspace)) == {str(older)}


def test_past_the_project_bound_falls_back_to_projects_running_the_eval(
    organization, user, workspace, template, clickhouse_usage, monkeypatch
):
    """Past MAX_SOURCE_PROJECTS the scope narrows to the readable projects
    that run this eval now: still never an unreadable project, and a log
    whose trace lives only in another readable project is no longer listed."""

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
    log, row = ledger.row(trace_id=trace_id, config_id=config.id)
    _, elsewhere_row = ledger.row(trace_id=elsewhere_trace, config_id=config.id)
    _, walled_row = ledger.row(trace_id=walled_trace, config_id=walled_config.id)
    clickhouse_usage(
        traces=[
            (trace_id, configured.id, 0, 1),
            (elsewhere_trace, unconfigured.id, 0, 1),
            (walled_trace, walled.id, 0, 1),
        ],
        logs=[row, elsewhere_row, walled_row],
    )

    assert _listed(_usage(template, organization, workspace)) == {str(log)}
    assert [event for event, _ in warnings] == [
        "eval_usage.source_project_scope_too_large"
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


@pytest.mark.parametrize("stamped", [False, True], ids=["older-worker", "current"])
def test_a_snapshot_an_older_worker_cached_under_the_new_identity_is_a_miss(
    auth_client, organization, workspace, template, settings, monkeypatch, stamped
):
    """During a rolling deploy a pre-TH-4804 worker can take a refresh job for
    the current identity and cache a payload listed by eval config under it.
    Only a payload that says it lists trace logs by their source is served."""

    settings.EVAL_USAGE_CLICKHOUSE_ENABLED = True
    monkeypatch.setattr(separate_evals, "is_clickhouse_enabled", lambda: True)
    current_identity = {
        "organization_id": str(organization.id),
        "workspace_id": str(workspace.id),
        "template_id": str(template.id),
        "page": 0,
        "page_size": 25,
        "period": "30d",
        "start_date": None,
        "end_date": None,
        "runs": "success",
        "trace_logs_by": "source_project",
    }
    payload = {
        "template_id": str(template.id),
        "stats": {"runs_period": 7, "success_count": 7, "error_count": 0},
        "query_complete": True,
        "query_status": "complete",
        "query_sampled": False,
    }
    if stamped:
        payload["trace_logs_by"] = "source_project"
    publish_exact_snapshot("eval-usage", current_identity, payload)
    cache_only = exact_aggregation_cache.read_or_schedule_exact_snapshot
    monkeypatch.setattr(
        separate_evals,
        "read_or_schedule_exact_snapshot",
        lambda namespace, identity, **kwargs: cache_only(
            namespace, identity, **{**kwargs, "schedule_on_miss": False}
        ),
    )
    monkeypatch.setattr(
        separate_evals, "eval_usage_snapshot_is_stale", lambda **kwargs: False
    )

    response = auth_client.get(
        f"/model-hub/eval-templates/{template.id}/usage/",
        {"page": 0, "page_size": 25, "period": "30d"},
    )

    assert response.status_code == 200, response.content
    result = response.json()["result"]
    if stamped:
        assert result["query_status"] == "complete"
        assert result["stats"]["runs_period"] == 7
    else:
        assert result["query_status"] == "pending"
        assert result["stats"]["runs_period"] == 0


def test_every_computed_usage_payload_names_its_listing_rule(
    organization, workspace, template, clickhouse_usage
):
    assert _usage(template, organization, workspace)["trace_logs_by"] == (
        "source_project"
    )
