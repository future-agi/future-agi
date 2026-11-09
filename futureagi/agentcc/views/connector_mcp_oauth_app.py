"""Catalogue OAuth app configuration; secrets are write-only and sealed."""

from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from agentcc.permissions import IsOrgGatewayAdmin
from agentcc.serializers.contracts import AgentccErrorResponseSerializer
from agentcc.services import connector_catalog as catalog
from agentcc.services import connector_mcp_oauth_app as apps
from agentcc.services import mcp_credentials as vault
from agentcc.services.config_push import OrgConfigRejected
from agentcc.services.mcp_upstream_oauth import ConnectRefused
from agentcc.views.connector_oauth_clients import _SealedSecretField
from agentcc.views.mcp_probe import MCPProbeOrgThrottle
from tfc.utils.api_contracts import validated_request
from tfc.utils.general_methods import GeneralMethods

ERRORS = dict.fromkeys(
    (400, 403, 404, 409, 429, 502, 503), AgentccErrorResponseSerializer
)


class MCPOAuthDiscoveryRequestSerializer(serializers.Serializer):
    provider = serializers.CharField(max_length=64, required=False)
    discovery_url = serializers.CharField(max_length=2048, trim_whitespace=False)
    url_variables = serializers.DictField(
        child=serializers.CharField(), required=False, default=dict
    )


class MCPOAuthAppRequestSerializer(MCPOAuthDiscoveryRequestSerializer):
    mode = serializers.ChoiceField(choices=["shared", "per_user"])
    registration = serializers.ChoiceField(choices=["dcr", "manual"])
    client_id = serializers.CharField(
        max_length=vault.URL_MAX_LENGTH,
        trim_whitespace=False,
        required=False,
        write_only=True,
    )
    client_secret = _SealedSecretField(required=False)

    def validate(self, attrs):
        present = {name for name in ("client_id", "client_secret") if name in attrs}
        if attrs["registration"] == "manual" and len(present) != 2:
            raise serializers.ValidationError(
                "A manual client needs both client_id and client_secret."
            )
        if attrs["registration"] == "dcr" and present:
            raise serializers.ValidationError(
                "Dynamic registration takes no manual client credentials."
            )
        return attrs


class MCPOAuthConfigurationSerializer(serializers.Serializer):
    configured = serializers.BooleanField()
    mode = serializers.ChoiceField(choices=["shared", "per_user"])
    registration = serializers.ChoiceField(choices=["dcr", "manual"])
    has_secret = serializers.BooleanField()
    discovery_url = serializers.CharField()
    redirect_uri = serializers.CharField()
    version = serializers.IntegerField(required=False)
    gateway_synced = serializers.BooleanField(required=False)


class MCPOAuthConfigurationResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = MCPOAuthConfigurationSerializer()


class MCPOAuthDiscoverySerializer(serializers.Serializer):
    issuer = serializers.CharField()
    dcr_supported = serializers.BooleanField()
    pkce_s256 = serializers.BooleanField()
    redirect_uri = serializers.CharField()


class MCPOAuthDiscoveryResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = MCPOAuthDiscoverySerializer()


class _View(APIView):
    permission_classes = [IsAuthenticated]
    gm = GeneralMethods()

    def get_permissions(self):
        permissions = super().get_permissions()
        if self.request.method != "GET":
            permissions.append(IsOrgGatewayAdmin())
        return permissions

    def get_throttles(self):
        return [MCPProbeOrgThrottle()] if self.request.method == "POST" else []

    def call(self, request, operation, *args, created=False):
        org = getattr(request, "organization", None)
        if org is None:
            return self.gm.bad_request("Organization context is required")
        try:
            result = operation(*args)
        except ConnectRefused as error:
            return self.gm.custom_error_response(
                error.status, str(error), code=error.code
            )
        except catalog.ConnectorCatalogError as error:
            status = 400
            if isinstance(error, catalog.ConnectorNotFound):
                status = 404
            elif isinstance(error, catalog.ConnectorConflict):
                status = 409
            elif isinstance(error, catalog.CatalogUnavailable):
                status = 503
            return self.gm.custom_error_response(status, str(error), code=error.code)
        except (vault.MCPCredentialError, OrgConfigRejected):
            return self.gm.custom_error_response(
                400,
                "OAuth configuration was refused. Check the client and server settings.",
                code="oauth_configuration_refused",
            )
        response = self.gm.success_response(result, status=201 if created else 200)
        response["Cache-Control"] = "no-store"
        return response


class MCPOAuthAppView(_View):
    @validated_request(
        responses={200: MCPOAuthConfigurationResponseSerializer, **ERRORS}
    )
    def get(self, request, app_id):
        return self.call(
            request, apps.configuration, getattr(request, "organization", None), app_id
        )

    @validated_request(
        request_serializer=MCPOAuthAppRequestSerializer,
        responses={201: MCPOAuthConfigurationResponseSerializer, **ERRORS},
        request_methods=["POST"],
        reject_unknown_fields=True,
    )
    def post(self, request, app_id):
        return self.call(
            request,
            apps.configure,
            getattr(request, "organization", None),
            app_id,
            request.validated_data,
            request.user,
            created=True,
        )


class MCPOAuthDiscoveryView(_View):
    @validated_request(
        request_serializer=MCPOAuthDiscoveryRequestSerializer,
        responses={200: MCPOAuthDiscoveryResponseSerializer, **ERRORS},
        request_methods=["POST"],
        reject_unknown_fields=True,
    )
    def post(self, request, app_id):
        return self.call(request, apps.test_discovery, app_id, request.validated_data)
