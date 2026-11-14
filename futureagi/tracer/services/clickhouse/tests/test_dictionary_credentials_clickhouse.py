"""The dictionary credentials fingerprint against a real ClickHouse.

bootstrap_cdc re-creates a packaged dictionary whose live
``create_table_query`` lacks the fingerprint COMMENT of the current
credentials, reads it again, and fails the bootstrap ("dictionary source
credentials not visible after re-create") if it is still stale. That converges
only if the server hands the COMMENT back unchanged and hides the password,
which the service-free doubles in test_dictionary_credentials.py assume.

Skips without the test ClickHouse (see conftest's ``_ch_test_owned_database``).
"""

from __future__ import annotations

import pytest

from conftest import _ch_test_owned_database, _open_ch_test_http_client
from tracer.services.clickhouse import oss_cdc_bootstrap as core
from tracer.services.clickhouse.v2.apply_schema_rewriter import (
    dictionary_credentials_outdated,
)

pytestmark = pytest.mark.integration

USER = "app"
# A single quote, a backslash and a double quote: every escaping hazard.
PASSWORD = "p'a\\ss\"word"
DICTIONARIES = tuple(
    name for name in core.DEPENDENT if name not in core._VIEW_PREREQUISITES
)


@pytest.fixture
def ch():
    # Creating a dictionary loads nothing, so its source tables need not exist.
    with _ch_test_owned_database("test_dictionary_credentials_") as database:
        client = _open_ch_test_http_client(database=database)
        try:
            yield client, core._definitions(database)[0]
        finally:
            client.close()


def _live(client) -> dict[str, str]:
    rows = client.query(
        "SELECT name, create_table_query FROM system.tables "
        "WHERE database = currentDatabase() AND engine = 'Dictionary'"
    ).result_rows
    return dict(rows)


def _stale(client, create, *, user=USER, password=PASSWORD):
    return core._stale_dependent_dictionaries(
        client, create, ch_user=user, ch_password=password
    )


def test_the_fingerprint_comes_back_and_the_password_does_not(ch):
    client, create = ch
    for name in DICTIONARIES:
        client.command(core.with_dictionary_credentials(create[name], USER, PASSWORD))

    live = _live(client)
    assert sorted(live) == sorted(DICTIONARIES)
    for name, sql in live.items():
        assert not dictionary_credentials_outdated(sql, USER, PASSWORD), sql
        assert dictionary_credentials_outdated(sql, USER, "rotated"), sql
        assert dictionary_credentials_outdated(sql, "someone_else", PASSWORD), sql
        assert PASSWORD not in sql and "p\\'a" not in sql, name


def test_the_bootstrap_repair_converges_across_a_rotation(ch):
    client, create = ch
    # Created before credentials were injected: the packaged text as is.
    for name in DICTIONARIES:
        client.command(create[name])
    assert _stale(client, create) == DICTIONARIES

    repaired = core._update_dictionary_credentials(
        client, create, ch_user=USER, ch_password=PASSWORD
    )
    assert repaired == DICTIONARIES
    assert _stale(client, create) == ()

    # The password rotates: every dictionary is re-created once, then settles.
    assert _stale(client, create, password="rotated") == DICTIONARIES
    repaired = core._update_dictionary_credentials(
        client, create, ch_user=USER, ch_password="rotated"
    )
    assert repaired == DICTIONARIES
    assert _stale(client, create, password="rotated") == ()
    assert (
        core._update_dictionary_credentials(
            client, create, ch_user=USER, ch_password="rotated"
        )
        == ()
    )
