"""Member-scoped partner connections, opt-in and hidden while disabled."""

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import HttpResponseRedirect
from rest_framework.exceptions import NotFound, PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from agentcc.mcp_personal_auth import (
    IsIndividualPrincipal,
    PersonalConnectionAuthentication,
    is_individual_member,
)
from agentcc.models.mcp_credential import AgentccMCPCredential
from agentcc.models.mcp_vendor import AgentccMCPVendorBinding as Binding
from agentcc.models.mcp_vendor import AgentccMCPVendorConnection as Connection
from agentcc.permissions import is_gateway_admin
from agentcc.serializers.mcp_vendor import (
    VendorBindingDisabledResponse,
    VendorBindingDisableRequest,
    VendorBindingRequest,
    VendorBindingResponse,
    VendorConnectionResponse,
    VendorConnectionSelector,
    VendorConnectRequest,
    VendorConnectResponse,
)
from agentcc.services import mcp_upstream_oauth as connect
from agentcc.services import mcp_vendor_connections as lifecycle
from agentcc.services.mcp_vendor_credentials import enabled
from agentcc.services.mcp_vendor_http import VendorUnavailable
from tfc.utils.api_contracts import validated_request


def validated(serializer_class, data):
    from rest_framework.exceptions import ValidationError as Invalid

    serializer = serializer_class(data=data)
    if not isinstance(data, dict) and not hasattr(data, "keys"):
        raise Invalid("Expected an object.")
    if set(data) - set(serializer.fields):
        raise Invalid("Unknown fields are not accepted.")
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


class VendorView(APIView):
    authentication_classes = [PersonalConnectionAuthentication]
    permission_classes = [IsAuthenticated, IsIndividualPrincipal]

    def initial(self, request, *args, **kwargs):
        if not enabled():
            raise NotFound()
        super().initial(request, *args, **kwargs)
        if not is_individual_member(
            request.user, getattr(request, "organization", None), allow_legacy=False
        ):
            raise PermissionDenied("An active organization membership is required.")

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        response["Referrer-Policy"] = "no-referrer"
        return response


class VendorBindingsView(VendorView):
    @validated_request(
        request_serializer=VendorBindingRequest,
        responses={201: VendorBindingResponse},
        reject_unknown_fields=True,
    )
    def post(self, request):
        if not is_gateway_admin(request):
            raise PermissionDenied()
        data = validated(VendorBindingRequest, request.data)
        entry = connect.server_entry(request.organization.pk, data["server_id"]) or {}
        config = entry.get("credential") or {}
        if (
            config.get("mode") != "vendor_end_user"
            or config.get("vendor") != data["vendor"]
        ):
            return Response({"error": "vendor_server_required"}, status=409)
        credential = AgentccMCPCredential.objects.filter(
            pk=data.pop("credential_id"),
            organization=request.organization,
            server_id=data["server_id"],
            scope="shared",
            status="active",
        ).first()
        if credential is None:
            raise NotFound()
        try:
            with transaction.atomic():
                row = Binding.objects.create(
                    organization=request.organization, credential=credential, **data
                )
        except (ValidationError, IntegrityError):
            return Response({"error": "binding_conflict"}, status=409)
        return Response(
            {
                "id": str(row.pk),
                "server_id": row.server_id,
                "vendor": row.vendor,
                "enabled": row.enabled,
            },
            status=201,
        )


class VendorBindingDetailView(VendorView):
    def row(self, request, binding_id):
        if not is_gateway_admin(request):
            raise PermissionDenied()
        row = Binding.objects.filter(
            pk=binding_id, organization=request.organization
        ).first()
        if row is None:
            raise NotFound()
        return row

    @validated_request(
        request_serializer=VendorBindingDisableRequest,
        responses={200: VendorBindingDisabledResponse},
        reject_unknown_fields=True,
        validation_error_response=lambda errors: Response(
            {"error": "disable_only"}, status=400
        ),
    )
    def patch(self, request, binding_id):
        row = self.row(request, binding_id)
        # Configuration is immutable; replace a cleaned-up binding to change it.
        if request.data != {"enabled": False}:
            return Response({"error": "disable_only"}, status=400)
        lifecycle.disable_binding(row.pk)
        return Response({"id": str(row.pk), "enabled": False})

    def delete(self, request, binding_id):
        row = self.row(request, binding_id)
        try:
            lifecycle.disable_binding(row.pk, delete=True)
        except lifecycle.ConnectionRefused as error:
            return Response({"error": error.code}, status=error.status)
        return Response(status=204)


class VendorConnectionsView(VendorView):
    def rows(self, request):
        data = validated(VendorConnectionSelector, request.query_params)
        rows = Connection.objects.select_related("binding").filter(
            binding__organization=request.organization
        )
        if not is_gateway_admin(request):
            if data.get("user_id") and data["user_id"] != request.user.pk:
                raise PermissionDenied()
            rows = rows.filter(user=request.user, source="member")
        elif data.get("user_id"):
            rows = rows.filter(user_id=data["user_id"])
        for name, lookup in (
            ("server_id", "binding__server_id"),
            ("connection_id", "id"),
        ):
            if data.get(name):
                rows = rows.filter(**{lookup: data[name]})
        return rows.order_by("created_at")

    def get(self, request):
        return Response(
            {
                "connections": VendorConnectionResponse(
                    self.rows(request), many=True
                ).data
            }
        )

    def delete(self, request):
        if not request.query_params.get("server_id") and not request.query_params.get(
            "connection_id"
        ):
            return Response({"error": "connection_selector_required"}, status=400)
        rows = [lifecycle.disconnect(row.pk) for row in self.rows(request)]
        return Response({"connections": VendorConnectionResponse(rows, many=True).data})


class VendorConnectView(VendorView):
    @validated_request(
        request_serializer=VendorConnectRequest,
        responses={200: VendorConnectResponse},
        reject_unknown_fields=True,
    )
    def post(self, request, server_id):
        data = validated(VendorConnectRequest, request.data)
        binding = Binding.objects.filter(
            organization=request.organization, server_id=server_id
        ).first()
        if binding is None:
            raise NotFound()
        identity = {"source": "member", "id": str(request.user.pk)}
        if data.get("asserted_id"):
            if not is_gateway_admin(request):
                raise PermissionDenied()
            identity = {"source": "asserted", "id": data["asserted_id"]}
        if data.get("binding"):
            opened = connect.open_binding(data["binding"])
            if identity["source"] != "member" or opened != {
                "org_id": str(request.organization.pk),
                "server_id": server_id,
                "user_id": str(request.user.pk),
            }:
                raise PermissionDenied(
                    "The connect binding belongs to another identity."
                )
        try:
            url = lifecycle.start(binding.pk, identity)
        except lifecycle.ConnectionRefused as error:
            return Response({"error": error.code}, status=error.status)
        except VendorUnavailable:
            return Response({"error": "vendor_unavailable"}, status=503)
        return Response({"connect_url": url})


def vendor_connect_page(request, server_id):
    # Public GET does not start a session or authenticate/provision a user.
    if not enabled():
        from django.http import HttpResponseNotFound

        return HttpResponseNotFound()
    binding = request.GET.get("binding", "")
    response = HttpResponseRedirect(
        connect.console_connect_url(server_id, binding if len(binding) <= 4096 else "")
    )
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "no-referrer"
    return response
