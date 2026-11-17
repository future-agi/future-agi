import django.db.models.deletion
from django.db import migrations, models

UPDATE_SAFETY = {
    "schema_version": 1,
    "risk_classes": ["data_migration"],
    "tables": [
        "postgres:accounts_envelope_nonce",
    ],
    "mitigation": (
        "Atomic (Django default), one transaction. Creates two new, empty tables "
        "through CreateModel (accounts_service_grant with two check constraints and "
        "indexes on org, user, exp, agent_id, attempt_id and batch_ref; "
        "accounts_batch_cancellation_intent, one-to-one to a grant with PROTECT), "
        "and, through RunSQL, a third new, empty table accounts_envelope_nonce (aud, "
        "id, exp; primary key (aud, id)) with an index on exp; the model for it is "
        "unmanaged, so the RunSQL is its only DDL. No existing table is read, "
        "written or locked; no row is touched. If it fails part-way, the transaction "
        "rolls back and nothing exists; re-run it as is. The RunSQL has no IF NOT "
        "EXISTS: if an operator created accounts_envelope_nonce by hand, drop that "
        "empty table before the re-run. Reverse: DROP TABLE accounts_envelope_nonce, "
        "then the two CreateModel reverses drop their tables, which removes any "
        "grants, intents and nonces recorded since."
    ),
    "reviewer": "",
    "review": "",
    "large_volume_rehearsal": True,
}


class Migration(migrations.Migration):
    dependencies = [("accounts", "0031_retire_org_keys")]
    operations = [
        migrations.CreateModel(
            name="ServiceGrant",
            fields=[
                (
                    "jti",
                    models.CharField(max_length=32, primary_key=True, serialize=False),
                ),
                ("kind", models.CharField(max_length=24)),
                ("kid", models.CharField(max_length=4)),
                ("aud", models.CharField(max_length=32)),
                ("org", models.UUIDField(db_index=True)),
                ("workspace", models.UUIDField(null=True)),
                ("user", models.UUIDField(db_index=True, null=True)),
                ("via", models.CharField(max_length=8)),
                ("sub", models.CharField(max_length=160)),
                ("scope", models.CharField(max_length=24)),
                ("iat", models.BigIntegerField()),
                ("exp", models.BigIntegerField(db_index=True)),
                ("revoked_at", models.DateTimeField(null=True)),
                ("revoked_reason", models.CharField(blank=True, max_length=64)),
                ("agent_id", models.UUIDField(db_index=True, null=True)),
                ("attempt_id", models.UUIDField(db_index=True, null=True)),
                ("batch_ref", models.UUIDField(db_index=True, null=True)),
            ],
            options={
                "db_table": "accounts_service_grant",
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(exp__gt=models.F("iat")),
                        name="service_grant_positive_life",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(kind__in=["sandbox", "batch"]),
                        name="service_grant_registered_kind",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="BatchCancellationIntent",
            fields=[
                (
                    "grant",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        primary_key=True,
                        serialize=False,
                        to="accounts.servicegrant",
                    ),
                ),
                ("gateway_batch_id", models.CharField(blank=True, max_length=64)),
                ("organization_id", models.UUIDField()),
                ("attempts", models.PositiveIntegerField(default=0)),
                ("next_attempt_at", models.DateTimeField(db_index=True)),
                ("confirmed_at", models.DateTimeField(null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"db_table": "accounts_batch_cancellation_intent"},
        ),
        migrations.CreateModel(
            name="EnvelopeNonce",
            fields=[
                ("aud", models.CharField(max_length=32)),
                (
                    "id",
                    models.CharField(max_length=32, primary_key=True, serialize=False),
                ),
                ("exp", models.BigIntegerField(db_index=True)),
            ],
            options={"db_table": "accounts_envelope_nonce", "managed": False},
        ),
        migrations.RunSQL(
            """CREATE TABLE accounts_envelope_nonce (
                aud varchar(32) NOT NULL, id varchar(32) NOT NULL,
                exp bigint NOT NULL, PRIMARY KEY (aud, id));
                CREATE INDEX accounts_envelope_nonce_exp ON accounts_envelope_nonce (exp);""",
            "DROP TABLE accounts_envelope_nonce;",
        ),
    ]
