import uuid

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("model_hub", "0098_merge_20260513_1258"),
        ("accounts", "0020_user_last_timezone"),
    ]

    operations = [
        migrations.CreateModel(
            name="AnnotationNotificationState",
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
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("deleted", models.BooleanField(db_index=True, default=False)),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("last_realtime_digest_at", models.DateTimeField(blank=True, null=True)),
                ("last_daily_digest_at", models.DateTimeField(blank=True, null=True)),
                ("digest_enabled", models.BooleanField(default=True)),
                ("realtime_snoozed_until", models.DateTimeField(blank=True, null=True)),
                (
                    "daily_digest_hour_local",
                    models.IntegerField(
                        default=9,
                        help_text=(
                            "Hour of day (0-23) to send the daily digest in "
                            "the user's local TZ."
                        ),
                    ),
                ),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=models.deletion.CASCADE,
                        related_name="annotation_notification_state",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ("-created_at",),
                "abstract": False,
            },
        ),
    ]
