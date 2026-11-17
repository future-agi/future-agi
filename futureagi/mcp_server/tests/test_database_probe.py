"""A native connection for tests that must reach past Django's cursor.

Some migration tests open a second psycopg connection, as a concurrent request
or a stopped process would, and send SQL on it that Django's guarded cursor
never sees. ``test_database_connection`` hands out such a connection only when
it is opened to the test runner's own database, and checks that by name before
the caller sends any statement.
"""

import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.db import connection

from accounts.tests.api_secret_migration_probes import probe_connection


def test_database_connection(**kwargs):
    """``probe_connection(**kwargs)``, once it is known to reach the test database.

    The connection's database name must equal the Django test database name;
    otherwise the connection is closed and refused.
    """
    probe = probe_connection(**kwargs)
    expected = connection.settings_dict["NAME"]
    if probe.info.dbname != expected:
        probe.close()
        raise AssertionError(
            f"a native test connection reached the database {probe.info.dbname!r},"
            f" not the test database {expected!r}"
        )
    return probe


# A helper, not a test: pytest would otherwise collect it by its name.
test_database_connection.__test__ = False


def test_the_helper_refuses_a_connection_to_another_database(monkeypatch):
    test_name = connection.settings_dict["NAME"]
    names = [f"{test_name}_other", test_name]
    opened = []

    def fake(**kwargs):
        probe = Mock(info=SimpleNamespace(dbname=names.pop(0)))
        opened.append((kwargs, probe))
        return probe

    monkeypatch.setattr(sys.modules[__name__], "probe_connection", fake)

    with pytest.raises(AssertionError, match="not the test database"):
        test_database_connection(lock_timeout="5s")
    ((kwargs, refused),) = opened
    assert kwargs == {"lock_timeout": "5s"}
    refused.close.assert_called_once_with()
    refused.execute.assert_not_called()

    # The control: a connection to the test database itself is handed back open.
    returned = test_database_connection()
    assert returned is opened[-1][1]
    returned.close.assert_not_called()
