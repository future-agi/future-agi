"""The connector catalog API (CN9), under /agentcc/connectors/.

- ``GET catalog/`` lists the apps the org can add from the catalog, or has
  added, filtered by category, q, kind, tier and connected; paginated.
- ``GET catalog/{app}/`` is one of them.
- ``POST apps/{app}/add/ {provider, url_variables, mode, credential}`` adds
  one to the org's MCP config; ``DELETE`` on the same path removes it.

Reads need a login in the org; adding and removing need an org Owner or Admin
(``IsOrgGatewayAdmin``). An add carries the org's credential for the provider
(``credential.value``; none for a provider that needs none). It lists the
provider's tools through the gateway's probe with it, sets each tool's
posture from its annotations, and stores the credential in the MCP
credential vault in the same config version (CN-ADD). Adds share the probe's
10 a minute per org. No response or log carries a credential value. The
logic is the service's (agentcc.services.connector_catalog).
"""

from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.status import HTTP_201_CREATED
from rest_framework.views import APIView

from agentcc.permissions import IsOrgGatewayAdmin
from agentcc.serializers.contracts import AgentccErrorResponseSerializer
from agentcc.services import connector_catalog as catalog
from agentcc.services.config_push import OrgConfigRejected
from agentcc.services.mcp_credentials import MAX_SECRET_CHARS
from agentcc.services.org_config_writer import GATEWAY_SYNC_FAILED_MESSAGE
from agentcc.services.routing_key_rules import with_rejection_param
from agentcc.views.mcp_probe import MCPProbeOrgThrottle
from tfc.utils.api_contracts import validated_request
from tfc.utils.general_methods import GeneralMethods
from tfc.utils.pagination import ExtendedPageNumberPagination

_ERRORS = dict.fromkeys((400, 403, 404, 409, 502, 503), AgentccErrorResponseSerializer)
_STATUS = (
    (catalog.CatalogUnavailable, 503),
    (catalog.ConnectorNotFound, 404),
    (catalog.ConnectorConflict, 409),
    (catalog.ConnectorInvalid, 400),
    (catalog.ConnectorListingFailed, 502),
)


# --- Request contracts ----------------------------------------------------------


class _StrictString(serializers.CharField):
    """A JSON string as sent: a number or a boolean is refused, not coerced,
    and spaces are kept, so the catalog's own rules see the value as sent."""

    def __init__(self, **kwargs):
        kwargs.setdefault("trim_whitespace", False)
        super().__init__(**kwargs)

    def to_internal_value(self, data):
        if not isinstance(data, str):
            self.fail("invalid")
        return super().to_internal_value(data)


class ConnectorCatalogQuerySerializer(serializers.Serializer):
    category = serializers.RegexField(r"^[a-z][a-z0-9-]{0,31}$", required=False)
    q = serializers.CharField(max_length=100, required=False)
    kind = serializers.ChoiceField(choices=catalog.PROVIDER_KINDS, required=False)
    tier = serializers.ChoiceField(choices=catalog.TIERS, required=False)
    # A choice, not a BooleanField: a query string that leaves a BooleanField
    # out reads as false, which would filter instead of not filtering.
    connected = serializers.ChoiceField(choices=["true", "false"], required=False)
    page = serializers.IntegerField(min_value=1, required=False)
    limit = serializers.IntegerField(min_value=1, max_value=200, required=False)


class _CredentialValueSerializer(serializers.Serializer):
    """The org's secret for the provider, kept exactly as sent (the vault
    refuses surrounding spaces and control characters). Where it goes comes
    from the catalog, never from the request."""

    value = _StrictString(max_length=MAX_SECRET_CHARS, write_only=True)


class ConnectorAddRequestSerializer(serializers.Serializer):
    """The org comes from the session. ``provider`` defaults to the app's
    default provider, ``mode`` to agent (one shared account). ``credential``
    is required for a provider whose credential mode is shared, and refused
    for one that needs none."""

    provider = _StrictString(max_length=64, required=False)
    mode = serializers.ChoiceField(
        choices=catalog.ACCOUNT_MODES, required=False, default=catalog.AGENT
    )
    url_variables = serializers.DictField(
        child=_StrictString(), required=False, default=dict
    )
    credential = _CredentialValueSerializer(required=False)


# --- Response contracts ---------------------------------------------------------


class _VendorSerializer(serializers.Serializer):
    name = serializers.CharField(allow_blank=True)
    domains = serializers.ListField(child=serializers.CharField())


class _IconSerializer(serializers.Serializer):
    light = serializers.CharField()
    dark = serializers.CharField()


class _ToolSerializer(serializers.Serializer):
    name = serializers.CharField()
    title = serializers.CharField(allow_blank=True)
    description = serializers.CharField(allow_blank=True)
    read_only = serializers.BooleanField()
    destructive = serializers.BooleanField()
    idempotent = serializers.BooleanField()
    open_world = serializers.BooleanField()
    gateway_posture = serializers.ChoiceField(choices=[catalog.ALLOW, catalog.DENY])
    reason = serializers.CharField(allow_null=True)


class _URLVariableSerializer(serializers.Serializer):
    name = serializers.CharField()
    description = serializers.CharField()
    pattern = serializers.CharField()
    default = serializers.CharField(allow_null=True)


class _CredentialSerializer(serializers.Serializer):
    """What the server's credential is, and what an admin stores in the vault
    for it (kind, header_name, in and name, as POST /agentcc/mcp/credentials/
    takes them). Never a value."""

    mode = serializers.ChoiceField(choices=["none", "shared", "per_user"])
    kind = serializers.CharField(allow_null=True)
    header_name = serializers.CharField(allow_blank=True)
    name = serializers.CharField(
        allow_blank=True,
        help_text="The header (Authorization for a bearer), query parameter or "
        "URL variable the key goes in; empty with no credential.",
    )
    label = serializers.CharField(allow_blank=True)
    help = serializers.CharField(allow_blank=True)
    help_url = serializers.CharField(allow_blank=True)

    def get_fields(self):
        # ``in`` is a Python keyword, so it is declared here.
        fields = super().get_fields()
        fields["in"] = serializers.ChoiceField(
            choices=["", "header", "query", "url_variable"],
            allow_blank=True,
            help_text="Where the key goes: header, query or url_variable; empty "
            "with no credential.",
        )
        return fields


class _AddedCredentialSerializer(_CredentialSerializer):
    connected = serializers.BooleanField()


class _OwnOAuthAppSerializer(serializers.Serializer):
    client_group = serializers.CharField()
    redirect_uri = serializers.CharField()
    required = serializers.BooleanField()


class _ProviderConnectSerializer(serializers.Serializer):
    """How a per-person provider connects. A K2 pilot's sign-in carries
    ``state``, ``reason`` and ``own_app``; a discovered MCP OAuth provider
    carries ``configure_oauth``. ``served`` is always present."""

    served = serializers.BooleanField()
    state = serializers.ChoiceField(
        choices=["ready", "registration_required"], required=False
    )
    reason = serializers.CharField(allow_blank=True, required=False)
    own_app = _OwnOAuthAppSerializer(required=False)
    configure_oauth = serializers.BooleanField(required=False)


class _ProviderSerializer(serializers.Serializer):
    id = serializers.CharField()
    version = serializers.CharField()
    catalog_ref = serializers.CharField()
    kind = serializers.CharField()
    publisher = serializers.CharField()
    vendor_status = serializers.CharField()
    tier = serializers.CharField()
    lifecycle = serializers.CharField()
    account_modes = serializers.ListField(child=serializers.CharField())
    transport = serializers.CharField()
    era = serializers.CharField()
    url_template = serializers.CharField()
    url_variables = _URLVariableSerializer(many=True)
    credential = _CredentialSerializer()
    tools = _ToolSerializer(many=True)
    connect = _ProviderConnectSerializer(required=False)


class _VirtualServerRefSerializer(serializers.Serializer):
    slug = serializers.CharField()
    id = serializers.CharField(allow_null=True)


class _BaselineSerializer(serializers.Serializer):
    """The provider's first listing, taken at add: what the virtual server
    composes is what was allowed then."""

    tool_count = serializers.IntegerField(min_value=0)
    tools_hash = serializers.RegexField(
        r"^[0-9a-f]{64}$",
        help_text="The gateway's hash of the listing (mcp-probe-v1 tools_hash).",
    )


class _ServerSerializer(serializers.Serializer):
    server_id = serializers.CharField()
    catalog_ref = serializers.CharField()
    provider = serializers.CharField()
    version = serializers.CharField()
    credential_mode = serializers.CharField()
    enabled = serializers.BooleanField()
    connected = serializers.BooleanField()
    virtual_server = _VirtualServerRefSerializer(allow_null=True)
    start_url = serializers.CharField(required=False, allow_null=True)


class ConnectorAppSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()
    description = serializers.CharField()
    vendor = _VendorSerializer()
    ee_category = serializers.CharField()
    group = serializers.CharField()
    categories = serializers.ListField(child=serializers.CharField())
    tags = serializers.ListField(child=serializers.CharField())
    docs_url = serializers.CharField()
    icon = _IconSerializer(allow_null=True)
    lifecycle = serializers.CharField()
    default_provider = serializers.CharField(allow_null=True)
    providers = _ProviderSerializer(many=True)
    connected = serializers.BooleanField()
    servers = _ServerSerializer(many=True)


class _CatalogMetaSerializer(serializers.Serializer):
    version = serializers.CharField()
    sha256 = serializers.CharField()
    source_commit = serializers.CharField()


class ConnectorCatalogListResponseSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)
    total_pages = serializers.IntegerField()
    current_page = serializers.IntegerField()
    catalog = _CatalogMetaSerializer()
    results = ConnectorAppSerializer(many=True)


class ConnectorAppResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ConnectorAppSerializer()


class ConnectorAddResultSerializer(serializers.Serializer):
    app = serializers.CharField()
    provider = serializers.CharField()
    catalog_ref = serializers.CharField()
    server_id = serializers.CharField()
    mode = serializers.CharField()
    url = serializers.CharField()
    url_variables = serializers.DictField(child=serializers.CharField())
    credential = _AddedCredentialSerializer()
    virtual_server = _VirtualServerRefSerializer()
    tools = _ToolSerializer(
        many=True, help_text="The tools the provider listed at add, with posture."
    )
    baseline = _BaselineSerializer(required=False)
    start_url = serializers.CharField(required=False, allow_null=True)
    mcp_enabled = serializers.BooleanField()
    version = serializers.IntegerField()
    gateway_synced = serializers.BooleanField()
    gateway_warning = serializers.CharField(required=False)


class ConnectorAddResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ConnectorAddResultSerializer()


class ConnectorRemoveResultSerializer(serializers.Serializer):
    app = serializers.CharField()
    removed_servers = serializers.ListField(child=serializers.CharField())
    removed_virtual_servers = serializers.ListField(child=serializers.CharField())
    revoked_credentials = serializers.ListField(child=serializers.UUIDField())
    version = serializers.IntegerField()
    gateway_synced = serializers.BooleanField()
    gateway_warning = serializers.CharField(required=False)


class ConnectorRemoveResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ConnectorRemoveResultSerializer()


# --- Views -------------------------------------------------------------------


class _CatalogPagination(ExtendedPageNumberPagination):
    page_size = 50
    max_page_size = 200

    def get_paginated_response(self, data, bundle=None):
        response = super().get_paginated_response(data)
        response.data["catalog"] = {
            "version": bundle.version,
            "sha256": bundle.sha256,
            "source_commit": bundle.source_commit,
        }
        return response


class _CatalogView(APIView):
    """Reads need a login in the org; writes an org gateway admin."""

    permission_classes = [IsAuthenticated]
    write_methods = frozenset({"POST", "DELETE"})
    _gm = GeneralMethods()

    def get_permissions(self):
        permissions = super().get_permissions()
        if self.request.method in self.write_methods:
            permissions.append(IsOrgGatewayAdmin())
        return permissions

    def _refused(self, error):
        if isinstance(error, OrgConfigRejected):
            return with_rejection_param(self._gm.bad_request(error), error)
        status = next(code for kind, code in _STATUS if isinstance(error, kind))
        return self._gm.custom_error_response(status, str(error), code=error.code)

    def _no_org(self):
        return self._gm.bad_request("Organization context is required")

    def _written(self, result, status=None):
        if not result["gateway_synced"]:
            result["gateway_warning"] = GATEWAY_SYNC_FAILED_MESSAGE
        if status is None:
            return self._gm.success_response(result)
        return self._gm.success_response(result, status=status)


class ConnectorCatalogListView(_CatalogView):
    @validated_request(
        query_serializer=ConnectorCatalogQuerySerializer,
        responses={200: ConnectorCatalogListResponseSerializer, **_ERRORS},
        reject_unknown_fields=True,
    )
    def get(self, request):
        org = getattr(request, "organization", None)
        if org is None:
            return self._no_org()
        query = request.validated_query_data
        connected = query.get("connected")
        try:
            bundle, apps = catalog.catalog_for_org(
                org,
                request.user,
                category=query.get("category"),
                q=query.get("q"),
                kind=query.get("kind"),
                tier=query.get("tier"),
                connected=None if connected is None else connected == "true",
            )
        except catalog.ConnectorCatalogError as e:
            return self._refused(e)
        paginator = _CatalogPagination()
        page = paginator.paginate_queryset(apps, request, view=self)
        return paginator.get_paginated_response(page, bundle)


class ConnectorCatalogAppView(_CatalogView):
    @validated_request(responses={200: ConnectorAppResponseSerializer, **_ERRORS})
    def get(self, request, app_id):
        org = getattr(request, "organization", None)
        if org is None:
            return self._no_org()
        try:
            app = catalog.app_for_org(org, request.user, app_id)
        except catalog.ConnectorCatalogError as e:
            return self._refused(e)
        return self._gm.success_response(app)


class ConnectorAddView(_CatalogView):
    def get_throttles(self):
        # An add calls the gateway's probe, so it also spends the probe's
        # budget (10 a minute per org, shared with Test connection).
        throttles = super().get_throttles()
        if self.request.method == "POST":
            throttles.append(MCPProbeOrgThrottle())
        return throttles

    @validated_request(
        request_serializer=ConnectorAddRequestSerializer,
        responses={201: ConnectorAddResponseSerializer, **_ERRORS},
        request_methods=["POST"],
        reject_unknown_fields=True,
    )
    def post(self, request, app_id):
        org = getattr(request, "organization", None)
        if org is None:
            return self._no_org()
        data = request.validated_data
        credential = data.get("credential")
        try:
            result = catalog.add_connector(
                org,
                app_id,
                provider_id=data.get("provider"),
                mode=data["mode"],
                url_variables=data["url_variables"],
                actor=request.user,
                credential=credential["value"] if credential else None,
            )
        except (catalog.ConnectorCatalogError, OrgConfigRejected) as e:
            return self._refused(e)
        return self._written(result, status=HTTP_201_CREATED)

    @validated_request(responses={200: ConnectorRemoveResponseSerializer, **_ERRORS})
    def delete(self, request, app_id):
        org = getattr(request, "organization", None)
        if org is None:
            return self._no_org()
        try:
            result = catalog.remove_connector(org, app_id, actor=request.user)
        except (catalog.ConnectorCatalogError, OrgConfigRejected) as e:
            return self._refused(e)
        return self._written(result)
