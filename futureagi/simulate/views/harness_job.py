from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from simulate.serializers.harness_job import (
    HarnessJobActionSerializer,
    HarnessJobAdjustmentSerializer,
    HarnessJobCreateSerializer,
    HarnessPreflightSerializer,
    HarnessSecretFileUploadResponseSerializer,
    HarnessSourceUploadResponseSerializer,
)
from simulate.services.harness_sandbox import (
    HarnessSandboxClient,
    HarnessSandboxRejected,
    HarnessSandboxUnavailable,
)
from simulate.services.harness_credentials import (
    credential_file_ref,
    harness_reporting_environment,
    materialize_secret_refs,
    request_scope,
    save_environment_credentials,
    store_credential_file,
)
from tfc.utils.api_contracts import validated_request


class HarnessJobViewSet(viewsets.ViewSet):
    """Control-plane facade over the configured ALK sandbox provider."""

    permission_classes = [IsAuthenticated]

    def _client(self) -> HarnessSandboxClient:
        return HarnessSandboxClient()

    def _owned_job(self, request, job_id: str):
        from simulate.models import HarnessEnvironmentCredentials

        organization, workspace = request_scope(request)
        filters = {
            "harness_job_id": str(job_id),
            "organization": organization,
            "deleted": False,
        }
        if workspace is not None:
            filters["workspace"] = workspace
        return HarnessEnvironmentCredentials.objects.filter(**filters).first()

    def _not_found(self):
        # Do not reveal that a sandbox job exists in another tenant.
        return Response({"detail": "not found"}, status=status.HTTP_404_NOT_FOUND)

    @validated_request(
        request_serializer=HarnessJobCreateSerializer,
        reject_unknown_fields=True,
    )
    def create(self, request):
        organization, workspace = request_scope(request)
        if organization is None:
            return Response(
                {"detail": "an organization is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        payload = dict(request.validated_data)
        customer_environment = dict(payload.get("environment_values") or {})
        durable_refs = dict(payload.get("secret_refs") or {})
        client = self._client()
        try:
            payload["secret_refs"] = materialize_secret_refs(
                durable_refs, organization=organization, client=client
            )
            payload["controller_environment_values"] = (
                harness_reporting_environment(
                    organization=organization,
                    workspace=workspace,
                )
            )
            result = client.submit(payload)
            job_id = str(result.get("job", {}).get("job_id") or "").strip()
            if not job_id:
                raise HarnessSandboxUnavailable(
                    "ALK sandbox accepted a job without returning a job id"
                )
            save_environment_credentials(
                job_id,
                environment_values=customer_environment,
                secret_refs=durable_refs,
                organization=organization,
                workspace=workspace,
            )
        except HarnessSandboxRejected as exc:
            return Response({"detail": str(exc)}, status=exc.status_code)
        except HarnessSandboxUnavailable as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE
            )
        return Response(result, status=status.HTTP_202_ACCEPTED)

    def list(self, request):
        from simulate.models import HarnessEnvironmentCredentials

        organization, workspace = request_scope(request)
        profiles = HarnessEnvironmentCredentials.objects.filter(
            organization=organization, deleted=False
        )
        if workspace is not None:
            profiles = profiles.filter(workspace=workspace)
        allowed = set(profiles.values_list("harness_job_id", flat=True))
        try:
            jobs = self._client().list_jobs()
            return Response(
                [
                    item
                    for item in jobs
                    if str((item.get("job") or {}).get("job_id") or "") in allowed
                ]
            )
        except HarnessSandboxUnavailable as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE
            )

    @action(
        detail=False,
        methods=["post"],
        url_path="sources",
        parser_classes=[MultiPartParser],
    )
    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter(
                "files",
                openapi.IN_FORM,
                type=openapi.TYPE_FILE,
                required=True,
                description="Repeat this field once for every source file.",
            ),
            openapi.Parameter(
                "paths",
                openapi.IN_FORM,
                type=openapi.TYPE_STRING,
                required=True,
                description="Repeat in file order with each repository-relative path.",
            ),
            openapi.Parameter(
                "name",
                openapi.IN_FORM,
                type=openapi.TYPE_STRING,
                required=False,
            ),
        ],
        responses={201: HarnessSourceUploadResponseSerializer},
    )
    def source_upload(self, request):
        files = request.FILES.getlist("files")
        paths = request.data.getlist("paths")
        if not files or len(files) != len(paths):
            return Response(
                {"detail": "one relative path is required per file"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(files) > 5_000:
            return Response(
                {"detail": "source may contain at most 5000 files"},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )
        total = sum(int(getattr(uploaded, "size", 0) or 0) for uploaded in files)
        if total > 200 * 1024 * 1024:
            return Response(
                {"detail": "source may not exceed 200 MiB"},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )
        try:
            result = self._client().upload_source(
                files, paths, str(request.data.get("name") or "uploaded-agent")
            )
        except HarnessSandboxRejected as exc:
            return Response({"detail": str(exc)}, status=exc.status_code)
        except HarnessSandboxUnavailable as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE
            )
        return Response(result, status=status.HTTP_201_CREATED)

    @action(
        detail=False,
        methods=["post"],
        url_path="secret-files",
        parser_classes=[MultiPartParser],
    )
    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter(
                "file",
                openapi.IN_FORM,
                type=openapi.TYPE_FILE,
                required=True,
                description="Credential file; transferred without entering job JSON.",
            ),
            openapi.Parameter(
                "environment_name",
                openapi.IN_FORM,
                type=openapi.TYPE_STRING,
                required=True,
                description="Environment variable that will point to the mounted file.",
            ),
        ],
        responses={201: HarnessSecretFileUploadResponseSerializer},
    )
    def secret_file_upload(self, request):
        uploaded = request.FILES.get("file")
        environment_name = str(request.data.get("environment_name") or "").strip()
        if uploaded is None or not environment_name:
            return Response(
                {"detail": "file and environment_name are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if int(getattr(uploaded, "size", 0) or 0) > 5 * 1024 * 1024:
            return Response(
                {"detail": "credential file may not exceed 5 MiB"},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )
        organization, workspace = request_scope(request)
        if organization is None:
            return Response(
                {"detail": "an organization is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        record = store_credential_file(
            uploaded,
            environment_name,
            organization=organization,
            workspace=workspace,
        )
        result = {
            "environment_name": environment_name,
            "secret_ref": credential_file_ref(record),
            "size": record.size,
        }
        return Response(result, status=status.HTTP_201_CREATED)

    @validated_request(
        request_serializer=HarnessPreflightSerializer,
        reject_unknown_fields=True,
    )
    @action(detail=False, methods=["post"])
    def preflight(self, request):
        try:
            return Response(self._client().preflight(request.validated_data))
        except HarnessSandboxRejected as exc:
            return Response({"detail": str(exc)}, status=exc.status_code)
        except HarnessSandboxUnavailable as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE
            )

    def retrieve(self, request, pk=None):
        if self._owned_job(request, str(pk)) is None:
            return self._not_found()
        try:
            return Response(self._client().get(str(pk)))
        except HarnessSandboxRejected as exc:
            return Response({"detail": str(exc)}, status=exc.status_code)
        except HarnessSandboxUnavailable as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE
            )

    @validated_request(
        request_serializer=HarnessJobActionSerializer,
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        if self._owned_job(request, str(pk)) is None:
            return self._not_found()
        try:
            return Response(self._client().cancel(str(pk)))
        except HarnessSandboxRejected as exc:
            return Response({"detail": str(exc)}, status=exc.status_code)
        except HarnessSandboxUnavailable as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE
            )

    @validated_request(
        request_serializer=HarnessJobAdjustmentSerializer,
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"])
    def adjust(self, request, pk=None):
        if self._owned_job(request, str(pk)) is None:
            return self._not_found()
        try:
            return Response(self._client().adjust(str(pk), request.validated_data))
        except HarnessSandboxRejected as exc:
            return Response({"detail": str(exc)}, status=exc.status_code)
        except HarnessSandboxUnavailable as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE
            )

    @action(detail=False, methods=["get"])
    def health(self, request):
        try:
            return Response(self._client().health())
        except HarnessSandboxUnavailable as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE
            )
