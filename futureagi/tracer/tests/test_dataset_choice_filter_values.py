"""Offline reader contracts: execute the real method, never import Django.

ORM/CH boundaries are recording doubles; this does not attest CDC or PG filter
membership. Run with -c /dev/null --noconftest, explicit root, network denied.
"""

import ast
import json
import os
import socket
import subprocess
import sys
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
    analytics = Mock()
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
        "ReadDeadlineExceeded": DeadlineExceeded,
        "is_clickhouse_enabled": lambda: True,
        "AnalyticsQueryService": lambda: analytics,
        "_FINITE_NATIVE_FILTER_VALUE_MAX": 5000,
        "_LEGACY_NATIVE_FILTER_VALUE_MAX": 1000,
        "_FINITE_NATIVE_FILTER_VALUE_MAX_RESULT_BYTES": 1048576,
        "_FILTER_VALUES_INTERACTIVE_TIMEOUT_MS": 1000,
        "status": NS(
            HTTP_503_SERVICE_UNAVAILABLE=503,
            HTTP_500_INTERNAL_SERVER_ERROR=500,
            HTTP_422_UNPROCESSABLE_ENTITY=422,
        ),
        "is_clickhouse_api_read_unavailable_error": lambda exc: True,
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
        records = [
            value if isinstance(value, dict) else {"val": value} for value in raw
        ]
        # The query returns one row per distinct raw value, with both observed
        # interpretations retained. Explanations/usage never enter this key.
        grouped = {}
        for row in records:
            value = row["val"]
            mode = row.get(
                "choice_modes",
                2
                if literal_match_reference(value, row.get("choice_value_infos"))
                else 1,
            )
            grouped[value] = grouped.get(value, 0) | mode
        analytics.execute_ch_query.return_value = NS(
            data=[
                {"val": value, "choice_modes": mode} for value, mode in grouped.items()
            ]
        )
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
        analytics=analytics,
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
        digesting_reader.invoke([{"val": "\ud800", "choice_modes": 2}])["status"] == 503
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


@pytest.mark.parametrize("origin", ["evaluation", "others"])
def test_scope_search_deadline_and_cursor_are_forwarded_unchanged(reader, origin):
    reader.column.source = origin
    response = reader.invoke(["west"], search="we", cursor="bound-cursor")
    sql, params = reader.analytics.execute_ch_query.call_args.args
    assert "FROM model_hub_cell FINAL" in sql
    assert "_peerdb_is_deleted = 0" in sql
    assert ("positionCaseInsensitiveUTF8(value, %(search)s) > 0" in sql) == (
        origin == "others"
    )
    assert ("groupBitOr(if(literal_choice, 2, 1)) AS choice_modes" in sql) == (
        origin == "evaluation"
    )
    assert ("GROUP BY value ORDER BY val LIMIT %(result_limit)s" in sql) == (
        origin == "evaluation"
    )
    assert "value_infos AS choice_value_infos" not in sql
    assert "DISTINCT value AS val, value_infos" not in sql
    assert params == {
        "dataset_id": DATASET,
        "column_id": COLUMN,
        "search": "we",
        "result_limit": 5001,
    }
    assert reader.manager.select_related.return_value.get.call_args.kwargs == {
        "id": COLUMN,
        "dataset_id": DATASET,
        "dataset__workspace": reader.request.workspace,
        "dataset__organization_id": "owned-org",
        "dataset__deleted": False,
        "deleted": False,
    }
    options = reader.analytics.execute_ch_query.call_args.kwargs
    assert options == {
        "timeout_ms": 1000,
        "settings": {
            "max_result_rows": 5001,
            "max_result_bytes": 1048576,
            "result_overflow_mode": "throw",
        },
    }
    assert response["query_params"]["cursor"] == "bound-cursor"
    assert response["query"] == {
        "source": "dataset_column",
        "metric_name": COLUMN,
        "metric_type": "custom_column",
        "dataset_id": DATASET,
        "attribute_type": "array",
    }
    assert reader.deadline.remaining_ms.call_count == 2


def test_deadline_and_inventory_remain_fail_closed(reader):
    reader.deadline.remaining_ms.side_effect = [1000, DeadlineExceeded()]
    assert reader.invoke(["west"])["status"] == 503
    reader.deadline.remaining_ms.side_effect = None
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
    assert (
        "positionCaseInsensitiveUTF8"
        not in reader.analytics.execute_ch_query.call_args.args[0]
    )


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
    assert len(reader.analytics.execute_ch_query.return_value.data) == 2
    sql = reader.analytics.execute_ch_query.call_args.args[0]
    assert "GROUP BY value ORDER BY val" in sql
    assert "groupBitOr(if(literal_choice, 2, 1)) AS choice_modes" in sql
    assert "DISTINCT value AS val, value_infos" not in sql
    assert "any(value_infos)" not in sql and "groupArray" not in sql


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


@pytest.mark.parametrize("mode", [0, 4, 7])
def test_invalid_aggregate_protocol_is_not_a_complete_vocabulary(reader, mode):
    assert reader.invoke([{"val": "west", "choice_modes": mode}])["status"] == 503


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
