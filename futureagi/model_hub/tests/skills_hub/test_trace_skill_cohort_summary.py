"""Observe summary regressions against the mounted endpoint and the native binder."""

from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from model_hub.models.skills_hub import HubSkillVersion
from model_hub.tests.skills_hub import test_trace_projections_http as projections
from tracer.services import trace_skills
from tracer.services.clickhouse.runtime_cohorts import cohort_filter
from tracer.tests._ch_seed import seed_ch_spans
from tracer.tests.test_skill_filters import skill

pytestmark = [pytest.mark.django_db, pytest.mark.integration]
ch, project = projections.ch, projections.project
http, seeded = projections.http, projections.seeded
START = projections.START


@pytest.fixture
def native_summary(http, ch, monkeypatch, request):
    from conftest import _ch_test_native_client

    with _ch_test_native_client(database=ch.database) as client:

        class NativeService:
            supports_per_query_read_settings = getattr(request, "param", True)

            def execute_ch_query(
                self, sql, params, *, settings=None, server_execution_cap_ms, **kwargs
            ):
                assert 0 < server_execution_cap_ms <= 3000
                if not (settings or {}).get("server_side_params"):
                    rendered = client.substitute_params(
                        sql, params, client.connection.context
                    )
                    assert len(rendered.encode()) < 262144
                else:
                    assert self.supports_per_query_read_settings
                    assert len(sql.encode()) < 262144
                rows, columns = client.execute(
                    sql,
                    params,
                    with_column_types=True,
                    settings={"max_query_size": 262144, **(settings or {})},
                )
                data = [
                    dict(zip((name for name, _ in columns), row, strict=True))
                    for row in rows
                ]
                return SimpleNamespace(row_count=len(data), data=data)

        monkeypatch.setattr(trace_skills, "V2AnalyticsQueryService", NativeService)

        def summary(filters, *, start=START, end=START + timedelta(hours=1)):
            response = http(
                projection="cohort",
                filters=filters,
                **{"from": start.isoformat(), "to": end.isoformat()},
            )
            assert response.status_code == 200, response.data
            assert response.data["result"]["truncated"] is False
            return response.data["result"]["summary"]

        yield summary


def seed(client, *rows):
    seed_ch_spans(list(rows), client=client, version_from_created_at=True)


def test_context_only_handle_needs_no_load_but_explicit_group_does(
    native_summary,
    ch,
    seeded,
    project,
    hub_actor,
    publish_skill,
    workspace,
    organization,
):
    from model_hub.services.skills_hub.versions import create_skill

    context = create_skill(
        organization=organization,
        workspace=workspace,
        actor=hub_actor,
        name="cohort-context",
        description="Context fixture",
        tags=[],
    )
    version = publish_skill(context).version
    context.kind = "agent_context"
    context.save(update_fields=["kind"])
    trace = str(uuid4())
    seed(
        ch,
        seeded.span(
            "context",
            trace_id=trace,
            name="context.load",
            parent_span_id="",
            cost=3,
            span_attributes={
                "fi.context.id": str(context.pk),
                "fi.context.version_id": str(version.pk),
                "fi.context.digest": version.digest,
            },
        ),
    )
    handle = cohort_filter(project.pk, [trace])
    result = native_summary([handle, skill("root_status", "ok")])
    assert result["traces"] == 1 and result["cost"]["per_trace_mean"] == 3
    assert (
        native_summary([handle, skill("load_result", "loaded", group=1)])["traces"] == 0
    )
    assert (
        native_summary([handle, skill("load_result", "failed", group=1)])["traces"] == 0
    )
    assert (
        native_summary(
            [handle, skill("version_id", str(seeded.version.pk), "not_equals")]
        )["traces"]
        == 1
    )
    # A second handle is conjunctive; the other authorized trace is not in the first set.
    assert (
        native_summary([handle, cohort_filter(project.pk, [seeded.trace])])["traces"]
        == 0
    )


def test_digest_mismatch_handle_preserves_its_trace_not_a_verified_load(
    native_summary,
    ch,
    seeded,
    project,
):
    trace = str(uuid4())
    seed(
        ch,
        seeded.span(
            "mismatch",
            trace_id=trace,
            parent_span_id="",
            name="skill.load",
            cost=7,
            span_attributes={**seeded.attrs, "fi.skill.digest": "wrong"},
        ),
    )
    handle = cohort_filter(project.pk, [trace])
    result = native_summary([handle])
    assert result["traces"] == 1 and result["cost"]["per_trace_mean"] == 7
    assert (
        native_summary([handle, skill("version_id", str(seeded.version.pk))])["traces"]
        == 0
    )


@pytest.mark.parametrize(
    "equals", [False, True], ids=["request-window", "datetime-equals"]
)
def test_subsecond_cohort_uses_half_open_native_window(
    native_summary, ch, seeded, equals
):
    hit = START + timedelta(microseconds=500000)
    start, end = (
        (hit, hit + timedelta(microseconds=1))
        if equals
        else (
            START + timedelta(microseconds=100000),
            START + timedelta(microseconds=900000),
        )
    )
    for event_time in (start - timedelta(microseconds=1), hit, end):
        seed(
            ch,
            seeded.span(
                "boundary",
                trace_id=str(uuid4()),
                name="skill.load",
                parent_span_id="",
                cost=4 if event_time == hit else 8,
                start_time=event_time,
                end_time=event_time + timedelta(milliseconds=5),
                span_attributes=seeded.attrs,
            ),
        )
    moved = seeded.span(
        "moved",
        trace_id=str(uuid4()),
        parent_span_id="",
        name="skill.load",
        start_time=hit,
        end_time=hit + timedelta(milliseconds=5),
        span_attributes=seeded.attrs,
    )
    seed(
        ch,
        moved,
        {
            **moved,
            "start_time": end,
            "end_time": end + timedelta(milliseconds=5),
            "created_at": START + timedelta(minutes=1),
        },
    )
    filters = [skill("version_id", str(seeded.version.pk))]
    if equals:
        filters.append(
            {
                "column_id": "start_time",
                "filter_config": {
                    "col_type": "SYSTEM_METRIC",
                    "filter_type": "datetime",
                    "filter_op": "equals",
                    "filter_value": hit.isoformat(),
                },
            }
        )
        result = native_summary(filters)
    else:
        result = native_summary(filters, start=start, end=end)
    assert result["traces"] == 1 and result["cost"]["per_trace_mean"] == 4


@pytest.mark.parametrize(
    ("selected_version", "native_summary"),
    [(True, False), (False, True)],
    indirect=["native_summary"],
    ids=["selected-version-locked-profile", "all-versions-server-binding"],
)
def test_large_catalogue_fits_native_parser_for_selected_and_unrestricted_versions(
    selected_version,
    native_summary,
    seeded,
    ch,
):
    # Last version must not vanish through prefix truncation when no filter
    # narrows the visible catalogue. Distinct IDs and digests reflect real sizes.
    versions = [
        HubSkillVersion(
            skill=seeded.skill,
            number=seeded.version.number + i,
            digest=f"sha256:{i:064x}",
            file_count=1,
            total_bytes=1,
            frontmatter={},
            checks={},
        )
        for i in range(1, 2001)
    ]
    HubSkillVersion.objects.bulk_create(versions)
    last = versions[-1]
    attrs = {
        **seeded.attrs,
        "fi.skill.version_id": str(last.pk),
        "fi.skill.digest": last.digest,
    }
    seed(
        ch,
        seeded.span(
            "last-version",
            trace_id=str(uuid4()),
            parent_span_id="",
            name="skill.load",
            span_attributes=attrs,
        ),
    )
    seed(
        ch,
        seeded.span(
            "wrong-digest",
            trace_id=str(uuid4()),
            parent_span_id="",
            name="skill.load",
            span_attributes={**attrs, "fi.skill.digest": seeded.version.digest},
        ),
    )
    if selected_version:
        assert native_summary([skill("version_id", str(last.pk))])["traces"] == 1
    else:
        assert native_summary([skill("agent", "worker")])["traces"] == 2


def test_summary_replay_cap_counts_candidate_keys_for_selected_version(
    native_summary, http, ch, seeded, publish_skill, make_skill_files
):
    selected = publish_skill(
        seeded.skill,
        files=make_skill_files(seeded.skill.name, body="Selected version."),
    ).version
    selected_attrs = {
        **seeded.attrs,
        "fi.skill.version_id": str(selected.pk),
        "fi.skill.version": selected.number,
        "fi.skill.digest": selected.digest,
    }
    # With the fixture's original load, there are 1,025 distinct load keys.
    # Only the final trace's elected load matches; a historic match on another
    # key must nominate it for replay but cannot override its newer revision.
    others = [
        seeded.span(f"other-{i}", name="skill.load", span_attributes=seeded.attrs)
        for i in range(1023)
    ]
    stale = {**others[0], "service_name": "before", "span_attributes": selected_attrs}
    others[0]["created_at"] = START + timedelta(minutes=1)
    selected_trace = str(uuid4())
    seed(
        ch,
        stale,
        *others,
        seeded.span(
            "selected",
            trace_id=selected_trace,
            parent_span_id="",
            name="skill.load",
            cost=17,
            span_attributes=selected_attrs,
        ),
    )
    result = native_summary([skill("version_id", str(selected.pk))])
    assert result["traces"] == 1 and result["cost"]["per_trace_mean"] == 17

    # A failure in a separate group doesn't need a catalogue identity of its own.
    seed(
        ch,
        seeded.span(
            "failed",
            trace_id=selected_trace,
            parent_span_id="selected",
            name="skill.load",
            status="ERROR",
            span_attributes={"fi.skill.name": "reported", "error.type": "integrity"},
        ),
    )
    both = native_summary(
        [skill("version_id", str(selected.pk)), skill("load_result", "failed", group=1)]
    )
    assert both["traces"] == 1

    # Now every successful key is a candidate. The exact aggregate must not report the
    # first 1,024 as the complete result or expose the native throwIf as a 500.
    response = http(
        projection="cohort",
        filters=[skill("load_result", "loaded")],
        **{"from": START.isoformat(), "to": (START + timedelta(hours=1)).isoformat()},
    )
    assert response.status_code == 503, response.data
    assert response.data["code"] == "runtime_read_too_large"
    assert "summary" not in response.data.get("result", {})


def test_large_stored_handle_fits_native_parser(native_summary, seeded, project):
    ids = [str(uuid4()) for _ in range(7000)] + [seeded.trace]
    result = native_summary([cohort_filter(project.pk, ids)])
    assert result["traces"] == 1 and result["cost"]["per_trace_mean"] == 13


def test_locked_native_profile_rejects_oversized_handle_before_submission(
    http, project, monkeypatch
):
    class LockedService:
        supports_per_query_read_settings = False

        def execute_ch_query(self, *args, **kwargs):
            pytest.fail("Oversized or unbound statement reached a locked profile")

    monkeypatch.setattr(trace_skills, "V2AnalyticsQueryService", LockedService)
    response = http(
        projection="cohort",
        filters=[cohort_filter(project.pk, [str(uuid4()) for _ in range(7000)])],
        **{"from": START.isoformat(), "to": (START + timedelta(hours=1)).isoformat()},
    )
    assert (
        response.status_code == 503
        and response.data["code"] == "runtime_read_too_large"
    )
