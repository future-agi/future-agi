"""A text in/not_in member made of comma-joined UUIDs is a list of ids.

Production eval task ``fab16b53`` stored ``whatfix.ent_id not_in`` with one
member ``"<id>, <id>, <id>, <id>"`` typed into a free-text value box. Both the
Observe list and the task reconcile bound it as ONE literal, so the filter
excluded nothing and 3,785 of the task's 3,994 evaluated traces carried an
excluded id. Members that legitimately contain commas (a picked system prompt,
free text such as ``"Paris, France"``) must stay one exact value.
"""

import pytest
from rest_framework import serializers

from tracer.models.eval_task import RowType
from tracer.selectors.eval_tasks import row_resolver
from tracer.serializers.filters import FILTER_LIST_MAX_VALUES, FilterItemField
from tracer.services.clickhouse.query_builders.filters import ClickHouseFilterBuilder
from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    compile_trace_filter_plans,
)

pytestmark = pytest.mark.unit

IDS = [
    "3f1c1d7a-8a57-4c1e-9d0b-1f0a2b3c4d5e",
    "7b2e4f60-1c3d-4e5f-8a9b-0c1d2e3f4a5b",
    "9c8d7e6f-5a4b-4c3d-9e1f-0a1b2c3d4e5f",
    "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d",
]
PASTED_IDS = ", ".join(IDS)


def _leaf(value, *, op="not_in", types=None, column="whatfix.ent_id"):
    config = {
        "col_type": "SPAN_ATTRIBUTE",
        "filter_type": "text",
        "filter_op": op,
        "filter_value": value,
    }
    if types is not None:
        config["attribute_value_types"] = types
    return {"column_id": column, "filter_config": config}


def _stored_task_values(item):
    ui_filters = row_resolver._task_ui_filters(
        {"filters": [item]}, row_type=RowType.TRACES
    )
    plan = compile_trace_filter_plans(ui_filters)[0]
    return [
        value
        for name, value in plan.params.items()
        if name.startswith("latest_filter_param_")
    ]


def _request_values(item):
    return FilterItemField().run_validation(item)["filter_config"]["filter_value"]


@pytest.mark.parametrize("op", ["in", "not_in"])
def test_stored_task_filter_binds_each_pasted_id(op):
    assert _stored_task_values(_leaf([PASTED_IDS], op=op)) == [tuple(IDS)]


@pytest.mark.parametrize("op", ["in", "not_in"])
def test_request_filter_validates_each_pasted_id(op):
    assert _request_values(_leaf([PASTED_IDS], op=op)) == IDS


def test_list_and_task_bind_the_same_ids():
    """The Observe list (validated request) and the task agree on one shape."""
    stored = _leaf([PASTED_IDS])
    request = FilterItemField().run_validation(_leaf([PASTED_IDS]))
    _, list_params = ClickHouseFilterBuilder().translate([request])

    assert tuple(IDS) in list_params.values()
    assert _stored_task_values(stored) == [tuple(IDS)]


def test_pasted_ids_keep_other_members_and_drop_empty_parts():
    value = [f"{IDS[0]},{IDS[1]},", IDS[2]]
    assert _request_values(_leaf(value)) == IDS[:3]


@pytest.mark.parametrize(
    ("value", "types"),
    [
        (["Paris, France"], None),
        ([f"{IDS[0]}, not-an-id"], None),
        ([PASTED_IDS], ["string"]),
    ],
    ids=["free-text", "mixed-parts", "picker-provenance"],
)
def test_members_with_commas_otherwise_stay_one_exact_value(value, types):
    item = _leaf(value, types=types)
    folded = tuple(member.lower() for member in value)

    assert _request_values(item) == value
    assert _stored_task_values(item) == [folded]


def test_split_ids_keep_the_list_value_bound():
    ids = [
        f"00000000-0000-4000-8000-{index:012d}"
        for index in range(FILTER_LIST_MAX_VALUES + 1)
    ]

    assert _request_values(_leaf([", ".join(ids[:-1])])) == ids[:-1]
    with pytest.raises(
        serializers.ValidationError, match=f"at most {FILTER_LIST_MAX_VALUES} values"
    ):
        _request_values(_leaf([", ".join(ids)]))
