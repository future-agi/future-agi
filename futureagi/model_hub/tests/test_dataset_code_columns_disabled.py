"""
Capability contract: custom Python code columns are disabled and nondestructive.

The code executor never runs user code (its exec() stays commented out), so
before this change every code operation could only fail, and the shown
edit/re-run door turned an existing column's values into errors: rerun-
operation cleared every cell to RUNNING and queued execute_python_code_async,
which then wrote value=None/status=ERROR into each one.

These tests drive the real authenticated routes and the real worker bodies
with synthetic data owned by two separate organizations:

- preview execute_code, preview conditional with a code branch,
  conditional-column with a code branch and rerun-operation execute_code /
  conditional (an override or the stored config) answer 400 with one fixed
  message and leave every stored column, cell and dataset field unchanged,
  with nothing dispatched and no task queued;
- the queued/direct worker entry points refuse before their first read or
  write and end as a non-retryable failure (no retry, no false success);
- unknown, foreign-organization, unauthenticated and strict-validation
  refusals keep their own answers, and harmless non-code conditional configs
  (text that merely mentions code) still preview, create, re-run and run.

The retired positive execution case in test_dataset_dynamic_columns.py stays
skipped and unproven; the negative helper cases there are separate.
"""

import inspect
import json
import uuid

import pytest
from django.test import TestCase
from rest_framework import status

from model_hub.models.choices import (
    CellStatus,
    DataTypeChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row

# The fixed, non-reflective refusal (never echoes the submitted code).
DISABLED = (
    "Custom Python code is turned off. Existing values stay readable, but "
    "code cannot be tested, added, edited or re-run."
)

# Benign code: no pattern the old regex screen refuses.
BENIGN_CODE = "def main(**kwargs):\n    return 'diagnosis complete'\n"

# A conditional config whose only branch runs code (an `else` branch, so the
# old path reaches the executor without asking a model to judge a condition).
CODE_CONDITIONAL = [
    {
        "branch_type": "else",
        "condition": "",
        "branch_node_config": {
            "type": "extract_code",
            "config": {"code": BENIGN_CODE, "concurrency": 2},
        },
    }
]

# Harmless: a static value whose text (and the ignored condition text)
# mentions code. Structurally it is a static_value branch.
HARMLESS_VALUE = "extract_code: def main(**kwargs): return 1"
HARMLESS_CONDITIONAL = [
    {
        "branch_type": "else",
        "condition": "mentions extract_code and def main(**kwargs)",
        "branch_node_config": {
            "type": "static_value",
            "config": {"value": HARMLESS_VALUE, "extract_code": "not a type"},
        },
    }
]

PASSWORD = "disabled-disabled-code-password"


def _tenant(label):
    """A new organization with its owner, default workspace and a dataset
    holding a data column, a Python code column, a code conditional column
    and a harmless conditional column, each with three stored cells."""
    from accounts.models.organization import Organization
    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.user import User
    from accounts.models.workspace import Workspace, WorkspaceMembership
    from tfc.constants.levels import Level
    from tfc.constants.roles import OrganizationRoles
    from tfc.middleware.workspace_context import (
        clear_workspace_context,
        set_workspace_context,
    )

    clear_workspace_context()
    org = Organization.objects.create(name=f"R566 Org {label}")
    set_workspace_context(organization=org)
    user = User.objects.create_user(
        email=f"r566-{label}-{uuid.uuid4().hex[:8]}@futureagi.com",
        password=PASSWORD,
        name=f"R566 {label}",
        organization=org,
        organization_role=OrganizationRoles.OWNER,
    )
    membership, _ = OrganizationMembership.no_workspace_objects.get_or_create(
        user=user,
        organization=org,
        defaults={
            "role": OrganizationRoles.OWNER,
            "level": Level.OWNER,
            "is_active": True,
        },
    )
    workspace = Workspace.objects.create(
        name=f"R566 Workspace {label}",
        organization=org,
        is_default=True,
        is_active=True,
        created_by=user,
    )
    WorkspaceMembership.no_workspace_objects.get_or_create(
        user=user,
        workspace=workspace,
        defaults={
            "role": "Workspace Owner",
            "level": Level.OWNER,
            "is_active": True,
            "organization_membership": membership,
        },
    )
    set_workspace_context(workspace=workspace, organization=org, user=user)
    dataset = Dataset.objects.create(
        name=f"r566-{label}-{uuid.uuid4().hex[:6]}",
        organization=org,
        workspace=workspace,
        user=user,
    )

    def column(name, source, metadata=None):
        return Column.objects.create(
            name=name,
            dataset=dataset,
            data_type=DataTypeChoices.TEXT.value,
            source=source,
            metadata=metadata or {},
            status=StatusType.COMPLETED.value,
        )

    question = column("question", SourceChoices.OTHERS.value)
    python = column(
        "py_out",
        SourceChoices.PYTHON_CODE.value,
        {"code": BENIGN_CODE, "concurrency": 2},
    )
    cond_code = column(
        "cond_code",
        SourceChoices.CONDITIONAL.value,
        {"config": CODE_CONDITIONAL, "concurrency": 2},
    )
    cond_plain = column(
        "cond_plain",
        SourceChoices.CONDITIONAL.value,
        {"config": HARMLESS_CONDITIONAL, "concurrency": 2},
    )
    columns = [question, python, cond_code, cond_plain]
    dataset.column_order = [str(c.id) for c in columns]
    dataset.column_config = {
        str(c.id): {"is_visible": True, "is_frozen": None} for c in columns
    }
    dataset.save(update_fields=["column_order", "column_config"])
    for i in range(3):
        row = Row.objects.create(dataset=dataset, order=i)
        for col in columns:
            Cell.objects.create(
                dataset=dataset,
                row=row,
                column=col,
                value=f"{label}-{col.name}-{i}",
                value_infos=json.dumps({"kept": f"{label}-{i}"}),
                status=CellStatus.PASS.value,
            )
    clear_workspace_context()
    return {
        "org": org,
        "user": user,
        "workspace": workspace,
        "dataset": dataset,
        "question": question,
        "python": python,
        "cond_code": cond_code,
        "cond_plain": cond_plain,
    }


def _client(tenant):
    from conftest import WorkspaceAwareAPIClient

    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=tenant["user"])
    client.set_workspace(tenant["workspace"])
    return client


def _state(tenant):
    """Every stored field a refused operation could touch, for one dataset."""
    from tfc.middleware.workspace_context import clear_workspace_context

    clear_workspace_context()
    dataset = Dataset.all_objects.get(id=tenant["dataset"].id)
    columns = sorted(
        (
            str(c.id),
            c.name,
            c.source,
            c.status,
            json.dumps(c.metadata, sort_keys=True),
            c.deleted,
            c.updated_at.isoformat(),
        )
        for c in Column.all_objects.filter(dataset_id=dataset.id)
    )
    cells = sorted(
        (
            str(c.id),
            str(c.column_id),
            str(c.row_id),
            c.value,
            json.dumps(c.value_infos, sort_keys=True),
            c.status,
            c.deleted,
            c.updated_at.isoformat(),
        )
        for c in Cell.all_objects.filter(column__dataset_id=dataset.id)
    )
    rows = sorted(
        (str(r.id), r.order, r.deleted)
        for r in Row.all_objects.filter(dataset_id=dataset.id)
    )
    return {
        "column_order": list(dataset.column_order or []),
        "column_config": json.dumps(dataset.column_config, sort_keys=True),
        "dataset_updated_at": dataset.updated_at.isoformat(),
        "columns": columns,
        "cells": cells,
        "rows": rows,
    }


class _Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))


@pytest.fixture
def r566(db, monkeypatch):
    """Two organizations, recorded task queues, recorded dispatch into the
    executor and the conditional row processor, and a tripwire on the model
    client (no provider request may start)."""
    from model_hub.views import dynamic_columns
    from tfc.middleware.workspace_context import clear_workspace_context

    queued = {
        "execute_code": _Recorder(),
        "conditional": _Recorder(),
    }
    monkeypatch.setattr(
        dynamic_columns.execute_python_code_async, "delay", queued["execute_code"]
    )
    monkeypatch.setattr(
        dynamic_columns.conditional_column_async, "delay", queued["conditional"]
    )

    dispatched = {"executor": [], "process_row": []}
    original_execute = dynamic_columns.ExecutePythonCodeView._execute_python_code
    original_process_row = dynamic_columns.ConditionalColumnView._process_row

    def spy_execute(self, row, code):
        dispatched["executor"].append(str(row.id))
        return original_execute(self, row, code)

    def spy_process_row(self, row, config, org_id=None):
        dispatched["process_row"].append(str(row.id))
        return original_process_row(self, row, config, org_id)

    monkeypatch.setattr(
        dynamic_columns.ExecutePythonCodeView, "_execute_python_code", spy_execute
    )
    monkeypatch.setattr(
        dynamic_columns.ConditionalColumnView, "_process_row", spy_process_row
    )

    model_calls = []

    def no_provider(*args, **kwargs):
        model_calls.append((args, kwargs))
        raise AssertionError("a provider request was attempted")

    monkeypatch.setattr(dynamic_columns, "RunPrompt", no_provider)

    a = _tenant("a")
    b = _tenant("b")
    clients = {"a": _client(a), "b": _client(b)}
    yield {
        "a": a,
        "b": b,
        "clients": clients,
        "queued": queued,
        "dispatched": dispatched,
    }
    for client in clients.values():
        client.stop_workspace_injection()
    clear_workspace_context()
    assert model_calls == []


def _assert_disabled(response):
    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.content
    body = response.json()
    assert body["message"] == DISABLED
    # Non-reflective: the submitted code never comes back.
    assert "diagnosis complete" not in response.content.decode()


def _nothing_queued(env):
    assert env["queued"]["execute_code"].calls == []
    assert env["queued"]["conditional"].calls == []


# ---------------------------------------------------------------------------
# Request boundaries
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_preview_execute_code_is_refused_before_dispatch(r566):
    a = r566["a"]
    before = _state(a)

    response = r566["clients"]["a"].post(
        f"/model-hub/datasets/{a['dataset'].id}/preview/execute_code/",
        {"code": BENIGN_CODE},
        format="json",
    )

    _assert_disabled(response)
    assert r566["dispatched"]["executor"] == []
    _nothing_queued(r566)
    assert _state(a) == before


@pytest.mark.django_db
def test_preview_conditional_with_a_code_branch_is_refused_before_dispatch(r566):
    a = r566["a"]
    before = _state(a)

    response = r566["clients"]["a"].post(
        f"/model-hub/datasets/{a['dataset'].id}/preview/conditional/",
        {"config": CODE_CONDITIONAL},
        format="json",
    )

    _assert_disabled(response)
    assert r566["dispatched"] == {"executor": [], "process_row": []}
    _nothing_queued(r566)
    assert _state(a) == before


@pytest.mark.django_db
def test_preview_conditional_without_code_still_previews(r566):
    """Positive control: text that mentions code is not a code branch."""
    a = r566["a"]

    response = r566["clients"]["a"].post(
        f"/model-hub/datasets/{a['dataset'].id}/preview/conditional/",
        {"config": HARMLESS_CONDITIONAL},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK, response.content
    results = response.json()["result"]["preview_results"]
    assert [r["output"] for r in results] == [json.dumps(HARMLESS_VALUE)] * 3
    assert len(r566["dispatched"]["process_row"]) == 3
    assert r566["dispatched"]["executor"] == []


@pytest.mark.django_db
def test_conditional_column_with_a_code_branch_is_refused_before_writes(r566):
    a = r566["a"]
    before = _state(a)

    response = r566["clients"]["a"].post(
        f"/model-hub/datasets/{a['dataset'].id}/conditional-column/",
        {"new_column_name": "disabled-new-code", "config": CODE_CONDITIONAL},
        format="json",
    )

    _assert_disabled(response)
    assert not Column.all_objects.filter(name="disabled-new-code").exists()
    _nothing_queued(r566)
    assert _state(a) == before


@pytest.mark.django_db
def test_conditional_column_without_code_is_still_created(r566):
    """Positive control: a harmless conditional column is created and queued."""
    a = r566["a"]

    response = r566["clients"]["a"].post(
        f"/model-hub/datasets/{a['dataset'].id}/conditional-column/",
        {"new_column_name": "disabled-new-plain", "config": HARMLESS_CONDITIONAL},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK, response.content
    created = Column.all_objects.get(name="disabled-new-plain", dataset=a["dataset"])
    assert created.metadata["config"] == HARMLESS_CONDITIONAL
    assert len(r566["queued"]["conditional"].calls) == 1
    assert r566["queued"]["conditional"].calls[0][0][0] == HARMLESS_CONDITIONAL
    assert r566["queued"]["execute_code"].calls == []


@pytest.mark.django_db
@pytest.mark.parametrize(
    "target,body",
    [
        pytest.param(
            "python",
            {"operation_type": "execute_code", "config": {"code": BENIGN_CODE}},
            id="python-column-new-code",
        ),
        pytest.param(
            "python", {"operation_type": "execute_code"}, id="python-column-stored"
        ),
        pytest.param(
            "question",
            {
                "operation_type": "execute_code",
                "config": {"code": BENIGN_CODE, "concurrency": 2},
            },
            id="data-column-new-code",
        ),
        pytest.param(
            "cond_code", {"operation_type": "conditional"}, id="conditional-stored"
        ),
        pytest.param(
            "cond_plain",
            {"operation_type": "conditional", "config": {"config": CODE_CONDITIONAL}},
            id="conditional-override",
        ),
    ],
)
def test_rerun_of_code_is_refused_before_any_write(r566, target, body):
    a = r566["a"]
    before = _state(a)

    response = r566["clients"]["a"].post(
        f"/model-hub/columns/{a[target].id}/rerun-operation/", body, format="json"
    )

    _assert_disabled(response)
    _nothing_queued(r566)
    assert _state(a) == before


@pytest.mark.django_db
def test_rerun_of_a_harmless_conditional_still_reruns(r566):
    """Positive control: the stored harmless config re-runs after commit."""
    a = r566["a"]

    with TestCase.captureOnCommitCallbacks(execute=True):
        response = r566["clients"]["a"].post(
            f"/model-hub/columns/{a['cond_plain'].id}/rerun-operation/",
            {"operation_type": "conditional"},
            format="json",
        )
        assert r566["queued"]["conditional"].calls == []
        assert r566["queued"]["execute_code"].calls == []

    assert response.status_code == status.HTTP_200_OK, response.content
    calls = r566["queued"]["conditional"].calls
    assert len(calls) == 1
    assert calls[0][0][0] == HARMLESS_CONDITIONAL
    assert calls[0][0][3] == str(a["cond_plain"].id)
    assert r566["queued"]["execute_code"].calls == []


@pytest.mark.django_db
def test_refusals_of_other_orgs_unknown_ids_and_anonymous_callers_are_kept(r566):
    """Another organization's ids answer not-found, unknown ids too, and an
    anonymous caller is refused; none of them reaches the disabled answer,
    dispatches or changes either organization's data."""
    from conftest import WorkspaceAwareAPIClient

    a, b = r566["a"], r566["b"]
    client_b = r566["clients"]["b"]
    before_a, before_b = _state(a), _state(b)

    foreign = [
        (
            f"/model-hub/columns/{a['python'].id}/rerun-operation/",
            {"operation_type": "execute_code", "config": {"code": BENIGN_CODE}},
        ),
        (
            f"/model-hub/columns/{a['cond_code'].id}/rerun-operation/",
            {"operation_type": "conditional"},
        ),
        (
            f"/model-hub/datasets/{a['dataset'].id}/preview/execute_code/",
            {"code": BENIGN_CODE},
        ),
        (
            f"/model-hub/datasets/{a['dataset'].id}/preview/conditional/",
            {"config": CODE_CONDITIONAL},
        ),
        (
            f"/model-hub/datasets/{a['dataset'].id}/conditional-column/",
            {"new_column_name": "disabled-b-steal", "config": CODE_CONDITIONAL},
        ),
    ]
    for url, body in foreign:
        response = client_b.post(url, body, format="json")
        assert response.status_code == status.HTTP_404_NOT_FOUND, (
            url,
            response.content,
        )

    unknown = [
        (
            f"/model-hub/columns/{uuid.uuid4()}/rerun-operation/",
            {"operation_type": "execute_code", "config": {"code": BENIGN_CODE}},
        ),
        (
            f"/model-hub/datasets/{uuid.uuid4()}/preview/execute_code/",
            {"code": BENIGN_CODE},
        ),
        (
            f"/model-hub/datasets/{uuid.uuid4()}/conditional-column/",
            {"new_column_name": "disabled-unknown", "config": CODE_CONDITIONAL},
        ),
    ]
    for url, body in unknown:
        response = client_b.post(url, body, format="json")
        assert response.status_code == status.HTTP_404_NOT_FOUND, (
            url,
            response.content,
        )

    anonymous = WorkspaceAwareAPIClient()
    for url, body in foreign[:3]:
        response = anonymous.post(url, body, format="json")
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ), (url, response.content)

    assert r566["dispatched"] == {"executor": [], "process_row": []}
    _nothing_queued(r566)
    assert not Column.all_objects.filter(
        name__in=["disabled-b-steal", "disabled-unknown"]
    ).exists()
    assert _state(a) == before_a
    assert _state(b) == before_b


@pytest.mark.django_db
def test_strict_validation_still_answers_before_the_disabled_check(r566):
    a = r566["a"]
    before = _state(a)

    response = r566["clients"]["a"].post(
        f"/model-hub/columns/{a['python'].id}/rerun-operation/",
        {"operation_type": "execute_code", "operationType": "alias"},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["details"]["operationType"] == ["Unknown field."]
    _nothing_queued(r566)
    assert _state(a) == before


@pytest.mark.django_db
def test_existing_code_columns_stay_readable_after_refusals(r566):
    a = r566["a"]
    client = r566["clients"]["a"]
    client.post(
        f"/model-hub/columns/{a['python'].id}/rerun-operation/",
        {"operation_type": "execute_code"},
        format="json",
    )
    client.post(
        f"/model-hub/columns/{a['cond_code'].id}/rerun-operation/",
        {"operation_type": "conditional"},
        format="json",
    )

    table = client.get(
        f"/model-hub/develops/{a['dataset'].id}/get-dataset-table/",
        {"page_size": 10, "current_page_index": 0},
    )
    assert table.status_code == status.HTTP_200_OK, table.content
    rows = sorted(table.json()["result"]["table"], key=lambda r: r["order"])
    for name in ("python", "cond_code"):
        col_id = str(a[name].id)
        assert [r[col_id]["cell_value"] for r in rows] == [
            f"a-{a[name].name}-{i}" for i in range(3)
        ]
        assert [r[col_id]["status"] for r in rows] == [CellStatus.PASS.value] * 3

    config = client.get(f"/model-hub/columns/{a['python'].id}/operation-config/")
    assert config.status_code == status.HTTP_200_OK, config.content
    assert config.json()["result"]["metadata"]["code"] == BENIGN_CODE


# ---------------------------------------------------------------------------
# Worker entry points (queued activity body and direct call)
# ---------------------------------------------------------------------------


def _worker_bodies(name):
    """The activity body of `name` in both modules that define it, and the
    one the drop-in registry hands a worker (the last module imported wins:
    the worker imports dynamic_columns, then develop_dataset)."""
    from model_hub.views import develop_dataset, dynamic_columns
    from tfc.temporal.drop_in.decorator import get_activity_by_name

    bodies = {
        "dynamic_columns": getattr(dynamic_columns, name)._original_func,
        "develop_dataset": getattr(develop_dataset, name)._original_func,
    }
    registered = get_activity_by_name(name)["func"]
    assert registered in bodies.values()
    bodies["registry"] = registered
    return bodies


def _call(body, first, tenant, column_key):
    args = [first, tenant["dataset"].id, 2, str(tenant[column_key].id)]
    if "is_rerun" in inspect.signature(body).parameters:
        args.append(True)
    try:
        body(*args)
    except Exception as exc:  # noqa: BLE001 - the test asserts on it
        return exc
    return None


def _assert_terminal_refusal(exc):
    from temporalio.exceptions import ApplicationError

    assert isinstance(exc, ApplicationError), repr(exc)
    assert exc.non_retryable is True
    assert exc.type == "CodeExecutionDisabled"
    assert exc.message == DISABLED


@pytest.mark.django_db
@pytest.mark.parametrize("module", ["dynamic_columns", "develop_dataset", "registry"])
def test_python_code_worker_refuses_before_touching_data(r566, module):
    from tfc.middleware.workspace_context import clear_workspace_context

    a = r566["a"]
    body = _worker_bodies("execute_python_code_async")[module]
    before = _state(a)

    clear_workspace_context()
    exc = _call(body, BENIGN_CODE, a, "python")

    assert _state(a) == before
    _assert_terminal_refusal(exc)
    assert r566["dispatched"]["executor"] == []


@pytest.mark.django_db
@pytest.mark.parametrize("module", ["dynamic_columns", "develop_dataset", "registry"])
def test_code_conditional_worker_refuses_before_touching_data(r566, module):
    from tfc.middleware.workspace_context import clear_workspace_context

    a = r566["a"]
    body = _worker_bodies("conditional_column_async")[module]
    before = _state(a)

    clear_workspace_context()
    exc = _call(body, CODE_CONDITIONAL, a, "cond_code")

    assert _state(a) == before
    _assert_terminal_refusal(exc)
    assert r566["dispatched"]["executor"] == []


@pytest.mark.django_db
def test_harmless_conditional_worker_still_runs(r566):
    """Positive control: a non-code conditional re-run fills its cells."""
    from tfc.middleware.workspace_context import clear_workspace_context

    a = r566["a"]
    body = _worker_bodies("conditional_column_async")["dynamic_columns"]

    clear_workspace_context()
    exc = _call(body, HARMLESS_CONDITIONAL, a, "cond_plain")

    assert exc is None, repr(exc)
    cells = Cell.all_objects.filter(column=a["cond_plain"]).order_by("row__order")
    assert [(c.value, c.status) for c in cells] == [
        (HARMLESS_VALUE, CellStatus.PASS.value)
    ] * 3
    column = Column.all_objects.get(id=a["cond_plain"].id)
    assert column.status == StatusType.COMPLETED.value
    assert r566["dispatched"]["executor"] == []
