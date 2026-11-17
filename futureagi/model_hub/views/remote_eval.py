"""Remote eval authoring, one-row tests and background run scoring."""
import copy
import secrets
from datetime import timedelta
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from integrations.services.credentials import CredentialManager
from model_hub.models.choices import OwnerChoices
from model_hub.models.develop_dataset import Cell, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate, EvalTemplateVersion
from model_hub.models.remote_eval import RemoteEvalConfig, RemoteEvalJob
from model_hub.serializers.remote_eval import RemoteSaveSerializer, RemoteScoreRunSerializer, RemoteTestSerializer
from model_hub.services.remote_eval import call_scorer, mapped_inputs, prepare_definition, public_auth, validate_task_budget
from model_hub.services.remote_eval_runs import options, scoped_runs, selected_run
from model_hub.utils.eval_validators import validate_eval_name
from model_hub.views.composite_jury import jury_scope


def validated(serializer_class, data):
    serializer = serializer_class(data=data)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


def templates(request):
    org, workspace = jury_scope(request)
    return EvalTemplate.no_workspace_objects.filter(
        organization=org, workspace=workspace, deleted=False,
        owner=OwnerChoices.USER.value, eval_type="remote",
    )


def scoped_template(request, template_id, *, lock=False):
    # Fetch the definition and encrypted credential in one database snapshot;
    # an editor changing the URL must not pair its new secret with the old URL.
    queryset = templates(request).select_related("remote_credentials")
    if lock:
        queryset = queryset.select_for_update(of=("self",))
    template = queryset.filter(id=template_id).first()
    if template is None:
        raise NotFound("Remote scorer not found.")
    return template


def credentials_of(template):
    try:
        return CredentialManager.decrypt(bytes(template.remote_credentials.encrypted_credentials))
    except (RemoteEvalConfig.DoesNotExist, ValueError):
        raise ValidationError("Scorer credentials are unavailable; re-enter them in the editor.") from None


def detail_of(template):
    remote = copy.deepcopy(template.config["remote"])
    remote["auth"] = public_auth(remote.get("auth", {}))
    return {"id": str(template.id), "name": template.name, "description": template.description or "",
            "eval_type": "remote", "pass_threshold": template.pass_threshold,
            "remote": remote, "version": template.config.get("remote_version", "V1"),
            "host": urlsplit(remote["url"]).hostname,
            "signing": {"previous_valid_until": remote["auth"].get("previous_valid_until")}}


def save_definition(template, remote, credentials, request):
    template.config = {**(template.config or {}), "remote": remote,
                       "eval_type_id": "RemoteEvaluator", "required_keys": ["output", *remote.get("mapping", {})]}
    output = remote.get("output", {}).get("type", "score")
    template.output_type_normalized = "pass_fail" if output == "pass_fail" else "percentage"
    template.choice_scores = {choice["label"]: choice["score"] for choice in remote.get("output", {}).get("choices", [])}
    template.proxy_agi = False
    try:
        template.save()
    except DjangoValidationError:
        raise ValidationError("The evaluation name or definition is invalid or already exists.") from None
    RemoteEvalConfig.objects.update_or_create(
        template=template, defaults={"encrypted_credentials": CredentialManager.encrypt(credentials)},
    )
    snapshot = copy.deepcopy(template.config)
    snapshot.pop("remote_version_id", None)
    snapshot.pop("remote_version", None)
    version = EvalTemplateVersion.objects.create_version(
        eval_template=template, prompt_messages=[], config_snapshot=snapshot,
        user=request.user, organization=template.organization, workspace=template.workspace,
    )
    template.config["remote_version_id"] = str(version.id)
    template.config["remote_version"] = f"V{version.version_number}"
    template.save(update_fields=["config"])


class RemoteCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        org, workspace = jury_scope(request)
        data = validated(RemoteSaveSerializer, request.data)
        if "remote" not in data or "name" not in data:
            raise ValidationError("Name and remote definition are required.")
        try:
            name = validate_eval_name(data["name"])
        except ValueError as exc:
            raise ValidationError(str(exc)) from None
        remote, credentials, secret = prepare_definition(data["remote"], new=True)
        with transaction.atomic():
            template = EvalTemplate(
                name=name, description=data.get("description", ""), organization=org, workspace=workspace,
                eval_type="remote", owner=OwnerChoices.USER.value,
                pass_threshold=data.get("pass_threshold", 0.5),
            )
            save_definition(template, remote, credentials, request)
        result = detail_of(template)
        if secret:
            result["signing_secret"] = secret
        return Response({"result": result})


class RemoteDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, template_id):
        template = scoped_template(request, template_id)
        result = detail_of(template)
        mode = request.query_params.get("for")
        org, workspace = jury_scope(request)
        if mode == "score-run":
            result["score_run_options"] = options(org.id, workspace.id)
        elif mode == "score-run-calls":
            raw = {"run_id": request.query_params.get("run_id")}
            if request.query_params.get("row_ids"):
                raw["row_ids"] = request.query_params["row_ids"].split(",")
            data = validated(RemoteScoreRunSerializer, raw)
            _, count = selected_run(org.id, workspace.id, data["run_id"], data.get("row_ids"), count_only=True)
            result["score_run_calls"] = {"calls": count, "cells": count}
        elif mode == "test":
            result["test_rows"] = []
            for dataset in Dataset.no_workspace_objects.filter(organization=org, workspace=workspace, deleted=False).order_by("name")[:50]:
                rows = Row.no_workspace_objects.filter(dataset=dataset, deleted=False).order_by("order")[:50]
                result["test_rows"].append({"dataset_id": str(dataset.id), "dataset_name": dataset.name,
                                            "rows": [{"id": str(row.id), "label": f"Row {row.order + 1}", "preview": ""} for row in rows]})
        return Response({"result": result})

    def patch(self, request, template_id):
        data = validated(RemoteSaveSerializer, request.data)
        with transaction.atomic():
            template = scoped_template(request, template_id, lock=True)
            old = template.config["remote"]
            remote, credentials, secret = prepare_definition(data.get("remote", old), old, credentials_of(template), new=True)
            if data.get("rotate_secret"):
                if remote["auth"]["kind"] != "hmac":
                    raise ValidationError("Only signed scorers have a signing secret to rotate.")
                secret = secrets.token_urlsafe(32)
                credentials["previous_secret"] = credentials["signing_secret"]
                credentials["previous_valid_until"] = (timezone.now() + timedelta(hours=24)).timestamp()
                credentials["signing_secret"] = secret
                remote["auth"]["previous_valid_until"] = (timezone.now() + timedelta(hours=24)).isoformat()
            for field in ("description", "pass_threshold"):
                if field in data:
                    setattr(template, field, data[field])
            save_definition(template, remote, credentials, request)
        result = detail_of(template)
        if secret:
            result["signing_secret"] = secret
        return Response({"result": result})


class RemoteTestView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        org, workspace = jury_scope(request)
        data = validated(RemoteTestSerializer, request.data)
        template = scoped_template(request, data["template_id"]) if data.get("template_id") else None
        if template is None and "remote" not in data:
            raise ValidationError("A saved scorer or remote definition is required.")
        old = template.config["remote"] if template else None
        remote, credentials, _ = prepare_definition(
            data.get("remote", old), old, credentials_of(template) if template else None,
        )
        inputs = data.get("inputs", {})
        if data.get("row_id") or data.get("dataset_id"):
            row = Row.no_workspace_objects.filter(
                id=data.get("row_id"), dataset_id=data.get("dataset_id"), deleted=False,
                dataset__organization=org, dataset__workspace=workspace, dataset__deleted=False,
            ).first()
            if row is None:
                raise NotFound("Dataset row not found.")
            cells = Cell.no_workspace_objects.filter(row=row, dataset_id=row.dataset_id, column__dataset_id=row.dataset_id, deleted=False, column__deleted=False).select_related("column")
            values = {}
            for cell in cells:
                values[cell.column.name] = cell.value
                values[str(cell.column_id)] = cell.value
            inputs = mapped_inputs(remote, values)
        result = call_scorer(remote, credentials, inputs, data.get("output", ""),
                             data.get("pass_threshold", template.pass_threshold if template else 0.5))
        return Response({"result": result})


class RemoteScoreRunView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, template_id):
        from model_hub.tasks.remote_eval import score_remote_run

        template = scoped_template(request, template_id)
        data = validated(RemoteScoreRunSerializer, request.data)
        run, items = selected_run(template.organization_id, template.workspace_id, data["run_id"], data.get("row_ids"))
        remote = copy.deepcopy(template.config["remote"])
        validate_task_budget(remote, len(items))
        # Validate all mappings before admitting a billable job.
        for item in items:
            mapped_inputs(remote, item.input)
        with transaction.atomic():
            job = RemoteEvalJob.objects.create(
                template=template, run=run,
                definition={"remote": remote, "pass_threshold": template.pass_threshold,
                            "version_id": template.config["remote_version_id"]},
                encrypted_credentials=CredentialManager.encrypt(credentials_of(template)),
                row_ids=list(dict.fromkeys(str(item.example_id) for item in items)), total=len(items),
            )

            def enqueue():
                try:
                    score_remote_run.delay(str(job.id))
                except Exception:
                    RemoteEvalJob.objects.filter(id=job.id).update(status="failed", error="Could not queue remote scoring.")

            transaction.on_commit(enqueue)
        return Response({"result": {"job_id": str(job.id), "status": "queued"}}, status=202)


class RemoteJobView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, job_id):
        org, workspace = jury_scope(request)
        job = RemoteEvalJob.objects.filter(id=job_id, template__in=templates(request),
                                            run__in=scoped_runs(org.id, workspace.id)).first()
        if job is None:
            raise NotFound("Remote scoring job not found.")
        # A killed worker cannot leave a permanently spinning browser. We do not
        # replay calls after a loss because the remote service may have billed them.
        if job.status in ("queued", "calling", "scoring") and job.updated_at < timezone.now() - timedelta(hours=1):
            RemoteEvalJob.objects.filter(id=job.id, status=job.status, updated_at=job.updated_at).update(
                status="failed", error="Remote scoring worker stopped; start a new job to retry.",
            )
            job.refresh_from_db()
        return Response({"result": {"job_id": str(job.id), "status": job.status, "sent": job.sent,
                                     "total": job.total, "failed": job.failed, "result": job.result, "error": job.error}})
