"""Creates pytest's test database by migrating from zero, and does nothing else.

Backend CI runs this file on its own:

    uv run pytest .ci/migrated_db.py -q

- in a pytest shard that found no database dump for its checkout, before the
  shard's measured run, so no test's recorded time includes the migrate;
- in the from-zero job, which then dumps the result (db_dump.py).

The one case asks pytest-django for the database setup only. It opens no
transaction and truncates nothing, so the database it leaves is exactly what
`migrate` and post_migrate produced. --reuse-db (pytest.ini) keeps it.
"""


def test_the_test_database_is_migrated_from_zero(django_db_setup):
    pass
