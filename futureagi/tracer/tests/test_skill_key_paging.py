"""Physical nomination, bounded replay and the error contract shared by readers."""

from datetime import timedelta
from uuid import uuid4

import pytest

from tracer.services.clickhouse.query_builders import filters
from tracer.services.clickhouse.query_builders.filters import SkillEvidenceWindow
from tracer.services.clickhouse.v2.query_builders.filters import (
    ClickHouseFilterBuilderV2,
)
from tracer.tests import test_skill_filters as evidence

skill_query_client = evidence.skill_query_client


def _native_rows(native, sql, binds, **settings):
    rows, columns = native.execute(
        sql, binds, with_column_types=True, settings=settings
    )
    names = [name for name, _ in columns]
    return [dict(zip(names, row, strict=True)) for row in rows]


@pytest.mark.integration
def test_raw_pages_follow_physical_order_and_charge_each_logical_key_once(
    skill_query_client, monkeypatch
):
    project = str(uuid4())
    window = SkillEvidenceWindow.from_bounds(
        evidence.START, evidence.END + timedelta(hours=1)
    )
    compiler = ClickHouseFilterBuilderV2(project_id=project)
    # The first physical key sorts LAST by logical identity. Equal full keys
    # can straddle LIMIT 1, while later revisions move across ALL intervening
    # sort dimensions, including pages containing only an already-charged key.
    first = evidence.span_row(
        project, "page-order", id="ffffffffffffffff", observation_type="a"
    )
    last = evidence.span_row(
        project, "page-order", id="0000000000000000", observation_type="z"
    )
    moved = {**first, "resource_attributes": {"service.name": "writer's service"}}
    revisions = [
        first,
        first,
        moved,
        {**moved, "start_time": evidence.END},
        {**first, "observation_type": "b"},
    ]
    evidence.insert_rows(skill_query_client, [*revisions, last])
    # Two logical keys fit exactly. A repeated nomination must not overflow.
    monkeypatch.setattr(filters, "_SKILL_PAGED_KEY_BUDGET", 2, raising=False)

    settings = {}

    def read_keys(sql, params):
        assert "writer's service" not in sql
        return skill_query_client.query(
            sql, parameters=params, settings=settings
        ).named_results()

    pages = compiler.iter_skill_load_replays(
        read_keys, skill_window=window, batch_size=1
    )
    observed = [key[2] for _, binds in pages for key in binds["skill_replay_keys"]]
    assert observed == [first["id"], last["id"]]

    # Discovery must not require a window-wide DISTINCT even when its first
    # page is tiny. Apply a diagnostic DISTINCT limit, not a span-read ceiling.
    settings.update(max_rows_in_distinct=1, distinct_overflow_mode="throw")
    pages = compiler.iter_skill_load_replays(
        read_keys, skill_window=window, batch_size=1
    )
    assert [
        key[2] for _, binds in pages for key in binds["skill_replay_keys"]
    ] == observed

    # A third genuinely new key must fail closed, not return a complete prefix.
    evidence.insert_rows(
        skill_query_client, [evidence.span_row(project, "third", observation_type="zz")]
    )
    with pytest.raises(RuntimeError, match="Skill load replay key budget exceeded"):
        list(
            compiler.iter_skill_load_replays(
                read_keys, skill_window=window, batch_size=1
            )
        )


@pytest.mark.integration
@pytest.mark.parametrize("failed", [True, False])
@pytest.mark.parametrize("deleted", [True, False])
def test_only_possible_winners_are_charged_on_both_replay_paths(
    skill_query_client, monkeypatch, failed, deleted
):
    project = str(uuid4())
    window = SkillEvidenceWindow.from_bounds(evidence.START, evidence.END)
    compiler = ClickHouseFilterBuilderV2(project_id=project)
    catalogue = [(evidence.SKILL, evidence.VERSION, evidence.DIGEST)]
    binds = {"project_id": project, "catalogue": catalogue, **window.params}
    good = evidence.span_row(project, "admitted")
    if failed:
        good.update(status="ERROR", span_attributes={"fi.skill.name": "refund"})
    decoys = [
        evidence.span_row(project, f"unrelated-{i}", version=evidence.OTHER_VERSION)
        for i in range(3)
    ]
    # ERROR with a version, OK with an empty version, and a wrong digest are
    # not evidence for either the failed or the catalogue-verified selector.
    decoys.extend(
        [
            evidence.span_row(project, "error-with-version", status="ERROR"),
            evidence.span_row(project, "empty-version", version=""),
            evidence.span_row(project, "wrong-digest"),
        ]
    )
    decoys[-1]["span_attributes"]["fi.skill.digest"] = "unverified"
    # A tombstone can retain the entire matching outcome/catalogue. Unlike
    # this live witness, that physical row can never be an admissible winner.
    deleted_only = {**good, "id": uuid4().hex[:16], "deleted": True}
    evidence.insert_rows(skill_query_client, [good, *decoys, deleted_only])

    def rows(sql, params):
        return list(skill_query_client.query(sql, parameters=params).named_results())

    # Inspect the nomination before narrowing to a catalogue. Even a fully
    # matching tombstone cannot spend a slot; all paths must replay it if an
    # older live revision of the same logical key nominates it.
    nominated = {
        key[2]
        for _, page in compiler.iter_skill_load_replays(
            rows, skill_window=window, failed=failed
        )
        for key in page["skill_replay_keys"]
    }
    assert good["id"] in nominated and deleted_only["id"] not in nominated
    monkeypatch.setattr(filters, "SKILL_REPLAY_BATCH_SIZE", 1)
    selector = {} if failed else {"verified_identities_param": "catalogue"}
    sql = compiler.skill_live_rows_sql(skill_window=window, failed=failed, **selector)

    assert [row["span_id"] for row in rows(sql, binds)] == [good["id"]]

    def replay():
        return list(
            compiler.iter_skill_load_replays(
                rows,
                skill_window=window,
                failed=failed,
                batch_size=1,
                params=binds,
                **selector,
            )
        )

    ((winner_sql, winner_binds),) = replay()
    assert winner_binds["skill_replay_keys"] == [
        (project, good["trace_id"], good["id"])
    ]
    assert [row["span_id"] for row in rows(winner_sql, winner_binds)] == [good["id"]]

    if not failed:
        assert rows(sql, {**binds, "catalogue": []}) == []
    # Its older state still nominates this key. A correction under another
    # sorting key must win even though the correction cannot nominate itself.
    evidence.insert_rows(
        skill_query_client,
        [
            {
                **good,
                "created_at": evidence.END,
                "resource_attributes": {"service.name": "corrected"},
                "deleted": deleted,
                "status": good["status"] if deleted else "OK",
                "span_attributes": (good if deleted else decoys[-1])["span_attributes"],
            }
        ],
    )
    assert rows(sql, binds) == []
    ((winner_sql, winner_binds),) = replay()
    assert rows(winner_sql, winner_binds) == []


@pytest.mark.integration
@pytest.mark.parametrize(
    "late_match", [False, True], ids=["short-final-page", "full-final-page"]
)
def test_dense_prefix_pages_to_eof_in_storage_order(skill_query_client, late_match):
    from conftest import _ch_test_native_client

    project, trace = str(uuid4()), str(uuid4())
    window = SkillEvidenceWindow.from_bounds(evidence.START, evidence.END)
    compiler = ClickHouseFilterBuilderV2(project_id=project)
    # Four granules with the same primary-key prefix and sparse matches. ID 1
    # sorts last physically (different service), not second as in logical order.
    population, stride = 4 * 8192, 2048
    with _ch_test_native_client(database=skill_query_client.database) as native:
        native.execute(
            """INSERT INTO spans (project_id, trace_id, id, start_time, observation_type,
                                 service_name, name, status, attrs_string)
               SELECT toUUID(%(project)s), %(trace)s, leftPad(toString(number), 16, '0'),
                      fromUnixTimestamp64Micro(%(skill_window_start_us)s), 'llm',
                      if(number = 1, 'z', 'worker'),
                      if(modulo(number, %(stride)s) = 0 OR number IN (1, %(tail)s), 'skill.load', 'llm.chat'),
                      'OK', map('fi.skill.version_id', 'v1')
               FROM numbers(%(n)s)""",
            {
                **window.params,
                "project": project,
                "trace": trace,
                "stride": stride,
                "n": population,
                "tail": population - int(late_match),
            },
        )
        lengths = []

        def read(sql, binds):
            rows = _native_rows(native, sql, binds, use_skip_indexes=0, max_threads=1)
            lengths.append(len(rows))
            return rows

        keys = [
            key
            for _, binds in compiler.iter_skill_load_replays(
                read, skill_window=window, batch_size=3
            )
            for key in binds["skill_replay_keys"]
        ]
    expected = [
        *range(0, population, stride),
        *([population - 1] if late_match else []),
        1,
    ]
    assert keys == [(project, trace, f"{n:016d}") for n in expected]
    # Lookahead proves EOF even when the last page fills the replay batch.
    assert lengths == [4] * 5 + [3 if late_match else 2]


@pytest.mark.integration
def test_native_full_page_cannot_rebind_an_oversized_service(skill_query_client):
    from conftest import _ch_test_native_client

    project = str(uuid4())
    window = SkillEvidenceWindow.from_bounds(evidence.START, evidence.END)
    compiler = ClickHouseFilterBuilderV2(project_id=project)
    service = "'" * (256 * 1024 + 1)
    rows = [
        evidence.span_row(
            project,
            "cursor-width",
            id=kind * 16,
            observation_type=kind,
            resource_attributes={"service.name": service},
        )
        for kind in ("a", "b", "c")
    ]
    evidence.insert_rows(skill_query_client, rows)
    with _ch_test_native_client(database=skill_query_client.database) as native:
        calls = []

        def read(sql, binds):
            page = _native_rows(native, sql, binds, max_query_size=256 * 1024)
            calls.append(page)
            return page

        pages = compiler.iter_skill_load_replays(
            read, skill_window=window, batch_size=2
        )
        # The short logical IDs can still be replayed by an existence reader.
        assert [key[2] for key in next(pages)[1]["skill_replay_keys"]] == [
            r["id"] for r in rows[:2]
        ]
        with pytest.raises(RuntimeError, match="Skill load replay key budget exceeded"):
            next(pages)
        # The value was not even transferred, much less rebound into native SQL.
        assert (
            len(calls) == 1 and [row["service_name"] for row in calls[0]] == [None] * 3
        )


@pytest.mark.integration
def test_escaped_logical_keys_split_before_native_parser_limit(skill_query_client):
    from conftest import _ch_test_native_client

    project = str(uuid4())
    compiler = ClickHouseFilterBuilderV2(project_id=project)
    window = SkillEvidenceWindow.from_bounds(evidence.START, evidence.END)
    ids = {f"{i:04d}" + "'" * 1800 for i in range(96)}
    evidence.insert_rows(
        skill_query_client, [evidence.span_row(project, "wide-ids", id=i) for i in ids]
    )
    with _ch_test_native_client(database=skill_query_client.database) as native:

        def read(sql, params):
            return _native_rows(native, sql, params, max_query_size=256 * 1024)

        winners = [
            r["span_id"]
            for sql, params in compiler.iter_skill_load_replays(
                read, skill_window=window
            )
            for r in read(sql, params)
        ]
        assert len(winners) == len(ids) and set(winners) == ids
