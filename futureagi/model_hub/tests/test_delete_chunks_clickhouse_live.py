"""Live ClickHouse tests for ``EmbeddingManager.delete_chunks``.

``delete_chunks`` removes one knowledge-base file's chunks, scoped by the
knowledge base (``eval_id``), the ``file_id`` metadata value and the
``organization_id`` metadata value. These tests run the real ``ALTER TABLE
... DELETE`` against a disposable ClickHouse database and check which rows
survive:

* normal ids delete exactly the matching rows and nothing in a sibling file,
  knowledge base or organization;
* ids carrying quotes, backslashes or SQL fragments are compared as data, so
  they can never widen the delete to other rows or tenants;
* empty ids and a missing table are no-ops.

The vector client is pinned to ``mutations_sync=2`` so each delete has
settled before the test reads the table back.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from agentic_eval.core.database import ch_vector
from agentic_eval.core.embeddings import embedding_manager
from conftest import _open_ch_test_native_client

TABLE = "kb_chunks"


@pytest.fixture
def live_kb():
    """An EmbeddingManager and an admin client on one owned test database."""

    database = f"test_delete_chunks_{uuid.uuid4().hex}"
    admin = _open_ch_test_native_client(owned_database=database)
    admin.execute(f"CREATE DATABASE {database}")
    host, port = admin.connection.hosts[0]
    client_kwargs = {
        "host": host,
        "port": port,
        "user": admin.connection.user,
        "password": admin.connection.password,
        "database": database,
        "settings": {"mutations_sync": 2},
    }
    patches = pytest.MonkeyPatch()
    patches.setattr(
        ch_vector, "get_clickhouse_client_kwargs", lambda: dict(client_kwargs)
    )
    patches.setenv("CH_DATABASE", database)
    patches.setattr(
        embedding_manager,
        "model_manager",
        SimpleNamespace(_use_serving=True, text_model=lambda text: [1.0, 0.0]),
    )
    patches.setattr(embedding_manager, "serving_available", lambda: True)
    manager = embedding_manager.EmbeddingManager()
    try:
        manager.db_client.create_table(TABLE, database=database)
        yield SimpleNamespace(manager=manager, admin=admin, table=f"{database}.{TABLE}")
    finally:
        manager.db_client.close()
        patches.undo()
        admin.execute(f"DROP DATABASE {database} SYNC")
        admin.disconnect()


def _insert(kb, *, eval_id: str, file_id: str, organization_id: str) -> str:
    row_id = str(uuid.uuid4())
    kb.admin.execute(
        f"INSERT INTO {kb.table} (id, eval_id, vector, metadata.key, metadata.value) VALUES",
        [
            (
                uuid.UUID(row_id),
                uuid.UUID(eval_id),
                [1.0, 0.0],
                ["file_id", "organization_id"],
                [file_id, organization_id],
            )
        ],
    )
    return row_id


def _surviving_ids(kb) -> set[str]:
    return {str(row[0]) for row in kb.admin.execute(f"SELECT id FROM {kb.table}")}


@pytest.fixture
def tenants(live_kb):
    """Two organizations, two knowledge bases and two files, one row each.

    ``target`` holds two chunks so the positive control proves every chunk of
    the file goes, not just one.
    """

    kb_a, kb_b = str(uuid.uuid4()), str(uuid.uuid4())
    org_1, org_2 = str(uuid.uuid4()), str(uuid.uuid4())
    file_1, file_2 = str(uuid.uuid4()), str(uuid.uuid4())

    def row(eval_id, file_id, organization_id):
        return _insert(
            live_kb, eval_id=eval_id, file_id=file_id, organization_id=organization_id
        )

    rows = SimpleNamespace(
        kb_a=kb_a,
        kb_b=kb_b,
        org_1=org_1,
        org_2=org_2,
        file_1=file_1,
        file_2=file_2,
        target={row(kb_a, file_1, org_1), row(kb_a, file_1, org_1)},
        same_kb_other_file=row(kb_a, file_2, org_1),
        same_file_other_org=row(kb_a, file_1, org_2),
        other_kb_same_file=row(kb_b, file_1, org_1),
        other_org_other_kb=row(kb_b, file_2, org_2),
    )
    rows.others = {
        rows.same_kb_other_file,
        rows.same_file_other_org,
        rows.other_kb_same_file,
        rows.other_org_other_kb,
    }
    rows.all = rows.target | rows.others
    assert _surviving_ids(live_kb) == rows.all
    return rows


def test_delete_chunks_removes_exactly_the_matching_file_chunks(live_kb, tenants):
    live_kb.manager.delete_chunks(
        file_id=tenants.file_1,
        kb_id=tenants.kb_a,
        table_name=TABLE,
        organization_id=tenants.org_1,
    )

    assert _surviving_ids(live_kb) == tenants.others


def test_delete_chunks_accepts_uuid_objects(live_kb, tenants):
    live_kb.manager.delete_chunks(
        file_id=uuid.UUID(tenants.file_1),
        kb_id=uuid.UUID(tenants.kb_a),
        table_name=TABLE,
        organization_id=uuid.UUID(tenants.org_1),
    )

    assert _surviving_ids(live_kb) == tenants.others


@pytest.mark.parametrize(
    "field, suffix",
    [
        ("file_id", "' OR '1'='1"),
        ("file_id", "\\' OR '1'='1"),
        ("file_id", "' OR 1=1 OR 'a'='a"),
        ("organization_id", "' OR '1'='1"),
        ("organization_id", "' OR 1=1 --"),
        ("kb_id", "' OR '1'='1"),
    ],
)
def test_delete_chunks_treats_sql_in_ids_as_data(live_kb, tenants, field, suffix):
    ids = {
        "file_id": tenants.file_1,
        "kb_id": tenants.kb_a,
        "organization_id": tenants.org_1,
    }
    ids[field] += suffix

    # The call returns normally: a rejected id never surfaces a database
    # error (or the SQL around it) to the caller.
    assert live_kb.manager.delete_chunks(table_name=TABLE, **ids) is None

    # No stored id carries the suffix, so nothing matches and nothing goes:
    # not the target file, and above all no other file, KB or organization.
    assert _surviving_ids(live_kb) == tenants.all


def test_delete_chunks_matches_quoted_ids_literally(live_kb, tenants):
    """An id that contains quote characters is matched as that exact string."""

    quoted_file = "report'; DROP TABLE kb_chunks; --.pdf"
    quoted_row = _insert(
        live_kb,
        eval_id=tenants.kb_a,
        file_id=quoted_file,
        organization_id=tenants.org_1,
    )

    live_kb.manager.delete_chunks(
        file_id=quoted_file,
        kb_id=tenants.kb_a,
        table_name=TABLE,
        organization_id=tenants.org_1,
    )

    survivors = _surviving_ids(live_kb)
    assert quoted_row not in survivors
    assert survivors == tenants.all


@pytest.mark.parametrize("field", ["file_id", "organization_id"])
def test_delete_chunks_with_an_empty_id_deletes_nothing(live_kb, tenants, field):
    ids = {
        "file_id": tenants.file_1,
        "kb_id": tenants.kb_a,
        "organization_id": tenants.org_1,
    }
    ids[field] = ""

    live_kb.manager.delete_chunks(table_name=TABLE, **ids)

    assert _surviving_ids(live_kb) == tenants.all


def test_delete_chunks_on_a_missing_table_is_a_no_op(live_kb, tenants):
    live_kb.manager.delete_chunks(
        file_id=tenants.file_1,
        kb_id=tenants.kb_a,
        table_name="kb_chunks_missing",
        organization_id=tenants.org_1,
    )

    assert _surviving_ids(live_kb) == tenants.all
