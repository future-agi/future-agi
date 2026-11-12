"""V10's frozen-W4 leaf. Composition must merge with W4/SIEM's accepted leaves."""

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("prism", "0063_mcp_credential_grant_revision"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AgentccMCPVendorBinding",
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
                ("server_id", models.CharField(max_length=64)),
                (
                    "vendor",
                    models.CharField(
                        choices=[("pipedream", "Pipedream"), ("composio", "Composio")],
                        max_length=16,
                    ),
                ),
                (
                    "identity_policy",
                    models.CharField(
                        choices=[
                            ("member", "Member"),
                            ("member_or_asserted", "Member or asserted"),
                        ],
                        default="member",
                        max_length=24,
                    ),
                ),
                (
                    "project_id",
                    models.CharField(blank=True, default="", max_length=128),
                ),
                ("environment", models.CharField(default="development", max_length=16)),
                ("app_slug", models.CharField(blank=True, default="", max_length=100)),
                ("toolkits", models.JSONField(default=list)),
                ("tools", models.JSONField(default=dict)),
                ("enabled", models.BooleanField(default=False)),
                ("encrypted_bearer", models.TextField(blank=True, null=True)),
                ("bearer_expires_at", models.DateTimeField(blank=True, null=True)),
                ("bearer_version", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "credential",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to="prism.agentccmcpcredential",
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="accounts.organization",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("organization", "server_id"),
                        name="mcp_vendor_binding_org_server",
                    )
                ]
            },
        ),
        migrations.CreateModel(
            name="AgentccMCPVendorConnection",
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
                ("end_user_key", models.CharField(max_length=80)),
                (
                    "source",
                    models.CharField(
                        choices=[("member", "Member"), ("asserted", "Asserted")],
                        max_length=8,
                    ),
                ),
                (
                    "vendor_account_ref",
                    models.CharField(blank=True, default="", max_length=256),
                ),
                ("encrypted_session", models.TextField(blank=True, null=True)),
                ("encrypted_endpoint", models.TextField(blank=True, null=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("active", "Active"),
                            ("revoked", "Revoked"),
                            ("needs_reauth", "Needs reauthentication"),
                        ],
                        default="pending",
                        max_length=16,
                    ),
                ),
                ("version", models.PositiveIntegerField(default=1)),
                ("vendor_revoke_pending", models.BooleanField(default=False)),
                ("offboard_requested", models.BooleanField(default=False)),
                ("gateway_purge_pending", models.BooleanField(default=False)),
                (
                    "vendor_revoke_outcome",
                    models.CharField(blank=True, default="", max_length=16),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "binding",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="connections",
                        to="prism.agentccmcpvendorbinding",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("binding", "end_user_key"),
                        name="mcp_vendor_connection_identity",
                    )
                ]
            },
        ),
        migrations.CreateModel(
            name="AgentccMCPVendorReceipt",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("step", models.CharField(max_length=32)),
                ("outcome", models.CharField(max_length=16)),
                ("reason", models.CharField(blank=True, default="", max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "connection",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="receipts",
                        to="prism.agentccmcpvendorconnection",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="AgentccMCPVendorAudit",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("organization_id", models.UUIDField()),
                ("server_id", models.CharField(max_length=64)),
                ("end_user_hash", models.CharField(max_length=16)),
                ("outcome", models.CharField(max_length=32)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
        ),
    ]
