"""Create Skills tables without changing or backfilling existing data."""

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0030_queue_existing_demo_trace_publications"),
        ("model_hub", "0122_backfill_queueitem_source_preview"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="HubSkill",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("deleted", models.BooleanField(db_index=True, default=False)),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=64)),
                ("description", models.CharField(max_length=1024)),
                ("tags", models.JSONField(default=list)),
                ("latest_version_number", models.PositiveIntegerField(default=0)),
                ("source", models.JSONField(default=dict)),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="hub_skills", to="accounts.organization")),
                ("workspace", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="hub_skills", to="accounts.workspace")),
                ("created_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="hub_skills_created", to=settings.AUTH_USER_MODEL)),
                ("updated_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="hub_skills_updated", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ("-created_at",)},
        ),
        migrations.CreateModel(
            name="HubSkillVersion",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("number", models.PositiveIntegerField()),
                ("digest", models.CharField(max_length=71)),
                ("note", models.TextField(blank=True, max_length=2000)),
                ("file_count", models.PositiveIntegerField()),
                ("total_bytes", models.PositiveIntegerField()),
                ("frontmatter", models.JSONField()),
                ("checks", models.JSONField()),
                ("parent_number", models.PositiveIntegerField(null=True)),
                ("source", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("skill", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="versions", to="model_hub.hubskill")),
                ("created_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="hub_skill_versions", to=settings.AUTH_USER_MODEL)),
                ("created_by_api_key", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="hub_skill_versions", to="accounts.orgapikey")),
            ],
        ),
        migrations.CreateModel(
            name="HubSkillBlob",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("sha256", models.CharField(max_length=64)),
                ("size_bytes", models.PositiveIntegerField()),
                ("content", models.TextField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="hub_skill_blobs", to="accounts.organization")),
            ],
        ),
        migrations.CreateModel(
            name="HubSkillFile",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("path", models.CharField(max_length=255)),
                ("size_bytes", models.PositiveIntegerField()),
                ("sha256", models.CharField(max_length=64)),
                ("token_estimate", models.PositiveIntegerField()),
                ("version", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="files", to="model_hub.hubskillversion")),
                ("blob", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="files", to="model_hub.hubskillblob")),
            ],
        ),
        migrations.CreateModel(
            name="HubSkillLabel",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=40)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("skill", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="labels", to="model_hub.hubskill")),
                ("version", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="labels", to="model_hub.hubskillversion")),
                ("updated_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="hub_skill_labels", to=settings.AUTH_USER_MODEL)),
                ("updated_by_api_key", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="hub_skill_labels", to="accounts.orgapikey")),
            ],
        ),
        migrations.CreateModel(
            name="HubSkillLabelEvent",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("label", models.CharField(max_length=40)),
                ("from_version", models.PositiveIntegerField(null=True)),
                ("to_version", models.PositiveIntegerField(null=True)),
                ("note", models.CharField(blank=True, max_length=500)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("skill", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="label_events", to="model_hub.hubskill")),
                ("actor", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="hub_skill_label_events", to=settings.AUTH_USER_MODEL)),
                ("actor_api_key", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="hub_skill_label_events", to="accounts.orgapikey")),
            ],
        ),
        migrations.CreateModel(
            name="HubSkillDraft",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("base_version", models.PositiveIntegerField(null=True)),
                ("files", models.JSONField(default=list)),
                ("note", models.TextField(blank=True)),
                ("revision", models.PositiveIntegerField(default=1)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("skill", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="drafts", to="model_hub.hubskill")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="hub_skill_drafts", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="HubSkillUsageDaily",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("day", models.DateField()),
                ("channel", models.CharField(choices=[("api", "api"), ("download", "download"), ("mcp", "mcp")], max_length=16)),
                ("consumer_key", models.CharField(max_length=80)),
                ("consumer_label", models.CharField(max_length=200)),
                ("count", models.PositiveIntegerField()),
                ("last_at", models.DateTimeField()),
                ("skill", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="usage_days", to="model_hub.hubskill")),
                ("version", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="usage_days", to="model_hub.hubskillversion")),
            ],
        ),
        migrations.AddConstraint(model_name="hubskill", constraint=models.UniqueConstraint(condition=models.Q(deleted=False), fields=("workspace", "name"), name="uniq_hub_skill_ws_name_live")),
        migrations.AddIndex(model_name="hubskill", index=models.Index(fields=["workspace", "updated_at"], name="hub_skill_ws_updated_idx")),
        migrations.AddIndex(model_name="hubskill", index=models.Index(fields=["organization", "workspace", "deleted"], name="hub_skill_org_ws_deleted_idx")),
        migrations.AddConstraint(model_name="hubskillversion", constraint=models.UniqueConstraint(fields=("skill", "number"), name="uniq_hub_skill_version_number")),
        migrations.AddIndex(model_name="hubskillversion", index=models.Index(fields=["skill", "-number"], name="hub_skill_version_number_idx")),
        migrations.AddConstraint(model_name="hubskillblob", constraint=models.UniqueConstraint(fields=("organization", "sha256"), name="uniq_hub_skill_blob_org_sha")),
        migrations.AddConstraint(model_name="hubskillfile", constraint=models.UniqueConstraint(fields=("version", "path"), name="uniq_hub_skill_file_path")),
        migrations.AddConstraint(model_name="hubskilllabel", constraint=models.UniqueConstraint(fields=("skill", "name"), name="uniq_hub_skill_label")),
        migrations.AddIndex(model_name="hubskilllabelevent", index=models.Index(fields=["skill", "-created_at"], name="hub_skill_label_event_time_idx")),
        migrations.AddConstraint(model_name="hubskilldraft", constraint=models.UniqueConstraint(fields=("skill", "user"), name="uniq_hub_skill_draft_user")),
        migrations.AddConstraint(model_name="hubskillusagedaily", constraint=models.UniqueConstraint(fields=("version", "day", "channel", "consumer_key"), name="uniq_hub_skill_usage_day")),
        migrations.AddIndex(model_name="hubskillusagedaily", index=models.Index(fields=["skill", "day"], name="hub_skill_usage_day_idx")),
    ]
