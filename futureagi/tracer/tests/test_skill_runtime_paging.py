"""Native ClickHouse regressions for bounded trace hydration and runtime pickers."""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.runtime_usage import read_runtime_statement
from tracer.services.clickhouse.read_budget import ReadDeadline
from tracer.services.clickhouse.v2.query_builders.skill_runtime import (
    RuntimeReadSession,
    SkillRuntimeQueryBuilder,
)
from tracer.tests import test_skill_filters as evidence
from tracer.tests.test_skill_runtime_limits import _NativeReads

pytestmark = pytest.mark.integration
skill_query_client = evidence.skill_query_client


@pytest.fixture
def runtime_session(skill_query_client):
    from conftest import _ch_test_native_client

    with _ch_test_native_client(database=skill_query_client.database) as native:
        reads = _NativeReads(native, per_query_settings=True)
        yield RuntimeReadSession(reads, deadline=ReadDeadline.start(60_000))


@pytest.fixture(scope="module")
def trace_page(skill_query_client):
    project, foreign = str(uuid4()), str(uuid4())
    loads = []
    for trace in range(100):
        for load in range(11):
            # The earliest load per trace sorts *last* in storage order. SQL
            # ordering each replay separately is not sufficient for hydration.
            when = evidence.START + timedelta(seconds=load)
            loads.append(
                evidence.span_row(
                    project,
                    f"trace-page-{trace}",
                    id=f"{trace * 11 + load:016x}",
                    start_time=when,
                    end_time=when + timedelta(milliseconds=5),
                    resource_attributes={"service.name": "z" if load == 0 else "a"},
                )
            )
    evidence.insert_rows(
        skill_query_client,
        [
            *loads,
            {**loads[0], "project_id": foreign},
            evidence.span_row(project, "off-page"),
        ],
    )
    trace_ids = list(dict.fromkeys(row["trace_id"] for row in loads))
    builder = SkillRuntimeQueryBuilder(
        project_ids=[project],
        start=datetime(1970, 1, 1, tzinfo=UTC),
        end=datetime(2100, 1, 1, tzinfo=UTC),
    )
    return builder, trace_ids, loads


def test_trace_page_hydrates_all_1100_loads_in_order(trace_page, runtime_session):
    builder, trace_ids, loads = trace_page
    rows = read_runtime_statement(
        runtime_session, builder.build_trace_loads(trace_ids=trace_ids), max_rows=10_000
    )
    expected = sorted(loads, key=lambda row: (row["start_time"], row["id"]))
    assert [(str(r["project_id"]), r["trace_id"], r["span_id"]) for r in rows] == [
        (r["project_id"], r["trace_id"], r["id"]) for r in expected
    ]


@contextmanager
def _locked_service(database, monkeypatch):
    from conftest import _ch_test_native_client
    from tracer.services.clickhouse.client import ClickHouseClient
    from tracer.services.clickhouse.query_service import AnalyticsQueryService

    with _ch_test_native_client(database=database) as native:
        # Lock this connection without installing any skill-specific profile.
        native.execute("SET readonly = 1")
        assert native.execute("SELECT getSetting('readonly')") == [(1,)]
        client = ClickHouseClient(
            host="unused.invalid", server_enforced_readonly=True, pool_size=1
        )
        # Only substitute how the pool gets its safe test connection. The
        # production client still strips SQL SETTINGS and sends no driver settings.
        monkeypatch.setattr(client, "_create_client", lambda: native)
        yield AnalyticsQueryService(ch_client=client)


def test_locked_transport_hydrates_empty_and_1100_loads(
    skill_query_client, trace_page, monkeypatch
):
    from tracer.services.trace_skills import _TraceProjectionSession

    builder, ids, loads = trace_page
    with _locked_service(skill_query_client.database, monkeypatch) as service:
        session = _TraceProjectionSession(service, deadline=ReadDeadline.start(60_000))
        for failed in (False, True):
            assert (
                read_runtime_statement(
                    session,
                    builder.build_trace_loads(trace_ids=["empty"], failed=failed),
                    max_rows=10_000,
                )
                == []
            )
        rows = read_runtime_statement(
            session, builder.build_trace_loads(trace_ids=ids), max_rows=10_000
        )
        expected = sorted(loads, key=lambda r: (r["start_time"], r["id"]))
        assert [(str(r["project_id"]), r["trace_id"], r["span_id"]) for r in rows] == [
            (r["project_id"], r["trace_id"], r["id"]) for r in expected
        ]


def test_trace_loads_stop_at_one_cumulative_sentinel(
    skill_query_client, runtime_session
):
    project = str(uuid4())
    template = evidence.span_row(project, "large-trace")
    evidence.insert_rows(
        skill_query_client, [{**template, "id": f"{i:016x}"} for i in range(11_000)]
    )
    builder = SkillRuntimeQueryBuilder(
        project_ids=[project], start=evidence.START, end=evidence.END
    )
    statement = builder.build_trace_loads(trace_ids=[template["trace_id"]])
    with pytest.raises(SkillsHubError) as denial:
        read_runtime_statement(runtime_session, statement, max_rows=10_000)
    assert (denial.value.code, denial.value.status) == ("runtime_read_too_large", 503)
    # Every admitted key in this fixture has exactly one live winner. The
    # first replay to cross the contract must be the last database call.
    calls = runtime_session.service.calls
    prior_rows = sum(len(call.get("skill_replay_keys", ())) for call in calls[:-1])
    assert prior_rows < 10_001 <= prior_rows + len(calls[-1]["skill_replay_keys"])


@pytest.fixture(scope="module")
def picker(skill_query_client):
    project = str(uuid4())
    evidence.insert_rows(
        skill_query_client,
        [
            *(
                evidence.span_row(project, f"other-{i}", version=evidence.OTHER_VERSION)
                for i in range(1025)
            ),
            evidence.span_row(project, "selected"),
        ],
    )
    return SkillRuntimeQueryBuilder(
        project_ids=[project], start=evidence.START, end=evidence.END
    )


@pytest.mark.parametrize("failed", [True, False], ids=["failed-names", "exact-version"])
def test_runtime_pickers_ignore_1025_unrelated_loads(picker, runtime_session, failed):
    rows = read_runtime_statement(
        runtime_session,
        picker.build_filter_values(
            field="skill.name" if failed else "skill.version_id",
            failed=failed,
            verified_identities=[]
            if failed
            else [(evidence.SKILL, evidence.VERSION, evidence.DIGEST)],
        ),
        max_rows=101,
    )
    assert rows == ([] if failed else [{"value": evidence.VERSION}])


@pytest.mark.parametrize(
    "failed", [False, True], ids=["context-shortlist", "failed-resource-name"]
)
def test_picker_nominates_only_requested_context_or_failed_name(
    skill_query_client, runtime_session, failed
):
    project = str(uuid4())
    requested, other = (
        ("requested's name", "different name")
        if failed
        else (str(uuid4()), str(uuid4()))
    )
    attr = "fi.skill.name" if failed else "fi.context.id"

    def load(label, value):
        row = evidence.span_row(project, label)
        if failed:
            row.update(
                status="ERROR", span_attributes={attr: value, "error.type": "not_found"}
            )
        else:
            row["span_attributes"][attr] = value
        return row

    selected = load("selected", requested)
    evidence.insert_rows(
        skill_query_client,
        [*(load(f"decoy-{i}", other) for i in range(1025)), selected],
    )
    builder = SkillRuntimeQueryBuilder(
        project_ids=[project], start=evidence.START, end=evidence.END
    )
    statement = builder.build_filter_values(
        field="skill.name" if failed else "skill.context_id",
        failed=failed,
        skill_name=requested if failed else None,
        context_ids=[] if failed else [requested],
        verified_identities=[]
        if failed
        else [(evidence.SKILL, evidence.VERSION, evidence.DIGEST)],
    )
    assert read_runtime_statement(runtime_session, statement, max_rows=101) == [
        {"value": requested}
    ]

    # Change the sort key as well as the attribute. Even after a merge the
    # stale matching physical row still nominates this key, but cannot win it.
    evidence.insert_rows(
        skill_query_client,
        [
            {
                **selected,
                "created_at": evidence.END,
                "observation_type": "corrected",
                "span_attributes": {**selected["span_attributes"], attr: other},
            }
        ],
    )
    assert read_runtime_statement(runtime_session, statement, max_rows=101) == []


def test_unpaged_picker_maps_real_replay_overflow(picker, runtime_session):
    # Now every other-version key is actually a possible winner. A LIMIT on
    # the output cannot make the embedded source exact: return the typed 503.
    with pytest.raises(SkillsHubError) as denial:
        read_runtime_statement(
            runtime_session,
            picker.build_filter_values(
                field="skill.version_id",
                verified_identities=[
                    (evidence.SKILL, evidence.OTHER_VERSION, evidence.DIGEST)
                ],
            ),
            max_rows=101,
        )
    assert (denial.value.code, denial.value.status) == ("runtime_read_too_large", 503)


@pytest.mark.parametrize("mode", ["verified", "mismatch", "failed"])
def test_usage_charges_only_requested_loads_before_replay(
    skill_query_client, runtime_session, monkeypatch, mode
):
    from tracer.services.clickhouse.query_builders import filters

    project = str(uuid4())
    monkeypatch.setattr(filters, "_SKILL_PAGED_KEY_BUDGET", 1, raising=False)
    selected = evidence.span_row(project, "selected")
    other = evidence.span_row(project, "other")
    if mode == "failed":
        selected.update(
            status="ERROR", span_attributes={"fi.skill.name": "owner's skill"}
        )
        other.update(status="ERROR", span_attributes={"fi.skill.name": "other"})
    elif mode == "mismatch":
        selected["span_attributes"]["fi.skill.digest"] = "unverified"
        other["span_attributes"]["fi.skill.id"] = str(uuid4())
    else:
        other["span_attributes"]["fi.skill.version_id"] = evidence.OTHER_VERSION
    decoys = [{**other, "id": f"{i:016x}"} for i in range(3)]
    if mode == "mismatch":
        # A verified load of this same skill cannot be a mismatch either.
        decoys.append(evidence.span_row(project, "verified"))
    evidence.insert_rows(skill_query_client, [selected, *decoys])
    builder = SkillRuntimeQueryBuilder(
        project_ids=[project], start=evidence.START, end=evidence.END
    )
    statement = builder.build_usage_loads(
        identities=[(evidence.SKILL, evidence.VERSION, evidence.DIGEST)],
        skill_names=["owner's skill"],
        failed=mode == "failed",
        mismatch=mode == "mismatch",
    )
    rows = read_runtime_statement(runtime_session, statement, max_rows=20_000)
    assert [r["span_id"] for r in rows] == [selected["id"]]
    assert [
        key[2]
        for call in runtime_session.service.calls
        for key in call.get("skill_replay_keys", ())
    ] == [selected["id"]]

    # A matching old revision still nominates; a correction in a different
    # physical sort group must win, even though it could not itself nominate.
    evidence.insert_rows(
        skill_query_client,
        [
            {
                **selected,
                "span_attributes": other["span_attributes"],
                "observation_type": "corrected",
                "created_at": evidence.END,
            }
        ],
    )
    assert read_runtime_statement(runtime_session, statement, max_rows=20_000) == []
