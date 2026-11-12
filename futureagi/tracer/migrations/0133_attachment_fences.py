"""Database fences also cover QuerySet.update and concurrent service workers.

Rollback: disable attachment admission, drain/abort transfers, erase private
objects via the deletion outbox, then reverse 0133 and 0132. Reversing 0132 drops
control records, not object storage; never use it as a physical-erasure shortcut.

This is the LAST attachment migration on purpose: every table it fences is
created by 0132, so the triggers attach to final table shapes. Each upload
names its own parent, so occurrences are not tied to a per-session parent.

Why this is RunSQL and not Django schema operations: every fence here compares a
row's OLD and NEW values (identity, tombstone, snapshot and blob immutability,
monotonic policy/deletion generations, the allowed state transitions) or reads
another table (project/workspace scope, parent-record scope, a verified blob
before ``ready``). CheckConstraint, UniqueConstraint and Index see one row
version of one table, so none of them can express these rules, and Django has
no trigger operation. The tables are new and empty when this runs. The
update-safety gate therefore needs a human UPDATE_SAFETY review for this file;
none is declared here, because only a real reviewer can supply one.
"""

from django.db import migrations

FORWARD = r"""
CREATE FUNCTION attachment_scope_fence() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM tracer_project p JOIN accounts_workspace w
    ON w.id = p.workspace_id WHERE p.id = NEW.project_id
    AND p.organization_id = NEW.organization_id AND p.workspace_id = NEW.workspace_id
    AND w.organization_id = NEW.organization_id) THEN
    RAISE EXCEPTION 'attachment scope mismatch' USING ERRCODE = '23514';
  END IF;
  IF TG_OP = 'UPDATE' THEN
    IF (NEW.organization_id, NEW.workspace_id, NEW.project_id) IS DISTINCT FROM
       (OLD.organization_id, OLD.workspace_id, OLD.project_id)
       OR NEW.policy_version < OLD.policy_version
       OR NEW.deletion_generation < OLD.deletion_generation THEN
      RAISE EXCEPTION 'attachment scope immutable' USING ERRCODE = '23514';
    END IF;
    IF (NEW.enabled, NEW.require_media_redaction, NEW.allow_unredacted_media_with_text_pii,
        NEW.record_audio)
       IS DISTINCT FROM
       (OLD.enabled, OLD.require_media_redaction, OLD.allow_unredacted_media_with_text_pii,
        OLD.record_audio)
       AND NEW.policy_version <= OLD.policy_version THEN
      RAISE EXCEPTION 'attachment policy version required' USING ERRCODE = '23514';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER attachment_scope_fence BEFORE INSERT OR UPDATE ON tracer_attachmentscope
  FOR EACH ROW EXECUTE FUNCTION attachment_scope_fence();

CREATE FUNCTION attachment_record_fence() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  n jsonb := to_jsonb(NEW);
  o jsonb;
  fk text;
  target_table text;
  target_scope uuid;
BEGIN
  IF TG_OP = 'UPDATE' THEN
    o := to_jsonb(OLD);
    FOREACH fk IN ARRAY ARRAY['scope_id', 'client_id', 'attachment_id', 'occurrence_id',
      'capture_session_id', 'writer_id', 'writer_kind', 'trace_id', 'span_id',
      'correlation_nonce', 'capture_policy', 'request_fingerprint', 'policy_version',
      'deletion_generation', 'expected_bytes', 'mime_type', 'direction', 'source_kind'] LOOP
      IF n->fk IS DISTINCT FROM o->fk THEN
        RAISE EXCEPTION 'attachment identity immutable' USING ERRCODE = '23514';
      END IF;
    END LOOP;
    IF o->>'deleted_at' IS NOT NULL AND n->>'deleted_at' IS DISTINCT FROM o->>'deleted_at' THEN
      RAISE EXCEPTION 'attachment tombstone immutable' USING ERRCODE = '23514';
    END IF;
    IF TG_TABLE_NAME = 'tracer_attachmentblob' AND n IS DISTINCT FROM o THEN
      RAISE EXCEPTION 'attachment blob immutable' USING ERRCODE = '23514';
    END IF;
    IF o ? 'state' AND n->>'state' IS DISTINCT FROM o->>'state' THEN
      IF NOT (
        (o->>'state' = 'pending_upload' AND n->>'state' IN ('validating','failed','rejected','expired','deleted')) OR
        (o->>'state' = 'validating' AND n->>'state' IN ('ready','failed','rejected','expired','deleted')) OR
        (o->>'state' IN ('ready','failed','rejected','expired') AND n->>'state' = 'deleted')
      ) THEN
        RAISE EXCEPTION 'attachment transition forbidden' USING ERRCODE = '23514';
      END IF;
    END IF;
    IF o->>'quarantine_version' <> '' AND
       (n->>'quarantine_version', n->>'quarantine_key') IS DISTINCT FROM
       (o->>'quarantine_version', o->>'quarantine_key') THEN
      RAISE EXCEPTION 'attachment snapshot immutable' USING ERRCODE = '23514';
    END IF;
  ELSE
    IF n ? 'state' AND n->>'state' <> 'pending_upload' THEN
      RAISE EXCEPTION 'attachment initial state forbidden' USING ERRCODE = '23514';
    END IF;
  END IF;
  FOREACH fk IN ARRAY ARRAY['attachment_id','capture_session_id','occurrence_id','upload_id'] LOOP
    IF n->>fk IS NOT NULL THEN
      target_table := CASE fk WHEN 'attachment_id' THEN 'tracer_attachment'
        WHEN 'capture_session_id' THEN 'tracer_capturesession'
        WHEN 'occurrence_id' THEN 'tracer_attachmentoccurrence'
        WHEN 'upload_id' THEN 'tracer_attachmentupload' END;
      EXECUTE format('SELECT scope_id FROM %I WHERE id = $1', target_table)
        INTO target_scope USING (n->>fk)::uuid;
      IF target_scope IS NULL OR target_scope <> NEW.scope_id THEN
        RAISE EXCEPTION 'attachment parent scope mismatch' USING ERRCODE = '23514';
      END IF;
    END IF;
  END LOOP;
  IF TG_TABLE_NAME = 'tracer_attachmentupload' THEN
    IF NOT EXISTS (SELECT 1 FROM tracer_attachmentoccurrence o WHERE o.id=NEW.occurrence_id
      AND o.attachment_id=NEW.attachment_id AND o.capture_session_id=NEW.capture_session_id) THEN
      RAISE EXCEPTION 'attachment upload mismatch' USING ERRCODE = '23514';
    END IF;
  END IF;
  IF n->>'state' = 'ready' AND TG_TABLE_NAME IN ('tracer_attachment','tracer_attachmentupload') THEN
    IF NOT EXISTS (SELECT 1 FROM tracer_attachmentblob b WHERE b.attachment_id =
      CASE WHEN TG_TABLE_NAME = 'tracer_attachment' THEN NEW.id ELSE (n->>'attachment_id')::uuid END
      AND b.scope_id=NEW.scope_id AND b.storage_version <> '' AND b.byte_length > 0) THEN
      RAISE EXCEPTION 'attachment verified blob required' USING ERRCODE = '23514';
    END IF;
  END IF;
  RETURN NEW;
END $$;
"""

TABLES = (
    "attachment",
    "attachmentblob",
    "attachmentoccurrence",
    "attachmentupload",
    "capturesession",
    "uploadreservation",
    "attachmentoutbox",
    "attachmentwritegrant",
)
for table in TABLES:
    FORWARD += f"\nCREATE TRIGGER attachment_record_fence BEFORE INSERT OR UPDATE ON tracer_{table} FOR EACH ROW EXECUTE FUNCTION attachment_record_fence();"

REVERSE = "\n".join(
    f"DROP TRIGGER attachment_record_fence ON tracer_{table};" for table in TABLES
)
REVERSE += "\nDROP FUNCTION attachment_record_fence(); DROP TRIGGER attachment_scope_fence ON tracer_attachmentscope; DROP FUNCTION attachment_scope_fence();"


class Migration(migrations.Migration):
    dependencies = [("tracer", "0132_attachment_control_records")]
    operations = [migrations.RunSQL(FORWARD, REVERSE)]
