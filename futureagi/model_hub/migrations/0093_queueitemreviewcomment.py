# Generated manually on 2026-05-12

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0019_merge_20260407_1927"),
        ("model_hub", "0092_annotationqueueannotator_roles"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="QueueItemReviewComment",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("deleted", models.BooleanField(db_index=True, default=False)),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "action",
                    models.CharField(
                        choices=[
                            ("comment", "Comment"),
                            ("approve", "Approve"),
                            ("request_changes", "Request Changes"),
                        ],
                        default="comment",
                        max_length=30,
                    ),
                ),
                ("comment", models.TextField()),
                (
                    "label",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="review_comments",
                        to="model_hub.annotationslabels",
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="annotation_review_comments",
                        to="accounts.organization",
                    ),
                ),
                (
                    "queue_item",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="review_comments",
                        to="model_hub.queueitem",
                    ),
                ),
                (
                    "reviewer",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="annotation_review_comments",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "target_annotator",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="targeted_annotation_review_comments",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="annotation_review_comments",
                        to="accounts.workspace",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["queue_item", "created_at"],
                        name="model_hub_q_queue_i_6808f7_idx",
                    ),
                    models.Index(
                        fields=["queue_item", "label"],
                        name="model_hub_q_queue_i_432376_idx",
                    ),
                    models.Index(
                        fields=["queue_item", "target_annotator"],
                        name="model_hub_q_queue_i_3aeaf9_idx",
                    ),
                ],
            },
        ),
    ]
