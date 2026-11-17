"""Workspace-scoped jury authoring and read-only execution snapshots."""

from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from model_hub.models.choices import OwnerChoices
from model_hub.models.evals_metric import (
    CompositeEvalChild,
    EvalTemplate,
    EvalTemplateVersion,
)
from model_hub.models.evaluation import Evaluation
from model_hub.serializers.composite_jury import (
    JuryDefinitionResponseSerializer,
    JuryDefinitionSerializer,
    JuryReportQuerySerializer,
    JuryReportResponseSerializer,
    JuryVersionQuerySerializer,
)
from model_hub.serializers.contracts import MODEL_HUB_ERROR_RESPONSES
from model_hub.services.composite_jury import validate_definition
from model_hub.services.jury_dataset_reports import aggregate_run
from model_hub.services.jury_versions import jury_definition, selected_jury_version
from tfc.utils.api_contracts import validated_request


def jury_scope(request):
    org = getattr(request, "organization", None) or request.user.organization
    workspace = getattr(request, "workspace", None)
    if org is None or workspace is None or workspace.organization_id != org.id:
        raise NotFound("Workspace not found.")
    return org, workspace


def scoped_parent(request, template_id, *, lock=False):
    org, workspace = jury_scope(request)
    queryset = EvalTemplate.no_workspace_objects.filter(
        organization=org,
        workspace=workspace,
        deleted=False,
        template_type="composite",
        owner=OwnerChoices.USER.value,
    )
    if lock:
        queryset = queryset.select_for_update()
    parent = queryset.filter(id=template_id).first()
    if parent is not None:
        return parent
    unsupported = EvalTemplate.no_workspace_objects.filter(
        Q(owner=OwnerChoices.SYSTEM.value)
        | Q(organization=org, workspace__isnull=True),
        id=template_id,
        deleted=False,
        template_type="composite",
    ).first()
    if unsupported is not None:
        from model_hub.services.jury_versions import require_jury_scope

        require_jury_scope(unsupported, org, workspace)
    raise NotFound("Composite jury not found in the active workspace.")


class CompositeJuryConfigView(APIView):
    permission_classes = [IsAuthenticated]

    @validated_request(
        query_serializer=JuryVersionQuerySerializer,
        responses={200: JuryDefinitionResponseSerializer, **MODEL_HUB_ERROR_RESPONSES},
    )
    def get(self, request, template_id):
        parent = scoped_parent(request, template_id)
        version_id = request.query_params.get("version_id")
        if version_id:
            version = selected_jury_version(parent, version_id)
            return Response(
                {"result": jury_definition(parent, version) or {"seats": []}}
            )
        return Response({"result": (parent.config or {}).get("jury", {"seats": []})})

    @validated_request(
        request_serializer=JuryDefinitionSerializer,
        responses={200: JuryDefinitionResponseSerializer, **MODEL_HUB_ERROR_RESPONSES},
        reject_unknown_fields=True,
    )
    def put(self, request, template_id):
        org, workspace = jury_scope(request)
        with transaction.atomic():
            parent = scoped_parent(request, template_id, lock=True)
            links = list(
                CompositeEvalChild.objects.filter(
                    parent=parent, deleted=False
                ).select_related("child", "pinned_version")
            )
            definition = validate_definition(request.data, links, org, workspace)
            parent.config = {**(parent.config or {}), "jury": definition}
            parent.save(update_fields=["config"])
            EvalTemplateVersion.objects.create_version(
                eval_template=parent,
                user=request.user,
                organization=org,
                workspace=workspace,
                criteria=parent.description or "",
            )
        return Response({"result": definition})


class CompositeJuryReportView(APIView):
    permission_classes = [IsAuthenticated]

    @validated_request(
        query_serializer=JuryReportQuerySerializer,
        responses={200: JuryReportResponseSerializer, **MODEL_HUB_ERROR_RESPONSES},
    )
    def get(self, request, template_id):
        parent = scoped_parent(request, template_id)
        org, workspace = jury_scope(request)
        records = Evaluation.objects.filter(
            eval_template=parent,
            organization=org,
            workspace=workspace,
            parent_evaluation__isnull=True,
            deleted=False,
            data__has_key="jury",
        ).order_by("-created_at", "-id")
        headers = Evaluation.objects.filter(
            eval_template=parent,
            organization=org,
            workspace=workspace,
            deleted=False,
            data__jury_dataset_run=True,
        ).order_by("-created_at", "-id")
        dataset_id = request.query_params.get("dataset_id")
        if dataset_id:
            dataset_id = serializers.UUIDField().run_validation(dataset_id)
            headers = headers.filter(input_data__dataset_id=str(dataset_id))
        run_id = request.query_params.get("run_id")
        selected_header = None
        if run_id:
            run_id = serializers.UUIDField().run_validation(run_id)
            selected_header = headers.filter(id=run_id).first()
        elif headers.exists():
            selected_header = headers.first()
        if selected_header:
            report = aggregate_run(selected_header)
            report["runs"] = [
                {"run_id": str(item.id), "run_label": item.created_at.isoformat()}
                for item in headers[:50]
            ]
            return Response({"result": report})
        if dataset_id:
            if run_id:
                raise NotFound("Jury run not found.")
            return Response({"result": {"runs": [], "kind": "dataset", "seats": []}})
        recent = list(records[:50])
        if run_id:
            run_id = serializers.UUIDField().run_validation(run_id)
            selected = get_object_or_404(records, id=run_id)
        else:
            selected = recent[0] if recent else None
        runs = [
            {"run_id": str(row.id), "run_label": row.created_at.isoformat()}
            for row in recent
        ]
        if selected is not None and not any(
            run["run_id"] == str(selected.id) for run in runs
        ):
            runs.append(
                {
                    "run_id": str(selected.id),
                    "run_label": selected.created_at.isoformat(),
                }
            )
        return Response(
            {
                "result": {
                    "runs": runs,
                    "run_id": str(selected.id) if selected else None,
                    **(selected.data["jury"] if selected else {"seats": []}),
                }
            }
        )
