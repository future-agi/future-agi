from __future__ import annotations

import json
import uuid

from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from simulate.serializers.harness_environment import (
    HarnessAgentCredentialsResponseSerializer,
)
from simulate.serializers.harness_job import (
    HarnessJobActionSerializer,
    HarnessJobAdjustmentSerializer,
    HarnessJobCreateSerializer,
    HarnessJobExtendSerializer,
    HarnessJobReadSerializer,
    HarnessPreflightResponseSerializer,
    HarnessPreflightSerializer,
    HarnessRunCreateResponseSerializer,
    HarnessRunCreateSerializer,
    HarnessScenarioAmendSerializer,
    HarnessSecretFileUploadResponseSerializer,
    HarnessSecretValuesSerializer,
    HarnessSourceUploadResponseSerializer,
)
from simulate.serializers.hosted_harness_conversation import (
    HarnessConversationMessageCreateSerializer,
    HarnessConversationReadSerializer,
)
from simulate.services.harness_credentials import (
    HOSTED_FILE_KEY_PREFIX,
    credential_file_ref,
    request_scope,
    store_credential_file,
)
from simulate.services.harness_provider import get_harness_provider
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import ApiTextErrorResponseSerializer


class HarnessJobViewSet(viewsets.ViewSet):
    """Provider-neutral control plane for hosted ALK harness jobs.

    Validates the v1.6 request contract and delegates execution to the public backend selected by
    ``settings.HARNESS_PROVIDER`` (``hosted`` or ``sandbox``). The hosted backend independently
    selects its managed sandbox runtime.
    """

    permission_classes = [IsAuthenticated]

    @validated_request(
        request_serializer=HarnessJobCreateSerializer,
        responses={202: HarnessJobReadSerializer},
        reject_unknown_fields=True,
    )
    def create(self, request):
        return get_harness_provider().create(request)

    @swagger_auto_schema(responses={200: HarnessJobReadSerializer(many=True)})
    def list(self, request):
        return get_harness_provider().list(request)

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
        return get_harness_provider().source_upload(request)

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
        # Managed sandboxes cannot dereference the local-sandbox
        # ``harness_environment_file`` manager. Google ADC crosses the hosted seam as encrypted
        # JSON; the guest recreates the 0600 file and exports GOOGLE_APPLICATION_CREDENTIALS.
        if get_harness_provider().name == "hosted":
            if environment_name != "GOOGLE_APPLICATION_CREDENTIALS":
                return Response(
                    {
                        "detail": (
                            "Hosted credential uploads currently support only "
                            "GOOGLE_APPLICATION_CREDENTIALS JSON files"
                        )
                    },
                    status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                )
            # Named for the error reporters' scrubbers: it holds a private key.
            secret_content = uploaded.read()
            try:
                secret_document = json.loads(secret_content.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return Response(
                    {"detail": "Google credential file must contain valid UTF-8 JSON"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if not isinstance(secret_document, dict):
                return Response(
                    {"detail": "Google credential JSON must contain an object"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            required_fields = {"type", "project_id", "client_email", "private_key"}
            missing_fields = sorted(
                field for field in required_fields if not secret_document.get(field)
            )
            if secret_document.get("type") != "service_account" or missing_fields:
                detail = (
                    "Google credential file must be service-account JSON containing "
                    "type, project_id, client_email, and private_key"
                )
                return Response({"detail": detail}, status=status.HTTP_400_BAD_REQUEST)

            from simulate.models import HostedHarnessSecret

            key = f"{HOSTED_FILE_KEY_PREFIX}{uuid.uuid4().hex}"
            HostedHarnessSecret.objects.create(
                organization=organization,
                name=key,
                version="1",
                encrypted_value=json.dumps(secret_document, separators=(",", ":")),
            )
            result = {
                "environment_name": "GOOGLE_APPLICATION_CREDENTIALS_JSON",
                "secret_ref": {
                    "manager": "platform-vault",
                    "key": key,
                    "version": "1",
                    "purpose": "target_provider",
                },
                "size": len(secret_content),
            }
        else:
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
        request_serializer=HarnessSecretValuesSerializer,
        responses={202: HarnessAgentCredentialsResponseSerializer},
        reject_unknown_fields=True,
    )
    @action(detail=False, methods=["post"], url_path="secret-values")
    @swagger_auto_schema(
        request_body=HarnessSecretValuesSerializer,
        responses={202: HarnessAgentCredentialsResponseSerializer},
    )
    def secret_values(self, request):
        """Make an environment's next agent version from new credential values.

        The values (and staged ``files``) are stored encrypted on the new version, which
        is checked next and returned with 202.
        """
        organization, _workspace = request_scope(request)
        if organization is None:
            return Response(
                {"detail": "an organization is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return self._agent_credentials(request, organization)

    @staticmethod
    def _agent_credentials(request, organization):
        from simulate.models import HostedHarnessJob
        from simulate.services.harness_agent_versions import (
            change_credentials,
            staged_credential_files,
        )
        from simulate.services.harness_provider import scope_jobs
        from simulate.services.harness_versioning.read import agent_version_item
        from simulate.services.hosted_harness import HostedHarnessError

        # Locals holding values are named for the error tracker's scrubber.
        secret_values = request.validated_data["environment_values"]
        environment = scope_jobs(
            HostedHarnessJob.no_workspace_objects.filter(
                organization=organization,
                id=request.validated_data["environment_id"],
                environment__isnull=True,
                deleted=False,
            ),
            request,
        ).first()
        if environment is None:
            return Response(
                {"detail": "Environment not found"}, status=status.HTTP_404_NOT_FOUND
            )
        try:
            secret_files = staged_credential_files(
                organization, request.validated_data.get("files") or []
            )
            version = change_credentials(
                environment,
                secret_values=secret_values,
                secret_files=secret_files,
                user=request.user,
            )
        except HostedHarnessError as exc:
            return Response(exc.as_dict(), status=exc.status_code)
        environment.refresh_from_db(fields=["agent_version"])
        return Response(
            {"agent_version": agent_version_item(environment, version)},
            status=status.HTTP_202_ACCEPTED,
        )

    @validated_request(
        request_serializer=HarnessPreflightSerializer,
        responses={200: HarnessPreflightResponseSerializer},
        reject_unknown_fields=True,
    )
    @action(detail=False, methods=["post"])
    def preflight(self, request):
        return get_harness_provider().preflight(request)

    @swagger_auto_schema(responses={200: HarnessJobReadSerializer})
    def retrieve(self, request, pk=None):
        return get_harness_provider().retrieve(request, pk)

    @validated_request(
        request_serializer=HarnessJobActionSerializer,
        responses={200: HarnessJobReadSerializer},
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        return get_harness_provider().cancel(request, pk)

    @validated_request(
        request_serializer=HarnessJobAdjustmentSerializer,
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"])
    def adjust(self, request, pk=None):
        return get_harness_provider().adjust(request, pk)

    @validated_request(
        request_serializer=HarnessJobExtendSerializer,
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"])
    def extend(self, request, pk=None):
        return get_harness_provider().extend(request, pk)

    @action(detail=True, methods=["get"], url_path="scenarios")
    def scenarios(self, request, pk=None):
        return get_harness_provider().list_scenarios(request, pk)

    @action(detail=True, methods=["get"], url_path="scenarios/coverage")
    def scenario_coverage(self, request, pk=None):
        return get_harness_provider().scenario_coverage(request, pk)

    @validated_request(
        request_serializer=HarnessScenarioAmendSerializer,
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"], url_path="scenarios/amend")
    def amend_scenarios(self, request, pk=None):
        return get_harness_provider().amend_scenarios(request, pk)

    @validated_request(
        request_serializer=HarnessRunCreateSerializer,
        responses={202: HarnessRunCreateResponseSerializer},
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"])
    def runs(self, request, pk=None):
        return get_harness_provider().run(request, pk)

    @validated_request(
        request_serializer=HarnessConversationMessageCreateSerializer,
        responses={
            202: HarnessConversationReadSerializer,
            409: ApiTextErrorResponseSerializer,
        },
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"], url_path=r"conversation/messages")
    def conversation_message(self, request, pk=None):
        return get_harness_provider().send_message(request, pk)

    @action(detail=False, methods=["get"])
    def health(self, request):
        return Response(get_harness_provider().health())
