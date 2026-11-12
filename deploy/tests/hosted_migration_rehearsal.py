"""Explicit cloud-only forward rehearsal on newly created loopback databases.

Run from futureagi with its venv and test settings. Never accepts a DSN or a
real database name. Does not rehearse, authorize or perform credential rotation.
"""

import importlib
import json
import os
import signal
import time
import uuid
from unittest.mock import patch


def main():
    if os.environ.get("G18_SYNTHETIC_REHEARSAL") != "1" or os.environ.get("PG_HOST") not in ("localhost", "127.0.0.1"):
        raise RuntimeError("Synthetic loopback-only rehearsal must be explicitly selected")
    signal.alarm(480)
    import django
    import psycopg
    from cryptography.fernet import Fernet
    from psycopg import sql

    stable = Fernet.generate_key().decode()
    os.environ["INTEGRATION_ENCRYPTION_KEY"] = stable
    django.setup()
    from django.conf import settings
    from django.db import connections
    from django.db.migrations.executor import MigrationExecutor
    from integrations.services.credentials import CredentialManager

    credentials = {"host": os.environ["PG_HOST"], "port": os.environ["PG_PORT"],
                   "user": os.environ["PG_USER"], "password": os.environ["PG_PASSWORD"], "connect_timeout": 2}
    db = "g18_rehearsal_" + uuid.uuid4().hex
    admin = psycopg.connect(dbname="postgres", autocommit=True, **credentials)
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db)))
    connection = connections["default"]
    connection.close()
    connection.settings_dict["NAME"] = db
    connection.settings_dict["OPTIONS"] = {"options": "-c lock_timeout=1000 -c statement_timeout=30000", "connect_timeout": 2}
    receipt = {"scope": "synthetic-loopback-only", "sourceSha": os.environ.get("OMEGA_EXEC_SOURCE_HEAD"), "databaseDropped": False}
    try:
        def migrate(name):
            executor = MigrationExecutor(connection)
            executor.migrate([("prism", name)])
            return MigrationExecutor(connection)

        old = migrate("0029_org_config_managed_models")
        old_apps = old.loader.project_state([("prism", "0029_org_config_managed_models")]).apps
        log_table = old_apps.get_model("prism", "AgentccRequestLog")._meta.db_table
        holder = psycopg.connect(dbname=db, **credentials)
        try:
            holder.execute(sql.SQL("LOCK TABLE {} IN ROW EXCLUSIVE MODE").format(sql.Identifier(log_table)))
            started = time.monotonic()
            try:
                migrate("0030_request_log_v2")
            except Exception:
                receipt["lockRefusalSeconds"] = round(time.monotonic() - started, 3)
            else:
                raise AssertionError("0030 unexpectedly completed while a conflicting lock was held")
        finally:
            holder.rollback()
            holder.close()
        assert receipt["lockRefusalSeconds"] < 5
        started = time.monotonic()
        migrate("0030_request_log_v2")
        receipt["uncontended0030Seconds"] = round(time.monotonic() - started, 3)
        executor = migrate("0046_org_config_db_defaults")
        apps = executor.loader.project_state([("prism", "0046_org_config_db_defaults")]).apps
        Org = apps.get_model("accounts", "Organization")
        Config = apps.get_model("prism", "AgentccOrgConfig")
        org = Org.objects.create(name="Synthetic hosted rehearsal")
        for version in range(1, 5):
            Config.objects.create(organization_id=org.pk, version=version, is_active=False,
                                  cache={"redis": {"password": "synthetic-clear"}},
                                  alerting={"channels": [{"type": "slack", "url": "https://sink.invalid/opaque-token"}]})
        with connection.cursor() as cursor:
            cursor.execute("SELECT column_name, column_default FROM information_schema.columns WHERE table_name = %s AND column_name IN ('identity','managed_models','tools')", [Config._meta.db_table])
            defaults = dict(cursor.fetchall())
        assert len(defaults) == 3 and all(defaults.values())
        receipt["dbDefaults0046"] = True
        migration = importlib.import_module("agentcc.migrations.0047_encrypt_org_config_secrets")
        transform = migration._transform_row
        visited = 0
        def interrupted(row, fn):
            nonlocal visited
            visited += 1
            if visited == 3:
                raise RuntimeError("synthetic interruption")
            return transform(row, fn)
        with patch.object(migration, "BATCH_SIZE", 1), patch.object(migration, "_transform_row", interrupted):
            try:
                with connection.schema_editor(atomic=False) as editor:
                    migration.forwards(apps, editor)
            except RuntimeError:
                pass
            else:
                raise AssertionError("interruption was not exercised")
        prior = {str(row.pk): row.cache["redis"]["password"] for row in Config.objects.all()
                 if row.cache["redis"]["password"].startswith("enc::")}
        assert len(prior) == 2
        executor = migrate("0051_encrypt_alert_channel_urls")
        from agentcc.hosted_migrations import RELEASE_BOUNDARY
        assert [node for node in executor.loader.graph.leaf_nodes() if node[0] == "prism"] == [RELEASE_BOUNDARY]
        assert ("prism", "0051_encrypt_alert_channel_urls") in executor.loader.graph.forwards_plan(RELEASE_BOUNDARY)
        for row in Config.objects.all():
            encrypted = row.cache["redis"]["password"]
            assert CredentialManager.decrypt(encrypted[5:].encode())["v"] == "synthetic-clear"
            assert row.alerting["channels"][0]["url"].startswith("enc::")
            if str(row.pk) in prior:
                assert encrypted == prior[str(row.pk)]
        before = list(Config.objects.order_by("pk").values_list("cache", "alerting"))
        migrate("0051_encrypt_alert_channel_urls")
        assert before == list(Config.objects.order_by("pk").values_list("cache", "alerting"))
        receipt.update(interruptedBackfillRecovery=True, idempotent=True, through="0051_encrypt_alert_channel_urls")
        blob = CredentialManager.encrypt({"probe": "synthetic"})
        assert CredentialManager.decrypt(blob)["probe"] == "synthetic"
        with patch.object(settings, "INTEGRATION_ENCRYPTION_KEY", Fernet.generate_key().decode()):
            try:
                CredentialManager.decrypt(blob)
            except ValueError:
                receipt["rotationSupported"] = False
            else:
                raise AssertionError("single-key transition unexpectedly succeeded")
        assert CredentialManager.decrypt(blob)["probe"] == "synthetic"
        receipt["rotationGate"] = "OWED independently qualified transition tooling and ops receipt"
    finally:
        connection.close()
        admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(db)))
        admin.close()
        receipt["databaseDropped"] = True
    print(json.dumps(receipt, sort_keys=True))


if __name__ == "__main__":
    main()
