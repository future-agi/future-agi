"""Driver-state controls complement the real PostgreSQL disconnect tests."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import psycopg
import pytest
from django.db import InterfaceError, OperationalError
from psycopg.errors import InvalidPassword, QueryCanceled, SyntaxError

from model_hub.utils.eval_metadata import _read_metadata

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "wrapper,driver",
    [
        (OperationalError, psycopg.OperationalError),
        (InterfaceError, psycopg.InterfaceError),
    ],
)
def test_broken_driver_connection_without_sqlstate_recovers(wrapper, driver):
    error = wrapper("transport read failed")
    error.__cause__ = driver("transport read failed")
    db = SimpleNamespace(
        connection=SimpleNamespace(broken=True),
        in_atomic_block=False,
        close=MagicMock(),
    )
    read = MagicMock(side_effect=[error, "metadata"])
    with (
        patch("model_hub.utils.eval_metadata.connections", {"metadata": db}),
        patch("model_hub.utils.eval_metadata.time.sleep") as sleep,
    ):
        assert _read_metadata(read, using="metadata") == "metadata"
    assert read.call_count == 2
    db.close.assert_called_once()
    sleep.assert_called_once_with(0.25)


@pytest.mark.parametrize(
    "driver",
    [
        InvalidPassword("authentication failed"),
        QueryCanceled("statement timeout"),
        SyntaxError("invalid SQL"),
        psycopg.OperationalError("password authentication failed"),
    ],
)
def test_deterministic_error_takes_precedence_over_broken_state(driver):
    error = OperationalError(str(driver))
    error.__cause__ = driver
    db = SimpleNamespace(
        connection=SimpleNamespace(broken=True),
        in_atomic_block=False,
        close=MagicMock(),
    )
    read = MagicMock(side_effect=error)
    with patch("model_hub.utils.eval_metadata.connections", {"metadata": db}):
        with pytest.raises(OperationalError) as caught:
            _read_metadata(read, using="metadata")
    assert caught.value is error
    read.assert_called_once()
    db.close.assert_not_called()


def test_unknown_operational_error_is_not_retried():
    error = OperationalError("unknown database error")
    error.__cause__ = psycopg.OperationalError(str(error))
    db = SimpleNamespace(
        connection=SimpleNamespace(broken=False),
        in_atomic_block=False,
        close=MagicMock(),
    )
    read = MagicMock(side_effect=error)
    with patch("model_hub.utils.eval_metadata.connections", {"metadata": db}):
        with pytest.raises(OperationalError) as caught:
            _read_metadata(read, using="metadata")
    assert caught.value is error
    read.assert_called_once()
    db.close.assert_not_called()
