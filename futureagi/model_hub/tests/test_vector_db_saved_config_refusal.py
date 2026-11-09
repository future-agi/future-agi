"""A saved vector-DB config whose Hugging Face model is now refused fails
clearly at run time (T6 #1, release-lead round-2 addition (b)).

Columns created before the allowlist keep their ``embedding_config`` in
``Column.metadata``, and the rerun, the async task and conditional
retrieval branches run it without the request serializer. The row must
fail as an error (``{"reason": ...}``, so the task writes an ERROR cell)
with the same message add_vector_db_column gives, naming the approved
models. Before the fix the row reached model serving, whose 400 detail was
dropped by ``raise_for_status()``, and ``_query_vector_db`` returned the
text "400 Client Error: ..." as the cell VALUE of a passing cell.

DB-free: ``Cell``/``Column`` lookups and model serving are mocked.
"""

import inspect
import json
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import requests

from agentic_eval.core.embeddings.serving_client import ModelServingClient
from model_hub.models.choices import CellStatus, StatusType
from model_hub.serializers.contracts import (
    APPROVED_HUGGINGFACE_EMBEDDING_MODELS,
    HUGGINGFACE_EMBEDDING_MODEL_NOT_APPROVED,
)
from model_hub.views import develop_dataset, dynamic_columns

HF_TYPES = ["huggingface", "sentence_transformers"]
REFUSED_MODEL = "nomic-ai/nomic-embed-text-v1"
APPROVED_LIST = "Approved models: " + ", ".join(
    sorted(APPROVED_HUGGINGFACE_EMBEDDING_MODELS)
)
SERVING_URL = "http://serving.invalid"
ORG_ID = "00000000-0000-0000-0000-00000000000a"
WORKSPACE_ID = "00000000-0000-0000-0000-00000000000b"
SERVING_ALLOWLIST_DETAIL = (
    "Hugging Face model is not on this server's allowlist. "
    f"Approved models: {sorted(APPROVED_HUGGINGFACE_EMBEDDING_MODELS)}"
)


def _saved_config(model=REFUSED_MODEL, embedding_type="huggingface"):
    # The shape add_vector_db_column stores in Column.metadata.
    return {
        "sub_type": "pinecone",
        "index_name": "saved-index",
        "top_k": 1,
        "namespace": "saved",
        "api_key": "00000000-0000-0000-0000-000000000001",
        "embedding_config": {"type": embedding_type, "model": model},
        "column_id": "00000000-0000-0000-0000-000000000002",
        "concurrency": 1,
        "vector_length": 384,
    }


@contextmanager
def _row_runtime(module, get_embeddings=None):
    """Mock the DB reads and the embedding call of one vector-DB row."""
    get_embeddings = get_embeddings or MagicMock(name="get_embeddings")
    with (
        patch.object(
            module.Cell.objects, "get", return_value=SimpleNamespace(value="query")
        ),
        patch.object(
            module.Column.objects, "get", return_value=SimpleNamespace(id="col")
        ),
        patch.object(module.model_manager, "get_embeddings", get_embeddings),
        patch.object(
            module.AddVectorDBColumnView,
            "_get_api_key_for_provider",
            return_value="synthetic-hf-key",
        ),
        patch.object(dynamic_columns, "require_serving"),
    ):
        yield get_embeddings


def _row(module, config):
    view = module.AddVectorDBColumnView()
    return view._process_row(
        SimpleNamespace(id="row"),
        SimpleNamespace(id="col"),
        config,
        ORG_ID,
        WORKSPACE_ID,
    )


def _serving_response(status, detail):
    response = requests.Response()
    response.status_code = status
    response.reason = {400: "Bad Request", 404: "Not Found", 500: "Server Error"}[
        status
    ]
    response.url = f"{SERVING_URL}/model/v1/infer/huggingface"
    response.headers["Content-Type"] = "application/json"
    response._content = json.dumps({"detail": detail}).encode()
    return response


def _real_client(response):
    client = ModelServingClient(base_url=SERVING_URL)
    client._require_serving = lambda: None
    client.session = MagicMock(name="session")
    client.session.post.return_value = response
    return client


# --- saved config runs: refused before model serving -------------------------


# develop_dataset.py keeps an older AddVectorDBColumnView whose
# add_vector_db_column_async shares the drop-in activity name with
# dynamic_columns.py (the registry keeps whichever the worker imports last),
# so both copies must refuse the same way.
@pytest.mark.parametrize(
    "module", [dynamic_columns, develop_dataset], ids=lambda m: m.__name__
)
@pytest.mark.parametrize("embedding_type", HF_TYPES)
def test_saved_config_with_refused_model_fails_the_row_clearly(module, embedding_type):
    with _row_runtime(module) as get_embeddings:
        value, value_infos = _row(module, _saved_config(embedding_type=embedding_type))

    # Nothing is sent to model serving, so no loader can run for the model.
    get_embeddings.assert_not_called()
    # "reason" is what makes the task write an ERROR cell, not a PASS cell.
    assert value_infos and APPROVED_LIST in value_infos["reason"], (value, value_infos)
    assert "not approved" in value_infos["reason"]


@pytest.mark.parametrize("model", ["/tmp/evil-model", "", "ALL-MPNET-BASE-V2"])
def test_saved_config_with_a_path_or_near_miss_fails_the_row_clearly(model):
    with _row_runtime(dynamic_columns) as get_embeddings:
        _, value_infos = _row(dynamic_columns, _saved_config(model=model))

    get_embeddings.assert_not_called()
    assert value_infos and APPROVED_LIST in value_infos["reason"]


def test_saved_conditional_retrieval_branch_with_refused_model_fails_clearly():
    branch = {
        "branch_type": "else",
        "condition": "",
        "branch_node_config": {"type": "retrieval", "config": _saved_config()},
    }
    with _row_runtime(dynamic_columns) as get_embeddings:
        value, value_infos = dynamic_columns.ConditionalColumnView()._process_row(
            SimpleNamespace(id="row"), [branch], ORG_ID
        )

    get_embeddings.assert_not_called()
    assert value_infos and APPROVED_LIST in value_infos["reason"], (value, value_infos)


@pytest.mark.parametrize(
    "embedding_config",
    [
        {"type": "huggingface", "model": "all-mpnet-base-v2"},
        {"type": "huggingface", "model": "sentence-transformers/all-MiniLM-L6-v2"},
        # No model: the all-mpnet-base-v2 default.
        {"type": "huggingface"},
        {"type": "openai", "model": "text-embedding-3-large"},
    ],
)
def test_saved_config_with_an_allowed_model_still_embeds(embedding_config):
    config = _saved_config()
    config["embedding_config"] = embedding_config
    get_embeddings = MagicMock(name="get_embeddings", return_value=[0.1, 0.2])
    with (
        _row_runtime(dynamic_columns, get_embeddings),
        patch.object(
            dynamic_columns.AddVectorDBColumnView,
            "_query_pinecone",
            return_value=["match"],
        ),
    ):
        value, value_infos = _row(dynamic_columns, config)

    get_embeddings.assert_called_once()
    assert (value, value_infos) == (["match"], {})


# --- a refusal from model serving keeps its reason ---------------------------


def test_serving_client_keeps_the_allowlist_detail_of_a_serving_400():
    client = _real_client(_serving_response(400, SERVING_ALLOWLIST_DETAIL))

    with pytest.raises(requests.exceptions.HTTPError) as excinfo:
        client.get_embeddings(["query"], "huggingface", REFUSED_MODEL, {"api_key": "k"})

    assert SERVING_ALLOWLIST_DETAIL in str(excinfo.value)


def test_serving_client_keeps_a_5xx_opaque():
    # A 500 detail embeds arbitrary exception text; it is not surfaced.
    client = _real_client(_serving_response(500, "Internal server error: internal"))

    with pytest.raises(requests.exceptions.HTTPError) as excinfo:
        client.get_embeddings(
            ["query"], "huggingface", "all-mpnet-base-v2", {"api_key": "k"}
        )

    assert "internal" not in str(excinfo.value).replace(SERVING_URL, "")


def test_a_serving_refusal_fails_the_saved_row_with_its_reason():
    # The backend check passes (sentence_transformers + an approved model),
    # model serving refuses: the row is an error carrying serving's reason,
    # not a passing cell whose value is "404 Client Error: ...". Serving's
    # exception_handler(404) (model_serving/app/main.py) rewrites every 404
    # body to "Not found", so that is all the row can say for this type; the
    # Retrieval form and the catalog say it is not available on this server.
    client = _real_client(_serving_response(404, "Not found"))
    config = _saved_config(
        model="all-mpnet-base-v2", embedding_type="sentence_transformers"
    )

    with _row_runtime(dynamic_columns, client.get_embeddings):
        value, value_infos = _row(dynamic_columns, config)

    client.session.post.assert_called_once()
    assert value_infos, (value, value_infos)
    assert value_infos["reason"] == "Model serving refused the request: Not found"


# --- the activities persist a refused saved config as an ERROR cell ----------
#
# The row-level refusal above only helps if the activity that runs the saved
# config writes it as an ERROR cell. Both activities' rerun branches built a
# PASS cell (value None, the refusal hidden in value_infos) for a row with no
# destination cell yet; the output UI shows a reason only for an error cell.
#
# develop_dataset.py defines add_vector_db_column_async and
# conditional_column_async again, under the same activity names. The worker
# runs whichever copy registers last, which is develop_dataset's with the
# worker's module order (tfc/temporal/common/registry.py), and those copies
# took no is_rerun: every vector-DB or conditional rerun that dynamic_columns
# dispatches died in the worker with a TypeError before writing any cell.
# Each case therefore runs against both copies.

ACTIVITY_MODULES = [dynamic_columns, develop_dataset]
ACTIVITIES = ["vector_db", "conditional"]
DATASET_ID = "00000000-0000-0000-0000-0000000000d1"
SOURCE_COLUMN_ID = "00000000-0000-0000-0000-000000000002"
TARGET_COLUMN_ID = "00000000-0000-0000-0000-0000000000c1"
SAVED_ROW = SimpleNamespace(id="00000000-0000-0000-0000-0000000000e1")


@contextmanager
def _activity_runtime(*, existing_cell, get_embeddings=None):
    """Mock the DB of one dataset with one row, for both module copies.

    ``persisted`` collects every cell the activity saves or bulk-creates.
    """
    persisted = []

    class FakeCell:
        objects = MagicMock(name="Cell.objects")
        # Cell.status defaults to PASS when the activity passes none.
        status = CellStatus.PASS.value

        def __init__(self, **fields):
            self.__dict__.update(fields)

        def save(self):
            persisted.append(self)

    FakeCell.objects.get.return_value = SimpleNamespace(value="query")
    FakeCell.objects.filter.return_value = (
        [
            FakeCell(
                row_id=SAVED_ROW.id,
                value="an earlier result",
                value_infos="{}",
                status=CellStatus.PASS.value,
            )
        ]
        if existing_cell
        else []
    )
    FakeCell.objects.bulk_create.side_effect = lambda cells, **_: persisted.extend(
        cells
    )
    column_model = MagicMock(name="Column")
    column_model.objects.get.return_value = SimpleNamespace(id=SOURCE_COLUMN_ID)
    row_model = MagicMock(name="Row")
    row_model.objects.filter.return_value = [SAVED_ROW]
    dataset_model = MagicMock(name="Dataset")
    dataset_model.objects.get.return_value = SimpleNamespace(
        organization=SimpleNamespace(id=ORG_ID)
    )
    get_embeddings = get_embeddings or MagicMock(name="get_embeddings")

    with ExitStack() as stack:
        for module in ACTIVITY_MODULES:
            for name, value in (
                ("Cell", FakeCell),
                ("Column", column_model),
                ("Row", row_model),
                ("Dataset", dataset_model),
                ("wrap_for_thread", lambda func: func),
            ):
                stack.enter_context(patch.object(module, name, value))
            stack.enter_context(
                patch.object(module.model_manager, "get_embeddings", get_embeddings)
            )
            stack.enter_context(
                patch.object(
                    module.AddVectorDBColumnView,
                    "_get_api_key_for_provider",
                    return_value="synthetic-hf-key",
                )
            )
        stack.enter_context(patch.object(dynamic_columns, "require_serving"))
        yield SimpleNamespace(
            persisted=persisted,
            column_model=column_model,
            get_embeddings=get_embeddings,
        )


def _retrieval_branches(config):
    return [
        {
            "branch_type": "else",
            "condition": "",
            "branch_node_config": {"type": "retrieval", "config": config},
        }
    ]


def _rerun(module, activity, config):
    # The positional args dynamic_columns' _rerun_vector_db and
    # _rerun_conditional dispatch.
    if activity == "vector_db":
        module.add_vector_db_column_async._original_func(
            config,
            DATASET_ID,
            1,
            SOURCE_COLUMN_ID,
            ORG_ID,
            TARGET_COLUMN_ID,
            WORKSPACE_ID,
            True,
        )
    else:
        module.conditional_column_async._original_func(
            _retrieval_branches(config), DATASET_ID, 1, TARGET_COLUMN_ID, True
        )


def _create(module, activity, config):
    # The positional args the create views dispatch.
    if activity == "vector_db":
        module.add_vector_db_column_async._original_func(
            config,
            DATASET_ID,
            1,
            SOURCE_COLUMN_ID,
            ORG_ID,
            TARGET_COLUMN_ID,
            WORKSPACE_ID,
        )
    else:
        module.conditional_column_async._original_func(
            _retrieval_branches(config), DATASET_ID, 1, TARGET_COLUMN_ID
        )


def _assert_one_refused_error_cell(runtime):
    runtime.get_embeddings.assert_not_called()
    assert len(runtime.persisted) == 1, [c.__dict__ for c in runtime.persisted]
    cell = runtime.persisted[0]
    assert cell.status == CellStatus.ERROR.value, cell.__dict__
    assert cell.value is None, cell.__dict__
    # Exactly the text the add_vector_db_column 400 gives, naming the models.
    assert json.loads(cell.value_infos) == {
        "reason": HUGGINGFACE_EMBEDDING_MODEL_NOT_APPROVED
    }
    assert APPROVED_LIST in HUGGINGFACE_EMBEDDING_MODEL_NOT_APPROVED
    # The column finishes; it is not left RUNNING.
    runtime.column_model.objects.filter.return_value.update.assert_called_with(
        status=StatusType.COMPLETED.value
    )


@pytest.mark.parametrize("module", ACTIVITY_MODULES, ids=lambda m: m.__name__)
@pytest.mark.parametrize("activity", ACTIVITIES)
@pytest.mark.parametrize(
    "existing_cell", [True, False], ids=["existing_cell", "missing_cell"]
)
@pytest.mark.parametrize("embedding_type", HF_TYPES)
def test_rerun_of_a_saved_refused_config_writes_an_error_cell(
    module, activity, existing_cell, embedding_type
):
    config = _saved_config(embedding_type=embedding_type)
    with _activity_runtime(existing_cell=existing_cell) as runtime:
        _rerun(module, activity, config)

    _assert_one_refused_error_cell(runtime)


@pytest.mark.parametrize("module", ACTIVITY_MODULES, ids=lambda m: m.__name__)
@pytest.mark.parametrize("activity", ACTIVITIES)
def test_run_of_a_saved_refused_config_writes_an_error_cell(module, activity):
    # A new column (no rerun) over a config that skipped the request check,
    # such as a conditional branch.
    with _activity_runtime(existing_cell=False) as runtime:
        _create(module, activity, _saved_config())

    _assert_one_refused_error_cell(runtime)


@pytest.mark.parametrize("module", ACTIVITY_MODULES, ids=lambda m: m.__name__)
@pytest.mark.parametrize("activity", ACTIVITIES)
@pytest.mark.parametrize(
    "existing_cell", [True, False], ids=["existing_cell", "missing_cell"]
)
def test_rerun_of_a_saved_approved_config_writes_its_result(
    module, activity, existing_cell
):
    config = _saved_config(model="all-mpnet-base-v2")
    get_embeddings = MagicMock(name="get_embeddings", return_value=[0.1, 0.2])
    with (
        _activity_runtime(
            existing_cell=existing_cell, get_embeddings=get_embeddings
        ) as runtime,
        patch.object(
            dynamic_columns.AddVectorDBColumnView,
            "_query_pinecone",
            return_value=["match"],
        ),
    ):
        _rerun(module, activity, config)

    get_embeddings.assert_called_once()
    assert len(runtime.persisted) == 1, [c.__dict__ for c in runtime.persisted]
    cell = runtime.persisted[0]
    assert (cell.status, cell.value) == (CellStatus.PASS.value, ["match"]), (
        cell.__dict__
    )


@pytest.mark.parametrize(
    "activity", ["add_vector_db_column_async", "conditional_column_async"]
)
def test_both_copies_of_a_shared_activity_name_take_the_same_args(activity):
    # The worker keeps one function per activity name; either copy must
    # accept every call the other module dispatches.
    older, current = (
        getattr(develop_dataset, activity),
        getattr(dynamic_columns, activity),
    )
    assert older._activity_name == current._activity_name == activity
    assert inspect.signature(older._original_func) == inspect.signature(
        current._original_func
    )
