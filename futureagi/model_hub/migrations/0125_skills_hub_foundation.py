import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0030_queue_existing_demo_trace_publications"),
        ("model_hub", "0124_skills_hub_v11"),
        ("simulate", "0102_agent_fix_engine_notes"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="hubskill",
            name="kind",
            field=models.CharField(
                choices=[("skill", "Skill"), ("agent_context", "Agent Context")],
                default="skill",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="hubskill",
            name="metadata_revision",
            field=models.PositiveBigIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="hubskill",
            name="security_revision",
            field=models.PositiveBigIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="hubskilldraft",
            name="context_spec",
            field=models.JSONField(default=dict),
        ),
        migrations.AddField(
            model_name="hubskilldraft",
            name="expected_head_version",
            field=models.PositiveIntegerField(null=True),
        ),
        migrations.AddField(
            model_name="hubskilldraft",
            name="head_review_fingerprint",
            field=models.CharField(blank=True, default="", max_length=71),
        ),
        migrations.AddField(
            model_name="hubskilldraft",
            name="resolved_lock",
            field=models.JSONField(default=dict),
        ),
        migrations.AddField(
            model_name="hubskillversion",
            name="origin_number",
            field=models.PositiveIntegerField(null=True),
        ),
        migrations.CreateModel(
            name="HubSkillContextBinding",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("name", models.CharField(max_length=120)),
                (
                    "kind",
                    models.CharField(
                        choices=[("repository", "repository"), ("runtime", "runtime")],
                        max_length=16,
                    ),
                ),
                ("selector", models.JSONField()),
                ("update_mode", models.CharField(max_length=24)),
                ("profile", models.CharField(max_length=64)),
                ("profile_version", models.CharField(max_length=64)),
                ("repository", models.JSONField(null=True)),
                ("configuration", models.JSONField(default=dict)),
                ("configuration_digest", models.CharField(blank=True, max_length=71)),
                ("revision", models.PositiveBigIntegerField(default=1)),
                ("expected_report_interval_seconds", models.PositiveIntegerField()),
                ("retired_at", models.DateTimeField(null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "context",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_bindings",
                        to="model_hub.hubskill",
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_%(class)s_records",
                        to="accounts.organization",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_%(class)s_records",
                        to="accounts.workspace",
                    ),
                ),
            ],
            options={
                "abstract": False,
            },
        ),
        migrations.CreateModel(
            name="HubSkillContextCapture",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("trace_ref", models.JSONField()),
                ("source_fingerprint", models.CharField(max_length=71)),
                ("source_hashes", models.JSONField(default=dict)),
                ("selected_text", models.JSONField(null=True)),
                ("expires_at", models.DateTimeField()),
                ("accepted_receipt", models.JSONField(null=True)),
                (
                    "context",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_captures",
                        to="model_hub.hubskill",
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_%(class)s_records",
                        to="accounts.organization",
                    ),
                ),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_captures",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "recorded_version",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="hub_skill_captures",
                        to="model_hub.hubskillversion",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_%(class)s_records",
                        to="accounts.workspace",
                    ),
                ),
            ],
            options={
                "abstract": False,
            },
        ),
        migrations.CreateModel(
            name="HubSkillContextCredential",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "purpose",
                    models.CharField(
                        choices=[("consume", "Consume"), ("report", "Report")],
                        max_length=16,
                    ),
                ),
                ("secret_hash", models.CharField(max_length=64)),
                ("grants", models.JSONField(default=list)),
                ("binding_ids", models.JSONField(default=list)),
                ("expires_at", models.DateTimeField()),
                ("revoked_at", models.DateTimeField(null=True)),
                ("last_used_at", models.DateTimeField(null=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="hub_skill_context_credentials",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_%(class)s_records",
                        to="accounts.organization",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_%(class)s_records",
                        to="accounts.workspace",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="HubSkillContextDependency",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("mount", models.CharField(max_length=255)),
                ("requested_selector", models.JSONField()),
                (
                    "child_skill",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="hub_skill_context_dependents",
                        to="model_hub.hubskill",
                    ),
                ),
                (
                    "child_version",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="hub_skill_context_parents",
                        to="model_hub.hubskillversion",
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_%(class)s_records",
                        to="accounts.organization",
                    ),
                ),
                (
                    "parent_version",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_context_dependencies",
                        to="model_hub.hubskillversion",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_%(class)s_records",
                        to="accounts.workspace",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="HubSkillContextOperation",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("principal_key", models.CharField(max_length=100)),
                ("operation", models.CharField(max_length=64)),
                ("idempotency_key", models.UUIDField()),
                ("request_digest", models.CharField(max_length=71)),
                ("result_resource_id", models.UUIDField(null=True)),
                ("response", models.JSONField(default=dict)),
                ("completed_at", models.DateTimeField(null=True)),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_%(class)s_records",
                        to="accounts.organization",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_%(class)s_records",
                        to="accounts.workspace",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="HubSkillContextQuarantine",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("revision", models.PositiveBigIntegerField()),
                ("active", models.BooleanField()),
                ("reason", models.CharField(max_length=2000)),
                (
                    "actor",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="hub_skill_quarantine_events",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_%(class)s_records",
                        to="accounts.organization",
                    ),
                ),
                (
                    "replacement_version",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="hub_skill_quarantine_replacements",
                        to="model_hub.hubskillversion",
                    ),
                ),
                (
                    "version",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_quarantine_events",
                        to="model_hub.hubskillversion",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_%(class)s_records",
                        to="accounts.workspace",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="HubSkillContextResolution",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("resource_kind", models.CharField(max_length=16)),
                ("digest", models.CharField(max_length=71)),
                ("requested", models.JSONField()),
                ("grant", models.JSONField()),
                ("expires_at", models.DateTimeField()),
                (
                    "credential",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_resolutions",
                        to="model_hub.hubskillcontextcredential",
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_%(class)s_records",
                        to="accounts.organization",
                    ),
                ),
                (
                    "version",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_resolutions",
                        to="model_hub.hubskillversion",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_%(class)s_records",
                        to="accounts.workspace",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="HubSkillContextSession",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("client_session_key", models.CharField(max_length=200)),
                ("incarnation_id", models.UUIDField()),
                ("digest", models.CharField(max_length=71)),
                ("binding_revision", models.PositiveBigIntegerField()),
                ("adapter_version", models.CharField(max_length=100)),
                ("runtime_version", models.CharField(max_length=100)),
                ("runtime_config_digest", models.CharField(max_length=71)),
                ("last_seen_at", models.DateTimeField()),
                ("expires_at", models.DateTimeField()),
                (
                    "binding",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_sessions",
                        to="model_hub.hubskillcontextbinding",
                    ),
                ),
                (
                    "credential",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="hub_skill_sessions",
                        to="model_hub.hubskillcontextcredential",
                    ),
                ),
                (
                    "label_event",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="hub_skill_sessions",
                        to="model_hub.hubskilllabelevent",
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_%(class)s_records",
                        to="accounts.organization",
                    ),
                ),
                (
                    "version",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="hub_skill_sessions",
                        to="model_hub.hubskillversion",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_%(class)s_records",
                        to="accounts.workspace",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="HubSkillContextReceipt",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("reporter_key", models.CharField(max_length=100)),
                ("event_id", models.UUIDField()),
                ("instance_id", models.UUIDField()),
                ("sequence", models.PositiveBigIntegerField()),
                ("stage", models.CharField(max_length=32)),
                ("digest", models.CharField(max_length=71)),
                ("materialization_digest", models.CharField(max_length=71, null=True)),
                ("request_digest", models.CharField(max_length=71)),
                ("observed_at", models.DateTimeField()),
                ("details", models.JSONField(default=dict)),
                ("origin", models.CharField(max_length=24)),
                ("expires_at", models.DateTimeField()),
                (
                    "binding",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_receipts",
                        to="model_hub.hubskillcontextbinding",
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_%(class)s_records",
                        to="accounts.organization",
                    ),
                ),
                (
                    "version",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="hub_skill_receipts",
                        to="model_hub.hubskillversion",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_%(class)s_records",
                        to="accounts.workspace",
                    ),
                ),
                (
                    "session",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="hub_skill_receipts",
                        to="model_hub.hubskillcontextsession",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="HubSkillDraftCheckpoint",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("draft_revision", models.PositiveBigIntegerField()),
                ("reason", models.CharField(max_length=32)),
                ("payload", models.JSONField(null=True)),
                ("digest", models.CharField(max_length=71)),
                ("expires_at", models.DateTimeField()),
                ("restored_revision", models.PositiveBigIntegerField(null=True)),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_%(class)s_records",
                        to="accounts.organization",
                    ),
                ),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_checkpoints",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "skill",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_checkpoints",
                        to="model_hub.hubskill",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_%(class)s_records",
                        to="accounts.workspace",
                    ),
                ),
            ],
            options={
                "abstract": False,
            },
        ),
        migrations.CreateModel(
            name="HubSkillEvidenceAnnotation",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("source", models.CharField(max_length=24)),
                ("source_id", models.UUIDField()),
                ("revision", models.PositiveBigIntegerField()),
                ("check_id", models.UUIDField(null=True)),
                ("attempt_id", models.UUIDField(null=True)),
                (
                    "verdict",
                    models.CharField(
                        choices=[
                            ("agree", "agree"),
                            ("disagree", "disagree"),
                            ("cannot_assess", "cannot_assess"),
                        ],
                        max_length=16,
                    ),
                ),
                ("cited_text", models.CharField(blank=True, max_length=2000)),
                ("comment", models.CharField(blank=True, max_length=2000)),
                (
                    "actor",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="hub_skill_evidence_annotations",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_%(class)s_records",
                        to="accounts.organization",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_%(class)s_records",
                        to="accounts.workspace",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="HubSkillIntakePlan",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("kind", models.CharField(max_length=16)),
                ("fingerprint", models.CharField(max_length=71)),
                ("inventory", models.JSONField()),
                ("payload", models.JSONField(null=True)),
                ("expires_at", models.DateTimeField()),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_%(class)s_records",
                        to="accounts.organization",
                    ),
                ),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_intake_plans",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_%(class)s_records",
                        to="accounts.workspace",
                    ),
                ),
            ],
            options={
                "abstract": False,
            },
        ),
        migrations.CreateModel(
            name="HubSkillIntakeReceipt",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("item_key", models.CharField(max_length=255)),
                ("result", models.JSONField()),
                (
                    "destination",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="hub_skill_intake_receipts",
                        to="model_hub.hubskill",
                    ),
                ),
                (
                    "operation",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="hub_skill_intake_receipt",
                        to="model_hub.hubskillcontextoperation",
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_%(class)s_records",
                        to="accounts.organization",
                    ),
                ),
                (
                    "plan",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="hub_skill_item_receipts",
                        to="model_hub.hubskillintakeplan",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_%(class)s_records",
                        to="accounts.workspace",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="HubSkillMigrationReceipt",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("schema", models.PositiveSmallIntegerField(default=1)),
                ("source_origin", models.CharField(max_length=255)),
                ("source_project", models.CharField(max_length=200)),
                ("source_resource", models.CharField(max_length=200)),
                ("source_revision", models.CharField(max_length=200)),
                ("source_digest", models.CharField(max_length=71)),
                ("destination_digest", models.CharField(max_length=71)),
                (
                    "relation",
                    models.CharField(
                        choices=[("identical", "identical"), ("derived", "derived")],
                        max_length=16,
                    ),
                ),
                ("mapping", models.JSONField()),
                (
                    "destination",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="hub_skill_migration_receipts",
                        to="model_hub.hubskill",
                    ),
                ),
                (
                    "destination_version",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="hub_skill_migration_receipts",
                        to="model_hub.hubskillversion",
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_%(class)s_records",
                        to="accounts.organization",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_%(class)s_records",
                        to="accounts.workspace",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="HubSkillNativeEvidence",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("visibility", models.CharField(default="private", max_length=16)),
                ("shared_at", models.DateTimeField(null=True)),
                ("candidate_digest", models.CharField(max_length=71)),
                ("execution_fingerprint", models.CharField(max_length=71)),
                ("configuration", models.JSONField()),
                ("planned_matrix", models.JSONField()),
                ("annotation_revision", models.PositiveBigIntegerField(default=0)),
                ("payload_expires_at", models.DateTimeField()),
                ("payload_purged_at", models.DateTimeField(null=True)),
                ("protected_locations", models.JSONField(default=list)),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_%(class)s_records",
                        to="accounts.organization",
                    ),
                ),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_native_evidence",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "simulation_job",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="hub_skill_native_evidence",
                        to="simulate.hostedharnessjob",
                    ),
                ),
                (
                    "skill",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_native_evidence",
                        to="model_hub.hubskill",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_%(class)s_records",
                        to="accounts.workspace",
                    ),
                ),
            ],
            options={
                "abstract": False,
            },
        ),
        migrations.CreateModel(
            name="HubSkillReleasePacket",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("revision", models.PositiveBigIntegerField()),
                ("fingerprint", models.CharField(max_length=71)),
                ("packet", models.JSONField()),
                (
                    "candidate",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="hub_skill_release_packets",
                        to="model_hub.hubskillversion",
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_%(class)s_records",
                        to="accounts.organization",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_%(class)s_records",
                        to="accounts.workspace",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="HubSkillReleaseDecision",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "decision",
                    models.CharField(
                        choices=[
                            ("approve", "approve"),
                            ("request_changes", "request_changes"),
                        ],
                        max_length=24,
                    ),
                ),
                ("comment", models.CharField(blank=True, max_length=2000)),
                ("currentness_at_action", models.JSONField(null=True)),
                ("post_move_state", models.JSONField(null=True)),
                (
                    "actor",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="hub_skill_release_decisions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "label_event",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="hub_skill_decisions",
                        to="model_hub.hubskilllabelevent",
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_%(class)s_records",
                        to="accounts.organization",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_%(class)s_records",
                        to="accounts.workspace",
                    ),
                ),
                (
                    "packet",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="hub_skill_decisions",
                        to="model_hub.hubskillreleasepacket",
                    ),
                ),
            ],
            options={
                "abstract": False,
            },
        ),
        migrations.CreateModel(
            name="HubSkillReleaseRequest",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("revision", models.PositiveBigIntegerField(default=1)),
                ("status", models.CharField(default="pending", max_length=24)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "author",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="hub_skill_release_requests",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_%(class)s_records",
                        to="accounts.organization",
                    ),
                ),
                (
                    "skill",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_release_requests",
                        to="model_hub.hubskill",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_%(class)s_records",
                        to="accounts.workspace",
                    ),
                ),
            ],
            options={
                "abstract": False,
            },
        ),
        migrations.CreateModel(
            name="HubSkillReleaseRecipient",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("last_seen_revision", models.PositiveBigIntegerField(default=0)),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_%(class)s_records",
                        to="accounts.organization",
                    ),
                ),
                (
                    "recipient",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_review_inbox",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_%(class)s_records",
                        to="accounts.workspace",
                    ),
                ),
                (
                    "request",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_recipients",
                        to="model_hub.hubskillreleaserequest",
                    ),
                ),
            ],
        ),
        migrations.AddField(
            model_name="hubskillreleasepacket",
            name="request",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="hub_skill_packets",
                to="model_hub.hubskillreleaserequest",
            ),
        ),
        migrations.CreateModel(
            name="HubSkillSpendPolicy",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("revision", models.PositiveBigIntegerField(default=1)),
                (
                    "period_limit_usd",
                    models.DecimalField(decimal_places=8, max_digits=18),
                ),
                ("enabled", models.BooleanField(default=False)),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_%(class)s_records",
                        to="accounts.organization",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="hub_skill_spend_policies",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_%(class)s_records",
                        to="accounts.workspace",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="HubSkillSpendPeriod",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("starts_at", models.DateTimeField()),
                ("ends_at", models.DateTimeField()),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_%(class)s_records",
                        to="accounts.organization",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_%(class)s_records",
                        to="accounts.workspace",
                    ),
                ),
                (
                    "policy",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="hub_skill_periods",
                        to="model_hub.hubskillspendpolicy",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="HubSkillSpendReservation",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("operation_id", models.UUIDField()),
                ("source", models.CharField(max_length=24)),
                ("source_id", models.UUIDField()),
                ("kind", models.CharField(max_length=16)),
                ("state", models.CharField(default="prepared", max_length=16)),
                ("claim_generation", models.PositiveBigIntegerField(default=0)),
                ("reserved_usd", models.DecimalField(decimal_places=8, max_digits=18)),
                (
                    "settled_usd",
                    models.DecimalField(decimal_places=8, max_digits=18, null=True),
                ),
                ("sender_active", models.BooleanField(default=False)),
                ("settled_at", models.DateTimeField(null=True)),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_%(class)s_records",
                        to="accounts.organization",
                    ),
                ),
                (
                    "parent",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="hub_skill_calls",
                        to="model_hub.hubskillspendreservation",
                    ),
                ),
                (
                    "period",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="hub_skill_reservations",
                        to="model_hub.hubskillspendperiod",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="hub_skill_%(class)s_records",
                        to="accounts.workspace",
                    ),
                ),
            ],
        ),
        migrations.AddIndex(
            model_name="hubskillcontextcredential",
            index=models.Index(
                fields=["organization", "workspace", "-created_at"],
                name="hub_ctx_credential_scope",
            ),
        ),
        migrations.AddConstraint(
            model_name="hubskillcontextdependency",
            constraint=models.UniqueConstraint(
                fields=("parent_version", "mount"), name="hub_ctx_dependency_mount"
            ),
        ),
        migrations.AddConstraint(
            model_name="hubskillcontextdependency",
            constraint=models.UniqueConstraint(
                fields=("parent_version", "child_skill"),
                name="hub_ctx_dependency_child",
            ),
        ),
        migrations.AddConstraint(
            model_name="hubskillcontextoperation",
            constraint=models.UniqueConstraint(
                fields=(
                    "organization",
                    "workspace",
                    "principal_key",
                    "operation",
                    "idempotency_key",
                ),
                name="hub_ctx_operation_key",
            ),
        ),
        migrations.AddIndex(
            model_name="hubskillcontextquarantine",
            index=models.Index(
                fields=["version", "-revision"], name="hub_ctx_quarantine_current"
            ),
        ),
        migrations.AddConstraint(
            model_name="hubskillcontextquarantine",
            constraint=models.UniqueConstraint(
                fields=("version", "revision"), name="hub_ctx_quarantine_revision"
            ),
        ),
        migrations.AddIndex(
            model_name="hubskillcontextresolution",
            index=models.Index(
                fields=["credential", "expires_at"], name="hub_ctx_resolution_expiry"
            ),
        ),
        migrations.AddConstraint(
            model_name="hubskillcontextsession",
            constraint=models.UniqueConstraint(
                fields=("binding", "client_session_key", "incarnation_id"),
                name="hub_ctx_session_identity",
            ),
        ),
        migrations.AddConstraint(
            model_name="hubskillcontextreceipt",
            constraint=models.UniqueConstraint(
                fields=("organization", "workspace", "reporter_key", "event_id"),
                name="hub_ctx_receipt_event",
            ),
        ),
        migrations.AddConstraint(
            model_name="hubskillcontextreceipt",
            constraint=models.UniqueConstraint(
                fields=("binding", "instance_id", "sequence"),
                name="hub_ctx_receipt_sequence",
            ),
        ),
        migrations.AddConstraint(
            model_name="hubskillevidenceannotation",
            constraint=models.UniqueConstraint(
                fields=("workspace", "source", "source_id", "revision"),
                name="hub_skill_annotation_revision",
            ),
        ),
        migrations.AddConstraint(
            model_name="hubskillintakereceipt",
            constraint=models.UniqueConstraint(
                fields=("plan", "item_key"), name="hub_skill_intake_item"
            ),
        ),
        migrations.AddConstraint(
            model_name="hubskillmigrationreceipt",
            constraint=models.UniqueConstraint(
                fields=(
                    "workspace",
                    "source_origin",
                    "source_project",
                    "source_resource",
                    "source_revision",
                    "destination",
                ),
                name="hub_skill_migration_identity",
            ),
        ),
        migrations.AddConstraint(
            model_name="hubskillreleaserecipient",
            constraint=models.UniqueConstraint(
                fields=("request", "recipient"), name="hub_skill_review_recipient"
            ),
        ),
        migrations.AddConstraint(
            model_name="hubskillreleasepacket",
            constraint=models.UniqueConstraint(
                fields=("request", "revision"), name="hub_skill_packet_revision"
            ),
        ),
        migrations.AddConstraint(
            model_name="hubskillspendpolicy",
            constraint=models.UniqueConstraint(
                fields=("organization", "workspace"), name="hub_skill_spend_workspace"
            ),
        ),
        migrations.AddConstraint(
            model_name="hubskillspendperiod",
            constraint=models.UniqueConstraint(
                fields=("policy", "starts_at"), name="hub_skill_spend_period"
            ),
        ),
        migrations.AddConstraint(
            model_name="hubskillspendreservation",
            constraint=models.UniqueConstraint(
                fields=("workspace", "operation_id"), name="hub_skill_spend_operation"
            ),
        ),
    ]
