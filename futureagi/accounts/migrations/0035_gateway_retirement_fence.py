"""Fence every non-retirement gateway UPDATE, including already-revoked saves."""

from django.db import migrations

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
