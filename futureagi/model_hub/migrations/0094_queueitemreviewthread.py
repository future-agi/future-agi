# Generated manually on 2026-05-12

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def create_threads_for_existing_comments(apps, schema_editor):
    QueueItemReviewComment = apps.get_model("model_hub", "QueueItemReviewComment")
    QueueItemReviewThread = apps.get_model("model_hub", "QueueItemReviewThread")
    db_alias = schema_editor.connection.alias

    for comment in (
        QueueItemReviewComment.objects.using(db_alias)
        .filter(thread__isnull=True, deleted=False)
        .iterator()
    ):
        if comment.label_id and comment.target_annotator_id:
            scope = "score"
        elif comment.label_id:
            scope = "label"
        else:
            scope = "item"

        blocking = comment.action == "request_changes"
        status = "open" if blocking else "resolved"
        thread = QueueItemReviewThread.objects.using(db_alias).create(
            queue_item_id=comment.queue_item_id,
            created_by_id=comment.reviewer_id,
            label_id=comment.label_id,
            target_annotator_id=comment.target_annotator_id,
            action=comment.action,
            scope=scope,
            status=status,
            blocking=blocking,
            organization_id=comment.organization_id,
            workspace_id=comment.workspace_id,
        )
        comment.thread_id = thread.id
        comment.save(update_fields=["thread"])


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0019_merge_20260407_1927"),
        ("model_hub", "0093_queueitemreviewcomment"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="QueueItemReviewThread",
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
                (
                    "scope",
                    models.CharField(
                        choices=[
                            ("item", "Item"),
                            ("label", "Label"),
                            ("score", "Score"),
                        ],
                        default="item",
                        max_length=20,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("open", "Open"),
                            ("addressed", "Addressed"),
                            ("resolved", "Resolved"),
                            ("reopened", "Reopened"),
                        ],
                        default="open",
                        max_length=20,
                    ),
                ),
                ("blocking", models.BooleanField(default=False)),
                ("addressed_at", models.DateTimeField(blank=True, null=True)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                ("reopened_at", models.DateTimeField(blank=True, null=True)),
                (
                    "addressed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="addressed_annotation_review_threads",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_annotation_review_threads",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "label",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="review_threads",
                        to="model_hub.annotationslabels",
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="annotation_review_threads",
                        to="accounts.organization",
                    ),
                ),
                (
                    "queue_item",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="review_threads",
                        to="model_hub.queueitem",
                    ),
                ),
                (
                    "reopened_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="reopened_annotation_review_threads",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "resolved_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="resolved_annotation_review_threads",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "target_annotator",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="targeted_annotation_review_threads",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="annotation_review_threads",
                        to="accounts.workspace",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["queue_item", "status"],
                        name="model_hub_q_queue_i_bb7246_idx",
                    ),
                    models.Index(
                        fields=["queue_item", "blocking", "status"],
                        name="model_hub_q_queue_i_e23d9f_idx",
                    ),
                    models.Index(
                        fields=["queue_item", "target_annotator"],
                        name="model_hub_q_queue_i_287212_idx",
                    ),
                    models.Index(
                        fields=["queue_item", "label"],
                        name="model_hub_q_queue_i_647e5e_idx",
                    ),
                ],
            },
        ),
        migrations.AddField(
            model_name="queueitemreviewcomment",
            name="thread",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="comments",
                to="model_hub.queueitemreviewthread",
            ),
        ),
        migrations.AlterField(
            model_name="queueitemreviewcomment",
            name="action",
            field=models.CharField(
                choices=[
                    ("comment", "Comment"),
                    ("approve", "Approve"),
                    ("request_changes", "Request Changes"),
                    ("addressed", "Addressed"),
                    ("resolve", "Resolve"),
                    ("reopen", "Reopen"),
                ],
                default="comment",
                max_length=30,
            ),
        ),
        migrations.RunPython(
            create_threads_for_existing_comments,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AddIndex(
            model_name="queueitemreviewcomment",
            index=models.Index(
                fields=["thread", "created_at"],
                name="model_hub_q_thread__42a052_idx",
            ),
        ),
    ]
