"""Authenticated Cekura chat-test transcript ingestion endpoint."""

import structlog
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.authentication import APIKeyAuthentication
from integrations.transformers.cekura_transformer import CekuraTransformer
from tracer.models.project import Project
from tracer.utils.langfuse_upsert import upsert_langfuse_trace

logger = structlog.get_logger(__name__)
_transformer = CekuraTransformer()


class CekuraIngestionView(APIView):
    """Import one Cekura chat-test run into a selected observe project.

    Cekura callers authenticate with the project owner's FutureAGI API key.
    The payload must contain ``project_id`` and either an ``id``/``run_id`` at
    its root or inside a ``run`` object.  ``turns``/``transcript`` is optional.
    """

    parser_classes = [JSONParser]
    authentication_classes = [APIKeyAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        project_id = request.data.get("project_id")
        if not project_id:
            return Response({"detail": "project_id is required."}, status=400)

        org = getattr(request, "organization", None) or request.user.organization
        project = Project.no_workspace_objects.filter(
            id=project_id, organization=org, trace_type="observe", deleted=False
        ).first()
        if not project:
            return Response({"detail": "Observe project not found."}, status=404)

        workspace = getattr(request, "workspace", None)
        if workspace and project.workspace_id and project.workspace_id != workspace.id:
            return Response({"detail": "Project is outside the active workspace."}, status=404)

        run = request.data.get("run", request.data)
        if not isinstance(run, dict):
            return Response({"detail": "run must be an object."}, status=400)

        try:
            created, spans_count, _ = upsert_langfuse_trace(
                assembled_trace=run,
                transformer=_transformer,
                project_id=str(project.id),
                org=org,
                workspace=project.workspace,
                org_id=org.id,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        except Exception:
            logger.exception("cekura_ingestion_failed", project_id=str(project.id))
            return Response({"detail": "Unable to import Cekura run."}, status=500)

        return Response(
            {"created": created, "spans_ingested": spans_count},
            status=201 if created else 200,
        )
