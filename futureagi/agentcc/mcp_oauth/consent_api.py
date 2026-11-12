"""The consent screen's API, called by the console page a signed-in person
lands on after ``/authorize``.

``GET /agentcc/mcp-oauth/consent/<request_id>/`` shows the client, where it
will send the person back, the organization(s) the person may grant it in and
the scopes. ``POST`` the same path with ``{"decision": "approve" | "deny",
"organization_id"}`` decides it and answers the URL the browser goes to next.
``GET /agentcc/mcp-oauth/consents/`` lists live consents in the selected
organization: the person's own by default, or all members' when an Owner or
Admin requests ``scope=organization``. ``DELETE /agentcc/mcp-oauth/consents/<id>/``
revokes only the person's own consent in that organization and its refresh
tokens, so the client's access ends within 15 minutes. List and revoke require
active membership in the selected organization.
All endpoints need the person's own console sign-in (the platform's bearer
authentication); the first person to open a sign-in request owns it, and anyone
else gets 404.
"""

from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from agentcc.mcp_oauth import authorize, resources, revocation
from agentcc.permissions import require_gateway_admin
from agentcc.serializers.contracts import AgentccErrorResponseSerializer
from tfc.utils.api_contracts import validated_request
from tfc.utils.general_methods import GeneralMethods

_ERRORS = {
    400: AgentccErrorResponseSerializer,
    403: AgentccErrorResponseSerializer,
    404: AgentccErrorResponseSerializer,
}
_NOT_FOUND = "This sign-in request has expired or was already used."
_NO_ORG = (
    "You are not a member of an organization that holds this MCP server. "
    "Ask an admin of its organization to invite you."
)
_CONSENT_MEMBERSHIP_REQUIRED = (
    "You must be an active member of the selected organization "
    "to manage MCP client connections."
)


class _ClientSerializer(serializers.Serializer):
    client_id = serializers.CharField()
    name = serializers.CharField()
    kind = serializers.CharField()
    verified = serializers.BooleanField()
    first_party = serializers.BooleanField()


class _OrganizationSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()


class _ScopeSerializer(serializers.Serializer):
    name = serializers.CharField()
    description = serializers.CharField()


class ConsentRequestSerializer(serializers.Serializer):
    client = _ClientSerializer()
    redirect_uri = serializers.CharField()
    redirect_host = serializers.CharField()
    resource = serializers.CharField()
    virtual_server = serializers.CharField(allow_null=True)
    organizations = _OrganizationSerializer(many=True)
    scopes = _ScopeSerializer(many=True)
    pre_approved = serializers.BooleanField()


class ConsentRequestResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ConsentRequestSerializer()


class ConsentDecisionRequestSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(choices=["approve", "deny"])
    organization_id = serializers.UUIDField(required=False)


class _RedirectSerializer(serializers.Serializer):
    redirect_to = serializers.CharField()


class ConsentDecisionResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = _RedirectSerializer()


class _ConsoleConsentView(APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        response["Referrer-Policy"] = "no-referrer"
        return response


class ConsentRequestView(_ConsoleConsentView):
    @validated_request(
        responses={200: ConsentRequestResponseSerializer, **_ERRORS},
        request_methods=["GET"],
    )
    def get(self, request, request_id):
        try:
            result = authorize.describe(request_id, request.user)
        except authorize.PendingNotFound:
            return self._gm.not_found(_NOT_FOUND)
        except authorize.NoOrganization:
            return self._gm.forbidden_response(_NO_ORG)
        return self._gm.success_response(result)

    @validated_request(
        request_serializer=ConsentDecisionRequestSerializer,
        responses={200: ConsentDecisionResponseSerializer, **_ERRORS},
        request_methods=["POST"],
        reject_unknown_fields=True,
    )
    def post(self, request, request_id):
        data = request.validated_data
        org_id = data.get("organization_id")
        try:
            target = authorize.decide(
                request_id,
                request.user,
                approve=data["decision"] == "approve",
                organization_id=str(org_id) if org_id else None,
            )
        except authorize.PendingNotFound:
            return self._gm.not_found(_NOT_FOUND)
        except authorize.NoOrganization:
            return self._gm.forbidden_response(_NO_ORG)
        except ValueError as e:
            return self._gm.bad_request(str(e))
        return self._gm.success_response({"redirect_to": target})


class _ConsentOrgSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()


class _ConsentUserSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()


class ConsentListQuerySerializer(serializers.Serializer):
    scope = serializers.ChoiceField(choices=["self", "organization"], default="self")


class ConsentSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    client_id = serializers.CharField()
    client_name = serializers.CharField()
    client_kind = serializers.CharField()
    organization = _ConsentOrgSerializer()
    resource = serializers.CharField()
    scopes = serializers.ListField(child=serializers.CharField())
    granted_at = serializers.DateTimeField()
    user = _ConsentUserSerializer()
    can_revoke = serializers.BooleanField()


class ConsentListResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = ConsentSerializer(many=True)


class _RevokedSerializer(serializers.Serializer):
    revoked = serializers.BooleanField()


class ConsentRevokeResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = _RevokedSerializer()


class ConsentListView(_ConsoleConsentView):
    @validated_request(
        query_serializer=ConsentListQuerySerializer,
        responses={200: ConsentListResponseSerializer, **_ERRORS},
        request_methods=["GET"],
    )
    def get(self, request):
        """Live grants in the selected organization. Self by default; an Owner
        or Admin may request scope=organization. Consent remains person-owned."""
        org = getattr(request, "organization", None)
        if org is None or org.pk not in resources.member_org_ids(request.user):
            return self._gm.forbidden_response(_CONSENT_MEMBERSHIP_REQUIRED)
        org_wide = request.validated_query_data["scope"] == "organization"
        if org_wide:
            require_gateway_admin(request)
        rows = revocation.consents_for(request.user, org, org_wide=org_wide)
        return self._gm.success_response(ConsentSerializer(rows, many=True).data)


class ConsentDetailView(_ConsoleConsentView):
    @validated_request(
        responses={200: ConsentRevokeResponseSerializer, **_ERRORS},
        request_methods=["DELETE"],
    )
    def delete(self, request, consent_id):
        org = getattr(request, "organization", None)
        if org is None or org.pk not in resources.member_org_ids(request.user):
            return self._gm.forbidden_response(_CONSENT_MEMBERSHIP_REQUIRED)
        if not revocation.revoke_consent(request.user, consent_id, org):
            return self._gm.not_found("Consent not found.")
        return self._gm.success_response({"revoked": True})
