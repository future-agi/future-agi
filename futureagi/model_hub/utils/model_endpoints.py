"""One endpoint per custom model, checked where it is saved and where it is called.

A ``custom`` provider model stores ``{"api_base", "headers"}`` (plus the
form's ``custom_provider`` flag): ``normalize_custom_model_config`` turns
what the create and edit views receive into that shape, so the URL the
validation POSTs to is the only URL stored. ``endpoint_url``, which the
run-prompt handler used to read while the views validated only
``api_base``, is accepted as an input alias and folded into ``api_base``.
``custom_endpoint_from_config`` reads a stored row at call time, including
rows saved before this shape existed; ``tfc.utils.ssrf_guard.guarded_post``
re-checks the address on every call.

``assert_model_config_endpoints_allowed`` applies the same address policy to
the base URL of any stored model configuration (openai/azure ``api_base``)
where litellm, not ``guarded_post``, makes the connection, and
``whitelist_model_config`` keeps only the keys each provider's configuration
needs, so a stored config cannot pass other litellm arguments
(``extra_headers``, ``aws_sts_endpoint``, ...).

A model configuration may not set the reserved identity fields
either, whatever their value: the create and edit forms and the admin refuse
one that does (``submitted_config_not_valid``, which also reads JSON text a
form sends as what it holds), with the sentence the other refusals use.
``_guard`` drops those fields from the arguments of every call that goes
through it (``without_platform_identity_fields``) and at any depth of a
Vertex configuration it is given, whoever set them; ``LLM.call_llm``, Run
Prompt's custom endpoint model and the vector database column's embedding
key drop them at any depth of a stored configuration or key they are given
or read; and ``LiteLLMModelManager.stored_credential_lookup``
(``get_api_key``) and ``get_provider_api_key`` drop them at any depth of
every stored row they return (``without_platform_identity_fields_at_any_depth``).

A model configuration may not be a Google credential other than a
service-account key, for any provider
(``is_another_kind_of_google_credential``), and a Vertex model configuration
must be a service-account key that signs in only at Google's own addresses
(``vertex_config_is_not_a_service_account_key``, which decides where a key
says it signs in with ``signs_in_only_at_googles_own_addresses``). Either is
refused with the same sentence, nothing echoed, by the create and edit forms
and the admin (through ``submitted_config_not_valid``; a custom model's edit
form names no provider, so there a configuration is taken as a Vertex one by
its shape), by ``LiteLLMModelManager`` for a stored row before any call, by
``validate_model_working`` before its call
(``is_google_credential_other_than_a_service_account_key``), and by
``_guard`` for every call that goes through it and is given a Vertex
configuration, under either name, which must also be an object or JSON text
of one.

A customer's Vertex model runs only with a complete service-account key of
the customer's own: an object, or JSON text of one, that once its location
is set aside gives a private key and a client email as text that is not
blank, gives no type or a service-account key's, and is a service-account
key that signs in only at Google's own addresses
(``_gives_a_complete_service_account_key``). Where only a customer's model
is called (the forms' validation call, Run Prompt's chat, image, speech and
transcription calls, an evaluation's model call), a Vertex call given
anything else (a key alone, a project alone, a client email without a
private key or a private key without a client email, a location alone, no
configuration, an empty object) is refused with the same sentence before it
is made (``refuse_vertex_call_without_a_service_account_key``). A row
stored for a Vertex model that gives anything else is refused the same way
where stored credentials are looked up, before any call is built, and a row
that gives one is handed on as an object without a key field
(``stored_row_as_a_call_takes_it``). Run
Prompt's chat call, and an evaluation's model call, for a model that runs on
the organization's Future AGI credits are not refused: they go through the
gateway's managed route. ``_guard`` refuses the same for every call through
it given a Vertex configuration. The platform's own features call the
platform's own model through one helper
(``agentic_eval.core.llm.platform_model.call_platform_model``), which takes
which feature is calling and the prompt, nothing else.

The prompt optimiser's calls made with a customer's key (its generator, its
reflection model and that model's check, its prompt variations) give a
Vertex model the object its saved key holds as the model's Vertex
configuration, with its location, and send no key
(``vertex_call_arguments``); such a call that gives no complete
service-account key is refused with the same sentence before it is made.
Run Prompt's image generation and speech calls also refuse, the same way, a
model whose provider is Vertex AI but whose name the model library sends to
another provider (``refuse_vertex_model_sent_to_another_provider``).

``guarded_completion``, ``guarded_acompletion`` and ``guarded_image_generation``
are litellm's entry points for a call that names a tenant base URL: they give
litellm a client built on ``tfc.utils.ssrf_guard.guarded_httpx_client``
(checked and pinned connection, no redirects, metadata headers dropped), run
the call inside ``tenant_endpoint_scope`` so that every client litellm builds
for itself on the way (routes that drop the client they are given) is a
guarded one too, and raise what fails as ``EndpointCallError``: generic,
categorised text (``endpoint_call_error_message``) with no exception chain,
so no handler can log, cache or send the endpoint's own error text.
"""

import contextlib
import contextvars
import copy
import functools
import inspect
import json
import threading

from tfc.utils.ssrf_guard import (
    ENDPOINT_ERROR_MESSAGES,
    AsyncGuardedHTTPTransport,
    EndpointBlocked,
    GuardedHTTPTransport,
    GuardedRequestError,
    check_endpoint_url,
    endpoint_status_message,
    is_metadata_or_forwarding_header,
    is_stripped_endpoint_header,
    model_endpoint_policy,
)

# Keys of a stored model configuration that name a base URL litellm (or the
# custom handler) connects to.
MODEL_CONFIG_URL_KEYS = ("api_base", "apiBase", "base_url", "endpoint_url")

_MISSING_URL = "Please provide the API Base."
_CONFLICTING_URLS = (
    "api_base and endpoint_url name different URLs. Give the model's "
    "chat-completions URL once, as api_base."
)
_BAD_URL = "API Base must be an http:// or https:// URL."
_BAD_HEADERS = "headers must be an object mapping header names to text values."
_BAD_HEADER_NAME = (
    "Header names cannot be empty or contain spaces, colons or line breaks."
)
_BAD_HEADER_VALUE = "Header values cannot contain line breaks."


class CustomModelConfigError(ValueError):
    """A custom model configuration that cannot be saved; ``str()`` says what
    to fix and is safe to show the user."""


def _endpoint_url(config: dict) -> str:
    urls = []
    for key in ("api_base", "endpoint_url"):
        value = config.get(key)
        if value is None or value == "":
            continue
        if not isinstance(value, str):
            raise CustomModelConfigError(_BAD_URL)
        value = value.strip()
        if value and value not in urls:
            urls.append(value)
    if not urls:
        raise CustomModelConfigError(_MISSING_URL)
    if len(urls) > 1:
        raise CustomModelConfigError(_CONFLICTING_URLS)
    return urls[0]


def _clean_headers(headers) -> dict:
    if headers is None:
        return {}
    if not isinstance(headers, dict):
        raise CustomModelConfigError(_BAD_HEADERS)
    cleaned = {}
    for name, value in headers.items():
        if not isinstance(name, str) or not name.strip():
            raise CustomModelConfigError(_BAD_HEADER_NAME)
        if any(ch.isspace() or ch == ":" or not ch.isprintable() for ch in name):
            raise CustomModelConfigError(_BAD_HEADER_NAME)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, str | int | float):
            raise CustomModelConfigError(_BAD_HEADERS)
        value = str(value)
        if "\r" in value or "\n" in value or "\x00" in value:
            raise CustomModelConfigError(_BAD_HEADER_VALUE)
        if is_stripped_endpoint_header(name):
            continue
        cleaned[name] = value
    return cleaned


def normalize_custom_model_config(config) -> dict:
    """The stored shape of a ``custom`` provider configuration.

    Keeps exactly one endpoint, under ``api_base`` (``endpoint_url`` is an
    alias; both given and different is an error), the headers (names and
    values checked; metadata, forwarding and hop-by-hop headers dropped) and
    a boolean ``custom_provider`` flag when present. Every other key is
    dropped. Raises CustomModelConfigError. Does not resolve or connect.
    """
    if not isinstance(config, dict):
        raise CustomModelConfigError(_MISSING_URL)
    url = _endpoint_url(config)
    if not url.lower().startswith(("http://", "https://")):
        raise CustomModelConfigError(_BAD_URL)
    normalized = {"api_base": url, "headers": _clean_headers(config.get("headers"))}
    if isinstance(config.get("custom_provider"), bool):
        normalized["custom_provider"] = config["custom_provider"]
    return normalized


def custom_endpoint_from_config(key_config) -> tuple[str, dict]:
    """(endpoint URL, headers) of a stored ``custom`` model, for a call.

    Rows saved before the stored shape was normalized may carry
    ``endpoint_url`` (what the handler called then) and ``api_base``; the
    handler's choice, ``endpoint_url``, wins, and the caller's guarded POST
    checks it like any other. Raises CustomModelConfigError when there is no
    usable URL.
    """
    if not isinstance(key_config, dict):
        raise CustomModelConfigError(_MISSING_URL)
    for key in ("endpoint_url", "api_base"):
        value = key_config.get(key)
        if isinstance(value, str) and value.strip():
            url = value.strip()
            break
    else:
        raise CustomModelConfigError(_MISSING_URL)
    headers = key_config.get("headers") or {}
    if not isinstance(headers, dict):
        raise CustomModelConfigError(_BAD_HEADERS)
    return url, headers


def assert_model_config_endpoints_allowed(config, *, allow_unresolvable=False) -> None:
    """Raise EndpointBlocked if any base URL in a model configuration
    (``MODEL_CONFIG_URL_KEYS``) may not be called under the deployment's
    address policy. ``allow_unresolvable=True`` (saving: a save should not
    depend on this container's DNS, and the call is checked again) lets a
    host that does not resolve here pass. Not a dict, or no URL keys:
    nothing to check."""
    if not isinstance(config, dict):
        return
    for key in MODEL_CONFIG_URL_KEYS:
        value = config.get(key)
        if value is None or value == "":
            continue
        if not isinstance(value, str):
            raise EndpointBlocked("invalid_url")
        check_endpoint_url(value, allow_unresolvable=allow_unresolvable)


# ----------------------------------------- values that hold a reference ---

# A string that starts with one of these (trimmed, in any case) holds a
# reference (such as os.environ/... or oidc/...) instead of a literal value.
# Refused wherever a model configuration is saved or edited, and again before
# every call.
_PLATFORM_VALUE_PREFIXES = ("os.environ/", "oidc/")

# What each prefix's string names: a value by its name, or an issuer (and what
# follows it). The count command reports the two apart.
PLATFORM_VALUE_FORMS = dict(
    zip(_PLATFORM_VALUE_PREFIXES, ("name", "issuer"), strict=True)
)

# Reserved identity fields: a model configuration may not set them,
# whatever their value. Refused wherever a model configuration is saved or
# edited; dropped from every call, whoever set them. Compared trimmed, in any
# case.
_PLATFORM_IDENTITY_FIELDS = frozenset(
    {
        "aws_web_identity_token",
        "aws_sts_endpoint",
        "aws_profile_name",
        "aws_role_name",
        "aws_session_name",
        "aws_bedrock_runtime_endpoint",
        "aws_external_id",
    }
)

# A Vertex model configuration must be a service-account key that signs in
# only at Google's own addresses. One that gives a type other than a
# service-account key's (compared exactly), says it signs in anywhere else
# (``_GOOGLES_OWN_ADDRESSES``), or carries at any depth a field only another
# kind of configuration has (one of these, or one starting with the AWS
# prefix; compared trimmed, in any case), is refused with the same sentence
# when it is saved or edited, before any call made with a stored row, before
# every call that goes through the shared guard and is given it, and before
# the custom-model forms' validation call.
_SERVICE_ACCOUNT_KEY_TYPE = "service_account"
# Where a Google key may say the platform signs in: for each field it may
# give, one of Google's own values. A field it does not give is Google's own
# by default.
_GOOGLES_OWN_ADDRESSES = {
    "token_uri": frozenset(
        {
            "https://oauth2.googleapis.com/token",
            # older genuine keys
            "https://accounts.google.com/o/oauth2/token",
        }
    ),
    "auth_uri": frozenset(
        {
            "https://accounts.google.com/o/oauth2/auth",
            "https://accounts.google.com/o/oauth2/v2/auth",
        }
    ),
    "universe_domain": frozenset({"googleapis.com"}),
}
_EXTERNAL_IDENTITY_FIELDS = frozenset(
    {"credential_source", "token_url", "service_account_impersonation_url"}
)
_EXTERNAL_IDENTITY_PREFIX = "aws_"
# For any provider, a configuration that gives one of these types, or carries
# one of the fields above, at any depth (compared trimmed, in any case), is
# another kind of Google credential than a service-account key: legitimate
# for no provider, it is refused the same way. Fields named like AWS keys'
# make a configuration another kind only for Vertex AI: other providers'
# keys carry such fields.
_OTHER_GOOGLE_CREDENTIAL_TYPES = frozenset(
    {
        "authorized_user",
        "external_account",
        "external_account_authorized_user",
        "gdch_service_account",
        "impersonated_service_account",
    }
)
# The two names a model call takes a Vertex configuration under.
_VERTEX_CREDENTIAL_ARGUMENTS = ("vertex_credentials", "vertex_ai_credentials")

INVALID_MODEL_CONFIGURATION = (
    "This model configuration is not valid; enter the key and settings from "
    "your provider."
)

# Arguments of a call that carry what is sent to the model (a prompt may
# quote any text), not a credential or a connection setting. Every other
# argument of a call is checked.
_CALL_CONTENT_ARGS = frozenset(
    {
        "messages",
        "prompt",
        "input",
        "file",
        "tools",
        "functions",
        "tool_choice",
        "function_call",
        "response_format",
        "metadata",
        "stop",
        "user",
    }
)


class ModelConfigurationRefused(CustomModelConfigError):
    """A model configuration, or the credentials of a call, holds a reference
    (such as os.environ/... or oidc/...) instead of a literal value.
    ``str()`` is ``INVALID_MODEL_CONFIGURATION`` and nothing else: never the
    value, the name in it or its form."""

    def __init__(self):
        super().__init__(INVALID_MODEL_CONFIGURATION)


def _nodes(value):
    """``value`` and every value of its dicts, lists, tuples or sets at any
    depth, each container once."""
    pending, seen = [value], set()
    while pending:
        current = pending.pop()
        if isinstance(current, dict | list | tuple | set | frozenset):
            if id(current) in seen:
                continue
            seen.add(id(current))
            pending.extend(current.values() if isinstance(current, dict) else current)
        yield current


def platform_value_references(value):
    """``(form, rest)`` for each string in ``value`` (``value`` itself, or a
    value of its dicts, lists, tuples or sets at any depth) that, trimmed and
    in any case, holds a reference (such as os.environ/... or oidc/...)
    instead of a literal value: ``form`` is what it names
    (``PLATFORM_VALUE_FORMS``), ``rest`` the trimmed
    string after its prefix, as written."""
    for current in _nodes(value):
        if not isinstance(current, str):
            continue
        trimmed = current.strip()
        lowered = trimmed.lower()
        for prefix in _PLATFORM_VALUE_PREFIXES:
            if lowered.startswith(prefix):
                yield PLATFORM_VALUE_FORMS[prefix], trimmed[len(prefix) :]
                break


def names_platform_value(value) -> bool:
    """Whether any string in ``value`` (``value`` itself, or a value of its
    dicts, lists, tuples or sets at any depth), trimmed and in any case,
    holds a reference (such as os.environ/... or oidc/...) instead of a
    literal value."""
    return next(platform_value_references(value), None) is not None


def refuse_platform_values(value) -> None:
    """Raise ModelConfigurationRefused when ``names_platform_value(value)``.
    Refuses; never strips or rewrites the value."""
    if names_platform_value(value):
        raise ModelConfigurationRefused()


def refuse_platform_values_in_call(kwargs) -> None:
    """Raise ModelConfigurationRefused when any argument of a call other than
    what it sends to the model (``_CALL_CONTENT_ARGS``) holds a reference
    (such as os.environ/... or oidc/...) instead of a literal value: keys,
    base URLs, cloud credentials, headers."""
    if not isinstance(kwargs, dict):
        return
    refuse_platform_values(
        [value for name, value in kwargs.items() if name not in _CALL_CONTENT_ARGS]
    )


def _is_platform_identity_field(name) -> bool:
    return isinstance(name, str) and name.strip().lower() in _PLATFORM_IDENTITY_FIELDS


def sets_platform_identity_field(value) -> bool:
    """Whether any dict in ``value`` (``value`` itself, or a value of its
    dicts, lists, tuples or sets at any depth) has one of the reserved
    identity fields as a key, trimmed and in any case."""
    return any(
        isinstance(node, dict) and any(map(_is_platform_identity_field, node))
        for node in _nodes(value)
    )


def without_platform_identity_fields(arguments):
    """A copy of ``arguments`` (a call's arguments or a stored configuration)
    without the reserved identity fields, trimmed and in any case;
    anything that is not a dict, unchanged."""
    if not isinstance(arguments, dict):
        return arguments
    return {
        name: value
        for name, value in arguments.items()
        if not _is_platform_identity_field(name)
    }


def _as_configuration(value):
    """``value``, or what it holds when it is JSON object or array text."""
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return value
        if isinstance(parsed, dict | list):
            return parsed
    return value


def _dropped_at_any_depth(value):
    if isinstance(value, dict):
        return {
            name: _dropped_at_any_depth(item)
            for name, item in value.items()
            if not _is_platform_identity_field(name)
        }
    if isinstance(value, list):
        return [_dropped_at_any_depth(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_dropped_at_any_depth(item) for item in value)
    return value


def without_platform_identity_fields_at_any_depth(value):
    """A copy of ``value`` (a stored key or configuration: a dict or a list,
    or JSON text of one) without the reserved identity fields at any
    depth of its structure, compared trimmed and in any case. JSON text comes
    back as JSON text, the same text when it held none of them; anything else
    unchanged."""
    if isinstance(value, str):
        parsed = _as_configuration(value)
        if parsed is value or not sets_platform_identity_field(parsed):
            return value
        return json.dumps(_dropped_at_any_depth(parsed))
    return _dropped_at_any_depth(value)


def stored_config_sets_platform_identity_field(*values) -> bool:
    """Whether any of ``values`` (a stored key or configuration, or JSON text
    of one) sets one of the reserved identity fields at any depth."""
    return any(
        sets_platform_identity_field(_as_configuration(value)) for value in values
    )


def is_vertex_provider(provider) -> bool:
    """Whether ``provider`` names Vertex AI, trimmed and in any case."""
    return str(provider or "").strip().lower().startswith("vertex_ai")


def _is_external_identity_field(name) -> bool:
    return isinstance(name, str) and name.strip().lower() in _EXTERNAL_IDENTITY_FIELDS


def _is_external_identity_key(name) -> bool:
    return _is_external_identity_field(name) or (
        isinstance(name, str)
        and name.strip().lower().startswith(_EXTERNAL_IDENTITY_PREFIX)
    )


def _has_key(value, is_key) -> bool:
    return any(
        isinstance(node, dict) and any(map(is_key, node)) for node in _nodes(value)
    )


def _is_other_google_credential_type(name, value) -> bool:
    return (
        isinstance(name, str)
        and name.strip().lower() == "type"
        and isinstance(value, str)
        and value.strip().lower() in _OTHER_GOOGLE_CREDENTIAL_TYPES
    )


def is_another_kind_of_google_credential(*values) -> bool:
    """Whether any of ``values`` (a key or a configuration, or JSON text of
    one), whatever its provider, is a Google credential other than a
    service-account key: at any depth it gives one of
    ``_OTHER_GOOGLE_CREDENTIAL_TYPES`` as its type or carries one of
    ``_EXTERNAL_IDENTITY_FIELDS``, compared trimmed and in any case. No
    provider's configuration has either."""
    return any(
        isinstance(node, dict)
        and any(
            _is_external_identity_field(name)
            or _is_other_google_credential_type(name, item)
            for name, item in node.items()
        )
        for value in values
        for node in _nodes(_as_configuration(value))
    )


def signs_in_only_at_googles_own_addresses(key) -> bool:
    """Whether a Google service-account key (a dict) says the platform signs
    in only at Google's own addresses: each field of
    ``_GOOGLES_OWN_ADDRESSES`` it gives is text equal to one of that field's
    values once surrounding whitespace is trimmed, and nothing else (another
    case, a port, userinfo, a path, a query or a trailing slash is another
    address; a value that is not text is refused). A field it does not give
    is Google's own by default. Anything other than a dict is refused
    (False). The one rule for every place that takes a customer's Google
    key."""
    if not isinstance(key, dict):
        return False
    for field, googles_own in _GOOGLES_OWN_ADDRESSES.items():
        if field not in key:
            continue
        value = key[field]
        if not isinstance(value, str) or value.strip() not in googles_own:
            return False
    return True


def vertex_config_is_not_a_service_account_key(provider, *values) -> bool:
    """Whether any of ``values`` (a key or a configuration, or JSON text of
    one) given for a Vertex model is anything other than a service-account
    key that signs in only at Google's own addresses: it gives a type other
    than a service-account key's (compared exactly), or says it signs in
    anywhere else (``signs_in_only_at_googles_own_addresses``), or carries
    at any depth a field only another kind of configuration has
    (``_EXTERNAL_IDENTITY_FIELDS``, or one starting with
    ``_EXTERNAL_IDENTITY_PREFIX``; trimmed, in any case). A configuration
    that gives no type is read as a service-account key, and is not refused
    for that alone; where it says it signs in is checked all the same.

    ``provider`` is the model's provider: for any other provider, False.
    None when the form names none (a custom model's edit form): a
    configuration is then taken as a Vertex one when it gives a type or one
    of the fields of ``_GOOGLES_OWN_ADDRESSES``, or carries one of
    ``_EXTERNAL_IDENTITY_FIELDS``."""
    if provider is not None and not is_vertex_provider(provider):
        return False
    for value in values:
        config = _as_configuration(value)
        if not isinstance(config, dict):
            continue
        if (
            provider is None
            and "type" not in config
            and not any(field in config for field in _GOOGLES_OWN_ADDRESSES)
            and not _has_key(config, _is_external_identity_field)
        ):
            continue
        if "type" in config and config["type"] != _SERVICE_ACCOUNT_KEY_TYPE:
            return True
        if not signs_in_only_at_googles_own_addresses(config):
            return True
        if _has_key(config, _is_external_identity_key):
            return True
    return False


def is_google_credential_other_than_a_service_account_key(provider, *values) -> bool:
    """Whether any of ``values`` may not be a model's Google credential:
    for any provider, another kind of Google credential
    (``is_another_kind_of_google_credential``); for a Vertex provider, or a
    form that names none (None), also anything other than a service-account
    key that signs in only at Google's own addresses
    (``vertex_config_is_not_a_service_account_key``)."""
    return is_another_kind_of_google_credential(
        *values
    ) or vertex_config_is_not_a_service_account_key(provider, *values)


def refuse_google_credential_other_than_a_service_account_key(
    provider, *values
) -> None:
    """Raise ModelConfigurationRefused when any of ``values`` may not be a
    model's Google credential for ``provider``
    (``is_google_credential_other_than_a_service_account_key``). Here None
    is no provider, not a form that names none: nothing is taken as a Vertex
    configuration by its shape alone."""
    if is_google_credential_other_than_a_service_account_key(
        str(provider or ""), *values
    ):
        raise ModelConfigurationRefused()


def _is_given_text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _gives_a_complete_service_account_key(value) -> bool:
    """Whether ``value`` (a Vertex configuration: an object, or JSON text of
    one) is a complete service-account key once its location is set aside:
    it gives a private key and a client email as text that is not blank,
    gives no type or a service-account key's, and is a service-account key
    that signs in only at Google's own addresses
    (``vertex_config_is_not_a_service_account_key``, which reads a
    configuration that gives no type as a service-account key). Anything
    else, an empty object and text that holds no object included, is not.
    The one test for a call and for a stored row."""
    configuration = _as_configuration(value)
    if not isinstance(configuration, dict):
        return False
    key = {name: item for name, item in configuration.items() if name != "location"}
    return (
        _is_given_text(key.get("private_key"))
        and _is_given_text(key.get("client_email"))
        and key.get("type", _SERVICE_ACCOUNT_KEY_TYPE) == _SERVICE_ACCOUNT_KEY_TYPE
        and not vertex_config_is_not_a_service_account_key("vertex_ai", key)
    )


def _is_vertex_call(provider, arguments) -> bool:
    """Whether a model call goes to Vertex AI: ``provider``, the model's
    provider, names it, or the model library sends the call's model there
    under the provider the call names."""
    return is_vertex_provider(provider) or is_vertex_provider(
        _litellm_provider(arguments)
    )


def _runs_on_credits(arguments) -> bool:
    """Whether a call carries, as its key, a model route on the
    organization's Future AGI credits (``managed_binding_in``)."""
    from model_hub.services.model_route import is_managed_route_binding

    return is_managed_route_binding(arguments.get("api_key"))


def vertex_call_has_no_service_account_key(
    provider, arguments, *, managed_route=False
) -> bool:
    """Whether a call to a customer's Vertex model carries no complete
    service-account key of the customer's own: ``arguments`` (what the call
    is about to be given) go to Vertex AI (``_is_vertex_call``; ``provider``
    is the model's provider), and either neither name a call takes a Vertex
    configuration under (``_VERTEX_CREDENTIAL_ARGUMENTS``) gives one, or a
    name gives anything other than a complete service-account key
    (``_gives_a_complete_service_account_key``). A key given alone, a
    project alone, a client email without a private key or a private key
    without a client email, no configuration, and an empty object all count
    as none. A Vertex call needs a complete service-account key of the
    customer's own.

    ``managed_route``: the call is made through ``guarded_completion`` or
    ``guarded_acompletion``, which send a call whose key is a model route on
    the organization's Future AGI credits through the gateway's managed
    route and never to the model library; such a call (``_runs_on_credits``)
    is then not counted. Only Run Prompt's chat payload passes it: the
    other places never take such a route (an evaluation's model on such a
    route is sent to it before its model call is built).

    For the places only a customer's model reaches. The platform's own
    features call their own helper, which does not apply this."""
    if not isinstance(arguments, dict) or not _is_vertex_call(provider, arguments):
        return False
    if managed_route and _runs_on_credits(arguments):
        return False
    given = [
        arguments[name]
        for name in _VERTEX_CREDENTIAL_ARGUMENTS
        if arguments.get(name) is not None
    ]
    return not given or not all(map(_gives_a_complete_service_account_key, given))


def refuse_vertex_call_without_a_service_account_key(
    provider, arguments, *, managed_route=False
) -> None:
    """Raise ModelConfigurationRefused, before any call, when a call to a
    customer's Vertex model carries no complete service-account key of the
    customer's own (``vertex_call_has_no_service_account_key``)."""
    if vertex_call_has_no_service_account_key(
        provider, arguments, managed_route=managed_route
    ):
        raise ModelConfigurationRefused()


def stored_vertex_row_gives_no_service_account_key(*values) -> bool:
    """Whether a row stored for a Vertex model gives no complete
    service-account key of the customer's own: none of ``values`` (its
    stored key and configuration, or JSON text of either) is one, by the
    test a call applies (``_gives_a_complete_service_account_key``; its
    location is set aside). A key given as text, a key alone, a project
    alone, a client email without a private key or a private key without a
    client email, an empty object, one that gives only a location, and
    nothing at all count as none. The stored-row form of
    ``vertex_call_has_no_service_account_key``, for counting such rows."""
    return not any(map(_gives_a_complete_service_account_key, values))


def stored_row_as_a_call_takes_it(value, *providers):
    """What a call is given from ``value``, a row's stored key or
    configuration. For a model of any provider other than Vertex AI
    (``providers``: the row's own and the one asked for, each read trimmed
    and in any case), ``value`` as it is. For a Vertex model, the object
    ``value`` holds, itself or as JSON text, without a ``key`` field, so the
    places that build a call give it as the model's Vertex configuration and
    send no key alone. A row that gives no complete service-account key
    (``stored_vertex_row_gives_no_service_account_key``) is refused before
    any call (ModelConfigurationRefused)."""
    if not any(is_vertex_provider(provider) for provider in providers):
        return value
    if stored_vertex_row_gives_no_service_account_key(value):
        raise ModelConfigurationRefused()
    configuration = _as_configuration(value)
    return {name: item for name, item in configuration.items() if name != "key"}


def refuse_vertex_model_sent_to_another_provider(provider, arguments) -> None:
    """Raise ModelConfigurationRefused, before any call, when ``provider``,
    the model's provider, names Vertex AI and the model library sends the
    call (``arguments``: what it is about to be given) to another provider
    (``_litellm_provider``). A name the library places nowhere is left to
    the call and the refusals that run before it."""
    if not is_vertex_provider(provider):
        return
    sent_to = _litellm_provider(arguments)
    if sent_to and not is_vertex_provider(sent_to):
        raise ModelConfigurationRefused()


def vertex_call_arguments(provider, arguments, configuration):
    """What a call to a customer's model is given, from ``arguments`` (the
    call's own arguments) and ``configuration`` (what the model's saved key
    holds). For a call that goes to Vertex AI (``_is_vertex_call``):
    ``arguments`` without a key, and, when ``configuration`` is an object,
    that object as the model's Vertex configuration under the names the chat
    payload builders use (``vertex_credentials``: JSON text of it without
    its location; ``vertex_location``: its location, when it gives one).
    Such a call is refused before it is made, with the same sentence, in
    three cases: the model's provider is Vertex AI and the model library
    sends the call to another provider
    (``refuse_vertex_model_sent_to_another_provider``); ``configuration`` is
    an object that gives no complete service-account key
    (``stored_vertex_row_gives_no_service_account_key``); or the call then
    carries no complete service-account key
    (``refuse_vertex_call_without_a_service_account_key``). Any other call:
    ``arguments`` themselves, unchanged."""
    if not _is_vertex_call(provider, arguments):
        return arguments
    refuse_vertex_model_sent_to_another_provider(provider, arguments)
    given = {name: item for name, item in arguments.items() if name != "api_key"}
    if isinstance(configuration, dict):
        if stored_vertex_row_gives_no_service_account_key(configuration):
            raise ModelConfigurationRefused()
        given["vertex_credentials"] = json.dumps(
            {name: item for name, item in configuration.items() if name != "location"}
        )
        if configuration.get("location"):
            given["vertex_location"] = configuration["location"]
    refuse_vertex_call_without_a_service_account_key(provider, given)
    return given


def _sent(value):
    """What a form sends, as the save check reads it: ``value``, or each of
    its items when it is a list or tuple (a form field's values, or several
    fields), each read as what it holds when it is JSON object or array
    text."""
    items = value if isinstance(value, list | tuple) else [value]
    return [_as_configuration(item) for item in items]


def submitted_config_not_valid(value, *, provider) -> bool:
    """Whether a key or configuration a create or edit form (or the admin) is
    sent for a model of ``provider`` may not be saved: it holds a
    non-literal value (``names_platform_value``) or sets one of the
    reserved identity fields (``sets_platform_identity_field``),
    whatever the provider, or may not be a model's Google credential
    (``is_google_credential_other_than_a_service_account_key``: another kind
    of Google credential for any provider; for a Vertex model, anything other
    than a service-account key that signs in only at Google's own addresses;
    ``provider`` None when the form names none); as sent or in what JSON text
    sent in it holds (``_sent``)."""
    given = _sent(value)
    return (
        names_platform_value([value, given])
        or sets_platform_identity_field([value, given])
        or is_google_credential_other_than_a_service_account_key(provider, *given)
    )


def refuse_submitted_config(value, *, provider) -> None:
    """Raise ModelConfigurationRefused when ``submitted_config_not_valid``:
    what a create or edit form refuses. Never strips or rewrites the value."""
    if submitted_config_not_valid(value, provider=provider):
        raise ModelConfigurationRefused()


# ------------------------------------------- stored provider configuration ---

_AWS_KEYS = (
    "key",
    "aws_access_key_id",
    "aws_secret_access_key",
    "aws_region_name",
    "aws_session_token",
)
# The keys each provider's stored configuration may keep, as the create and
# edit forms (and their JSON tabs) send them; "key" is the key-only shape.
_CONFIG_KEYS = {
    "openai": ("key", "api_base", "organization"),
    "azure": (
        "key",
        "api_base",
        "apiBase",
        "api_key",
        "apiKey",
        "api_version",
        "apiVersion",
        "azure_endpoint_type",
        "azureEndpointType",
    ),
    "bedrock": _AWS_KEYS,
    "sagemaker": _AWS_KEYS,
    # Not a provider configurations are saved under: what an Azure AI call
    # (an azure configuration's foundry endpoint) takes from a stored one.
    "azure_ai": ("api_base", "api_key", "api_version"),
}


def whitelist_model_config(provider, config):
    """``config`` with only the keys its provider's configuration needs
    (openai: key, api_base, organization; azure: base, key, version and
    endpoint type; bedrock/sagemaker: the AWS credentials and region;
    azure_ai, the provider of a call: base, key and version). Every other
    key would reach litellm as an argument, ``extra_headers`` or an endpoint
    override included. Other providers, and anything not a dict, are
    returned unchanged (``custom`` has ``normalize_custom_model_config``)."""
    keys = _CONFIG_KEYS.get(str(provider or "").strip().lower())
    if keys is None or not isinstance(config, dict):
        return config
    return {key: config[key] for key in keys if key in config}


# ------------------------------------------- litellm calls to a tenant URL ---


def _sdk_transport_classes():
    """Guarded transports whose refusals reach the OpenAI SDK as an
    ``OpenAIError``, which it re-raises without retrying (a refused endpoint
    stays refused), with the guard's exception as the cause."""
    global _SDK_TRANSPORTS
    if _SDK_TRANSPORTS is None:
        import openai

        def _sdk_error(exc, request):
            return openai.APIConnectionError(message=str(exc), request=request)

        class _SdkGuardedTransport(GuardedHTTPTransport):
            def handle_request(self, request):
                try:
                    return super().handle_request(request)
                except (EndpointBlocked, GuardedRequestError) as exc:
                    raise _sdk_error(exc, request) from exc

        class _AsyncSdkGuardedTransport(AsyncGuardedHTTPTransport):
            async def handle_async_request(self, request):
                try:
                    return await super().handle_async_request(request)
                except (EndpointBlocked, GuardedRequestError) as exc:
                    raise _sdk_error(exc, request) from exc

        _SDK_TRANSPORTS = (_SdkGuardedTransport, _AsyncSdkGuardedTransport)
    return _SDK_TRANSPORTS


_SDK_TRANSPORTS = None


def _guarded_http_client(policy, *, is_async):
    import httpx
    from litellm.llms.custom_httpx.http_handler import get_ssl_configuration

    sync_transport, async_transport = _sdk_transport_classes()
    verify = get_ssl_configuration()
    kwargs = {
        "follow_redirects": False,
        "trust_env": False,
        "timeout": httpx.Timeout(600.0, connect=30.0),
    }
    if is_async:
        return httpx.AsyncClient(
            transport=async_transport(policy, verify=verify), **kwargs
        )
    return httpx.Client(transport=sync_transport(policy, verify=verify), **kwargs)


# Sent as the key when a tenant endpoint has no key configured (a keyless
# local model server): a call to a tenant URL uses only the configuration's
# own key or this placeholder; no environment key is used. The SDK refuses an
# empty key.
_NO_TENANT_KEY = "no-key-configured"


def _tenant_api_key(kwargs) -> str:
    value = kwargs.get("api_key")
    return value if isinstance(value, str) and value else _NO_TENANT_KEY


def _openai_client(kwargs, policy, *, is_async, base_url=None):
    import openai

    client_class = openai.AsyncOpenAI if is_async else openai.OpenAI
    organization = kwargs.get("organization")
    return client_class(
        api_key=_tenant_api_key(kwargs),
        base_url=base_url or kwargs["api_base"],
        organization=organization if isinstance(organization, str) else None,
        http_client=_guarded_http_client(policy, is_async=is_async),
    )


def _azure_client(kwargs, policy, *, is_async):
    """The client litellm itself would build for this Azure call (the
    OpenAI client at /openai/v1/ for a v1 api_version, AzureOpenAI with the
    endpoint or deployment base otherwise), on a guarded http client."""
    import os

    import litellm
    import openai
    from litellm.llms.azure.common_utils import (
        BaseAzureLLM,
        select_azure_base_url_or_endpoint,
    )

    api_base = kwargs["api_base"]
    api_version = (
        kwargs.get("api_version")
        or litellm.api_version
        or os.getenv("AZURE_API_VERSION")
        or litellm.AZURE_DEFAULT_API_VERSION
    )
    if BaseAzureLLM._is_azure_v1_api_version(api_version):
        return _openai_client(
            kwargs, policy, is_async=is_async, base_url=f"{api_base}/openai/v1/"
        )
    params = select_azure_base_url_or_endpoint(
        {
            "api_key": _tenant_api_key(kwargs),
            "azure_endpoint": api_base,
            "api_version": api_version,
            "http_client": _guarded_http_client(policy, is_async=is_async),
        }
    )
    client_class = openai.AsyncAzureOpenAI if is_async else openai.AzureOpenAI
    return client_class(**params)


def _http_handler(policy, *, is_async):
    """litellm's own HTTP handler (azure_ai and other providers it calls with
    plain httpx) on a guarded http client."""
    from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler, HTTPHandler

    client = _guarded_http_client(policy, is_async=is_async)
    if not is_async:
        return HTTPHandler(client=client)

    class _GuardedAsyncHTTPHandler(AsyncHTTPHandler):
        def create_client(self, *args, **kwargs):
            return client

    return _GuardedAsyncHTTPHandler()


def _litellm_provider(kwargs):
    import litellm

    try:
        _model, provider, _key, _base = litellm.get_llm_provider(
            model=str(kwargs.get("model") or ""),
            custom_llm_provider=kwargs.get("custom_llm_provider"),
            api_base=kwargs.get("api_base"),
        )
    except Exception:
        return None
    return provider


# The providers whose tenant base URL FA passes to litellm (PayloadBuilder,
# validate_model_working, LLM.call_llm: openai, azure, azure_ai), and whose
# routes the guard covers: an explicit guarded client where litellm takes
# one, ``tenant_endpoint_scope`` where it builds its own.
_GUARDED_PROVIDERS = frozenset({"openai", "azure", "azure_ai"})


def _guarded_litellm_client(kwargs, policy, *, is_async, call):
    provider = _litellm_provider(kwargs)
    model = str(kwargs.get("model") or "")
    if provider not in _GUARDED_PROVIDERS:
        # A provider whose routes the guard has not been checked against:
        # refused on every deployment rather than left on litellm's own
        # client (redirects followed, its own DNS).
        raise EndpointBlocked("unsupported")
    if provider == "openai":
        return _openai_client(kwargs, policy, is_async=is_async)
    if provider == "azure" and call == "completion":
        return _azure_client(kwargs, policy, is_async=is_async)
    if provider == "azure_ai" and call == "completion" and "agents/" not in model:
        return _http_handler(policy, is_async=is_async)
    # Azure and Azure AI image generation and Azure AI agents take no client
    # (litellm passes client=None itself); tenant_endpoint_scope makes the
    # one litellm builds a guarded one.
    return None


def _guard(kwargs, *, is_async=False, call="completion"):
    """(litellm arguments, policy): ``kwargs`` without the reserved
    identity fields and None without a base URL; otherwise the guarded
    arguments (``guard_litellm_kwargs``) and the deployment's policy for the
    call. Every call, with a base URL or not, first refuses credentials that
    hold a non-literal value (``refuse_platform_values_in_call``,
    ModelConfigurationRefused); then the reserved identity fields are
    dropped, whoever set them (``without_platform_identity_fields``), and at
    any depth of a Vertex configuration given, under either name
    (``_VERTEX_CREDENTIAL_ARGUMENTS``), as an object or as JSON text
    (``without_platform_identity_fields_at_any_depth``). What is left of
    that configuration must be a complete service-account key, an object or
    JSON text of one (``_gives_a_complete_service_account_key``; anything
    else, an empty object and text that holds no object included, is
    refused: a Vertex call needs a complete service-account key of the
    customer's own), and no other kind of Google credential at any depth,
    or ModelConfigurationRefused
    (``refuse_google_credential_other_than_a_service_account_key``)."""
    refuse_platform_values_in_call(kwargs)
    kwargs = without_platform_identity_fields(kwargs)
    for name in _VERTEX_CREDENTIAL_ARGUMENTS:
        if kwargs.get(name) is None:
            continue
        kwargs[name] = without_platform_identity_fields_at_any_depth(kwargs[name])
        if not _gives_a_complete_service_account_key(kwargs[name]):
            raise ModelConfigurationRefused()
        refuse_google_credential_other_than_a_service_account_key(
            "vertex_ai", kwargs[name]
        )
    base_key = next((k for k in ("api_base", "base_url") if kwargs.get(k)), None)
    if base_key is None:
        return kwargs, None
    base = kwargs[base_key]
    if not isinstance(base, str):
        raise EndpointBlocked("invalid_url")
    policy = model_endpoint_policy()
    check_endpoint_url(base, policy=policy)
    guarded = dict(kwargs)
    guarded.pop("base_url", None)
    guarded["api_base"] = base
    # Every route gets the tenant's key (or the placeholder), a route that
    # drops the guarded client too: the call uses only the configuration's
    # own key; no environment key is used for the tenant's URL.
    guarded["api_key"] = _tenant_api_key(guarded)
    for header_key in ("extra_headers", "headers"):
        headers = guarded.get(header_key)
        if isinstance(headers, dict):
            guarded[header_key] = {
                name: value
                for name, value in headers.items()
                if not is_metadata_or_forwarding_header(name)
                and str(name).strip().lower() != "host"
            }
    client = _guarded_litellm_client(guarded, policy, is_async=is_async, call=call)
    if client is not None:
        guarded["client"] = client
    return guarded, policy


def guard_litellm_kwargs(kwargs, *, is_async=False, call="completion"):
    """litellm arguments for a call, always without the reserved
    identity fields: otherwise unchanged without a base URL; with one
    (``api_base``/``base_url``, a tenant's model endpoint), the URL checked
    under the deployment's policy, the tenant's key (or a placeholder) set,
    metadata and forwarding headers dropped from ``extra_headers``/
    ``headers``, and a guarded ``client`` for the provider litellm will use.
    Raises EndpointBlocked, or ModelConfigurationRefused for credentials that
    hold a reference (such as os.environ/... or oidc/...) instead of a
    literal value. A call must also run inside
    ``tenant_endpoint_scope`` (the ``guarded_*`` functions do both): some
    routes drop the client."""
    return _guard(kwargs, is_async=is_async, call=call)[0]


def calls_endpoint_url(kwargs) -> bool:
    """Whether litellm arguments (or a stored config) name a base URL."""
    return isinstance(kwargs, dict) and any(
        kwargs.get(key) for key in ("api_base", "base_url")
    )


# ------------------------------------- clients litellm builds for itself ---
#
# Some litellm routes do not use the client ``guard_litellm_kwargs`` gives
# them and build one through litellm's HTTP layer instead (litellm 1.84):
# the Responses-API bridge (the sync bridge drops the OpenAI client, the
# async bridge passes none on), Azure and Azure AI image generation
# (``client=None``), Azure AI agents (including their lazily started
# stream). litellm's own clients follow redirects and resolve names
# themselves. While a tenant call runs, inside ``tenant_endpoint_scope``,
# every client litellm builds is a guarded one under the call's policy:
#
# - ``HTTPHandler`` and ``AsyncHTTPHandler``, however they are constructed;
# - ``BaseOpenAILLM``'s own OpenAI SDK http clients;
# - litellm's client cache (``LLMClientCache``) neither returns nor stores
#   anything, so a cached unguarded handler is not reused and nothing built
#   in the call outlives it.
#
# litellm's lazily built module-level clients are built before the first
# scope (outside it), so they never become guarded ones. Outside a scope
# every hook passes straight through. The hooks patch four litellm classes
# process-wide, once; test_custom_model_litellm_routes_ssrf.py fails loudly
# if a litellm upgrade moves them.

_TENANT_CALL_POLICY = contextvars.ContextVar(
    "tenant_endpoint_call_policy", default=None
)
_HOOKS_LOCK = threading.Lock()
_HOOKS_INSTALLED = False


def _scope_policy():
    return _TENANT_CALL_POLICY.get()


def _hook_http_handler(handler_class):
    init = handler_class.__init__
    signature = inspect.signature(init)

    @functools.wraps(init)
    def __init__(self, *args, **kwargs):
        policy = _scope_policy()
        if policy is None:
            return init(self, *args, **kwargs)
        bound = signature.bind(self, *args, **kwargs)
        bound.arguments["client"] = _guarded_http_client(policy, is_async=False)
        return init(*bound.args, **bound.kwargs)

    handler_class.__init__ = __init__


def _hook_async_http_handler(handler_class):
    create_client = handler_class.create_client

    @functools.wraps(create_client)
    def create(self, *args, **kwargs):
        policy = _scope_policy()
        if policy is None:
            return create_client(self, *args, **kwargs)
        return _guarded_http_client(policy, is_async=True)

    handler_class.create_client = create


def _hook_sdk_http_clients(base_class):
    for name, is_async in (
        ("_get_sync_http_client", False),
        ("_get_async_http_client", True),
    ):
        original = getattr(base_class, name)

        def make(original, is_async):
            @functools.wraps(original)
            def build(*args, **kwargs):
                policy = _scope_policy()
                if policy is None:
                    return original(*args, **kwargs)
                return _guarded_http_client(policy, is_async=is_async)

            return staticmethod(build)

        setattr(base_class, name, make(original, is_async))


def _hook_client_cache(cache_class):
    for name in ("get_cache", "set_cache"):
        original = getattr(cache_class, name)

        def make(original):
            @functools.wraps(original)
            def bypass(self, *args, **kwargs):
                if _scope_policy() is not None:
                    return None
                return original(self, *args, **kwargs)

            return bypass

        setattr(cache_class, name, make(original))
    for name in ("async_get_cache", "async_set_cache"):
        original = getattr(cache_class, name)

        def make_async(original):
            @functools.wraps(original)
            async def bypass(self, *args, **kwargs):
                if _scope_policy() is not None:
                    return None
                return await original(self, *args, **kwargs)

            return bypass

        setattr(cache_class, name, make_async(original))


def _install_litellm_client_hooks():
    global _HOOKS_INSTALLED
    if _HOOKS_INSTALLED:
        return
    with _HOOKS_LOCK:
        if _HOOKS_INSTALLED:
            return
        import litellm
        from litellm.caching.llm_caching_handler import LLMClientCache
        from litellm.llms.custom_httpx.http_handler import (
            AsyncHTTPHandler,
            HTTPHandler,
        )
        from litellm.llms.openai.common_utils import BaseOpenAILLM

        # Built now, outside any scope (they are built lazily, on first use).
        _ = litellm.module_level_client, litellm.module_level_aclient
        _hook_client_cache(LLMClientCache)
        _hook_http_handler(HTTPHandler)
        _hook_async_http_handler(AsyncHTTPHandler)
        _hook_sdk_http_clients(BaseOpenAILLM)
        _HOOKS_INSTALLED = True


class tenant_endpoint_scope(contextlib.AbstractContextManager):  # noqa: N801
    """``with tenant_endpoint_scope(policy):`` while the block runs (in this
    thread or task, and wherever it copies its context to), every HTTP
    client litellm builds is a guarded one under ``policy``: checked and
    pinned connections, no redirects, metadata headers dropped. The
    ``guarded_*`` functions run each call, and each step of a stream it
    returns, inside one."""

    def __init__(self, policy):
        self._policy = policy
        self._token = None

    def __enter__(self):
        _install_litellm_client_hooks()
        self._token = _TENANT_CALL_POLICY.set(self._policy)
        return self

    def __exit__(self, *exc_info):
        _TENANT_CALL_POLICY.reset(self._token)
        return False


# --------------------------------------------- errors from a tenant call ---


class EndpointCallError(Exception):
    """A call to a tenant's model endpoint failed. ``str()`` is generic,
    categorised text (``endpoint_call_error_message``), safe to show the
    tenant, log, send or cache; ``category`` is ``EndpointBlocked``'s or
    ``GuardedRequestError``'s, or ``timeout``, ``connect``,
    ``bad_response``, ``status`` (``status_code`` set) or ``failed``;
    ``cause_type`` names the exception it replaces. Raised without a cause
    or context, so no exception chain carries the provider's text."""

    def __init__(
        self, message, category, *, status_code=None, cause_type=None,
        requires_text_messages=False,
    ):
        super().__init__(message)
        self.category = category
        self.status_code = status_code
        self.cause_type = cause_type
        # A coarse compatibility signal only; never retain provider text or a chain.
        self.requires_text_messages = requires_text_messages


class EndpointCallTimeout(EndpointCallError, TimeoutError):
    """The ``timeout`` category: a TimeoutError, so callers that retry
    timeouts still retry it."""


# The guard's own errors, whose text is generic and whose chain carries
# nothing of the provider's.
ENDPOINT_ERROR_TYPES = (EndpointCallError, EndpointBlocked, GuardedRequestError)


def is_endpoint_error(exc) -> bool:
    """Whether ``exc`` is one of ``ENDPOINT_ERROR_TYPES``."""
    return isinstance(exc, ENDPOINT_ERROR_TYPES)


def _exception_chain(exc):
    seen, pending = set(), [exc]
    while pending and len(seen) < 32:
        current = pending.pop(0)
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        pending.extend([current.__cause__, current.__context__])


def _categorise(exc):
    """(category, generic text, HTTP status or None) for what a call to a
    tenant's model endpoint raised."""
    import httpx
    import openai

    chain = list(_exception_chain(exc))
    for error in chain:
        if isinstance(error, EndpointCallError):
            return error.category, str(error), error.status_code
        if isinstance(error, (EndpointBlocked, GuardedRequestError)):
            return error.category, str(error), None
    for error in chain:
        if isinstance(error, (openai.APITimeoutError, httpx.TimeoutException)):
            return "timeout", ENDPOINT_ERROR_MESSAGES["timeout"], None
    for error in chain:
        if isinstance(error, json.JSONDecodeError) or "Invalid response object" in str(
            error
        ):
            return "bad_response", ENDPOINT_ERROR_MESSAGES["bad_response"], None
    for error in chain:
        # Before the status: litellm reports a connection failure as an
        # InternalServerError (500) whose chain holds the SDK's error.
        if isinstance(
            error, (openai.APIConnectionError, httpx.TransportError, ConnectionError)
        ):
            return "connect", ENDPOINT_ERROR_MESSAGES["connect"], None
    for error in chain:
        status = getattr(error, "status_code", None)
        if (
            isinstance(error, openai.APIStatusError)
            and isinstance(status, int)
            and status >= 400
        ):
            return "status", endpoint_status_message(status), status
    return "failed", ENDPOINT_ERROR_MESSAGES["failed"], None


def endpoint_call_error_message(exc) -> str:
    """Generic text for what a call to a tenant's model endpoint raised:
    the guard's own category, a timeout or connection failure, an answer
    that is not a completion, or the HTTP status. Never the provider's
    message, the URL or a response body."""
    return _categorise(exc)[1]


def endpoint_call_error(exc) -> EndpointCallError:
    """The EndpointCallError (EndpointCallTimeout for a timeout) that
    replaces ``exc``, which a call to a tenant's model endpoint raised.
    Raise it outside the ``except`` block that caught ``exc`` (and ``from
    None``) so that it has no context either."""
    if isinstance(exc, EndpointCallError):
        return exc
    category, message, status = _categorise(exc)
    error_class = EndpointCallTimeout if category == "timeout" else EndpointCallError
    requires_text_messages = (
        category == "status"
        and status == 400
        and any("value must be a string" in str(error) for error in _exception_chain(exc))
    )
    return error_class(
        message, category, status_code=status, cause_type=type(exc).__name__,
        requires_text_messages=requires_text_messages,
    )


class _EndpointStream:
    """A stream litellm returned for a tenant call: each step runs inside
    the call's ``tenant_endpoint_scope`` (a stream litellm starts lazily
    builds its client then) and raises EndpointCallError instead of the
    provider's error. Everything else is the stream's own."""

    def __init__(self, stream, policy):
        self._stream = stream
        self._policy = policy
        self._iterator = None
        self._async_iterator = None

    def __getattr__(self, name):
        return getattr(self._stream, name)

    def __iter__(self):
        return self

    def __next__(self):
        with tenant_endpoint_scope(self._policy):
            try:
                if self._iterator is None:
                    self._iterator = iter(self._stream)
                return next(self._iterator)
            except StopIteration:
                raise
            except Exception as exc:
                error = endpoint_call_error(exc)
        raise error from None

    def __aiter__(self):
        return self

    async def __anext__(self):
        with tenant_endpoint_scope(self._policy):
            try:
                if self._async_iterator is None:
                    self._async_iterator = self._stream.__aiter__()
                return await self._async_iterator.__anext__()
            except StopAsyncIteration:
                raise
            except Exception as exc:
                error = endpoint_call_error(exc)
        raise error from None


def _call_tenant_endpoint(function, kwargs, policy):
    with tenant_endpoint_scope(policy):
        try:
            response = function(**kwargs)
        except Exception as exc:
            error = endpoint_call_error(exc)
        else:
            if kwargs.get("stream"):
                return _EndpointStream(response, policy)
            return response
    raise error from None


async def _acall_tenant_endpoint(function, kwargs, policy):
    with tenant_endpoint_scope(policy):
        try:
            response = await function(**kwargs)
        except Exception as exc:
            error = endpoint_call_error(exc)
        else:
            if kwargs.get("stream"):
                return _EndpointStream(response, policy)
            return response
    raise error from None


_message_capture = contextvars.ContextVar("run_prompt_messages", default=None)


@contextlib.contextmanager
def capture_llm_messages():
    """Privately observe final messages; non-message dispatches remain unavailable."""
    captured = {}
    token = _message_capture.set(captured)
    try:
        yield captured
    finally:
        _message_capture.reset(token)


def _capture_llm_messages(payload):
    captured = _message_capture.get()
    if captured is not None:
        captured["messages"] = copy.deepcopy(payload.get("messages"))


def guarded_completion(**kwargs):
    """``litellm.completion``; with a tenant base URL, through
    ``guard_litellm_kwargs`` inside ``tenant_endpoint_scope``, raising
    EndpointBlocked (refused before any connection) or EndpointCallError.
    Credentials that hold a reference (such as os.environ/... or oidc/...)
    instead of a literal value are refused before any call, with a base URL
    or not (ModelConfigurationRefused)."""
    import litellm

    from model_hub.services.model_route import (
        managed_binding_in,
        managed_chat_completion,
    )

    binding = managed_binding_in(kwargs)
    if binding is not None:
        # A managed binding never reaches litellm: it is one admitted request
        # through the gateway's managed client.
        _capture_llm_messages(kwargs)
        return managed_chat_completion(binding, kwargs)
    guarded, policy = _guard(kwargs)
    _capture_llm_messages(guarded)
    if policy is None:
        return litellm.completion(**guarded)
    return _call_tenant_endpoint(litellm.completion, guarded, policy)


async def guarded_acompletion(**kwargs):
    """``litellm.acompletion``, guarded like ``guarded_completion``."""
    import litellm

    from model_hub.services.model_route import (
        managed_binding_in,
        managed_chat_completion_async,
    )

    binding = managed_binding_in(kwargs)
    if binding is not None:
        # One key: see guarded_completion.
        _capture_llm_messages(kwargs)
        return await managed_chat_completion_async(binding, kwargs)
    guarded, policy = _guard(kwargs, is_async=True)
    _capture_llm_messages(guarded)
    if policy is None:
        return await litellm.acompletion(**guarded)
    return await _acall_tenant_endpoint(litellm.acompletion, guarded, policy)


def guarded_image_generation(**kwargs):
    """``litellm.image_generation``, guarded like ``guarded_completion``."""
    import litellm

    from model_hub.services.model_route import (
        ManagedRouteUnsupported,
        managed_binding_in,
    )

    if managed_binding_in(kwargs) is not None:
        # One key: Future AGI credits run text chat completions only.
        raise ManagedRouteUnsupported()
    guarded, policy = _guard(kwargs, call="image_generation")
    _capture_llm_messages({})
    if policy is None:
        return litellm.image_generation(**guarded)
    return _call_tenant_endpoint(litellm.image_generation, guarded, policy)


# --------------------------------------------------- saved audio models ---


def audio_model_kwargs(provider, configuration):
    """The selected audio key and endpoint, not the stored dict as a key.

    RunPrompt has already whitelisted saved rows and may have renamed ``key``
    to ``api_key``. Do not whitelist that canonical call shape a second time.
    Vertex's service-account mapping belongs to its existing handler; scalar
    keys (including intentional platform calls without one) stay unchanged.
    """
    if not isinstance(configuration, dict) or is_vertex_provider(provider):
        return {"api_key": configuration}

    from model_hub.utils.azure_endpoints import normalize_azure_custom_model_config
    from model_hub.utils.model_key import ModelKeyMissing

    refuse_platform_values(configuration)
    refuse_google_credential_other_than_a_service_account_key(provider, configuration)
    if "key" in configuration:
        selected = configuration["key"]
    elif provider == "azure":
        # Preserve the existing Azure normalizer's canonical/camel precedence.
        selected = normalize_azure_custom_model_config(configuration)["api_key"]
    else:
        selected = configuration.get("api_key")
    # A present key is authoritative, while Azure aliases follow its existing
    # normalizer. The selected value must still be usable literal text.
    if not isinstance(selected, str) or not selected.strip():
        raise ModelKeyMissing()
    arguments = {"api_key": selected}
    for name in ("api_base", "base_url", "api_version", "organization"):
        if name in configuration:
            arguments[name] = configuration[name]
    if provider:
        arguments["custom_llm_provider"] = provider
    if provider == "azure":
        azure_configuration = dict(configuration, api_key=selected)
        if "api_base" not in azure_configuration and "base_url" in configuration:
            azure_configuration["api_base"] = configuration["base_url"]
        normalized = normalize_azure_custom_model_config(azure_configuration)
        arguments.pop("base_url", None)
        arguments.update(
            api_base=normalized["api_base"],
            api_version=normalized["api_version"],
            api_key=selected,
        )
        if normalized["api_base"] and normalized["azure_endpoint_type"] == "foundry":
            arguments["custom_llm_provider"] = "azure_ai"
    return arguments


def guarded_transcription(**kwargs):
    """Audio transcription under the same final guard and scope as chat."""
    import litellm

    from model_hub.services.model_route import (
        ManagedRouteUnsupported,
        managed_binding_in,
    )

    if managed_binding_in(kwargs) is not None:
        raise ManagedRouteUnsupported()
    guarded, policy = _guard(kwargs, call="transcription")
    _capture_llm_messages({})
    if policy is None:
        return litellm.transcription(**guarded)
    return _call_tenant_endpoint(litellm.transcription, guarded, policy)


def guarded_speech(**kwargs):
    """Audio speech under the same final guard and scope as chat."""
    import litellm

    from model_hub.services.model_route import (
        ManagedRouteUnsupported,
        managed_binding_in,
    )

    if managed_binding_in(kwargs) is not None:
        raise ManagedRouteUnsupported()
    guarded, policy = _guard(kwargs, call="speech")
    _capture_llm_messages({})
    if policy is None:
        return litellm.speech(**guarded)
    return _call_tenant_endpoint(litellm.speech, guarded, policy)


async def guarded_aspeech(**kwargs):
    """Async speech with an async guarded client and the tenant scope."""
    import litellm

    from model_hub.services.model_route import (
        ManagedRouteUnsupported,
        managed_binding_in,
    )

    if managed_binding_in(kwargs) is not None:
        raise ManagedRouteUnsupported()
    guarded, policy = _guard(kwargs, is_async=True, call="speech")
    _capture_llm_messages({})
    if policy is None:
        return await litellm.aspeech(**guarded)
    return await _acall_tenant_endpoint(litellm.aspeech, guarded, policy)
