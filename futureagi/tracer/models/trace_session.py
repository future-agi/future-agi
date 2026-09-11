import uuid

from django.db import models

from tfc.utils.base_model import BaseModel
from tracer.models.project import Project


class TraceSession(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="sessions",
        blank=False,
        null=False,
    )
    bookmarked = models.BooleanField(default=False)
    name = models.CharField(max_length=255, blank=True, null=True)

    def __str__(self):
        return f"Session {self.id}"

    class Meta:
        db_table = "trace_session"
        ordering = ["-created_at"]


class TraceSessionOverlay(BaseModel):
    """UI-sourced overlay for a TraceSession's user-editable fields.

    Part of the TraceSession three-way split (CH-derived dimensions, DESIGN §5):
    the session's *external identity* (`external_session_id`, `first_seen`) lives
    in the CH-native ``trace_sessions`` RMT / ``trace_sessions_dict``, while the
    *user overlay* — ``bookmarked`` and an optional ``display_name`` override —
    lives HERE, in PG, written ONLY by the UI. Rows exist only for sessions a user
    has actually bookmarked or renamed, so the table stays tiny: a genuine tier-3
    relational dimension that correctly stays in PG.

    The link to the session is a SOFT id (``trace_session_id`` is a plain UUID,
    NOT a ForeignKey): once the PG ``trace_session`` table is dropped at contract
    (P4), the canonical session row is the CH ``trace_sessions`` entry, which a
    PG FK cannot reference. This mirrors the Score / annotation soft-id pattern.

    Separating ``display_name`` (mutable, UI) from ``external_session_id``
    (immutable, ingestion) is what fixes the rename → duplicate-session bug
    (DESIGN §2.5 / §5.1): a rename writes ``display_name`` here and never touches
    the identity the deterministic id is computed from.
    """

    trace_session_id = models.UUIDField(db_index=True)  # soft id, not a FK
    project_id = models.UUIDField(db_index=True)
    bookmarked = models.BooleanField(default=False)
    display_name = models.CharField(max_length=255, null=True)  # override only

    def __str__(self):
        return f"SessionOverlay {self.trace_session_id}"

    class Meta:
        db_table = "trace_session_overlay"
        constraints = [
            models.UniqueConstraint(
                fields=["trace_session_id"], name="uniq_session_overlay"
            )
        ]
