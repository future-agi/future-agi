"""An org config INSERT that omits the newer columns saves.

prism 0029 and 0045 add ``managed_models``, ``tools`` and ``identity`` to
``agentcc_org_config`` as jsonb NOT NULL. Django fills an existing table's
rows with a temporary default and then drops it, so the columns end with no
database default. During a rolling deploy (and after a code rollback) old pods
still run a model without these fields: their INSERT omits the three columns,
and without a database default it fails the NOT NULL constraint. prism 0046
gives each column a database default of ``{}``.
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from agentcc.models.org_config import AgentccOrgConfig

NEW_COLUMNS = ("managed_models", "tools", "identity")


def _insert_like_an_old_pod(organization):
    """INSERT a config row the way a pod whose model predates 0029 does.

    The ORM's own insert path (``Model._do_insert``) with every concrete field
    except the three new ones, so the statement names only the old columns.
    """
    row = AgentccOrgConfig(
        organization=organization, version=1, change_description="old pod"
    )
    fields = [
        field
        for field in AgentccOrgConfig._meta.local_concrete_fields
        if field.column not in NEW_COLUMNS
    ]
    with CaptureQueriesContext(connection) as queries:
        AgentccOrgConfig._base_manager._insert([row], fields=fields, using="default")
    inserts = [
        q["sql"] for q in queries if q["sql"].lstrip().upper().startswith("INSERT")
    ]
    return row.pk, inserts


@pytest.mark.integration
class TestOrgConfigDatabaseDefaults:
    def test_an_insert_without_the_new_columns_gets_empty_sections(self, organization):
        pk, inserts = _insert_like_an_old_pod(organization)

        # The statement really leaves the new columns out; otherwise this
        # test would only prove the ORM's Python default.
        assert len(inserts) == 1, inserts
        for column in NEW_COLUMNS:
            assert f'"{column}"' not in inserts[0], inserts[0]
        stored = AgentccOrgConfig.no_workspace_objects.get(pk=pk)
        assert stored.managed_models == {}
        assert stored.tools == {}
        assert stored.identity == {}

    def test_each_new_column_is_not_null_with_an_empty_object_default(self, db):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT column_name, is_nullable, column_default "
                "FROM information_schema.columns "
                "WHERE table_schema = current_schema() "
                "AND table_name = 'agentcc_org_config' "
                "AND column_name = ANY(%s)",
                [list(NEW_COLUMNS)],
            )
            columns = {name: (nullable, default) for name, nullable, default in cursor}

        assert columns == dict.fromkeys(NEW_COLUMNS, ("NO", "'{}'::jsonb")), columns
