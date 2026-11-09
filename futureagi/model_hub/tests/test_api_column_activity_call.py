"""New API columns must call the canonical transport with the dataset scope.

DB-free: one in-memory row, mocked ORM/HTTP, and no outbound sockets. Keep the
real legacy delegate, canonical transport and thread executor so the positional
argument regression cannot be hidden by a permissive transport mock.
"""

import json
import socket
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import requests

from model_hub.models.choices import CellStatus, StatusType
from model_hub.models.develop_dataset import Cell, Dataset, Row
from model_hub.tests.rerun_fixtures import generation_orm
from model_hub.views import develop_dataset, dynamic_columns


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise OSError("network refused in API-column activity test")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


@pytest.mark.parametrize("location", ["headers", "params"])
@pytest.mark.parametrize(
    "secret_allowed,http_status",
    [(True, 200), (False, 200), (True, 503)],
    ids=["own", "foreign", "upstream-error"],
)
def test_new_api_column_fills_its_cell_with_dataset_scope(
    monkeypatch, location, secret_allowed, http_status
):
    dataset = Dataset(organization_id="own-org", workspace_id="own-workspace")
    row = Row(dataset=dataset)
    written = []
    # Real unsaved cells retain the model's defaults without touching the DB.
    cells = Mock(side_effect=Cell)
    cells.objects.bulk_create.side_effect = lambda values, **kwargs: written.extend(
        values
    )

    class _Rows(list):
        def order_by(self, *fields):
            return self

    rows = Mock()
    rows.objects.filter.return_value = _Rows([row])
    datasets = Mock()
    datasets.no_workspace_objects.get.return_value = dataset
    datasets.objects.get.return_value = dataset
    datasets.objects.filter.return_value.values_list.return_value.first.return_value = (
        dataset.organization_id
    )
    column = Mock()
    logger = Mock()
    # develop_dataset's copy delegates to dynamic_columns' implementation (T6
    # #1b), so both modules' ORM names are replaced by the same fakes.
    for module in (develop_dataset, dynamic_columns):
        for name, replacement in (
            ("Cell", cells),
            ("Row", rows),
            ("Dataset", datasets),
            ("Column", column),
            ("logger", logger),
        ):
            monkeypatch.setattr(module, name, replacement)

    secret = SimpleNamespace(
        id="secret",
        organization_id="own-org" if secret_allowed else "other-org",
        deleted=False,
        actual_key="fixture-credential",
    )

    def get_secret(**scope):
        if all(getattr(secret, key) == value for key, value in scope.items()):
            return secret
        raise dynamic_columns.SecretModel.DoesNotExist()

    secrets = Mock()
    secrets.filter.return_value.get.side_effect = get_secret
    monkeypatch.setattr(dynamic_columns.SecretModel, "no_workspace_objects", secrets)
    workspaces = Mock()
    workspaces.filter.return_value.exists.return_value = False
    monkeypatch.setattr(dynamic_columns.Workspace, "no_workspace_objects", workspaces)
    response = requests.Response()
    response.status_code = http_status
    response._content = b"pong"
    response.encoding = "utf-8"
    transport = Mock(return_value=response)
    monkeypatch.setattr(dynamic_columns.outbound_destination, "request", transport)
    config = {
        "url": "https://api.fixture.test/lookup",
        "method": "GET",
        "output_type": "string",
        location: {"X-Fixture": {"type": "Secret", "value": secret.id}},
    }

    tracked_column = SimpleNamespace(
        id="new-column", dataset_id=dataset.id, name="API output",
        source="api_call", metadata=config, deleted=False,
    )
    canonical = dynamic_columns.AddApiColumnView._make_api_call
    with (
        generation_orm(
            column=tracked_column, rows=lambda: [row], cells=lambda: written,
            cell_factory=cells,
            on_column_update=column.objects.filter.return_value.update,
        ) as snapshots,
        patch.object(
            dynamic_columns.AddApiColumnView,
            "_make_api_call",
            autospec=True,
            side_effect=canonical,
        ) as call,
    ):
        develop_dataset.add_api_column_async._original_func(
            config, dataset.id, 1, "new-column"
        )

    # The broken activity swallows the TypeError and marks the column completed
    # without writing anything; include its log in the regression failure.
    assert len(written) == 1, logger.error.call_args_list
    [cell] = written
    datasets.no_workspace_objects.get.assert_called_once_with(id=dataset.id)
    assert isinstance(cell, Cell)
    call.assert_called_once()
    _, submitted, sent_config = call.call_args.args
    assert sent_config == config
    assert call.call_args.kwargs == {"dataset": dataset}
    assert call.call_args.kwargs["dataset"] is dataset
    assert submitted.row is row
    assert submitted.dataset_id == dataset.id
    assert submitted.column_id == "new-column"
    assert cell.row is row
    assert cell.dataset_id == dataset.id
    assert cell.column_id == "new-column"
    secrets.filter.assert_called_once_with(
        dynamic_columns.Q(workspace_id=dataset.workspace_id)
    )
    secrets.filter.return_value.get.assert_called_once_with(
        id=secret.id, organization_id=dataset.organization_id, deleted=False
    )
    if secret_allowed:
        transport.assert_called_once()
        assert transport.call_args.kwargs[location] == {"X-Fixture": secret.actual_key}
        if http_status < 400:
            assert cell.value == "pong"
            assert cell.status == CellStatus.PASS.value
            assert json.loads(cell.value_infos) == {"response_status": http_status}
        else:
            assert cell.value == "upstream request failed"
            assert cell.status == CellStatus.ERROR.value
            assert json.loads(cell.value_infos) == {"response_status": 400}
    else:
        transport.assert_not_called()
        assert cell.value == dynamic_columns.outbound_destination.PUBLIC_ERROR
        assert cell.status == CellStatus.ERROR.value
        assert json.loads(cell.value_infos) == {"response_status": 400}
    column.objects.filter.return_value.update.assert_called_with(
        status=StatusType.COMPLETED.value
    )
    logger.error.assert_not_called()
    assert set(snapshots) == {("new-column", str(row.id))}
    assert snapshots[("new-column", str(row.id))].fingerprint.startswith("sha256:")
    assert tracked_column.status == StatusType.COMPLETED.value
