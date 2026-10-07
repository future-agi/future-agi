import base64
import binascii
import hashlib
import secrets
import traceback
from urllib.parse import urlencode

import requests
import structlog
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.http import HttpResponse, HttpResponseRedirect
from django.utils import timezone
from django.utils.http import urlsafe_base64_encode
from drf_yasg import openapi
from drf_yasg.utils import no_body, swagger_auto_schema
from rest_framework import viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView

from accounts.authentication import generate_encrypted_message
from accounts.gcp_marketplace_utils import encode_oauth_state, read_oauth_state
from accounts.gcp_marketplace_utils import process_signup as marketplace_signup
from accounts.models.auth_token import (
    AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES,
    AuthToken,
    AuthTokenType,
)
from accounts.models.user import User
from accounts.utils import first_signup, get_request_organization
from analytics.utils import (
    MixpanelEvents,
    MixpanelModes,
    get_mixpanel_properties,
    track_mixpanel_event,
)
from saml2_auth.forms import IDPUploadForm
from saml2_auth.models import SamlLoginAttempt, SAMLMetadataModel, SamlResponseCandidate
from saml2_auth.permissions import SAMLConfigPermission
from saml2_auth.serializers import (
    SAMLAuthLoginQuerySerializer,
    SAMLErrorResponseSerializer,
    SAMLIDPLoginQuerySerializer,
    SAMLIDPUploadDetailResponseSerializer,
    SAMLIDPUploadListResponseSerializer,
    SAMLOAuthCallbackQuerySerializer,
    SAMLSerializer,
    SAMLStringResponseSerializer,
    SAMLUrlResponseSerializer,
)
from saml2_auth.services import (
    SamlDenied,
    admit_attempt,
    build_sp_client,
    claim_attempt,
    issue_token,
    record_failure,
    require_active_membership,
    resolve_assertion_user,
    resolve_login_idp,
    resolve_login_user,
    store_candidate,
    update_attempt_request_id,
    validate_next_path,
    verify_assertion_bindings,
)
from tfc.settings.settings import (
    AUTH0_CALLBACK_URL,
    AUTH0_CLIENT_ID,
    AUTH0_CLIENT_SECRET,
    AUTH0_DOMAIN,
    BASE_DIR,  # noqa: F401 - retained as a non-I/O compatibility hook for integrations
    GITHUB_API_ENDPOINT,
    GITHUB_CALLBACK_URL,
    GITHUB_CLIENT_ID,
    GITHUB_CLIENT_SECRET,
    GITHUB_OAUTH_URL,
    GOOGLE_USERINFO_API,
    MICROSOFT_CALLBACK_URL,
    MICROSOFT_CLIENT_ID,
    MICROSOFT_CLIENT_SECRET,
    MICROSOFT_GRAPH_API,
    MICROSOFT_OAUTH_URL,
    default_error_next_url,
    default_next_url,
    get_assertion_url,
    get_entity_id,
    get_started_url,
)
from tfc.utils.api_contracts import validated_request
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)
security_logger = structlog.get_logger("saml2_auth.security")

SAML_REDIRECT_RESPONSES = {
    200: None,
    201: None,
    302: openapi.Response(description="Redirects to the configured frontend URL."),
    400: SAMLErrorResponseSerializer,
}

SAML_ACS_FORM_PARAMETERS = [
    openapi.Parameter(
        "SAMLResponse",
        openapi.IN_FORM,
        type=openapi.TYPE_STRING,
        required=True,
        description="Base64-encoded SAML response from the identity provider.",
    ),
    openapi.Parameter(
        "RelayState",
        openapi.IN_FORM,
        type=openapi.TYPE_STRING,
        required=False,
        description="Relay state configured for the organization IdP.",
    ),
]

SAML_COMPLETE_QUERY_PARAMETERS = [
    openapi.Parameter(
        "c",
        openapi.IN_QUERY,
        type=openapi.TYPE_STRING,
        required=True,
        description="One-time SAML response candidate key.",
    )
]

SAML_IDP_UPLOAD_FORM_PARAMETERS = [
    openapi.Parameter(
        "name",
        openapi.IN_FORM,
        type=openapi.TYPE_STRING,
        required=False,
        description="Display name for the identity provider.",
    ),
    openapi.Parameter(
        "identity_type",
        openapi.IN_FORM,
        type=openapi.TYPE_INTEGER,
        required=True,
        description="Identity provider type.",
    ),
    openapi.Parameter(
        "is_enabled",
        openapi.IN_FORM,
        type=openapi.TYPE_BOOLEAN,
        required=False,
        description="Whether this IdP is enabled.",
    ),
    openapi.Parameter(
        "file",
        openapi.IN_FORM,
        type=openapi.TYPE_FILE,
        required=False,
        description="SAML metadata XML file.",
    ),
]


def get_alias(request):
    return request.get_host().split(".")[0]


def _format_form_errors(errors):
    for field, messages in errors.items():
        if isinstance(messages, (list, tuple)):
            message = messages[0] if messages else "Invalid value."
        else:
            message = messages
        return f"{field}: {message}"
    return "Invalid request."


def _saml_denial_redirect(reason):
    security_logger.warning("saml_completion_denied", reason=reason)
    encoded = urlsafe_base64_encode(
        b"SAML is not enabled for your organization. Please contact your Administrator"
    )
    return HttpResponseRedirect(f"{default_error_next_url}&reason={encoded}")


def record_failure_for_candidate(candidate_key, reason):
    candidate = (
        SamlResponseCandidate.objects.filter(candidate_key=candidate_key)
        .only("attempt_id")
        .first()
    )
    if candidate:
        record_failure(candidate.attempt_id, reason)


class ACSView(APIView):
    _gm = GeneralMethods()
    parser_classes = [FormParser, MultiPartParser]
    permission_classes = (AllowAny,)
    authentication_classes = []

    @swagger_auto_schema(
        request_body=no_body,
        manual_parameters=SAML_ACS_FORM_PARAMETERS,
        runtime_request_validation=True,
        responses={**SAML_REDIRECT_RESPONSES},
    )
    def post(self, request, *args, **kwargs):
        try:
            if not settings.SAML_LOGIN_ENABLED:
                raise SamlDenied("idp_unavailable")
            content_length = request.META.get("CONTENT_LENGTH")
            if (
                content_length is None
                or int(content_length) > settings.SAML_MAX_ACS_BODY_BYTES
            ):
                raise SamlDenied("acs_malformed")
            if len(request.body) > settings.SAML_MAX_ACS_BODY_BYTES:
                raise SamlDenied("acs_malformed")
            relay_state = request.POST.get("RelayState")
            saml_response = request.POST.get("SAMLResponse")
            if not relay_state or not saml_response:
                raise SamlDenied("acs_malformed")
            try:
                payload = base64.b64decode(saml_response, validate=True)
            except (ValueError, binascii.Error):
                raise SamlDenied("acs_malformed") from None
            if len(payload) > settings.SAML_MAX_RESPONSE_BYTES:
                raise SamlDenied("acs_malformed")
            attempt = SamlLoginAttempt.objects.filter(
                relay_key=relay_state,
                state=SamlLoginAttempt.State.PENDING,
                expires_at__gt=timezone.now(),
            ).first()
            if attempt is None:
                raise SamlDenied("attempt_unavailable")
            candidate = store_candidate(attempt=attempt, payload=payload)
            response = HttpResponseRedirect(
                f"/saml2_auth/complete/?{urlencode({'c': candidate.candidate_key})}"
            )
            response.status_code = 303
            return response
        except (SamlDenied, ValueError):
            return _saml_denial_redirect("acs_malformed")


class CompleteView(APIView):
    permission_classes = (AllowAny,)
    authentication_classes = []

    @swagger_auto_schema(
        manual_parameters=SAML_COMPLETE_QUERY_PARAMETERS,
        responses={**SAML_REDIRECT_RESPONSES},
    )
    def get(self, request, *args, **kwargs):
        candidate_key = request.GET.get("c", "")
        cookie_name = None
        claimed_attempt_id = None
        try:
            candidate = SamlResponseCandidate.objects.only("attempt_id").get(
                candidate_key=candidate_key
            )
            attempt_ref = SamlLoginAttempt.objects.only("id").get(
                id=candidate.attempt_id
            )
            cookie_name = f"fai_saml_b_{attempt_ref.id.hex[:16]}"
            binder = request.COOKIES.get(cookie_name)
            if not binder:
                raise SamlDenied("browser_unbound")
            attempt, payload = claim_attempt(candidate_key=candidate_key, binder=binder)
            claimed_attempt_id = attempt.id
            idp = SAMLMetadataModel.objects.get(
                id=attempt.idp_id,
                deleted=False,
                is_enabled=True,
                organization_id=attempt.organization_id,
                security_generation=attempt.idp_generation,
            )
            if hashlib.sha256(idp.meta.encode()).hexdigest() != attempt.idp_meta_sha256:
                raise SamlDenied("idp_changed")
            from saml2 import BINDING_HTTP_POST

            parsed = build_sp_client(idp).parse_authn_request_response(
                base64.b64encode(payload).decode(),
                BINDING_HTTP_POST,
                outstanding={attempt.request_id: attempt.relay_key},
            )
            facts = verify_assertion_bindings(parsed, attempt, idp)
            subject = parsed.get_subject()
            user = resolve_assertion_user(
                parsed.get_identity() or {}, getattr(subject, "text", None)
            )
            require_active_membership(user, idp.organization)
            token = issue_token(
                attempt_id=attempt.id,
                user=user,
                not_on_or_after=facts.not_on_or_after,
            )
            security_logger.info(
                "saml_login_succeeded",
                org_id=str(idp.organization_id),
                user_id=str(user.id),
            )
            response = HttpResponseRedirect(
                f"{default_next_url}?{urlencode({'sso_token': token, 'next': attempt.next_path, 'auth': 'saml'})}"
            )
        except SamlDenied as exc:
            if claimed_attempt_id:
                record_failure(claimed_attempt_id, exc.reason)
            elif candidate_key:
                record_failure_for_candidate(candidate_key, exc.reason)
            response = _saml_denial_redirect(exc.reason)
        except (SAMLMetadataModel.DoesNotExist, SamlResponseCandidate.DoesNotExist):
            if claimed_attempt_id:
                record_failure(claimed_attempt_id, "response_invalid")
            elif candidate_key:
                record_failure_for_candidate(candidate_key, "response_invalid")
            response = _saml_denial_redirect("response_invalid")
        except Exception as exc:
            security_logger.warning(
                "saml_completion_rejected", exc_type=type(exc).__name__
            )
            if claimed_attempt_id:
                record_failure(claimed_attempt_id, "response_invalid")
            elif candidate_key:
                record_failure_for_candidate(candidate_key, "response_invalid")
            response = _saml_denial_redirect("response_invalid")
        if cookie_name:
            response.delete_cookie(cookie_name, path="/saml2_auth/")
        return response


class IDPLoginView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    _gm = GeneralMethods()

    @validated_request(
        query_serializer=SAMLIDPLoginQuerySerializer,
        responses={200: SAMLUrlResponseSerializer, 400: SAMLErrorResponseSerializer},
        reject_unknown_fields=True,
    )
    def get(self, request, *args, **kwargs):
        msg = "SSO is not enabled for your organisation. Please contact to your administration."
        try:
            if not settings.SAML_LOGIN_ENABLED:
                raise SamlDenied("idp_unavailable")
            work_email = request.validated_query_data.get("email", "")
            user = resolve_login_user(work_email)
            if user is None:
                raise SamlDenied("identity_missing")
            idp = resolve_login_idp(user)
            if idp is None:
                raise SamlDenied("idp_unavailable")
            cookie_names = [
                name for name in request.COOKIES if name.startswith("fai_saml_b_")
            ]
            if len(cookie_names) >= settings.SAML_MAX_PENDING_COOKIES_PER_BROWSER:
                raise SamlDenied("browser_capacity")
            binder = secrets.token_urlsafe(32)
            attempt = admit_attempt(
                user=user,
                idp=idp,
                binder=binder,
                next_path=validate_next_path(request.validated_query_data.get("next")),
            )
            request_id, info = build_sp_client(idp).prepare_for_authenticate(
                relay_state=attempt.relay_key
            )
            update_attempt_request_id(attempt, request_id)
            redirect_url = next(
                value for key, value in info["headers"] if key == "Location"
            )
            response = self._gm.success_response({"url": redirect_url})
            response.set_cookie(
                f"fai_saml_b_{attempt.id.hex[:16]}",
                binder,
                max_age=settings.SAML_ATTEMPT_TTL_SECONDS,
                httponly=True,
                samesite="Lax",
                secure=settings.ENV_TYPE not in {"local", "test", "development"},
                path="/saml2_auth/",
            )
            return response
        except (SamlDenied, StopIteration):
            security_logger.warning("saml_login_denied", reason="idp_unavailable")
            return self._gm.bad_request(msg)
        except Exception as exc:
            security_logger.warning(
                "saml_login_denied",
                reason="idp_unavailable",
                exc_type=type(exc).__name__,
            )
            return self._gm.bad_request(msg)


class AvailableIDPs(APIView):
    _gm = GeneralMethods()
    permission_classes = (AllowAny,)
    authentication_classes = []

    def get(self, request):
        try:
            email = request.GET.get("email")
            if not email:
                return self._gm.bad_request("Email is required")

            # if not is_work_email(email):
            #     return self._gm.bad_request("Only Work email is permitted")

            saml_objects = SAMLMetadataModel.objects.filter(
                organization=get_request_organization(request)
            )

            identity_types = [obj.get_identity_type for obj in saml_objects]

            return self._gm.success_response(identity_types)

        except Exception as e:
            traceback.print_exc()
            logger.error(e)
            return self._gm.bad_request(f"error: {e}")


class IDPUploadViews(viewsets.ModelViewSet):
    form = IDPUploadForm
    _gm = GeneralMethods()
    parser_classes = (FormParser, MultiPartParser)
    # authentication_classes = (ProgrammaticAuthentication,)
    permission_classes = (IsAuthenticated, SAMLConfigPermission)
    # rbac = 'idp'
    queryset = SAMLMetadataModel.objects.filter(deleted=False)
    lookup_field = "id"
    lookup_url_kwarg = "id"
    http_method_names = ["get", "post", "head", "delete", "options", "put"]
    parser_classes = (FormParser, MultiPartParser)

    def get_queryset(self):
        organization = get_request_organization(self.request)
        if organization is None:
            return SAMLMetadataModel.objects.none()
        return SAMLMetadataModel.objects.filter(
            deleted=False, organization=organization
        )

    def check_permissions(self, request):
        try:
            return super().check_permissions(request)
        except PermissionDenied:
            self._log_config_denied(
                request,
                self.kwargs.get(self.lookup_url_kwarg),
                "permission_denied",
            )
            raise

    def _metadata_too_large(self, request):
        content_length = request.META.get("CONTENT_LENGTH")
        maximum = getattr(settings, "SAML_MAX_METADATA_BYTES", 262144)
        try:
            if content_length is not None and int(content_length) > maximum:
                return True
        except ValueError:
            return True
        return len(request.body) > maximum

    def _request_data(self, request):
        if self._metadata_too_large(request):
            return None
        form = IDPUploadForm(request.POST, request.FILES)
        if not form.is_valid():
            return None
        data = form.cleaned_data.copy()
        metadata_file = data.pop("file", None)
        data.pop("organization", None)
        data.pop("relay_state", None)
        if metadata_file is not None:
            try:
                data["meta"] = metadata_file.read().decode()
            except (UnicodeDecodeError, OSError):
                return None
        return data

    @staticmethod
    def _revoke_idp_tokens(idp):
        """Revoke scoped SAML credentials once the additive token fields land."""

        if "origin_idp" not in {field.name for field in AuthToken._meta.get_fields()}:
            return
        AuthToken.no_workspace_objects.filter(origin_idp=idp, is_active=True).update(
            is_active=False
        )

    def _log_config_denied(self, request, target_id, reason):
        security_logger.warning(
            "saml_config_denied",
            actor_id=str(request.user.id),
            target_id=str(target_id) if target_id else None,
            method=request.method,
            reason=reason,
        )

    def get_serializer_class(self):
        if self.request.method == "GET":
            return SAMLSerializer
        # if self.request.method == "PUT":
        #     return WorkspaceTagsSerializer

    @swagger_auto_schema(
        responses={
            200: SAMLIDPUploadListResponseSerializer,
            400: SAMLErrorResponseSerializer,
            500: SAMLErrorResponseSerializer,
        }
    )
    def list(self, request, *args, **kwargs):
        try:
            # Get the response from parent class
            response = super().list(request, *args, **kwargs)
            # Convert response.data to a dictionary we can modify
            data = response.data.copy() if hasattr(response, "data") else {}

            data["acs_url"] = get_assertion_url
            data["audience_url"] = get_entity_id

            # Check if we have results and need to modify the name
            if data.get("results") and len(data["results"]) > 0:
                result = data["results"][0]
                if not result.get("name") or result.get("name") == "null":
                    result["name"] = next(
                        name
                        for val, name in SAMLMetadataModel.IDENTITY_CHOICES
                        if val == 1
                    )

            return self._gm.success_response(data)
        except Exception as e:
            logger.error(f"Error in IDPUploadViews.list: {str(e)}")  # Add logging
            return self._gm.internal_server_error_response(get_error_message("US25"))

    @swagger_auto_schema(
        responses={
            200: SAMLIDPUploadDetailResponseSerializer,
            400: SAMLErrorResponseSerializer,
            500: SAMLErrorResponseSerializer,
        }
    )
    def retrieve(self, request, *args, **kwargs):
        existing_saml_metadata_model = self.get_object()
        name = existing_saml_metadata_model.name
        if not name or name == "null":
            name = existing_saml_metadata_model.get_identity_type
        return self._gm.success_response(
            {
                "is_enabled": existing_saml_metadata_model.is_enabled,
                "identity_type": existing_saml_metadata_model.identity_type,
                "name": name,
                "acs_url": get_assertion_url,
                "audience_url": get_entity_id,
            }
        )

    @swagger_auto_schema(
        request_body=no_body,
        manual_parameters=SAML_IDP_UPLOAD_FORM_PARAMETERS,
        runtime_request_validation=True,
        responses={
            200: SAMLStringResponseSerializer,
            400: SAMLErrorResponseSerializer,
            500: SAMLErrorResponseSerializer,
        },
    )
    def create(self, request, *args, **kwargs):
        data = self._request_data(request)
        organization = get_request_organization(request)
        if data is None or organization is None:
            return self._gm.bad_request("Please select a XML file.")
        if SAMLMetadataModel.objects.filter(
            deleted=False, organization=organization
        ).exists():
            return self._gm.bad_request(
                "Maximum supported identity providers reached. Please edit or delete an existing IdP."
            )
        # relay_state is unique and no longer read for login (each attempt
        # gets its own relay key), so the server assigns an opaque value
        # rather than the empty default every upload would otherwise share.
        SAMLMetadataModel.objects.create(
            organization=organization,
            relay_state=f"idp-{secrets.token_hex(16)}",
            **data,
        )
        return self._gm.success_response("Success")

    @swagger_auto_schema(
        responses={
            200: SAMLStringResponseSerializer,
            400: SAMLErrorResponseSerializer,
            500: SAMLErrorResponseSerializer,
        }
    )
    def destroy(self, request, *args, **kwargs):
        obj = self.get_object()
        with transaction.atomic():
            obj = self.get_queryset().select_for_update().get(id=obj.id)
            obj.deleted = True
            obj.deleted_at = timezone.now()
            obj.security_generation += 1
            obj.save(update_fields=["deleted", "deleted_at", "security_generation"])
            self._revoke_idp_tokens(obj)
        security_logger.info(
            "saml_config_changed",
            actor_id=str(request.user.id),
            org_id=str(obj.organization_id),
            row_id=str(obj.id),
            changed_fields=["deleted"],
            generation=obj.security_generation,
        )
        return self._gm.success_response("Success")

    @swagger_auto_schema(
        request_body=no_body,
        manual_parameters=SAML_IDP_UPLOAD_FORM_PARAMETERS,
        runtime_request_validation=True,
        responses={
            200: SAMLStringResponseSerializer,
            400: SAMLErrorResponseSerializer,
            500: SAMLErrorResponseSerializer,
        },
    )
    def update(self, request, *args, **kwargs):
        original = self.get_object()
        data = self._request_data(request)
        if data is None:
            return self._gm.bad_request("Please select a XML file.")
        if (
            data.get("identity_type")
            and int(original.identity_type) != int(data["identity_type"])
            and "meta" not in data
        ):
            return self._gm.bad_request("Please select a XML file.")
        changed_fields = [
            field for field, value in data.items() if getattr(original, field) != value
        ]
        with transaction.atomic():
            row = self.get_queryset().select_for_update().get(id=original.id)
            for field, value in data.items():
                setattr(row, field, value)
            if changed_fields:
                row.security_generation += 1
                row.save(update_fields=[*changed_fields, "security_generation"])
                self._revoke_idp_tokens(row)
        if changed_fields:
            security_logger.info(
                "saml_config_changed",
                actor_id=str(request.user.id),
                org_id=str(original.organization_id),
                row_id=str(original.id),
                changed_fields=changed_fields,
                generation=row.security_generation,
            )
        return self._gm.success_response("Success")


import urllib.parse  # noqa: E402

from jose import jwt  # noqa: E402


class Auth0LoginView(APIView):
    _gm = GeneralMethods()
    permission_classes = (AllowAny,)
    authentication_classes = []

    @validated_request(
        query_serializer=SAMLAuthLoginQuerySerializer,
        responses={
            200: SAMLUrlResponseSerializer,
            400: SAMLErrorResponseSerializer,
        },
        reject_unknown_fields=True,
    )
    def get(self, request, *args, **kwargs):
        provider = request.validated_query_data.get("provider", None)
        if not provider:
            return self._gm.bad_request("Provider is required")

        onboarding_token = request.validated_query_data.get("onboarding_token") or ""

        # Microsoft's callback does not read the state back, so it would build a
        # second free organization and strand the paid one.
        if onboarding_token and provider == "microsoft":
            return self._gm.bad_request(
                "Marketplace sign-up supports Google and GitHub sign-in"
            )

        if provider == "google":
            params = {
                "response_type": "code",
                "client_id": AUTH0_CLIENT_ID,
                "redirect_uri": AUTH0_CALLBACK_URL,
                "scope": "openid profile email",
            }
            # A Marketplace customer who picks Google over the sign-up form must
            # still land in the organization the procurement account created.
            if onboarding_token:
                params["state"] = encode_oauth_state(onboarding_token)
            auth_url = f"https://{AUTH0_DOMAIN}/auth?" + urllib.parse.urlencode(params)
            return self._gm.success_response({"url": auth_url})
        elif provider == "github":
            params = {
                "client_id": GITHUB_CLIENT_ID,
                "redirect_uri": GITHUB_CALLBACK_URL,
                "scope": "user:email",  # adjust scopes as needed
            }
            # A Marketplace customer who picks GitHub over the sign-up form must
            # still land in the organization the procurement account created.
            if onboarding_token:
                params["state"] = encode_oauth_state(onboarding_token)
            auth_url = f"{GITHUB_OAUTH_URL}/authorize?" + urllib.parse.urlencode(params)
            logger.info(f"Redirecting user to GitHub auth URL: {auth_url}")
            return self._gm.success_response({"url": auth_url})
        elif provider == "microsoft":
            params = {
                "client_id": MICROSOFT_CLIENT_ID,
                "redirect_uri": MICROSOFT_CALLBACK_URL,
                "response_type": "code",
                "scope": "openid profile email User.Read",
                # "state": some_random_string,  # recommended: generate and store in session for CSRF protection
            }
            auth_url = f"{MICROSOFT_OAUTH_URL}/authorize?" + urllib.parse.urlencode(
                params
            )
            logger.info(f"Redirecting user to Microsoft auth URL: {auth_url}")
            return self._gm.success_response({"url": auth_url})
        else:
            return self._gm.bad_request("Not Implemented")


def resolve_sso_user(user_email, name, onboarding_token, mode):
    """Find or create the user behind a verified SSO identity.

    Shared by the OAuth callbacks so the Marketplace rules hold whichever
    provider the customer picks: an onboarding token makes them the owner of the
    organization their procurement account already created, and an account that
    exists already can never absorb a subscription, because it belongs to an
    organization with its own billing.

    Returns (user, next_url, new_org).
    """
    try:
        user_model = User.objects.get(email=user_email)
        if not user_model.is_active:
            raise Exception("User is no longer active.")

        if onboarding_token:
            raise Exception("An account with this email already exists")

        properties = get_mixpanel_properties(user=user_model, mode=mode)
        track_mixpanel_event(MixpanelEvents.SSO_LOGIN.value, properties)
        return user_model, default_next_url, "false"

    except User.DoesNotExist:
        if onboarding_token:
            user_model = marketplace_signup(onboarding_token, user_email, name)
            properties = get_mixpanel_properties(user=user_model, mode=mode)
            track_mixpanel_event(MixpanelEvents.SSO_SIGNUP.value, properties)
        else:
            # first_signup emits its own Mixpanel event.
            data = {"full_name": name, "email": user_email}
            user_model = first_signup(data, mode=mode)
        return user_model, get_started_url, "true"


class Auth0CallbackView(APIView):
    permission_classes = (AllowAny,)
    authentication_classes = []
    _gm = GeneralMethods()

    @validated_request(
        query_serializer=SAMLOAuthCallbackQuerySerializer,
        responses=SAML_REDIRECT_RESPONSES,
    )
    def get(self, request, *args, **kwargs):
        try:
            new_org = "false"
            code = request.validated_query_data.get("code")
            if not code:
                logger.error("No code provided in callback.")
                raise Exception("Authorization code not provided.")
            logger.info(f"CODE: {code}")

            # Exchange code for access token
            token_url = f"https://{AUTH0_DOMAIN}/token"
            token_payload = {
                "grant_type": "authorization_code",
                "client_id": AUTH0_CLIENT_ID,
                "client_secret": AUTH0_CLIENT_SECRET,
                "code": code,
                "audience": AUTH0_CLIENT_ID,
                "redirect_uri": AUTH0_CALLBACK_URL,
            }

            response = requests.post(token_url, json=token_payload, timeout=10)
            logger.info(f"RESPONSE: {response}")
            logger.info(f"RESPONSE JSON: {response.text}")

            tokens = response.json()
            id_token = tokens.get("id_token")

            # Decode the ID token
            if id_token:
                access_token = tokens.get("access_token")
                decoded = jwt.decode(
                    id_token,
                    options={"verify_signature": False},
                    key=AUTH0_CLIENT_ID,
                    audience=AUTH0_CLIENT_ID,
                    access_token=access_token,
                )
                logger.info(f"DECODED: {decoded}")

                user_email = decoded.get("email")
                onboarding_token = read_oauth_state(
                    request.validated_query_data.get("state")
                )

                name = decoded.get("name")
                if not name:
                    get_name_google = (
                        f"{GOOGLE_USERINFO_API}?alt=json&access_token={access_token}"
                    )
                    user_info_response = requests.get(get_name_google, timeout=10)
                    logger.info(f"GOOGLE NAME RESPONSE: {user_info_response.text}")
                    if user_info_response.status_code == 200:
                        name = user_info_response.json().get("name")
                    else:
                        # return self._gm.bad_request("Unable to fetch Name")
                        raise Exception("Unable to fetch Name")

                # if not is_work_email(user_email):
                #     # return self._gm.bad_request("Email must be a work email")
                #     raise Exception("Email must be a work email")

                user_model, next_url, new_org = resolve_sso_user(
                    user_email,
                    name,
                    onboarding_token,
                    MixpanelModes.GOOGLE.value,
                )

                access_token = AuthToken.objects.create(
                    user=user_model,
                    auth_type=AuthTokenType.ACCESS.value,
                    last_used_at=timezone.now(),
                    is_active=True,
                )

                access_token_encrypted = generate_encrypted_message(
                    {"user_id": str(user_model.id), "id": str(access_token.id)}
                )
                cache.set(
                    f"access_token_{str(access_token.id)}",
                    {"token": access_token_encrypted, "user": user_model},
                    timeout=AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES * 60,
                )

                next_url += (
                    f"?sso_token={str(access_token_encrypted)}&is_new_user={new_org}"
                )
                login_next_url = request.session.get("login_next_url", None)
                if login_next_url:
                    next_url += f"&next={login_next_url}"
                    del request.session["login_next_url"]
                response = HttpResponse(status=302)
                response["Location"] = next_url
                response["new_org"] = new_org
                return response

            encoded = urlsafe_base64_encode(b"Unable to Process your request currently")
            redirect_url = f"{default_error_next_url}&reason={encoded}"
            return HttpResponseRedirect(redirect_url)
        except Exception as e:
            traceback.print_exc()
            encoded = urlsafe_base64_encode(str(e).encode("utf-8"))
            redirect_url = f"{default_error_next_url}&reason={encoded}"
            return HttpResponseRedirect(redirect_url)


class GithubCallbackView(APIView):
    _gm = GeneralMethods()
    permission_classes = (AllowAny,)
    authentication_classes = []

    @validated_request(
        query_serializer=SAMLOAuthCallbackQuerySerializer,
        responses=SAML_REDIRECT_RESPONSES,
    )
    def get(self, request, *args, **kwargs):
        try:
            new_org = "false"
            code = request.validated_query_data.get("code")
            if not code:
                logger.error("No code provided in callback.")
                # return self._gm.error_response("Authorization code not provided.", status=400)
                raise Exception("Authorization code not provided.")

            logger.info(f"GitHub callback received with code: {code}")

            token_url = f"{GITHUB_OAUTH_URL}/access_token"
            token_payload = {
                "client_id": GITHUB_CLIENT_ID,
                "client_secret": GITHUB_CLIENT_SECRET,
                "code": code,
                "redirect_uri": GITHUB_CALLBACK_URL,
            }
            headers = {
                "Accept": "application/json",  # Ask GitHub to return JSON
            }

            token_response = requests.post(
                token_url, data=token_payload, headers=headers, timeout=10
            )
            if token_response.status_code != 200:
                # return self._gm.bad_request("Failed to retrieve access token.", status=token_response.status_code)
                raise Exception("Failed to retrieve access token.")

            token_json = token_response.json()
            access_token = token_json.get("access_token")
            if not access_token:
                # return self._gm.bad_request("Access token not found.", status=400)
                raise Exception("Access token not found.")

            user_api_url = f"{GITHUB_API_ENDPOINT}/user"
            user_headers = {
                "Authorization": f"token {access_token}",
                "Accept": "application/json",
            }
            user_response = requests.get(user_api_url, headers=user_headers, timeout=10)
            if user_response.status_code != 200:
                # return self._gm.bad_request("Failed to retrieve user info.", status=user_response.status_code)
                raise Exception("Failed to retrieve user info.")
            # logger.info(f"")

            github_user = user_response.json()

            # Optionally get the user's email (in some cases the primary email is not in the main user object)
            if not github_user.get("email"):
                emails_api_url = f"{GITHUB_API_ENDPOINT}/user/emails"
                emails_response = requests.get(
                    emails_api_url, headers=user_headers, timeout=10
                )
                if emails_response.status_code == 200:
                    emails = emails_response.json()
                    primary_emails = [
                        e for e in emails if e.get("primary") and e.get("verified")
                    ]
                    if primary_emails:
                        github_user["email"] = primary_emails[0]["email"]

            # Process the user data: create or update a local user
            user_email = github_user.get("email")
            if not user_email:
                logger.error("GitHub did not provide an email address.")
                # return self._gm.bad_request("Email address not available.", status=400)
                raise Exception("Email address not available.")

            name = github_user.get("name")
            if not name:
                name = github_user.get("login")

            # if not is_work_email(user_email):
            #     # return self._gm.bad_request("Email must be a work email")
            #     raise Exception("Email must be a work email")

            onboarding_token = read_oauth_state(
                request.validated_query_data.get("state")
            )

            user_model, next_url, new_org = resolve_sso_user(
                user_email,
                name,
                onboarding_token,
                MixpanelModes.GITHUB.value,
            )

            access_token = AuthToken.objects.create(
                user=user_model,
                auth_type=AuthTokenType.ACCESS.value,
                last_used_at=timezone.now(),
                is_active=True,
            )

            access_token_encrypted = generate_encrypted_message(
                {"user_id": str(user_model.id), "id": str(access_token.id)}
            )
            cache.set(
                f"access_token_{str(access_token.id)}",
                {"token": access_token_encrypted, "user": user_model},
                timeout=AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES * 60,
            )

            next_url += (
                f"?sso_token={str(access_token_encrypted)}&is_new_user={new_org}"
            )
            login_next_url = request.session.get("login_next_url", None)
            if login_next_url:
                next_url += f"&next={login_next_url}"
                del request.session["login_next_url"]
            response = HttpResponse(status=302)
            response["Location"] = next_url
            response["new_org"] = new_org
            return response

        except Exception as e:
            traceback.print_exc()
            encoded = urlsafe_base64_encode(str(e).encode("utf-8"))
            redirect_url = f"{default_error_next_url}&reason={encoded}"
            return HttpResponseRedirect(redirect_url)


class MicrosoftCallbackView(APIView):
    _gm = GeneralMethods()
    permission_classes = (AllowAny,)
    authentication_classes = []

    @validated_request(
        query_serializer=SAMLOAuthCallbackQuerySerializer,
        responses=SAML_REDIRECT_RESPONSES,
    )
    def get(self, request, *args, **kwargs):
        try:
            new_org = "false"
            code = request.validated_query_data.get("code")
            if not code:
                logger.error("No code provided in callback.")
                raise Exception("Authorization code not provided.")

            logger.info(f"Microsoft callback received with code: {code}")

            # Exchange code for access token
            token_url = f"{MICROSOFT_OAUTH_URL}/token"
            token_payload = {
                "client_id": MICROSOFT_CLIENT_ID,
                "client_secret": MICROSOFT_CLIENT_SECRET,
                "code": code,
                "redirect_uri": MICROSOFT_CALLBACK_URL,
                "grant_type": "authorization_code",
            }
            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
            }

            token_response = requests.post(
                token_url, data=token_payload, headers=headers
            )
            if token_response.status_code != 200:
                logger.error(f"Token response error: {token_response.text}")
                raise Exception("Failed to retrieve access token.")

            token_json = token_response.json()
            access_token = token_json.get("access_token")
            if not access_token:
                raise Exception("Access token not found.")

            # Get user information from Microsoft Graph API
            user_api_url = f"{MICROSOFT_GRAPH_API}/me"
            user_headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            }
            user_response = requests.get(user_api_url, headers=user_headers)
            if user_response.status_code != 200:
                logger.error(f"User info response error: {user_response.text}")
                raise Exception("Failed to retrieve user information.")

            microsoft_user = user_response.json()
            logger.info(f"Microsoft user info: {microsoft_user}")

            # Extract user information
            user_email = microsoft_user.get("mail") or microsoft_user.get(
                "userPrincipalName"
            )
            if not user_email:
                raise Exception("Email not found in Microsoft account.")

            name = microsoft_user.get("displayName")
            if not name:
                name = (
                    microsoft_user.get("givenName", "")
                    + " "
                    + microsoft_user.get("surname", "")
                )
                name = name.strip()

            try:
                user_model = User.objects.get(
                    email=user_email,
                )
                if not user_model.is_active:
                    raise Exception("User is no longer active.")
                next_url = default_next_url

                properties = get_mixpanel_properties(
                    user=user_model, mode=MixpanelModes.MICROSOFT.value
                )
                track_mixpanel_event(MixpanelEvents.SSO_LOGIN.value, properties)

            except User.DoesNotExist:
                new_org = "true"
                data = {"full_name": name, "email": user_email}
                user_model = first_signup(data, mode=MixpanelModes.MICROSOFT.value)
                next_url = get_started_url

            access_token = AuthToken.objects.create(
                user=user_model,
                auth_type=AuthTokenType.ACCESS.value,
                last_used_at=timezone.now(),
                is_active=True,
            )

            access_token_encrypted = generate_encrypted_message(
                {"user_id": str(user_model.id), "id": str(access_token.id)}
            )
            cache.set(
                f"access_token_{str(access_token.id)}",
                {"token": access_token_encrypted, "user": user_model},
                timeout=AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES * 60,
            )

            next_url += (
                f"?sso_token={str(access_token_encrypted)}&is_new_user={new_org}"
            )
            login_next_url = request.session.get("login_next_url", None)
            if login_next_url:
                next_url += f"&next={login_next_url}"
                del request.session["login_next_url"]
            response = HttpResponse(status=302)
            response["Location"] = next_url
            response["new_org"] = new_org
            return response

        except Exception as e:
            traceback.print_exc()
            encoded = urlsafe_base64_encode(str(e).encode("utf-8"))
            redirect_url = f"{default_error_next_url}&reason={encoded}"
            return HttpResponseRedirect(redirect_url)
