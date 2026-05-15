import datetime
import os.path
import traceback

import requests

# from accounts.models.user_permissions import UserPermission
import structlog
from django.core.cache import cache
from django.db.models import Q
from django.http import HttpResponse, HttpResponseRedirect
from django.utils import timezone
from django.utils.http import urlsafe_base64_encode
from rest_framework import viewsets
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView
from saml2 import BINDING_HTTP_POST, BINDING_HTTP_REDIRECT, entity
from saml2.client import Saml2Client
from saml2.config import Config as Saml2Config

from accounts.authentication import generate_encrypted_message
from accounts.models.auth_token import (
    AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES,
    AuthToken,
    AuthTokenType,
)
from accounts.models.user import User
from accounts.utils import first_signup, get_request_organization, is_work_email
from analytics.utils import (
    MixpanelEvents,
    MixpanelModes,
    get_mixpanel_properties,
    track_mixpanel_event,
)
from saml2_auth.forms import IDPUploadForm
from saml2_auth.models import SAMLMetadataModel
from saml2_auth.serializers import SAMLSerializer
from tfc.middleware.workspace_context import get_current_organization

# from user.permissions_manager import PermissionManager
# from authentications.programatic_authentication import IsAuthenticated
from tfc.settings.settings import (
    AUTH0_CALLBACK_URL,
    AUTH0_CLIENT_ID,
    AUTH0_CLIENT_SECRET,
    AUTH0_DOMAIN,
    BASE_DIR,
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
    get_name_id_format,
    get_started_url,
)
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)

try:
    import urllib.parse as _urlparse
    from urllib.parse import unquote
except ImportError:
    import urllib.parse as _urlparse
    from urllib.parse import unquote

try:
    pass
except Exception:
    import urllib.error
    import urllib.parse


def _get_metadata(alias):
    saml_metadata_model = SAMLMetadataModel.objects.filter(identity_type=alias).first()
    meta_dir = os.path.join(
        BASE_DIR,
        "metadata",
    )
    if not os.path.exists(meta_dir):
        os.makedirs(meta_dir)
    meta_file_path = os.path.join(meta_dir, f"{saml_metadata_model.relay_state}.xml")
    if not os.path.isfile(meta_file_path):
        with open(meta_file_path, "w") as fh:
            fh.write(saml_metadata_model.meta)

    return {"local": [meta_file_path]}, saml_metadata_model.identity_type


def _get_saml_client(alias, acs_url):
    metadata, identity_type = _get_metadata(alias)
    saml_settings = {
        "metadata": metadata,
        "service": {
            "sp": {
                "endpoints": {
                    "assertion_consumer_service": [
                        (acs_url, BINDING_HTTP_REDIRECT),
                        (acs_url, BINDING_HTTP_POST),
                    ],
                },
                "allow_unsolicited": True,
                "authn_requests_signed": False,
                "logout_requests_signed": True,
                "want_assertions_signed": True,
                "want_response_signed": False,
            },
        },
        "entityid": get_entity_id,
    }

    saml_settings["service"]["sp"]["name_id_format"] = get_name_id_format

    spConfig = Saml2Config()
    spConfig.load(saml_settings)
    spConfig.allow_unknown_attributes = True
    saml_client = Saml2Client(config=spConfig)
    return saml_client, identity_type


def get_alias(request):
    return request.get_host().split(".")[0]


class ACSView(APIView):
    _gm = GeneralMethods()
    parser_classes = [FormParser, MultiPartParser, JSONParser]

    def save_auth_response(self, authn_response, user_identity):
        """Save SAML authentication response to a file"""
        try:
            log_dir = os.path.join(BASE_DIR, "saml_logs")
            if not os.path.exists(log_dir):
                os.makedirs(log_dir)

            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = os.path.join(log_dir, f"saml_response_{timestamp}.txt")

            with open(filename, "w") as f:
                f.write("Authentication Response:\n")
                f.write(str(authn_response) + "\n\n")
                f.write("User Identity:\n")
                f.write(str(user_identity))

            logger.info(f"SAML response saved to {filename}")
        except Exception as e:
            logger.error(f"Failed to save SAML response: {str(e)}")

    def post(self, request, *args, **kwargs):
        try:
            resp = request.POST.get("SAMLResponse", None)
            relay_state = request.POST.get("RelayState", "None Provided")
            saml_obj = SAMLMetadataModel.objects.get(relay_state=relay_state)
            if not saml_obj:
                raise Exception("RelayState No Valid")

            saml_client, identity_type = _get_saml_client(
                saml_obj.identity_type, get_assertion_url
            )

            if not resp:
                raise Exception("Unauthorised")

            authn_response = saml_client.parse_authn_request_response(
                resp, entity.BINDING_HTTP_POST
            )
            if authn_response is None:
                raise Exception("Unauthorised")
            user_identity = authn_response.get_identity()

            try:
                name_id = authn_response.get_subject().text
            except Exception:
                pass

            if user_identity is None:
                raise Exception("Unauthorised")
            attributes = SAMLMetadataModel.get_attributes(identity_type)
            if not user_identity:
                authn_response.parse_assertion(attributes)
                user_identity = authn_response.ava

            if user_identity:
                user_email = user_identity.get(attributes[0])[0]
            else:
                user_email = name_id

            name = None
            """
            For AWS.
            """
            if user_identity and "name" in user_identity:
                name = user_identity["name"][0]

            if not name:
                """
                For Google or OKTA.
                """
                names = []
                if user_identity and attributes[1] in user_identity:
                    first_name = user_identity[attributes[1]][0]
                    names.append(first_name)

                if user_identity and attributes[2] in user_identity:
                    last_name = user_identity[attributes[2]][0]
                    names.append(last_name)
                name = " ".join(names)
                if not name:
                    # Create name from email by taking the part before @ and replacing dots/underscores with spaces
                    name = (
                        user_email.split("@")[0]
                        .replace(".", " ")
                        .replace("_", " ")
                        .title()
                    )

            # user_name = authn_response.get_subject().text

            user_model = User.objects.filter(email=user_email).get()

            if not user_model.is_active:
                raise Exception("User is no longer active.")

            user_model.organization = saml_obj.organization
            user_model.save()
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

            next_url = default_next_url
            next_url += f"?sso_token={str(access_token_encrypted)}"
            login_next_url = request.session.get("login_next_url", None)
            if login_next_url:
                next_url += f"&next={login_next_url}"
                del request.session["login_next_url"]

            properties = get_mixpanel_properties(
                user=user_model, mode=MixpanelModes.SAML.value
            )
            track_mixpanel_event(MixpanelEvents.SSO_LOGIN.value, properties)
            return HttpResponseRedirect(next_url)

        except Exception as e:
            self._gm.error_log(api_view="ACSView.post", code="ACS001", message=str(e))
            traceback.print_exc()
            encoded = urlsafe_base64_encode(
                b"SAML is not enabled for your organization. Please contact your Administrator"
            )
            redirect_url = f"{default_error_next_url}&reason={encoded}"
            return HttpResponseRedirect(redirect_url)


class IDPLoginView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    _gm = GeneralMethods()

    def get(self, request, *args, **kwargs):
        msg = "SSO is not enabled for your organisation. Please contact to your administration."
        try:
            # provider = request.GET.get('provider')
            work_email = request.GET.get("email")
            if not work_email:
                return self._gm.bad_request("Email is required")

            work_email = work_email.lower()

            if not is_work_email(work_email):
                return self._gm.bad_request("Only Work email is permitted")

            try:
                user = User.objects.get(email=work_email)
            except User.DoesNotExist:
                logger.info("User Not Found")
                return self._gm.bad_request(msg)

            org_name = (get_current_organization() or user.organization).name
            saml_data = SAMLMetadataModel.objects.filter(
                Q(relay_state__istartswith=f"{org_name}")
            ).first()

            if not saml_data:
                logger.info("Saml Data does not Exist")
                return self._gm.bad_request(msg)

            alias = saml_data.identity_type
            next_url = request.GET.get("next", default_next_url)

            try:
                if "next=" in unquote(next_url):
                    next_url = _urlparse.parse_qs(
                        _urlparse.urlparse(unquote(next_url)).query
                    )["next"][0]
            except (KeyError, IndexError):
                next_url = request.GET.get("next", default_next_url)
            request.session["login_next_url"] = next_url

            saml_client, identity_type = _get_saml_client(alias, get_assertion_url)
            _, info = saml_client.prepare_for_authenticate()

            redirect_url = None

            for key, value in info["headers"]:
                if key == "Location":
                    redirect_url = value
                    break

            if not redirect_url:
                logger.info("No redirect Url")
                return self._gm.bad_request(msg)
            return self._gm.success_response({"url": redirect_url})

        except Exception as e:
            traceback.print_exc()
            logger.error(e)
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
    parser_classes = (FormParser, MultiPartParser, JSONParser)  # Add this line
    # authentication_classes = (ProgrammaticAuthentication,)
    permission_classes = (IsAuthenticated,)
    # rbac = 'idp'
    queryset = SAMLMetadataModel.objects.filter(deleted=False)
    lookup_field = "id"
    lookup_url_kwarg = "id"
    http_method_names = ["get", "post", "head", "delete", "options", "put"]
    parser_classes = (FormParser, MultiPartParser, JSONParser)  # Add this line

    def get_serializer_class(self):
        if self.request.method == "GET":
            return SAMLSerializer
        # if self.request.method == "PUT":
        #     return WorkspaceTagsSerializer

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

    def retrieve(self, request, *args, **kwargs):
        try:
            uuid = kwargs.get(self.lookup_url_kwarg)
            data = {}
            existing_saml_metadata_model = SAMLMetadataModel.objects.get(
                id=uuid, deleted=False
            )
            if existing_saml_metadata_model:
                data["is_enabled"] = existing_saml_metadata_model.is_enabled
                data["identity_type"] = existing_saml_metadata_model.identity_type
                name = existing_saml_metadata_model.name
                if (
                    not existing_saml_metadata_model.name
                    or existing_saml_metadata_model.name == "null"
                ):
                    name = existing_saml_metadata_model.get_identity_type
                data["name"] = name
                data["acs_url"] = get_assertion_url
                data["audience_url"] = get_entity_id
            return self._gm.success_response(data)
        except SAMLMetadataModel.DoesNotExist:
            return self._gm.bad_request("Invalid saml group.")
        except Exception as e:
            logger.error(e)
            traceback.print_exc()
            return self._gm.internal_server_error_response(get_error_message("US25"))

    def create(self, request, *args, **kwargs):
        try:
            form = IDPUploadForm(request.POST, request.FILES)
            if not form.is_valid():
                return self._gm.bad_request(form.errors)
            data = form.cleaned_data
            data["organization"] = get_request_organization(request)
            if "file" in data:
                try:
                    meta = data.pop("file").read()
                    data["meta"] = meta.decode()
                except Exception as e:
                    logger.error(str(e))
                    return self._gm.bad_request("Please select a XML file.")
            if SAMLMetadataModel.objects.filter(
                deleted=False, organization=data["organization"]
            ).exists():
                return self._gm.bad_request(
                    "Maximum supported identity providers reached. Please edit or delete an existing IdP."
                )
            SAMLMetadataModel.objects.create(**data)
            return self._gm.success_response("Success")
        except Exception as e:
            logger.error(e)
            traceback.print_exc()
            return self._gm.internal_server_error_response(get_error_message("US25"))

    def destroy(self, request, *args, **kwargs):
        try:
            uuid = kwargs.get(self.lookup_url_kwarg)
            obj = SAMLMetadataModel.objects.get(id=uuid)
            obj.deleted = True
            obj.deleted_at = datetime.datetime.now()
            obj.save()
            return self._gm.success_response("Success")
        except SAMLMetadataModel.DoesNotExist:
            return self._gm.bad_request("Invalid saml group.")
        except Exception as e:
            logger.error(e)
            return self._gm.internal_server_error_response(get_error_message("US25"))

    def update(self, request, *args, **kwargs):
        try:
            uuid = kwargs.get(self.lookup_url_kwarg)
            form = IDPUploadForm(request.POST, request.FILES)
            saml_model = SAMLMetadataModel.objects.filter(id=uuid, deleted=False).get()
            if not form.is_valid():
                return self._gm.bad_request(form.errors)
            data = form.cleaned_data
            data["organization"] = get_request_organization(request)
            if int(saml_model.identity_type) != int(
                data.get("identity_type")
            ) and not data.get("file"):
                return self._gm.bad_request("Please select a XML file.")
            if "file" in data:
                try:
                    meta = data.pop("file").read()
                    data["meta"] = meta.decode()
                except Exception:
                    logger.info("No file in update SSO.")
            SAMLMetadataModel.objects.filter(id=uuid).update(**data)
            return self._gm.success_response("Success")
        except SAMLMetadataModel.DoesNotExist:
            return self._gm.bad_request("Invalid saml group.")
        except Exception as e:
            traceback.print_exc()
            logger.error(e)
            return self._gm.internal_server_error_response(get_error_message("US25"))


import urllib.parse  # noqa: E402

from jose import jwt  # noqa: E402


class Auth0LoginView(APIView):
    _gm = GeneralMethods()
    permission_classes = (AllowAny,)
    authentication_classes = []

    def get(self, request):
        provider = request.GET.get("provider", None)
        if not provider:
            return self._gm.bad_request("Provider is required")

        if provider == "google":
            auth_url = f"https://{AUTH0_DOMAIN}/auth?" + urllib.parse.urlencode(
                {
                    "response_type": "code",
                    "client_id": AUTH0_CLIENT_ID,
                    "redirect_uri": AUTH0_CALLBACK_URL,
                    "scope": "openid profile email",
                }
            )
            return self._gm.success_response({"url": auth_url})
        elif provider == "github":
            params = {
                "client_id": GITHUB_CLIENT_ID,
                "redirect_uri": GITHUB_CALLBACK_URL,
                "scope": "user:email",  # adjust scopes as needed
                # "state": some_random_string,  # recommended: generate and store in session for CSRF protection
            }
            auth_url = f"{GITHUB_OAUTH_URL}/authorize?" + urllib.parse.urlencode(params)
            logger.info(f"Redirecting user to GitHub auth URL: {auth_url}")
            return self._gm.success_response(dict(url=auth_url))
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
            return self._gm.success_response(dict(url=auth_url))
        else:
            return self._gm.bad_request("Not Implemented")


class Auth0CallbackView(APIView):
    permission_classes = (AllowAny,)
    authentication_classes = []
    _gm = GeneralMethods()

    def get(self, request):
        try:
            new_org = "false"
            code = request.GET.get("code")
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

                try:
                    user_model = User.objects.get(
                        email=user_email,
                    )
                    if not user_model.is_active:
                        raise Exception("User is no longer active.")

                    next_url = default_next_url

                    properties = get_mixpanel_properties(
                        user=user_model, mode=MixpanelModes.GOOGLE.value
                    )
                    track_mixpanel_event(MixpanelEvents.SSO_LOGIN.value, properties)

                except User.DoesNotExist:
                    new_org = "true"
                    data = {"full_name": name, "email": user_email}
                    user_model = first_signup(data, mode=MixpanelModes.GOOGLE.value)
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
                    f"?sso_token={str(access_token_encrypted)}"
                    f"&is_new_user={new_org}"
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

    def get(self, request):
        try:
            new_org = "false"
            code = request.GET.get("code")
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

            try:
                user_model = User.objects.get(
                    email=user_email,
                )
                if not user_model.is_active:
                    raise Exception("User is no longer active.")
                next_url = default_next_url

                properties = get_mixpanel_properties(
                    user=user_model, mode=MixpanelModes.GITHUB.value
                )
                track_mixpanel_event(MixpanelEvents.SSO_LOGIN.value, properties)

            except User.DoesNotExist:
                new_org = "true"
                data = {"full_name": name, "email": user_email}
                user_model = first_signup(data, mode=MixpanelModes.GITHUB.value)
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
                f"?sso_token={str(access_token_encrypted)}"
                f"&is_new_user={new_org}"
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

    def get(self, request):
        try:
            new_org = "false"
            code = request.GET.get("code")
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
                f"?sso_token={str(access_token_encrypted)}"
                f"&is_new_user={new_org}"
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
