"""A04 request-local PG evidence, NOT a serving capability or endpoint binding.

Only the cooperative A03 domain is detected. External/cascade producer coverage,
CH/helpers/privacy and bounded execution must be qualified independently. No
locks are acquired here: the A03 writer gate must never be held over a response.
All reads are on default at READ COMMITTED, before/after *complete* assembly.
The immutable materialized bytes can be shared by native/shadow assembly without
letting lazy querysets or cached pre-qualification Project objects escape.
"""

import json
from dataclasses import dataclass
from uuid import UUID

from django.db import connection
from rest_framework.renderers import JSONRenderer

from tfc.routers import force_primary
from tracer.services.derived_metrics.base_list_current_pg_state import (
    PgProjectScope,
    PgResponseVector,
    PgRevisionUnavailable,
    read_pg_response_vector,
)


@dataclass(frozen=True, repr=False)
class PgDependencies:
    payload: bytes

    def values(self):
        # Each consumer gets a separate value, never a shared mutable config.
        return json.loads(self.payload)


def _project(request, project_id):
    from tracer.views.trace_session import _project_queryset_for_request

    # Use the real request workspace/organization predicate, not UUID equality
    # on a previously loaded object. Explicit using defeats replica routers.
    project = (
        _project_queryset_for_request(request)
        .using("default")
        .filter(id=project_id)
        .first()
    )
    if project is None:
        raise PgRevisionUnavailable("project authorization changed")
    return project


class PgResponseCapture:
    def __init__(self, request, project_id):
        self._request = request
        self._scopes = (PgProjectScope(UUID(str(project_id))),)
        # This MUST precede Project/session_config and every dependency read.
        self.vector: PgResponseVector = read_pg_response_vector(self._scopes)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_is_in_recovery(), current_setting('transaction_read_only')"
            )
            recovery, read_only = cursor.fetchone()
        if recovery or read_only != "off":
            raise PgRevisionUnavailable("PG capture requires the writable primary")
        self._connection = connection.connection
        self.project = _project(request, project_id)
        self._ownership = (self.project.organization_id, self.project.workspace_id)
        self._finished = False
        self._dependencies = {}

    def materialize(self, session_ids):
        """Execute native reducers, including their off-page annotator domain.

        Score.project is NOT a tracer.Project FK. Keep the native label-based
        scope, null handling, ordering and repeated-annotator precedence intact.
        This adapter does not certify tied native ordering for fast serving.
        """
        if self._finished:
            raise PgRevisionUnavailable("PG capture already released")
        key = tuple(str(value) for value in session_ids)
        if key not in self._dependencies:
            from model_hub.models.develop_annotations import AnnotationsLabels
            from tracer.models.trace_session import TraceSessionOverlay
            from tracer.views.trace_session import (
                TraceSessionView,
                ensure_project_session_property_identities,
                get_default_project_session_config,
            )

            with force_primary():
                labels = list(
                    AnnotationsLabels.objects.using("default").filter(
                        project_id=self.project.pk,
                        deleted=False,
                    )
                )
                # These native helpers eagerly consume all related User fields.
                columns = TraceSessionView._build_score_column_config(labels)
                scores = TraceSessionView._fetch_session_scores(key, labels)
                overlays = list(
                    TraceSessionOverlay.objects.using("default")
                    .filter(
                        project_id=self.project.pk,
                        trace_session_id__in=key,
                        deleted=False,
                    )
                    .values_list("trace_session_id", "display_name")
                )
                config = ensure_project_session_property_identities(
                    self.project.session_config or get_default_project_session_config()
                )
                for column in columns:
                    if not any(item["id"] == column["id"] for item in config):
                        config.append(column)
                self._dependencies[key] = PgDependencies(
                    JSONRenderer().render(
                        {
                            "config": config,
                            "scores": scores,
                            "label_ids": [str(label.pk) for label in labels],
                            "overlays": {str(sid): name for sid, name in overlays},
                        }
                    )
                )
        return self._dependencies[key]

    def finish(self, response):
        """Materialize serialization BEFORE the fresh vector and authorization gate.

        Returns bytes, not an ORM/DRF object capable of more lazy reads. A provider
        must also check source/helper/pin/policy state before emitting these bytes.
        Even successful capture is never production qualification.
        """
        if self._finished:
            raise PgRevisionUnavailable("PG capture already released")
        try:
            with force_primary():
                payload = JSONRenderer().render(response)
                project = _project(self._request, self.project.pk)
                if (project.organization_id, project.workspace_id) != self._ownership:
                    raise PgRevisionUnavailable("project ownership changed")
                if (
                    read_pg_response_vector(self._scopes) != self.vector
                    or connection.connection is not self._connection
                ):
                    raise PgRevisionUnavailable("PG response changed during assembly")
                return payload
        finally:
            self._finished = True
            self._dependencies.clear()


def assemble_pg_response(
    request,
    project_id,
    *,
    assemble,
    retry_native,
    deadline,
    privacy_safe,
    drain_and_release,
):
    """Integration seam; not installed on HTTP until the reader lane qualifies.

    `assemble(capture, deadline)` performs a whole synchronous response. The
    owner must supply its real acknowledged drain/release operation; it is called
    before any retry. `retry_native(request, project_id, deadline)` must reload
    ALL PG/config and execute the COMPLETE unbound native request under that same
    bounded policy. It receives no old capture/results. Neither callback may
    spawn unowned work. Privacy denial or spent deadline forbids retry.
    """
    changed = False
    try:
        with force_primary():
            deadline.remaining_ms()
            capture = PgResponseCapture(request, project_id)
            result = assemble(capture, deadline)
            payload = capture.finish(result)
            deadline.remaining_ms()
    except PgRevisionUnavailable:
        changed = True
    finally:
        # Every exit drains once, including serializer/database/worker failure.
        # If drain fails it supersedes the result: NEVER start a native retry.
        drain_and_release()
    deadline.remaining_ms()
    if not privacy_safe():
        raise PgRevisionUnavailable("response privacy unavailable")
    if changed:
        payload = retry_native(request, project_id, deadline)
        deadline.remaining_ms()
        if not privacy_safe():
            raise PgRevisionUnavailable("native retry privacy unavailable")
    return payload
