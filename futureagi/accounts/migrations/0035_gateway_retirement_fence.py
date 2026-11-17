"""Fence every non-retirement gateway UPDATE, including already-revoked saves."""

from django.db import migrations

UPDATE_SAFETY = {
    "schema_version": 1,
    "risk_classes": ["data_migration"],
    "tables": [
        "postgres:agentcc_api_key",
        "postgres:accounts_retirement_manifest",
    ],
    "mitigation": (
        "The migration runs in one transaction (atomic, the default): CREATE FUNCTION "
        "and CREATE TRIGGER either both apply or neither does, so a failure leaves "
        "nothing behind and the same migration can simply be re-run. It reads and "
        "changes no rows. CREATE TRIGGER takes a SHARE ROW EXCLUSIVE lock on "
        "agentcc_api_key while it runs. It waits behind transactions already writing "
        "keys, and under the job's lock_timeout it fails cleanly, and re-runnably, "
        "instead of queueing key writes behind it; run it when no long key-table "
        "transaction is open. If the function or trigger already exists without the "
        "migration being recorded (for example, a hand-run), the re-run stops on "
        '"already exists": run the reverse statements (DROP TRIGGER IF EXISTS '
        "accounts_gateway_retirement_fence ON agentcc_api_key; DROP FUNCTION IF "
        "EXISTS accounts_gateway_retirement_fence();), then re-run. The reverse "
        "migration runs the same two statements."
    ),
    "reviewer": "",
    "review": "",
    "large_volume_rehearsal": True,
}

FORWARD = """
CREATE FUNCTION accounts_gateway_retirement_fence() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF current_setting('futureagi.retirement_733', true) IS DISTINCT FROM 'on' THEN
        UPDATE accounts_retirement_manifest
        SET superseded_by = 'customer_changed'
        WHERE service = 'gateway' AND object_id = NEW.id::text
          AND reverted_at IS NULL AND superseded_by = '';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER accounts_gateway_retirement_fence
AFTER UPDATE ON agentcc_api_key
FOR EACH ROW EXECUTE FUNCTION accounts_gateway_retirement_fence();
"""
REVERSE = """
DROP TRIGGER IF EXISTS accounts_gateway_retirement_fence ON agentcc_api_key;
DROP FUNCTION IF EXISTS accounts_gateway_retirement_fence();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0034_retirement_manifest"),
        ("prism", "0024_rename_prism_to_agentcc"),
    ]
    operations = [migrations.RunSQL(FORWARD, REVERSE)]
