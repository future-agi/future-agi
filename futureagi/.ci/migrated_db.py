"""Creates pytest's test database by migrating from zero, and does nothing else.

Backend CI runs this file on its own:

    uv run pytest .ci/migrated_db.py -q

- in a pytest shard that found no database dump for its checkout, before the
  shard's measured run, so no test's recorded time includes the migrate;
- in the from-zero job, which then dumps the result (db_dump.py).

pytest-django creates and migrates only the databases a collected test asks
for with the django_db mark, so the one case carries it. The plain mark runs
the case inside a transaction that is rolled back; it truncates nothing (only
transaction=True does). The case reads one count, so a run that created no
database fails here instead of passing in a tenth of a second. --reuse-db
(pytest.ini) keeps the database for the next pytest run.
"""

import pytest
from django.db import connection


@pytest.mark.django_db
def test_the_test_database_is_migrated_from_zero():
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM django_migrations")
        assert cursor.fetchone()[0] > 0
