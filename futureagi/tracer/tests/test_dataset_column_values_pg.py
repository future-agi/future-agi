"""The dataset-column value readers answer from PostgreSQL, the dataset's store.

Both readers used to read the ``model_hub_cell`` ClickHouse mirror. PeerDB
creates it ``ORDER BY id``, so every lookup read the whole table (dev,
2026-09-25: 14.3M rows / 19.5 GB, 22-24 s for a 12-cell column), and it trails
every write by a CDC batch: a value typed into a fresh dataset was missing from
the picker while the table's own exact filter, which reads PostgreSQL, found it
(B04). ``lagging_mirror`` stands in for that mirror: it is enabled and has not
received any of these cells.
"""

import json
import uuid
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

URL = "/tracer/dashboard/filter_values/"


@pytest.fixture
def lagging_mirror():
    empty = MagicMock()
    empty.return_value.execute_ch_query.return_value = MagicMock(data=[])
    with (
        patch("tracer.views.dashboard.is_clickhouse_enabled", return_value=True),
        patch("tracer.views.dashboard.AnalyticsQueryService", empty),
        patch(
            "tracer.services.clickhouse.client.is_clickhouse_enabled",
            return_value=True,
        ),
        patch("tracer.services.clickhouse.query_service.AnalyticsQueryService", empty),
    ):
        yield


@pytest.fixture
def dataset(organization, workspace):
    from model_hub.models.develop_dataset import Dataset

    return Dataset.objects.create(
        name="values", organization=organization, workspace=workspace
    )


def _column(dataset, *, data_type="text", source="OTHERS"):
    from model_hub.models.choices import StatusType
    from model_hub.models.develop_dataset import Column

    return Column.objects.create(
        id=uuid.uuid4(),
        name=f"column-{uuid.uuid4().hex[:6]}",
        data_type=data_type,
        source=source,
        status=StatusType.RUNNING.value,
        dataset=dataset,
    )


def _cell(column, value, **fields):
    from model_hub.models.develop_dataset import Cell, Row

    row = Row.objects.create(dataset=column.dataset, order=Row.objects.count())
    return Cell.objects.create(
        dataset=column.dataset, column=column, row=row, value=value, **fields
    )


def _suggestions(client, column, **params):
    response = client.get(
        URL,
        {
            "source": "dataset_column",
            "metric_name": str(column.id),
            "dataset_id": str(column.dataset_id),
            **params,
        },
    )
    assert response.status_code == 200, response.content
    return [option["value"] for option in response.json()["result"]["values"]]


@pytest.mark.django_db
def test_a_freshly_written_value_is_suggested_immediately(
    auth_client, dataset, lagging_mirror
):
    column = _column(dataset)
    _cell(column, "qa_value_alpha")

    assert _suggestions(auth_client, column) == ["qa_value_alpha"]


@pytest.mark.django_db
def test_an_edited_cell_suggests_only_its_current_value(
    auth_client, dataset, lagging_mirror
):
    column = _column(dataset)
    cell = _cell(column, "before-edit")
    cell.value = "after-edit"
    cell.save()

    assert _suggestions(auth_client, column) == ["after-edit"]


@pytest.mark.django_db
def test_deleted_and_empty_cells_are_not_suggested(
    auth_client, dataset, lagging_mirror
):
    column = _column(dataset)
    _cell(column, "kept")
    _cell(column, "")
    _cell(column, None)
    # Deleting a row soft-deletes its cells; the table no longer shows them.
    _cell(column, "deleted-row").delete()

    assert _suggestions(auth_client, column) == ["kept"]


@pytest.mark.django_db
def test_other_columns_and_datasets_are_not_suggested(
    auth_client, dataset, organization, workspace, lagging_mirror
):
    from model_hub.models.develop_dataset import Dataset

    column = _column(dataset)
    _cell(column, "mine")
    _cell(_column(dataset), "same-dataset-other-column")
    other = Dataset.objects.create(
        name="other", organization=organization, workspace=workspace
    )
    _cell(_column(other), "other-dataset")

    assert _suggestions(auth_client, column) == ["mine"]


@pytest.mark.django_db
def test_search_narrows_case_insensitively(auth_client, dataset, lagging_mirror):
    column = _column(dataset)
    for value in ("English", "Spanish", "French"):
        _cell(column, value)

    assert _suggestions(auth_client, column, search="ISH") == ["English", "Spanish"]
    assert _suggestions(auth_client, column, search="ish", page_size=50) == [
        "English",
        "Spanish",
    ]


@pytest.mark.django_db
def test_evaluation_choices_decode_labels_and_same_cell_literals(
    auth_client, dataset, lagging_mirror
):
    column = _column(dataset, data_type="array", source="evaluation")
    _cell(column, "['neutral']")
    _cell(column, "{'choice': 'positive', 'score': 0.9}")
    literal = {"output": "choices", "data": {"result": "[west]"}}
    _cell(column, "[west]", value_infos=literal)
    # Historical cells store the metadata JSON once more as a JSON string.
    _cell(
        column,
        "[east]",
        value_infos=json.dumps({"output": "choices", "data": {"choice": "[east]"}}),
    )
    # Metadata about another value never makes this storage a literal label.
    _cell(column, "['north']", value_infos={"output": "choices", "data": "[west]"})

    assert _suggestions(auth_client, column) == [
        "[east]",
        "[west]",
        "neutral",
        "north",
        "positive",
    ]
    assert _suggestions(auth_client, column, search="WES") == ["[west]"]


@pytest.mark.django_db
def test_ai_filter_grounding_reads_the_current_column(dataset, lagging_mirror):
    from model_hub.views import ai_filter

    column = _column(dataset)
    _cell(column, "qa_value_alpha")
    _cell(column, "qa_value_beta").delete()
    _cell(_column(dataset), "qa_value_other_column")

    assert ai_filter._fetch_dataset_column_values(
        dataset.id, column.id, search_query="QA_VALUE"
    ) == ["qa_value_alpha"]


@pytest.mark.django_db
def test_eval_cells_whose_metadata_cannot_name_them_ship_no_metadata(
    auth_client, dataset, lagging_mirror
):
    """Only bracketed storage that its own metadata quotes can be a literal.

    Reading every cell's ``value_infos`` to decide that bit made a large eval
    column cross the result-byte cap and answer 503 (about 55k cells at dev's
    ~1.2 KB of metadata per cell).
    """
    from tracer.services import dataset_filter_values

    column = _column(dataset, data_type="array", source="evaluation")
    reason = "an explanation " * 64
    for _ in range(40):
        _cell(
            column,
            "positive",
            value_infos={"output": "choices", "data": "positive", "reason": reason},
        )
        # Historical double-encoded metadata whose choice is a list, not a
        # string naming the stored text.
        _cell(
            column,
            "['neutral']",
            value_infos=json.dumps(
                {"output": "choices", "data": ["neutral"], "reason": reason}
            ),
        )
    _cell(column, "[west]", value_infos={"output": "choices", "data": "[west]"})

    decoder = dataset_filter_values.literal_choice
    with (
        patch(
            "tracer.views.dashboard._FINITE_NATIVE_FILTER_VALUE_MAX_RESULT_BYTES",
            16 * 1024,
        ),
        patch.object(dataset_filter_values, "literal_choice", wraps=decoder) as decoded,
    ):
        assert _suggestions(auth_client, column) == ["[west]", "neutral", "positive"]
    assert [call.args[0] for call in decoded.call_args_list] == ["[west]"]


# The producer's json.dumps escaped every non-ASCII character of a reason, so
# each of these historical cells carried \u00XX escapes in its metadata.
_REASON = "L\u2019\u00e9motion dominante est claire. " * 32


def _choice_metadata(labels, *, encoded=True, **fields):
    metadata = {"output": "choices", "data": labels, "reason": _REASON, **fields}
    return json.dumps(metadata) if encoded else metadata


@pytest.mark.django_db
def test_container_cells_ship_no_metadata_however_it_was_encoded(
    auth_client, dataset, lagging_mirror
):
    """Metadata ships only for cells it can name as literals, at any size.

    Every cell of dev column 94f8a212 (``['anger', 'annoyance']``, ~3.1 KB of
    metadata each) shipped its metadata to Python, and so would JSON-list
    storage and any label with a slash, quote or accent. Past the byte budget
    (64 MiB, ~21k such cells) the picker answered 503 on every open.
    """
    from tracer.services import dataset_filter_values

    column = _column(dataset, data_type="array", source="evaluation")
    shapes = {
        "['anger', 'annoyance']": ["anger", "annoyance"],
        "['N/A']": ["N/A"],
        "['n\u00e9gatif']": ["n\u00e9gatif"],
        repr(["Doesn't answer"]): ["Doesn't answer"],
        '["Yes"]': ["Yes"],
    }
    for _ in range(12):
        for stored, labels in shapes.items():
            _cell(column, stored, value_infos=_choice_metadata(labels))
            _cell(column, stored, value_infos=_choice_metadata(labels, encoded=False))
    _cell(column, "[west]", value_infos={"output": "choices", "data": "[west]"})

    decoder = dataset_filter_values.literal_choice
    with (
        patch(
            "tracer.views.dashboard._FINITE_NATIVE_FILTER_VALUE_MAX_RESULT_BYTES",
            16 * 1024,
        ),
        patch.object(dataset_filter_values, "literal_choice", wraps=decoder) as decoded,
    ):
        assert _suggestions(auth_client, column) == [
            "[west]",
            "anger",
            "annoyance",
            "Doesn't answer",
            "N/A",
            "n\u00e9gatif",
            "Yes",
        ]
    assert [call.args[0] for call in decoded.call_args_list] == ["[west]"]


def _labels_deciding_every_cell(column):
    """The picker's answer with every live cell's literal bit decided in Python.

    This is what the reader returned when it shipped each candidate's metadata
    to ``literal_choice``: only bracketed storage reads differently as a
    literal, and a single undecodable storage text refuses the whole column.
    """
    from django.db import connection

    from tracer.services.dataset_choice_values import (
        InvalidChoiceCell,
        evaluation_choice_labels,
        literal_choice,
    )

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT value, value_infos::text FROM model_hub_cell "
            "WHERE column_id = %s AND deleted = false AND value <> ''",
            [column.id],
        )
        cells = cursor.fetchall()
    modes = {}
    for value, infos in cells:
        literal = ("[" in value or "{" in value) and literal_choice(value, infos)
        modes[value] = modes.get(value, 0) | (2 if literal else 1)
    labels = set()
    try:
        for value, mode in modes.items():
            if mode & 1:
                labels.update(evaluation_choice_labels(value))
            if mode & 2:
                labels.update(evaluation_choice_labels(value, literal=True))
    except InvalidChoiceCell:
        return None
    return labels


@pytest.mark.django_db
def test_literal_bits_equal_deciding_every_cell_in_python(
    auth_client, dataset, lagging_mirror
):
    column = _column(dataset, data_type="array", source="evaluation")
    containers = {
        "['anger', 'annoyance']": ["anger", "annoyance"],
        "['n\u00e9gatif', '\u96ea']": ["n\u00e9gatif", "\u96ea"],
        repr(["Doesn't answer", "O'Reilly"]): ["Doesn't answer", "O'Reilly"],
        '["Yes", "No"]': ["Yes", "No"],
        "['N/A']": ["N/A"],
        "{'choice': 'positive', 'score': 0.9}": {"result": "positive"},
        '{"choices": ["up", "down"], "score": 0.5}': {"choice": ["up", "down"]},
    }
    for stored, data in containers.items():
        for encoded in (True, False):
            _cell(column, stored, value_infos=_choice_metadata(data, encoded=encoded))
    literals = [
        ("[west]", {"output": "choices", "data": {"result": "[west]"}}),
        ("[east]", json.dumps({"output": "choices", "data": {"choice": "[east]"}})),
        ("{north}", {"output": "choices", "data": "{north}", "reason": _REASON}),
        ("[s\u00fcd]", json.dumps({"output": "choices", "data": "[s\u00fcd]"})),
        (
            "[s\u00fcd-ost]",
            json.dumps(
                {"output": "choices", "data": "[s\u00fcd-ost]"}, ensure_ascii=False
            ),
        ),
        # Legal JSON escapes no producer here writes: \/, an escaped printable
        # character in upper-case hex, and one accent escaped beside a literal.
        ("[a/b]", '{"output": "choices", "data": "[a\\/b]"}'),
        ("[up]", '{"output": "choices", "data": "\\u005Bup]"}'),
        ("[\u00e9t\u00e9]", '{"output": "choices", "data": "[\\u00e9t\u00e9]"}'),
    ]
    for stored, infos in literals:
        _cell(column, stored, value_infos=infos)
    # One storage text, a literal in one cell and a list in another.
    _cell(column, '["both"]', value_infos={"output": "choices", "data": '["both"]'})
    _cell(column, '["both"]', value_infos=_choice_metadata(["both"]))
    # Metadata naming another value, or repeating a key, names no literal.
    _cell(column, "['north']", value_infos={"output": "choices", "data": "[west]"})
    _cell(
        column,
        '["dup"]',
        value_infos='{"output": "choices", "data": "[\\"dup\\"]", "data": "[\\"dup\\"]"}',
    )
    # A deleted cell is neither a literal nor a container of its storage text.
    _cell(
        column, '["gone"]', value_infos={"output": "choices", "data": '["gone"]'}
    ).delete()
    _cell(column, '["kept"]', value_infos=_choice_metadata(["kept"]))
    deleted_literal = _cell(
        column, '["kept"]', value_infos={"output": "choices", "data": '["kept"]'}
    )
    deleted_literal.deleted = True
    deleted_literal.save()

    expected = _labels_deciding_every_cell(column)
    assert '["both"]' in expected and '["kept"]' not in expected
    values = _suggestions(auth_client, column)
    assert len(values) == len(expected)
    assert set(values) == expected


@contextmanager
def _defective_value_sql():
    """Make the readers' own SQL name a missing table, as a defect in it would."""
    from django.db import connection

    def break_value_reads(execute, sql, params, many, context):
        return execute(
            sql.replace("FROM model_hub_", "FROM missing_model_hub_"),
            params,
            many,
            context,
        )

    with connection.execute_wrapper(break_value_reads):
        yield


@pytest.mark.django_db
@pytest.mark.parametrize("source", ["OTHERS", "evaluation"])
def test_a_defect_in_the_value_read_is_a_logged_server_error(
    auth_client, dataset, lagging_mirror, source
):
    """A retry cannot fix a broken statement, so it must reach Sentry."""
    column = _column(dataset, data_type="array", source=source)
    _cell(column, "['west']")

    with (
        _defective_value_sql(),
        patch("tracer.views.dashboard.logger") as logger,
    ):
        response = auth_client.get(
            URL,
            {
                "source": "dataset_column",
                "metric_name": str(column.id),
                "dataset_id": str(column.dataset_id),
            },
        )

    assert response.status_code == 500, response.content
    assert response.json()["code"] == "server_error"
    logger.exception.assert_called_once()
    assert logger.exception.call_args.args == (
        "dataset_column_filter_values_query_failed",
    )
    logger.warning.assert_not_called()


@pytest.mark.django_db
def test_a_statement_timeout_stays_a_retryable_503(
    auth_client, dataset, lagging_mirror
):
    from django.db import connection

    column = _column(dataset, data_type="array", source="evaluation")
    _cell(column, "['west']")

    def outlast_the_wall(execute, sql, params, many, context):
        if "FROM model_hub_cell" in sql:
            sql = f"SELECT slow.* FROM ({sql}) AS slow, pg_sleep(5)"
        return execute(sql, params, many, context)

    # The read gives each statement the remaining wall as its timeout.
    with (
        patch("tracer.views.dashboard._FILTER_VALUES_INTERACTIVE_TIMEOUT_MS", 300),
        connection.execute_wrapper(outlast_the_wall),
        patch("tracer.views.dashboard.logger") as logger,
    ):
        response = auth_client.get(
            URL,
            {
                "source": "dataset_column",
                "metric_name": str(column.id),
                "dataset_id": str(column.dataset_id),
            },
        )

    assert response.status_code == 503, response.content
    assert response.json()["code"] == "service_unavailable"
    assert logger.warning.call_args.kwargs["error_type"] == "OperationalError"
    logger.exception.assert_not_called()


@pytest.mark.django_db
def test_ai_filter_grounding_logs_a_defect_in_its_value_read(dataset, lagging_mirror):
    from model_hub.views import ai_filter

    column = _column(dataset)
    _cell(column, "qa_value_alpha")

    with (
        _defective_value_sql(),
        patch.object(ai_filter, "logger") as logger,
        pytest.raises(ai_filter.SmartFilterGroundingError) as refused,
    ):
        ai_filter._fetch_dataset_column_values(
            dataset.id, column.id, search_query="qa_value"
        )

    assert refused.value.code == "ai_filter_grounding_unavailable"
    logger.exception.assert_called_once()
    assert logger.exception.call_args.args == ("dataset_column_values_query_failed",)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("data_type", "source", "stored"),
    [
        # Three distinct stored values: the read itself is over the cap.
        ("text", "OTHERS", ["a", "b", "c"]),
        # One stored value that decodes to three labels.
        ("array", "evaluation", ["['a', 'b', 'c']"]),
    ],
)
def test_an_inventory_over_the_cap_answers_the_registered_message(
    auth_client, dataset, lagging_mirror, data_type, source, stored
):
    from tfc.utils.error_codes import get_error_message

    column = _column(dataset, data_type=data_type, source=source)
    for value in stored:
        _cell(column, value)

    with patch("tracer.views.dashboard._LEGACY_NATIVE_FILTER_VALUE_MAX", 2):
        response = auth_client.get(
            URL,
            {
                "source": "dataset_column",
                "metric_name": str(column.id),
                "dataset_id": str(column.dataset_id),
            },
        )

    assert response.status_code == 422, response.content
    assert response.json()["code"] == "filter_value_inventory_too_broad"
    assert response.json()["message"] == get_error_message(
        "FILTER_VALUE_INVENTORY_TOO_BROAD"
    )
