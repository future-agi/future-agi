"""Thin foundation routes; broader context authoring remains separately integrated."""

from math import ceil
from uuid import UUID

from django.utils.http import parse_etags
from drf_yasg import openapi
from rest_framework.response import Response
from rest_framework.views import APIView

from model_hub.authentication.skills_context import (
    ContextCredentialAuthentication,
    ContextPurposePermission,
)
from model_hub.selectors.skills_hub_v12 import credentials_for_scope
from model_hub.serializers import skills_hub_v12 as s
from model_hub.serializers.skills_hub import SkillsError
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.v12 import consumption, credentials
from model_hub.services.skills_hub.v12.types import ResourceKind
from model_hub.views.skills_hub import SkillsView, domain_error
from model_hub.views.skills_hub import contract as skill_contract
from tfc.utils.general_methods import GeneralMethods

IDEMPOTENCY = openapi.Parameter(
    "Idempotency-Key",
    openapi.IN_HEADER,
    type=openapi.TYPE_STRING,
    format="uuid",
    required=True,
)
RESOLUTION = openapi.Parameter(
    "X-Context-Resolution",
    openapi.IN_HEADER,
    type=openapi.TYPE_STRING,
    format="uuid",
    required=True,
)


def contract(*args, **kwargs):
    return skill_contract(*args, responses={503: SkillsError}, **kwargs)


def header_uuid(request, name, *, required=True):
    value = request.headers.get(name)
    if value is None:
        if required:
            raise SkillsHubError(
                "context_precondition_required", f"{name} is required.", status=428
            )
        return None
    try:
        return UUID(value)
    except (ValueError, TypeError, AttributeError) as exc:
        raise SkillsHubError(
            "invalid_selector", f"{name} must be a UUID.", status=400
        ) from exc


class ContextCredentialsView(SkillsView):
    private_resource = True

    @contract(
        s.CredentialIssued,
        body=s.CredentialInput,
        status=201,
        manual_parameters=[IDEMPOTENCY],
    )
    def post(self, request):
        result = credentials.issue_credential(
            principal=request.skill_principal,
            request=request.validated_data,
            idempotency_key=header_uuid(request, "Idempotency-Key"),
        )
        response = self.output(
            request,
            s.CredentialIssued,
            result,
            status=201 if result["secret_available"] else 200,
        )
        response["Cache-Control"] = "private, no-store"
        return response

    @contract(s.CredentialPage, query=s.PageQuery)
    def get(self, request):
        credentials.require_credential_admin(principal=request.skill_principal)
        page, size = (
            request.validated_query_data["page"],
            request.validated_query_data["page_size"],
        )
        rows = credentials_for_scope(scope=request.skill_principal.scope)
        count = rows.count()
        return self.output(
            request,
            s.CredentialPage,
            {
                "results": [
                    credentials.credential_metadata(row)
                    for row in rows[(page - 1) * size : page * size]
                ],
                "count": count,
                "page": page,
                "page_size": size,
                "total_pages": ceil(count / size),
            },
        )


class ContextCredentialView(SkillsView):
    private_resource = True

    @contract(status=204)
    def delete(self, request, credential_id):
        credentials.revoke_credential(
            principal=request.skill_principal, credential_id=credential_id
        )
        return Response(status=204)


class ConsumptionView(APIView):
    authentication_classes = [ContextCredentialAuthentication]
    permission_classes = [ContextPurposePermission]
    context_purpose = "consume"
    resource_kind: ResourceKind = "skill"

    def handle_exception(self, exc):
        if isinstance(exc, SkillsHubError):
            return domain_error(exc)
        return super().handle_exception(exc)

    def output(self, serializer, value):
        return GeneralMethods().success_response(serializer(value).data)

    def continuation(self, request, resource_id, number):
        return {
            "principal": request.auth,
            "kind": self.resource_kind,
            "resource_id": resource_id,
            "number": number,
            "resolution_id": header_uuid(request, "X-Context-Resolution"),
        }


class ConsumptionResolveView(ConsumptionView):
    @contract(s.ConsumptionManifest, query=s.ResolveQuery)
    def get(self, request):
        result = consumption.resolve_consumption(
            principal=request.auth,
            kind=self.resource_kind,
            **request.validated_query_data,
        )
        # A resolution is a new lease: return its body even on a matching digest.
        response = self.output(s.ConsumptionManifest, result)
        response["ETag"] = f'"{result["digest"]}"'
        response["Cache-Control"] = "private, no-store"
        return response


class ConsumptionManifestView(ConsumptionView):
    @contract(
        s.ConsumptionManifest, query=s.ManifestQuery, manual_parameters=[RESOLUTION]
    )
    def get(self, request, resource_id, number):
        result = consumption.read_consumption_manifest(
            **self.continuation(request, resource_id, number),
            **request.validated_query_data,
        )
        etag = f'"{result["digest"]}"'
        # Authorization, lease expiry and the closure quarantine precede 304.
        response = (
            Response(status=304)
            if etag in parse_etags(request.headers.get("If-None-Match", ""))
            else self.output(s.ConsumptionManifest, result)
        )
        response["ETag"] = etag
        response["Cache-Control"] = "private, no-store"
        return response


class ConsumptionFileView(ConsumptionView):
    @contract(s.ConsumptionChunk, query=s.FileQuery, manual_parameters=[RESOLUTION])
    def get(self, request, resource_id, number):
        result = consumption.read_consumption_file(
            **self.continuation(request, resource_id, number),
            **request.validated_query_data,
        )
        response = self.output(s.ConsumptionChunk, result)
        response["Cache-Control"] = "private, no-store"
        return response


class ConsumptionAccessView(ConsumptionView):
    @contract(s.AccessCheck, body=s.EmptyInput, manual_parameters=[RESOLUTION])
    def post(self, request, resource_id, number):
        return self.output(
            s.AccessCheck,
            consumption.check_consumption_access(
                **self.continuation(request, resource_id, number)
            ),
        )
