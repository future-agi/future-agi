import uuid

from django.conf import settings
from django.db import models

from tracer.models.eval_task import EvalTask
from tracer.models.project import Project


class InstantEvalRun(models.Model):
    """What an Instant eval run was asked to read: the view it came from, how
    big that view was, how much of it to read, who started it and what it was
    expected to cost. The eval task does the reading; this records the ask.

    A sample is the first ``rows_requested`` rows of the view in
    ``cityHash64(row id)`` order, which is a uniform random sample that reads
    the same rows every time. ``hash_cursor`` is the largest hash read so far,
    so "Read 1,000 more" continues with the next rows in that order.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    eval_task = models.OneToOneField(
        EvalTask, on_delete=models.CASCADE, related_name="instant_run"
    )
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="instant_eval_runs"
    )
    organization = models.ForeignKey(
        "accounts.Organization",
        on_delete=models.CASCADE,
        related_name="instant_eval_runs",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="instant_eval_runs",
    )
    # The view's unit; a user question reads sessions (``row_type``).
    unit = models.CharField(max_length=16)
    row_type = models.CharField(max_length=16)
    # The view's filters and window when the run started, frozen.
    filters = models.JSONField(default=list, blank=True)
    date_from = models.DateTimeField(null=True, blank=True)
    date_to = models.DateTimeField(null=True, blank=True)
    # The view's date option (an Observe preset key, or "Custom") and how its
    # scope read ("Past 7D · 2 filters"). The page re-anchors a preset to now
    # when the question is asked again; the server only keeps them.
    date_preset = models.CharField(max_length=16, null=True, blank=True)
    label = models.CharField(max_length=300, null=True, blank=True)
    # The Users view's search text, which narrows a user question like a filter.
    search = models.CharField(max_length=200, blank=True, default="")
    population = models.IntegerField(default=0)
    population_is_lower_bound = models.BooleanField(default=False)
    # None reads the whole view.
    sample_size = models.IntegerField(null=True, blank=True)
    rows_requested = models.IntegerField(default=0)
    hash_cursor = models.CharField(max_length=24, blank=True, default="")
    questions = models.IntegerField(default=1)
    credits_estimate = models.FloatField(default=0)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    # Bumped whenever the run's answers may change wholesale (retry, resume,
    # read more, cancel, a resync), so a finished run's cached summary is
    # read again once and never otherwise.
    answers_version = models.IntegerField(default=0)
    # Answers recorded in InstantEvalAnswer from the start (runs started
    # here); False for runs whose answers still have to be copied over once.
    answers_synced = models.BooleanField(default=False)
    # The time zone the run was last viewed in: its summary is counted ahead
    # of time in it when the run finishes.
    last_tz = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tracer_instant_eval_run"
        indexes = [
            models.Index(
                fields=["organization", "created_at"],
                name="instant_run_org_created_idx",
            ),
        ]


class InstantEvalRow(models.Model):
    """One row an Instant eval run reads, with what the Ask page groups it by.

    Written once when the run picks its rows, so the summary and rows
    endpoints aggregate in SQL instead of reading ClickHouse on every poll.
    Answers stay in ``EvalLogger``; this table only joins to them.
    """

    id = models.BigAutoField(primary_key=True)
    task = models.ForeignKey(
        EvalTask,
        on_delete=models.CASCADE,
        related_name="instant_rows",
        db_constraint=False,
        # The unique index below starts with the task.
        db_index=False,
    )
    row_id = models.CharField(max_length=255)
    unit = models.CharField(max_length=16)
    # Position in the sample's hash order: 0 is read first.
    sample_rank = models.IntegerField(default=0)
    time = models.DateTimeField(null=True, blank=True)
    end_user_id = models.UUIDField(null=True, blank=True)
    user_label = models.CharField(max_length=255, blank=True, default="")
    model = models.CharField(max_length=255, blank=True, default="")
    turns = models.IntegerField(null=True, blank=True)
    duration_ms = models.BigIntegerField(null=True, blank=True)
    # The trace or session itself errored (not the eval).
    error = models.BooleanField(default=False)
    excerpt = models.TextField(blank=True, default="")
    trace_id = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        db_table = "tracer_instant_eval_row"
        constraints = [
            models.UniqueConstraint(
                fields=["task", "row_id"], name="instant_row_task_row_uniq"
            ),
        ]
        indexes = [
            # Newest-first paging, and the summary's scan without the excerpt.
            models.Index(
                fields=["task", "time", "id"],
                include=["end_user_id", "model", "turns", "error"],
                name="instant_row_task_time_id_idx",
            ),
            models.Index(
                fields=["task", "end_user_id"], name="instant_row_task_user_idx"
            ),
            models.Index(
                fields=["task", "sample_rank"], name="instant_row_task_rank_idx"
            ),
        ]


class InstantEvalAnswer(models.Model):
    """A row's latest settled answer to one question of its run: the label
    (or ``__error__`` / ``__skipped__``) and Jev's confidence. Written when
    the answer lands, so counting and filtering a run's answers never reads
    the eval logger's wide rows. A row with no answer yet has no entry here.

    ``time`` repeats the row's time so an answer filter reads rows in time
    order straight from the (task, question, label, time) index.
    """

    id = models.BigAutoField(primary_key=True)
    task = models.ForeignKey(
        EvalTask,
        on_delete=models.CASCADE,
        related_name="instant_answers",
        db_constraint=False,
        # The unique index below starts with the task.
        db_index=False,
    )
    row = models.ForeignKey(
        InstantEvalRow,
        on_delete=models.CASCADE,
        related_name="answers",
        db_constraint=False,
    )
    config_id = models.UUIDField()
    label = models.CharField(max_length=255)
    confidence = models.FloatField(null=True, blank=True)
    time = models.DateTimeField(null=True, blank=True)
    # When the answer landed: how fast a run is going.
    answered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "tracer_instant_eval_answer"
        constraints = [
            models.UniqueConstraint(
                fields=["task", "row", "config_id"],
                name="instant_answer_task_row_cfg_uniq",
            ),
        ]
        indexes = [
            models.Index(
                fields=["task", "config_id", "label", "time"],
                include=["row", "confidence"],
                name="instant_answer_label_time_idx",
            ),
            # Answers Jev was unsure of (the Ask page's "unsure" filter), a
            # tenth or so of a run's answers. Matches instant_eval_summary's
            # UNSURE_BELOW and failed labels.
            models.Index(
                fields=["task", "config_id"],
                include=["row"],
                condition=models.Q(confidence__lt=0.6)
                & ~models.Q(label__in=["__error__", "__skipped__"]),
                name="instant_answer_unsure_idx",
            ),
        ]
