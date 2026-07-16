"""Scaffolding probe: `ch_query_settings(log_comment=…)` must reach
`system.query_log` on every CH client the eval readers construct."""

import pytest

from tracer.services.clickhouse.v2 import get_reader
from tracer.services.clickhouse.v2.query_settings import ch_query_settings

pytestmark = pytest.mark.stress


def test_log_comment_reaches_query_log(stress_dataset):
    tag = "stress:scaffold:probe"
    with ch_query_settings(log_comment=tag):
        with get_reader() as reader:
            reader.list_by_ids(["00000000-0000-0000-0000-000000000000"])
    from tests.stress.ch_asserts import _query_log_rows

    assert len(_query_log_rows(tag)) == 1
