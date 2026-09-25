"""Offline reader contracts: execute the real method, never import Django.

ORM/PostgreSQL boundaries are recording doubles; this does not attest PG filter
membership (test_dataset_column_values_pg.py does). Run with -c /dev/null --noconftest, explicit root, network denied.
"""

import ast
import json
import os
import socket
import subprocess
import sys
import uuid
from hashlib import blake2b
from importlib.util import find_spec
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
DATASET = "11111111-1111-4111-8111-111111111111"
COLUMN = "22222222-2222-4222-8222-222222222222"
SHAPES = [
    ("catalog-west", ["catalog-west"]),
    ("['catalog-west', 'catalog-east']", ["catalog-east", "catalog-west"]),
    ("{'score': 0.2, 'choice': 'catalog-west'}", ["catalog-west"]),
    (
        "{'score': 0.5, 'choices': ['catalog-west', 'catalog-east']}",
        ["catalog-east", "catalog-west"],
    ),
    # Nested evaluator data.result / data.choice both produce these scored cells.
    ("{'score': 0.2, 'choice': 'catalog-west'}", ["catalog-west"]),
    ("{'score': 0.8, 'choice': 'catalog-east'}", ["catalog-east"]),
    ('["catalog-west","catalog-east"]', ["catalog-east", "catalog-west"]),
    ('{"score":0.2,"choice":"catalog-west"}', ["catalog-west"]),
    (
        '{"score":0.5,"choices":["catalog-west","catalog-east"]}',
        ["catalog-east", "catalog-west"],
    ),
    ('"catalog-west"', ['"catalog-west"']),
    ("""{'score': 0.4, 'choice': "O'Reilly"}""", ["O'Reilly"]),
]


# The one bounded pre-filter an eval-choice read may apply. Kept whole on
# purpose: each arm is what makes it a provable superset of the decoded match
# (see test_choice_prefilter_is_a_superset_of_the_decoded_match).
CHOICE_SEARCH_SUPERSET = (
    'AND (strpos(lower(value COLLATE "C"), '
    'lower(%(choice_search)s COLLATE "C")) > 0 '
    "OR strpos(value, chr(92)) > 0 "
    "OR octet_length(value) <> char_length(value)) "
)
GENERIC_SEARCH = "strpos(lower(value), lower(%(search)s)) > 0"


class DeadlineExceeded(Exception):
    pass


def literal_match_reference(value, infos):
    """Independent fixture oracle, not an execution/emulation of CH JSON SQL.

    Live constant-row SQL qualification is separate. This oracle supplies the
    aggregate protocol while the real reader's SQL/grouping/limits are asserted.
    """

    def unique_object(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("duplicate metadata key")
            result[key] = item
        return result

    try:
        for _ in range(2):
            if not isinstance(infos, str):
                break
            if len(infos) > 16384:
                return False
            infos = json.loads(infos, object_pairs_hook=unique_object)
        if not isinstance(infos, dict) or infos.get("output") != "choices":
            return False
        result = infos.get("data")
        if isinstance(result, dict):
            keys = set(result) & {"result", "choice"}
            if len(keys) != 1:
                return False
            result = result[next(iter(keys))]
        return isinstance(result, str) and result == value
    except (ValueError, RecursionError):
        return False


LITERAL_METADATA_CASES = [
    ("[west]", {"output": "choices", "data": {"result": "[west]"}}),
    ("[west]", {"output": "choices", "data": {"choice": "[west]"}}),
    ("[west]", {"output": "choices", "data": "[west]"}),
    ("[west]", {"output": "choices", "data": {"result": "[west]", "choice": "x"}}),
    ("[west]", {"output": "choices", "data": {"result": ["[west]"]}}),
    ("[west]", {"output": "choices", "data": {"result": "wrong"}}),
    ("[west]", {"output": "score", "data": {"result": "[west]"}}),
    ("[west]", {"data": {"result": "[west]"}}),
    ("[west]", None),
    ("[west]", "broken"),
    ("[west]", '{"output":"choices","data":{"result":"[west]","result":"x"}}'),
    ("[west]", '{"output":"choices","output":"choices","data":"[west]"}'),
    ("[west]", '{"output":"choices","data":"[west]","score":NaN}'),
    ('He said "雪"', {"output": "choices", "data": {"result": 'He said "雪"'}}),
]


@pytest.mark.parametrize("value,metadata", LITERAL_METADATA_CASES)
@pytest.mark.parametrize("encodings", [0, 1])
def test_literal_choice_agrees_with_the_fixture_oracle(value, metadata, encodings):
    """The reader's decoder and this file's independent oracle cannot drift.

    ``value_infos::text`` is the stored JSON; a historical cell stores that
    JSON once more as a JSON string, which both unwrap exactly once.
    """

    from tracer.services.dataset_choice_values import literal_choice

    text = json.dumps(metadata) if isinstance(metadata, dict) else metadata or ""
    if encodings:
        text = json.dumps(text)
    try:
        expected = literal_match_reference(value, text)
    except ValueError:
        expected = False
    if "NaN" in text:
        expected = False  # ClickHouse's isValidJSON and PostgreSQL both reject it
    assert literal_choice(value, text) is expected


@pytest.fixture(scope="session")
def dashboard_source():
    # Optional genuine old-source red: no checkout/revert or application import.
    if os.environ.get("CATALOG_CHOICE_TEST_BASELINE") == "HEAD":
        return subprocess.check_output(
            ["git", "show", "HEAD:futureagi/tracer/views/dashboard.py"],
            cwd=ROOT.parent,
            text=True,
        )
    return (ROOT / "tracer/views/dashboard.py").read_text()


@pytest.fixture(autouse=True)
def deny_sockets(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("offline socket guard")

    monkeypatch.setattr(socket, "socket", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


@pytest.fixture
def reader(dashboard_source):
    source = ast.parse(dashboard_source)
    method = next(
        node
        for node in ast.walk(source)
        if isinstance(node, ast.FunctionDef)
        and node.name == "_filter_values_dataset_column"
    )
    postgres = Mock()
    state = {"oversized_statement": None, "interpretations": True}
    column = NS(data_type="array", source="evaluation")
    manager = Mock()
    manager.select_related.return_value.get.return_value = column
    model = ModuleType("model_hub.models.develop_dataset")
    model.Column = NS(objects=manager, DoesNotExist=type("Missing", (Exception,), {}))
    deadline = Mock()
    deadline.remaining_ms.return_value = 1000
    request = NS(workspace=NS(organization_id="owned-org"))
    view = NS(
        _gm=NS(
            success_response=lambda data: {"status": 200, **data},
            bad_request=lambda message: {"status": 400},
            custom_error_response=lambda status, message, code: {
                "status": status,
                "code": code,
            },
        ),
        _finite_native_filter_values_response=Mock(
            side_effect=lambda request, **kw: {"status": 200, **kw}
        ),
    )
    scope = {
        "_run_filter_value_pg_read": lambda deadline, fn: fn(),
        "_run_filter_value_pg_statements": lambda deadline, read: read(postgres),
        "ReadDeadlineExceeded": DeadlineExceeded,
        "DatabaseError": type("DatabaseError", (Exception,), {}),
        "DashboardBoundedReadError": type("BoundedReadError", (Exception,), {}),
        "_FINITE_NATIVE_FILTER_VALUE_MAX": 5000,
        "_LEGACY_NATIVE_FILTER_VALUE_MAX": 1000,
        "_FINITE_NATIVE_FILTER_VALUE_MAX_RESULT_BYTES": 1048576,
        "_FILTER_VALUES_INTERACTIVE_TIMEOUT_MS": 1000,
        "status": NS(
            HTTP_503_SERVICE_UNAVAILABLE=503,
            HTTP_500_INTERNAL_SERVER_ERROR=500,
            HTTP_422_UNPROCESSABLE_ENTITY=422,
        ),
        "logger": Mock(),
    }
    search_method = next(
        node
        for node in source.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_filter_value_options_for_search"
    )
    exec(
        compile(
            ast.Module(body=[search_method, method], type_ignores=[]),
            "dashboard.py",
            "exec",
        ),
        scope,
    )

    def invoke(raw, **params):
        # One stored cell per record: its text and its JSONField metadata as
        # PostgreSQL renders ``value_infos::text``. ``literal`` stands for
        # producer metadata naming the raw value itself as the choice.
        cells = []
        for record in raw:
            record = record if isinstance(record, dict) else {"val": record}
            infos = record.get("choice_value_infos")
            if record.get("literal"):
                infos = {"output": "choices", "data": {"result": record["val"]}}
            cells.append(
                (
                    record["val"],
                    json.dumps(infos) if isinstance(infos, dict) else infos or "",
                )
            )

        def fetch(sql, params):
            if "value_infos" not in sql:
                distinct = dict.fromkeys(value for value, _infos in cells)
                rows = [{"val": value, "result_bytes": 0} for value in distinct]
            else:
                rows = [
                    {"id": index, "val": value, "value_infos": infos}
                    for index, (value, infos) in enumerate(cells)
                    if state["interpretations"]
                ]
                for row in rows:
                    row["result_bytes"] = 0
            if rows and state["oversized_statement"] == postgres.call_count - 1:
                rows[-1]["result_bytes"] = params["max_result_bytes"] + 1
            return rows

        postgres.reset_mock()
        postgres.side_effect = fetch
        with patch.dict(sys.modules, {"model_hub.models.develop_dataset": model}):
            return scope[method.name](
                view,
                request,
                DATASET,
                COLUMN,
                query_params={"page_size": 50, "search": "", **params},
                deadline=deadline,
            )

    return NS(
        invoke=invoke,
        column=column,
        postgres=postgres,
        state=state,
        manager=manager,
        deadline=deadline,
        request=request,
        view=view,
        scope=scope,
    )


@pytest.mark.parametrize("stored,expected", SHAPES)
def test_original_producer_shapes(reader, stored, expected):
    response = reader.invoke([stored])
    assert response["status"] == 200
    assert response["values"] == [{"value": v, "label": v} for v in expected]


@pytest.fixture
def digesting_reader(reader, dashboard_source):
    """Use the actual finite-response and digest path, with no cursor services."""
    tree = ast.parse(dashboard_source)
    nodes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name
        in (
            "_filter_value_digest",
            "_filter_value_content_digest",
            "_finite_native_filter_values_response",
        )
    ]
    reader.scope.update(
        {
            "json": json,
            "blake2b": blake2b,
            "_finite_filter_value_cursor_page": Mock(
                side_effect=lambda request, **kw: kw
            ),
            "ListCursorError": type("CursorError", (Exception,), {}),
        }
    )
    exec(
        compile(ast.Module(body=nodes, type_ignores=[]), "dashboard.py", "exec"),
        reader.scope,
    )
    reader.view._finite_native_filter_values_response = Mock(
        side_effect=lambda request, **kw: reader.scope[
            "_finite_native_filter_values_response"
        ](reader.view, request, **kw)
    )
    return reader


@pytest.mark.parametrize(
    "stored",
    [
        r'["\ud800"]',
        r'["\udc00"]',
        r"['\ud800']",
        r"['\udc00']",
        r"['\ud83d\ude00']",
        "\ud800",
        "\udc00",
    ],
)
def test_malformed_surrogates_fail_before_real_digest(digesting_reader, stored):
    from tracer.services.dataset_choice_values import (
        InvalidChoiceCell,
        evaluation_choice_labels,
    )

    with pytest.raises(InvalidChoiceCell):
        evaluation_choice_labels(stored)
    assert digesting_reader.invoke([stored]) == {
        "status": 503,
        "code": "service_unavailable",
    }
    digesting_reader.view._finite_native_filter_values_response.assert_not_called()


@pytest.mark.parametrize(
    "stored,expected",
    [
        (r'["\ud83d\ude00"]', ["😀"]),
        (r"['\U0001f600']", ["😀"]),
        ("😀", ["😀"]),
        (r"['\\ud800']", [r"\ud800"]),
        (r"{'choice':'\ufeff','score':0.4}", ["\ufeff"]),
        (r"{'choices':['west','\ufeff'],'score':0.3}", ["west", "\ufeff"]),
    ],
)
def test_valid_unicode_survives_real_reader_digest(digesting_reader, stored, expected):
    response = digesting_reader.invoke([stored])
    assert response["values"] == [{"value": v, "label": v} for v in expected]
    identity = response["content_identity"]
    assert identity["count"] == len(expected)
    assert identity["digest"] == digesting_reader.scope["_filter_value_content_digest"](
        response["values"]
    )


def test_literal_metadata_does_not_authorize_surrogate_scalars(digesting_reader):
    from tracer.services.dataset_choice_values import (
        InvalidChoiceCell,
        evaluation_choice_labels,
    )

    with pytest.raises(InvalidChoiceCell):
        evaluation_choice_labels("\ud800", literal=True)
    assert (
        digesting_reader.invoke([{"val": "\ud800", "literal": True}])["status"] == 503
    )


@pytest.mark.parametrize("character", ["\u0085", "\u001c", "\u001f"])
def test_python_blank_c1_labels_are_rejected_not_suggested(digesting_reader, character):
    assert digesting_reader.invoke([repr(["west", character])])["status"] == 503


@pytest.mark.parametrize(
    "origin", ["others", "evaluation_tags", "evaluation_reason", "run_prompt"]
)
@pytest.mark.parametrize(
    "stored,expected",
    [
        ('{"region":"catalog-west","count":2}', ["2", "catalog-west"]),
        ("[1,2,true]", ["1", "2", "True"]),
        ('[{"choice":"west","score":0.2}]', ["0.2", "west"]),
        ("['west']", ["['west']"]),
        ('{"nested":["west"]}', ['{"nested":["west"]}']),
    ],
)
def test_generic_array_semantics_unchanged(reader, origin, stored, expected):
    reader.column.source = origin
    assert [v["value"] for v in reader.invoke([stored])["values"]] == expected


@pytest.mark.parametrize(
    "origin", ["evaluation", "experiment_evaluation", "optimisation_evaluation"]
)
def test_only_choice_array_columns_use_the_decoder(reader, origin):
    reader.column.source = origin
    assert reader.invoke(["{'choice': 'west', 'score': 0.2}"])["values"] == [
        {"value": "west", "label": "west"}
    ]
    for data_type in ("json", "float", "text", "boolean"):
        reader.column.data_type = data_type
        stored = '{"choice":"west","score":0.2}'
        expected = ["0.2", "west"] if data_type == "json" else [stored]
        assert [v["value"] for v in reader.invoke([stored])["values"]] == expected


@pytest.mark.parametrize(
    "stored,expected",
    [
        (
            r"{'choice': 'O\'Reilly says \"雪\" \\ path', 'score': 0.4}",
            ['O\'Reilly says "雪" \\ path'],
        ),
        (
            r"['a\x00b', 'a\u96eab', 'a\U0001f600b', 'a\tb', 'a\nb']",
            ["a\x00b", "a\tb", "a\nb", "a雪b", "a😀b"],
        ),
        ('["O\'Reilly"]', ["O'Reilly"]),
        ("{'choices': 'west'}", ["west"]),
        ("[]", []),
    ],
)
def test_literal_escapes_and_labels_are_not_rewritten(reader, stored, expected):
    # Whitespace-only labels are not valid choices, but whitespace *within*
    # a label must survive exactly for the unchanged current-data filter.
    assert [v["value"] for v in reader.invoke([stored])["values"]] == sorted(
        expected, key=str.lower
    )


def test_untrusted_column_metadata_cannot_supply_or_replace_labels(reader):
    reader.column.metadata = {
        "output": "choices",
        "data": {"result": "wrong"},
        "choices": ["wrong"],
    }
    assert reader.invoke(["{'choice': 'west', 'score': 0.2}"])["values"] == [
        {"value": "west", "label": "west"}
    ]


# PostgreSQL holds the dataset and is what the dataset table filters read. The
# ClickHouse mirror is ordered by cell id (dev: 14.3M rows / 19.5 GB read for a
# 12-cell column) and trails every write by a CDC batch (B04).
CELL_SCOPE = (
    "FROM model_hub_cell "
    "WHERE dataset_id = %(dataset_id)s "
    "AND column_id = %(column_id)s "
    "AND deleted = false "
    "AND value <> '' "
)


@pytest.mark.parametrize("origin", ["evaluation", "others"])
def test_scope_search_deadline_and_cursor_are_forwarded_unchanged(reader, origin):
    reader.column.source = origin
    response = reader.invoke(["west"], search="we", cursor="bound-cursor")
    statements = [call.args for call in reader.postgres.call_args_list]
    # Eval choices read their interpretation metadata in a second statement of
    # the same snapshot, only after the value inventory proved finite.
    assert len(statements) == (2 if origin == "evaluation" else 1)
    inventory, params = statements[0]
    assert "SELECT DISTINCT value AS val FROM model_hub_cell " in inventory
    assert "ORDER BY val LIMIT %(result_limit)s" in inventory
    for sql, _params in statements:
        assert CELL_SCOPE in sql
        assert "FINAL" not in sql and "_peerdb" not in sql
        assert (GENERIC_SEARCH in sql) == (origin == "others")
        # The eval-choice arm may bound the read, but never by the plain
        # search parameter that the generic arm uses: its predicate is the
        # full escape- and non-ASCII-safe disjunction, so the decoded filter
        # below still owns the verdict.
        assert (CHOICE_SEARCH_SUPERSET in sql) == (origin == "evaluation")
        assert "<= %(max_result_bytes)s" in sql
    assert ("value_infos" in statements[-1][0]) == (origin == "evaluation")
    assert "value_infos" not in inventory
    assert params == {
        "dataset_id": uuid.UUID(DATASET),
        "column_id": uuid.UUID(COLUMN),
        "search": "we",
        "result_limit": 5001,
        "max_result_bytes": 1048576,
        **({"choice_search": "we"} if origin == "evaluation" else {}),
    }
    assert reader.manager.select_related.return_value.get.call_args.kwargs == {
        "id": COLUMN,
        "dataset_id": DATASET,
        "dataset__workspace": reader.request.workspace,
        "dataset__organization_id": "owned-org",
        "dataset__deleted": False,
        "deleted": False,
    }
    assert response["query_params"]["cursor"] == "bound-cursor"
    assert response["query"] == {
        "source": "dataset_column",
        "metric_name": COLUMN,
        "metric_type": "custom_column",
        "dataset_id": DATASET,
        "attribute_type": "array",
    }
    assert reader.deadline.remaining_ms.call_count == 1


@pytest.mark.parametrize("statement", [0, 1])
def test_an_oversized_result_is_refused_not_truncated(reader, statement):
    """PostgreSQL has no result-byte cap, so each read stops one row past it."""

    reader.state["oversized_statement"] = statement
    assert reader.invoke(["west"])["status"] == 503
    reader.view._finite_native_filter_values_response.assert_not_called()


def test_deadline_and_inventory_remain_fail_closed(reader):
    reader.deadline.remaining_ms.side_effect = [DeadlineExceeded()]
    assert reader.invoke(["west"])["status"] == 503
    reader.deadline.remaining_ms.side_effect = None
    run_statements = reader.scope["_run_filter_value_pg_statements"]
    for error in (DeadlineExceeded, reader.scope["DatabaseError"]):
        # A PostgreSQL statement_timeout surfaces as a DatabaseError.
        def timed_out(deadline, read, error=error):
            raise error("canceling statement due to statement timeout")

        reader.scope["_run_filter_value_pg_statements"] = timed_out
        assert reader.invoke(["west"])["status"] == 503
    reader.scope["_run_filter_value_pg_statements"] = run_statements
    reader.scope["_FINITE_NATIVE_FILTER_VALUE_MAX"] = 1
    assert reader.invoke(["east", "west"])["status"] == 422
    assert reader.invoke(['["east", "west"]'])["status"] == 422
    reader.view._finite_native_filter_values_response.assert_not_called()


def test_socket_guard_is_active():
    with pytest.raises(AssertionError, match="offline socket guard"):
        socket.socket()


@pytest.mark.parametrize(
    "value", ["[west]", "{west}", '["west"]', '{"choice":"literal"}']
)
@pytest.mark.parametrize("encodings", [0, 1, 2])
def test_literal_labels_need_exact_same_cell_metadata(reader, value, encodings):
    metadata = {"output": "choices", "data": {"result": value}}
    for _ in range(encodings):
        metadata = json.dumps(metadata)
    response = reader.invoke([{"val": value, "choice_value_infos": metadata}])
    assert response["values"] == [{"value": value, "label": value}]


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        "broken",
        {"data": {"result": "[west]"}},
        {"output": "choices", "data": {"result": "wrong"}},
        {"output": "choices", "data": {"result": "[west]", "choice": "wrong"}},
        '{"output":"choices","data":{"result":"[west]","result":"wrong"}}',
    ],
)
def test_ambiguous_literal_without_verifiable_metadata_stays_fail_closed(
    reader, metadata
):
    assert (
        reader.invoke([{"val": "[west]", "choice_value_infos": metadata}])["status"]
        == 503
    )


@pytest.mark.parametrize(
    "label", ["path\\name", 'He said "雪"', "雪", "a\nb", "O'Reilly"]
)
@pytest.mark.parametrize("page_size", [None, 50])
def test_search_matches_decoded_label_not_escaped_storage(reader, label, page_size):
    stored = json.dumps({"choice": label, "score": 0.2}, ensure_ascii=True)
    response = reader.invoke([stored, "unrelated"], search=label, page_size=page_size)
    assert response["values"] == [{"value": label, "label": label}]
    assert reader.invoke([stored], search="0.2", page_size=page_size)["values"] == []
    # "0.2" occurs in the stored score text but is not a decoded label, so a
    # row the bounded pre-filter keeps is still dropped by the decoder. The
    # pre-filter may narrow the read; it may never decide the answer.
    sql = reader.postgres.call_args.args[0]
    assert GENERIC_SEARCH not in sql
    assert CHOICE_SEARCH_SUPERSET in sql


@pytest.mark.parametrize("page_size,limit", [(None, 1000), (50, 5000)])
@pytest.mark.parametrize(
    "label,search",
    [
        ("target", " TARGET "),
        ("Straße", "STRASSE"),
        ("雪", " 雪 "),
        ("path\\name", "path\\name"),
        ("a\nb", "a\nb"),
    ],
)
def test_expanded_choice_cap_counts_only_decoded_matches(
    reader, page_size, limit, label, search
):
    # Every raw cell survives the prefilter, yet their distinct decoded labels
    # exceed the real cap. Repeated matches count once, including in later cells.
    unrelated = [f"other-{index}" for index in range(limit + 1)]
    raw = [
        json.dumps(unrelated[start : start + 255] + [label])
        for start in range(0, len(unrelated), 255)
    ]
    response = reader.invoke(raw, search=search, page_size=page_size)
    assert response["status"] == 200
    assert response["values"] == [{"value": label, "label": label}]


@pytest.mark.parametrize("page_size,limit", [(None, 1000), (50, 5000)])
def test_expanded_choice_search_can_return_an_exact_empty_result(
    reader, page_size, limit
):
    unrelated = [f"other-{index}" for index in range(limit + 1)]
    raw = [
        json.dumps({"choices": unrelated[start : start + 256], "score": 0.2})
        for start in range(0, len(unrelated), 256)
    ]
    response = reader.invoke(raw, search="0.2", page_size=page_size)
    assert response["status"] == 200
    assert response["values"] == []


@pytest.mark.parametrize("page_size,limit", [(None, 1000), (50, 5000)])
@pytest.mark.parametrize("extra", [0, 1])
def test_expanded_matching_choices_still_obey_the_distinct_cap(
    reader, page_size, limit, extra
):
    labels = [f"match-{index}" for index in range(limit + extra)]
    raw = [
        json.dumps(labels[start : start + 255] + ["match-0"])
        for start in range(0, len(labels), 255)
    ]
    response = reader.invoke(raw, search="match", page_size=page_size)
    if extra:
        assert response == {
            "status": 422,
            "code": "filter_value_inventory_too_broad",
        }
        reader.view._finite_native_filter_values_response.assert_not_called()
    else:
        assert response["status"] == 200
        assert response["values"] == [
            {"value": label, "label": label} for label in sorted(labels)
        ]


def test_choice_search_filters_both_interpretations_before_the_expanded_cap(reader):
    reader.scope["_FINITE_NATIVE_FILTER_VALUE_MAX"] = 1
    response = reader.invoke(
        [{"val": '["west"]', "literal": True}, '["west"]'], search='["'
    )
    assert response["status"] == 200
    assert response["values"] == [{"value": '["west"]', "label": '["west"]'}]


@pytest.mark.parametrize("stored", ['["match", 2]', '["other", 2]', r'["\ud800"]'])
def test_choice_search_does_not_skip_invalid_cells_after_a_match(reader, stored):
    assert reader.invoke(["match", stored], search="match") == {
        "status": 503,
        "code": "service_unavailable",
    }
    reader.view._finite_native_filter_values_response.assert_not_called()


def test_a_narrow_search_bounds_the_read_instead_of_scanning_everything(reader):
    """The read an oversized inventory gets must depend on the search.

    Before the pre-filter, an eval-choice column above the cap answered 422
    for every search, so the error's own advice — "enter a more specific
    search" — could not resolve it.
    """

    reader.invoke(["west"], search="")
    unfiltered = reader.postgres.call_args.args[0]
    reader.invoke(["west"], search="west")
    narrowed, params = reader.postgres.call_args.args
    assert CHOICE_SEARCH_SUPERSET not in unfiltered
    assert CHOICE_SEARCH_SUPERSET in narrowed
    assert params["choice_search"] == "west"


@pytest.mark.parametrize("search", ["雪", " 雪 ", "café", "İ", ""])
def test_a_search_sql_cannot_bound_safely_reads_everything(reader, search):
    """Fail open to the full bounded inventory, never to a wrong answer.

    An ASCII-only case-insensitive match agrees with Python's ``casefold``
    only while both sides stay ASCII, so a non-ASCII needle may not be
    matchable in SQL at all. The read then stays unfiltered and the cap still
    applies.
    """

    reader.invoke(["west"], search=search)
    sql, params = reader.postgres.call_args.args
    assert CHOICE_SEARCH_SUPERSET not in sql
    assert "choice_search" not in params


def test_the_prefilter_never_narrows_a_generic_array_column(reader):
    """Only decoded eval choices use it; every other column keeps its reader."""

    reader.column.source = "others"
    reader.invoke(['["west"]'], search="west")
    sql, params = reader.postgres.call_args.args
    assert CHOICE_SEARCH_SUPERSET not in sql
    assert "choice_search" not in params
    assert GENERIC_SEARCH in sql


@pytest.mark.parametrize(
    "arm",
    [
        "OR strpos(value, chr(92)) > 0 ",
        "OR octet_length(value) <> char_length(value)) ",
        # Any collation but "C" can lowercase ASCII into non-ASCII (Turkish
        # dotted I), which would drop a row Python's casefold keeps.
        'lower(value COLLATE "C")',
    ],
)
def test_every_arm_of_the_prefilter_is_load_bearing(reader, arm):
    """Deleting either safety arm must be visible here, not only in review.

    Each arm is what keeps the predicate a superset: escaped storage can decode
    to characters it does not literally contain, and a non-ASCII cell can
    casefold differently than PostgreSQL lowercases it.
    """

    reader.invoke(["west"], search="west")
    sql = reader.postgres.call_args.args[0]
    assert arm in sql


def test_same_storage_text_can_have_distinct_literal_and_container_meanings(reader):
    response = reader.invoke(
        [
            {
                "val": '["west"]',
                "choice_value_infos": {
                    "output": "choices",
                    "data": {"result": '["west"]'},
                },
            },
            {
                "val": '["west"]',
                "choice_value_infos": {
                    "output": "choices",
                    "data": {"result": ["west"]},
                },
            },
        ]
    )
    assert [v["value"] for v in response["values"]] == ['["west"]', "west"]


def test_choice_search_cannot_turn_an_overflow_into_sampled_success(reader):
    reader.scope["_FINITE_NATIVE_FILTER_VALUE_MAX"] = 1
    assert reader.invoke(["west", "east"], search="west")["status"] == 422
    reader.view._finite_native_filter_values_response.assert_not_called()


def test_many_explanations_do_not_change_distinct_value_cardinality(reader):
    records = [
        {
            "val": repr({"choice": label, "score": 0.2}),
            "choice_value_infos": json.dumps(
                {
                    "output": "choices",
                    "data": {"result": label},
                    "reason": f"explanation-{index}",
                    "usage": index,
                }
            ),
        }
        for label in ("west", "east")
        for index in range(6000)
    ]
    response = reader.invoke(records)
    assert response["values"] == [{"value": v, "label": v} for v in ("east", "west")]
    # The value inventory, and so the cap, counts distinct storage texts only.
    inventory = reader.postgres.call_args_list[0]
    assert "SELECT DISTINCT value AS val FROM" in inventory.args[0]
    assert len(reader.postgres.side_effect(*inventory.args)) == 2


def test_metadata_for_one_value_cannot_classify_another(reader):
    response = reader.invoke(
        [
            {
                "val": "[west]",
                "choice_value_infos": {"output": "choices", "data": "[east]"},
            },
            {
                "val": "[east]",
                "choice_value_infos": {"output": "choices", "data": "[east]"},
            },
        ]
    )
    assert response["status"] == 503


def test_a_value_without_an_interpretation_is_not_a_complete_vocabulary(reader):
    reader.state["interpretations"] = False
    assert reader.invoke(["west"])["status"] == 503
    reader.view._finite_native_filter_values_response.assert_not_called()


@pytest.fixture
def actual_producer():
    def extract(path, name, env=None):
        method = next(
            node
            for node in ast.walk(ast.parse(path.read_text()))
            if isinstance(node, ast.FunctionDef) and node.name == name
        )
        scope = {} if env is None else env
        exec(
            compile(ast.Module(body=[method], type_ignores=[]), str(path), "exec"),
            scope,
        )
        return scope[name]

    scoring = ModuleType("model_hub.utils.scoring")
    scoring.apply_choice_scores = extract(
        ROOT / "model_hub/utils/scoring.py", "apply_choice_scores"
    )
    formatter = extract(
        ROOT / "model_hub/views/eval_runner.py",
        "format_output",
        {"gc": NS(collect=lambda: None)},
    )
    response = extract(ROOT / "evaluations/engine/formatting.py", "extract_raw_result")
    django_spec = find_spec("django")
    assert django_spec is not None and django_spec.origin, (
        "installed Django is required"
    )
    fields_path = Path(django_spec.origin).parent / "db/models/fields/__init__.py"
    fields = ast.parse(fields_path.read_text())
    field = next(
        node
        for node in fields.body
        if isinstance(node, ast.ClassDef) and node.name == "TextField"
    )
    conversion = next(
        node
        for node in field.body
        if isinstance(node, ast.FunctionDef) and node.name == "to_python"
    )
    scope = {}
    exec(
        compile(
            ast.Module(body=[conversion], type_ignores=[]),
            "TextField.to_python",
            "exec",
        ),
        scope,
    )

    def produce(data, scored):
        template = NS(
            config={"output": "choices"},
            choice_scores={"west": 0.2, "east": 0.8} if scored else None,
        )
        metadata = response(NS(eval_results=[{"data": data}]), template)
        with patch.dict(sys.modules, {"model_hub.utils.scoring": scoring}):
            value = formatter(NS(eval_template=template), metadata, row=object())
        return {
            "val": scope["to_python"](None, value),
            "choice_value_infos": json.dumps(json.dumps(metadata)),
        }

    return produce


@pytest.mark.parametrize(
    "data,expected",
    [
        ("west", ["west"]),
        (["west", "east"], ["east", "west"]),
        ({"result": "west"}, ["west"]),
        ({"choice": "east"}, ["east"]),
        ({"result": ["west", "east"]}, ["east", "west"]),
        ("O'Reilly", ["O'Reilly"]),
        ("path\\name 雪", ["path\\name 雪"]),
        ("[west]", ["[west]"]),
        ("{west}", ["{west}"]),
        ('["west"]', ['["west"]']),
    ],
)
@pytest.mark.parametrize("scored", [False, True])
def test_actual_formatter_textfield_and_jsonfield_lineage(
    reader, actual_producer, data, expected, scored
):
    response = reader.invoke([actual_producer(data, scored)])
    assert response["values"] == [{"value": v, "label": v} for v in expected]


@pytest.mark.parametrize(
    "stored",
    [
        "['unterminated]",
        "[__import__('os').system('no')]",
        "['west'] * 1000000000",
        "['west', 2]",
        "{'score': 0.2}",
        "{'choice': {'nested': 'west'}}",
        "{'choice': 'west', 'choices': ['east']}",
        "{'choice': 'west', 'unknown': 'east'}",
        "{'choice': 'west', 'score': float('nan')}",
        '{"choice":"west","score":NaN}',
        '{"choice":"west","choice":"east"}',
        "{'choice': 'west', 'choice': 'east'}",
        "['we' 'st']",
        "{'choice': ['west']}",
        "{'choice': 'west', 'score': True}",
        "[b'west']",
        "[('west',)]",
        "[{'west'}]",
        "[x for x in []]",
        "['west' # comment\n]",
        '{"choice":"west","score":1e999}',
        '{"choice":"west","score":' + "9" * 1000 + "}",
        "{'choice': 'west', **{}}",
        "[" * 20 + "'west'" + "]" * 20,
        json.dumps(["west"] * 257),
        "w" * 16385,
    ],
    ids=lambda value: value[:75],
)
def test_malformed_evaluation_choices_fail_closed_without_blob_suggestions(
    reader, stored
):
    response = reader.invoke([stored])
    assert response == {"status": 503, "code": "service_unavailable"}
    reader.view._finite_native_filter_values_response.assert_not_called()
