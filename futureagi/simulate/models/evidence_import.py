import uuid

from django.conf import settings
from django.db import models, transaction

from accounts.models import Organization
from tfc.utils.base_model import BaseModel


class EvidenceImport(BaseModel):
    """Observe sessions or calls, dataset rows, or transcripts a person added,
    chosen to turn into tests of one environment. Observe sources and dataset
    rows are frozen as ids and read again on every view, so evidence deleted
    later reads as unavailable instead of living on here. A transcript has no
    source to read again, so its redacted text is kept on its case until the
    import is deleted."""

    class SourceKind(models.TextChoices):
        SESSION = "session", "Session"
        CALL = "call", "Call"
        TRANSCRIPT = "transcript", "Transcript"
        DATASET = "dataset", "Dataset rows"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, related_name="+"
    )
    workspace = models.ForeignKey(
        "accounts.Workspace",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="+",
    )
    environment = models.ForeignKey(
        "simulate.HostedHarnessJob",
        on_delete=models.CASCADE,
        related_name="evidence_imports",
    )
    # The Observe project; none for transcripts and dataset rows.
    project = models.ForeignKey(
        "tracer.Project",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="+",
    )
    # Dataset rows only: their dataset, and which of its columns is the
    # conversation, the labels and the expected outcome
    # (pydantic_schemas.evidence_import.DatasetColumns).
    dataset = models.ForeignKey(
        "model_hub.Dataset",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="+",
    )
    dataset_columns = models.JSONField(null=True, blank=True)
    # The environment's agent version when the import was made.
    agent_version = models.ForeignKey(
        "simulate.AgentVersion",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    source_kind = models.CharField(max_length=16, choices=SourceKind.choices)
    # Transcripts and dataset rows: the person said they come from production.
    # An Observe import's provenance is its project's.
    declared_production = models.BooleanField(default=False)
    # Transcripts only: what redaction replaced when the text was stored
    # (pydantic_schemas.evidence_import.StoredRedaction).
    redaction = models.JSONField(null=True, blank=True)
    # The throwaway job whose builder chat is drafting cases now; it and its chat
    # are deleted once the turn settles.
    drafting_job = models.ForeignKey(
        "simulate.HostedHarnessJob",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="evidence_drafting",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        db_table = "simulate_evidence_import"
        indexes = [
            models.Index(
                fields=["environment", "-created_at"],
                name="idx_evidence_import_env",
            ),
        ]
        constraints = [
            # An Observe import has its project, a dataset import its dataset,
            # and a transcript import neither.
            models.CheckConstraint(
                condition=(
                    models.Q(
                        source_kind="transcript",
                        project__isnull=True,
                        dataset__isnull=True,
                    )
                    | models.Q(
                        source_kind="dataset",
                        project__isnull=True,
                        dataset__isnull=False,
                    )
                    | (
                        ~models.Q(source_kind__in=("transcript", "dataset"))
                        & models.Q(project__isnull=False, dataset__isnull=True)
                    )
                ),
                name="evidence_import_source_by_kind",
            ),
        ]

    def delete(self, using=None, keep_parents=False):
        """Soft-deletes the import; a transcript's stored text goes with it."""
        with transaction.atomic(using=using):
            EvidenceImportCase._base_manager.filter(evidence_import=self).update(
                transcript=None
            )
            self.redaction = None
            super().delete(using=using, keep_parents=keep_parents)


class EvidenceImportCase(BaseModel):
    """One session, call, dataset row or transcript of an import: its id, what
    it held when the import was made, and what became of it."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending review"
        UNAVAILABLE = "unavailable", "Unavailable"
        DRAFTED = "drafted", "Drafted"
        BLOCKED = "blocked", "Blocked"
        ADDED = "added", "Added to the suite"
        # A person discarded it, keeping the cases of the import already added.
        DISCARDED = "discarded", "Discarded"
        # Its source or its content is already a case of the environment
        # (``duplicate_of``): it is proposed there, once, and counts as one more
        # source of that case.
        DUPLICATE = "duplicate", "Already imported"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    evidence_import = models.ForeignKey(
        EvidenceImport, on_delete=models.CASCADE, related_name="cases"
    )
    # Where the person put it in the selection.
    position = models.PositiveSmallIntegerField()
    source_id = models.CharField(max_length=64)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING
    )
    reason = models.CharField(max_length=255, blank=True, default="")
    # SHA-256 of what the source held when the import was made; blank when it
    # held nothing the project still keeps.
    digest = models.CharField(max_length=64, blank=True, default="")
    # SHA-256 of the conversation alone (turns, tool calls, expected outcome; no
    # ids or times), as the import stored it, so two sources holding the same
    # case are told apart from two different ones; blank when unreadable.
    content_digest = models.CharField(
        max_length=64, blank=True, default="", db_index=True
    )
    # The case of the environment this one repeats; its sources are that case's
    # provenance too.
    duplicate_of = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="duplicates",
    )
    # The scenario the builder drafted for the case: its key and title, its
    # folder (suite files only, never the environment's) and the environment it
    # was proved on.
    scenario_key = models.CharField(max_length=255, blank=True, default="")
    scenario_name = models.CharField(max_length=255, blank=True, default="")
    proposal_object_key = models.CharField(max_length=512, blank=True, default="")
    environment_hash = models.CharField(max_length=128, blank=True, default="")
    # Checks the builder added to the environment's catalogue for this scenario,
    # as catalogue entries (ALK's SubGoal): new names only, never an edit.
    new_sub_goals = models.JSONField(default=list, blank=True)
    # A transcript's redacted text, cut to the import's size
    # (pydantic_schemas.evidence_import.StoredTranscript); none for Observe sources.
    transcript = models.JSONField(null=True, blank=True)

    class Meta:
        db_table = "simulate_evidence_import_case"
        ordering = ("position",)
        constraints = [
            models.UniqueConstraint(
                fields=["evidence_import", "source_id"],
                name="uniq_evidence_import_case_source",
            ),
        ]
