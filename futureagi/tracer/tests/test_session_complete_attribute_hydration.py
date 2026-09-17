"""Page-scoped attribute hydration cannot silently discard physical roots."""

from datetime import timedelta
from uuid import UUID

import pytest

from tracer.tests import test_session_positive_witness_page as session_fixture
from tracer.tests.test_session_positive_witness_page import (
    PROJECT,
    START,
    builder,
)

pytestmark = pytest.mark.unit
engine = session_fixture.engine


@pytest.mark.parametrize("days", [7, 30, 365])
def test_all_session_root_attributes_beyond_old_500_row_cutoff(engine, days):
    engine.execute("ALTER TABLE spans ADD COLUMN attributes_extra String DEFAULT '{}'")
    session_id = str(UUID(int=100))
    engine.execute(
        """INSERT INTO spans (project_id, observation_type, service_name, start_time,
            trace_id, id, trace_session_id, attrs_string, attrs_number, attrs_bool, _version)
        SELECT toUUID(%(project)s), 'SPAN', '',
            fromUnixTimestamp64Micro(%(start_us)s, 'UTC'),
            concat('trace-', toString(number)), concat('root-', toString(number)),
            toUUID(%(session)s), map(concat('key_', toString(number)), 'value'),
            map('amount', toFloat64(number)), map('flag', toUInt8(modulo(number, 2))), 1
        FROM numbers(502)""",
        {"project": PROJECT, "session": session_id,
         "start_us": int(START.timestamp() * 1_000_000)},
    )
    # A newer tombstone removes one root; neither it nor unrelated sessions
    # may be hydrated. Remaining 501 roots must all contribute their keys.
    engine.insert(100, trace_id="trace-0", id="root-0", start_time=START,
                  _version=2, is_deleted=1, attrs_string={"deleted": "gone"})
    engine.insert(200, trace_id="other-trace", id="other-root",
                  attrs_string={"other_session": "excluded"})
    engine.insert(100, id="outside", start_time=START + timedelta(days=days),
                  attrs_string={"outside_window": "excluded"})
    subject = builder(days=days)
    rows = engine.execute(*subject.build_span_attributes_query([session_id]))
    assert len(rows) == 501
    assert {row["session_id"] for row in rows} == {session_id}
    assert {key for row in rows for key in row["attrs_string"]} == {
        f"key_{i}" for i in range(1, 502)
    }
    assert {row["attrs_number"]["amount"] for row in rows} == set(range(1, 502))
    assert {row["attrs_bool"]["flag"] for row in rows} == {0, 1}
