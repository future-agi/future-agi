"""Append-only completed-import rendezvous; historical migrations stay unchanged."""

from datetime import timedelta

from django.db import migrations, models
from django.db.models.lookups import Exact, GreaterThan, LessThanOrEqual

UPDATE_SAFETY = {
    "schema_version": 1,
    "risk_classes": ["data_migration", "constraint", "index", "not_null"],
    "tables": [
        "postgres:pg_proc",
        "postgres:pg_trigger",
        "postgres:tracer_attachmentproviderrecording",
        "postgres:tracer_attachmentproviderevent",
        "postgres:tracer_attachmentimportjob",
        "postgres:tracer_attachmentproviderconnection",
        "postgres:tracer_attachmentrecording",
        "postgres:django_migrations",
        "postgres:pg_constraint",
        "postgres:pg_index",
    ],
    "mitigation": "Backfill only auto mode and NULL pending fields. Preserve original event replay identities and all historical sources. Install immutable mode/link and bounded ciphertext guards transactionally. Qualify on disposable PostgreSQL and full app graph before human approval; never replace applied drafts.",
    "reviewer": "",
    "review": "",
    "large_volume_rehearsal": True,
}

PENDING_FIELDS = (
    "pending_egress_id",
    "pending_expires_at",
    "pending_due_at",
    "pending_key_id",
    "pending_nonce",
    "pending_ciphertext",
)


def all_or_none(*fields):
    return models.Q(**{field + "__isnull": True for field in fields}) | models.Q(
        **{field + "__isnull": False for field in fields}
    )


# Replace only the two historical provider clauses. Refuse an unexpected
# installed function rather than overwrite unrelated or installation-local SQL.
REPLACE_GUARD = r"""
DO $migration$
DECLARE body text; old_intent text; new_intent text; old_link text; new_link text;
BEGIN
  SELECT pg_get_functiondef('attachment_v1_guard()'::regprocedure) INTO body;
  old_intent := 'IF TG_OP=''UPDATE'' AND NEW.provider_state=''complete'' AND OLD.provider_state<>''complete'' AND NEW.import_job_id IS NULL AND NEW.dispatch_due_at IS NULL THEN';
  new_intent := 'IF TG_OP=''UPDATE'' AND NEW.dispatch_mode=''auto'' AND NEW.provider_state=''complete'' AND OLD.provider_state<>''complete'' AND NEW.import_job_id IS NULL AND NEW.dispatch_due_at IS NULL THEN';
  old_link := 'WHERE ij.id=NEW.import_job_id AND ij.scope_id=NEW.scope_id AND ij.provider_recording_id=NEW.id';
  new_link := 'WHERE ij.id=NEW.import_job_id AND ij.scope_id=NEW.scope_id AND ij.recording_id IS NOT NULL AND ij.upload_id IS NULL AND (ij.provider_recording_id=NEW.id OR (ij.source_kind=''RemoteUrlEnvelope'' AND ij.envelope_id=NEW.output_envelope_id AND ij.provider_recording_id IS NULL))';
  IF (length(body)-length(replace(body,old_intent,'')))<>length(old_intent)
      OR (length(body)-length(replace(body,old_link,'')))<>length(old_link) THEN
    RAISE EXCEPTION 'unexpected installed attachment provider guard';
  END IF;
  EXECUTE replace(replace(body,old_intent,new_intent),old_link,new_link);
END $migration$;
"""

GUARDS = r"""
CREATE FUNCTION attachment_completed_import_guard() RETURNS trigger AS $guard$
DECLARE j tracer_attachmentimportjob%ROWTYPE; r tracer_attachmentrecording%ROWTYPE;
BEGIN
  IF TG_TABLE_NAME='tracer_attachmentproviderrecording' THEN
    IF TG_OP='UPDATE' AND NEW.dispatch_mode IS DISTINCT FROM OLD.dispatch_mode THEN
      RAISE EXCEPTION 'attachment dispatch mode immutable' USING ERRCODE='23514';
    END IF;
    IF NEW.import_job_id IS NOT NULL THEN
      SELECT * INTO j FROM tracer_attachmentimportjob WHERE id=NEW.import_job_id;
      SELECT * INTO r FROM tracer_attachmentrecording WHERE id=j.recording_id;
      IF j.id IS NULL OR r.id IS NULL OR j.scope_id<>NEW.scope_id
          OR j.importer_kind<>NEW.registrar_kind OR j.importer_id<>NEW.registrar_id
          OR r.scope_id<>NEW.scope_id OR r.client_id<>NEW.recording_client_id
          OR r.capture_session_client_id<>NEW.capture_session_client_id
          OR r.correlation_nonce<>NEW.correlation_nonce OR r.trace_id<>NEW.trace_id OR r.span_id<>NEW.span_id
          OR r.direction<>NEW.direction OR r.capture_session_id IS DISTINCT FROM j.capture_session_id
          OR j.source_generation IS DISTINCT FROM NEW.source_generation
          OR j.source_generation_kind IS DISTINCT FROM NEW.source_generation_kind
          OR j.source_byte_length IS DISTINCT FROM NEW.output_bytes
          OR j.declared_bytes IS DISTINCT FROM NEW.output_bytes
          OR j.declared_duration_ms IS DISTINCT FROM NEW.output_duration_ms
          OR j.mime_type IS DISTINCT FROM NEW.source_mime_type
          OR (j.source_kind='ProviderFile' AND j.source_authority_generation IS DISTINCT FROM NEW.source_authority_generation)
          OR j.source_duration_ms IS DISTINCT FROM NEW.output_duration_ms
          OR j.source_mime_type IS DISTINCT FROM NEW.source_mime_type
          OR j.source_creator_kind IS DISTINCT FROM NEW.registrar_kind
          OR j.source_creator_id IS DISTINCT FROM NEW.registrar_id
          OR j.source_trace_id IS DISTINCT FROM NEW.trace_id OR j.source_span_id IS DISTINCT FROM NEW.span_id
          OR j.source_direction IS DISTINCT FROM NEW.direction
          OR r.policy_version<>j.policy_version OR r.deletion_generation<>j.deletion_generation THEN
        RAISE EXCEPTION 'attachment registered job identity differs' USING ERRCODE='23514';
      END IF;
    END IF;
    RETURN NEW;
  END IF;
  IF TG_OP='DELETE' THEN
    RAISE EXCEPTION 'attachment provider replay identity retained' USING ERRCODE='23514';
  END IF;
  IF TG_OP='UPDATE' THEN
    IF (NEW.id,NEW.scope_id,NEW.connection_id,NEW.event_id,NEW.created_at) IS DISTINCT FROM
        (OLD.id,OLD.scope_id,OLD.connection_id,OLD.event_id,OLD.created_at) THEN
      RAISE EXCEPTION 'attachment provider event identity immutable' USING ERRCODE='23514';
    END IF;
    IF NEW.pending_ciphertext IS NOT NULL AND (OLD.pending_ciphertext IS NULL OR
        (NEW.pending_egress_id,NEW.pending_expires_at,NEW.pending_key_id,NEW.pending_nonce,NEW.pending_ciphertext) IS DISTINCT FROM
        (OLD.pending_egress_id,OLD.pending_expires_at,OLD.pending_key_id,OLD.pending_nonce,OLD.pending_ciphertext)) THEN
      RAISE EXCEPTION 'attachment pending receipt cannot replace or rearm' USING ERRCODE='23514';
    END IF;
  END IF;
  IF NEW.pending_ciphertext IS NOT NULL AND (
      octet_length(NEW.pending_nonce)<>12 OR octet_length(NEW.pending_ciphertext)<=16
      OR octet_length(NEW.pending_ciphertext)>65552
      OR NEW.pending_egress_id !~ '^EG_[A-Za-z0-9]{1,61}$'
      OR NEW.pending_key_id !~ '^[A-Za-z0-9]{1,16}$'
      OR NEW.pending_expires_at<=NEW.created_at
      OR NEW.pending_expires_at>NEW.created_at+interval '600 seconds') THEN
    RAISE EXCEPTION 'attachment pending receipt bounds' USING ERRCODE='23514';
  END IF;
  IF TG_OP='INSERT' AND NEW.pending_ciphertext IS NOT NULL THEN
    -- The same connection lock serializes absent registration and receipt checks.
    PERFORM 1 FROM tracer_attachmentproviderconnection WHERE id=NEW.connection_id AND scope_id=NEW.scope_id FOR UPDATE;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'attachment pending connection differs' USING ERRCODE='23514';
    END IF;
    IF (SELECT count(*) FROM tracer_attachmentproviderevent WHERE connection_id=NEW.connection_id AND pending_ciphertext IS NOT NULL)>=32 THEN
      RAISE EXCEPTION 'attachment pending connection capacity' USING ERRCODE='23514';
    END IF;
  END IF;
  RETURN NEW;
END $guard$ LANGUAGE plpgsql;
CREATE TRIGGER attachment_completed_import_guard BEFORE INSERT OR UPDATE ON tracer_attachmentproviderrecording FOR EACH ROW EXECUTE FUNCTION attachment_completed_import_guard();
CREATE TRIGGER attachment_completed_import_guard BEFORE INSERT OR UPDATE OR DELETE ON tracer_attachmentproviderevent FOR EACH ROW EXECUTE FUNCTION attachment_completed_import_guard();
"""


class Migration(migrations.Migration):
    dependencies = [("tracer", "0143_attachment_value_contraction")]
    operations = [
        migrations.AddField(
            model_name="attachmentproviderrecording",
            name="dispatch_mode",
            field=models.CharField(
                max_length=12,
                choices=[("auto", "auto"), ("sdk_explicit", "sdk_explicit")],
                default="auto",
            ),
        ),
        *[
            migrations.AddField(
                model_name="attachmentproviderevent", name=name, field=field
            )
            for name, field in (
                ("pending_egress_id", models.CharField(max_length=64, null=True)),
                ("pending_expires_at", models.DateTimeField(null=True)),
                ("pending_due_at", models.DateTimeField(null=True)),
                ("pending_key_id", models.CharField(max_length=16, null=True)),
                ("pending_nonce", models.BinaryField(max_length=12, null=True)),
                ("pending_ciphertext", models.BinaryField(max_length=65552, null=True)),
            )
        ],
        migrations.AddConstraint(
            model_name="attachmentproviderrecording",
            constraint=models.CheckConstraint(
                condition=models.Q(dispatch_mode__in=["auto", "sdk_explicit"]),
                name="att_provider_dispatch_mode",
            ),
        ),
        migrations.AddConstraint(
            model_name="attachmentproviderrecording",
            constraint=models.CheckConstraint(
                condition=models.Q(dispatch_mode="auto")
                | models.Q(dispatch_due_at__isnull=True),
                name="att_provider_explicit_idle",
            ),
        ),
        migrations.AddConstraint(
            model_name="attachmentproviderevent",
            constraint=models.CheckConstraint(
                condition=all_or_none(*PENDING_FIELDS),
                name="att_provider_pending_group",
            ),
        ),
        migrations.AddConstraint(
            model_name="attachmentproviderevent",
            constraint=models.CheckConstraint(
                condition=models.Q(pending_due_at__isnull=True)
                | models.Q(pending_due_at__lte=models.F("pending_expires_at")),
                name="att_provider_pending_due_cap",
            ),
        ),
        migrations.AddConstraint(
            model_name="attachmentproviderevent",
            constraint=models.CheckConstraint(
                condition=models.Q(pending_ciphertext__isnull=True)
                | (
                    Exact(
                        models.Func(
                            models.F("pending_nonce"),
                            function="OCTET_LENGTH",
                            output_field=models.IntegerField(),
                        ),
                        12,
                    )
                    & GreaterThan(
                        models.Func(
                            models.F("pending_ciphertext"),
                            function="OCTET_LENGTH",
                            output_field=models.IntegerField(),
                        ),
                        16,
                    )
                    & LessThanOrEqual(
                        models.Func(
                            models.F("pending_ciphertext"),
                            function="OCTET_LENGTH",
                            output_field=models.IntegerField(),
                        ),
                        65552,
                    )
                ),
                name="att_provider_pending_lengths",
            ),
        ),
        migrations.AddConstraint(
            model_name="attachmentproviderevent",
            constraint=models.CheckConstraint(
                condition=models.Q(pending_expires_at__isnull=True)
                | (
                    models.Q(pending_expires_at__gt=models.F("created_at"))
                    & models.Q(
                        pending_expires_at__lte=models.F("created_at")
                        + timedelta(seconds=600)
                    )
                ),
                name="att_provider_pending_lifetime",
            ),
        ),
        migrations.RemoveIndex(
            model_name="attachmentproviderrecording", name="att_provider_dispatch_due"
        ),
        migrations.AddIndex(
            model_name="attachmentproviderrecording",
            index=models.Index(
                fields=["dispatch_due_at", "id"],
                condition=models.Q(
                    dispatch_mode="auto",
                    provider_state="complete",
                    import_job_id__isnull=True,
                    dispatch_due_at__isnull=False,
                ),
                name="att_provider_dispatch_due",
            ),
        ),
        migrations.AddIndex(
            model_name="attachmentproviderevent",
            index=models.Index(
                fields=["connection", "pending_egress_id", "id"],
                condition=models.Q(pending_ciphertext__isnull=False),
                name="att_provider_pending_conn",
            ),
        ),
        migrations.AddIndex(
            model_name="attachmentproviderevent",
            index=models.Index(
                fields=["pending_due_at", "id"],
                condition=models.Q(pending_ciphertext__isnull=False),
                name="att_provider_pending_due",
            ),
        ),
        migrations.RunSQL(REPLACE_GUARD),
        migrations.RunSQL(GUARDS),
    ]
