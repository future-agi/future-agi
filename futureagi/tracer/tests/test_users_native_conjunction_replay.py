"""Native typed hydration plus real Users AND matching; SELECT fixtures only."""

from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tracer.services.users_list_manager import UsersListManager
from tracer.tests.test_user_attribute_native_key_collision import wire
from tracer.tests.test_users_attribute_physical_replay import (
    PROJECT,
    REMAP,
    START,
    USER,
    execute,
    span,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("days", [7, 30, 365])
@pytest.mark.parametrize("width", [2, 5, 10])
def test_native_typed_conjunction_requires_every_latest_property(days, width):
    filters, physical_rows = [], []
    for index in range(width):
        key = f"compound_{index}"
        kind, value, storage = (
            ("text", "Alpha", "attrs_string"),
            ("number", 7, "attrs_number"),
            ("boolean", True, "attrs_bool"),
        )[index % 3]
        filters.append(wire(key, value, kind=kind))
        # Properties on different spans of the same canonical user must AND
        # together at user grain, not require a single span to contain them all.
        physical_rows.append(span(f"span-{index}", **{storage: {key: value}}))

    for cleared_index in [None, *range(width)]:
        rows = deepcopy(physical_rows)
        if cleared_index is not None:
            # The old matching version remains physically present. The newest
            # version clears only this leaf; every other leaf still matches.
            cleared = deepcopy(rows[cleared_index])
            cleared.update(_version=2, attrs_string={}, attrs_number={}, attrs_bool={})
            rows.append(cleared)
        manager = UsersListManager(
            organization_id=PROJECT,
            allowed_project_ids=[PROJECT],
            project_id=PROJECT,
            requested_columns=[],
            filters=deepcopy(filters),
        )
        candidate = {"end_user_id": USER, "user_id": "fixture-user"}
        batches = []

        def read(sql, params, *, _rows=rows, _batches=batches, **_kwargs):
            _batches.append(params["requested_attribute_keys"])
            return SimpleNamespace(
                data=execute(
                    _rows,
                    params["requested_attribute_keys"],
                    days=days,
                    compiled=(sql, params),
                )
            )

        with patch(
            "tracer.services.users_list_manager.V2AnalyticsQueryService"
        ) as service:
            service.return_value.execute_ch_query.side_effect = read
            attrs = manager._read_span_attributes(
                [candidate],
                None,
                start_date=START,
                end_date=START + timedelta(days=days),
                candidate_scan_ids=list(REMAP),
                candidate_end_user_id_map=REMAP,
            )
        manager._apply_span_attributes([candidate], attrs)
        assert manager._row_matches_filters(candidate) is (cleared_index is None)
        assert {key for batch in batches for key in batch} == {
            f"compound_{i}" for i in range(width)
        }
        assert max(map(len, batches)) <= 4
