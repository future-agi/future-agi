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
}


def whitelist_model_config(provider, config):
    """``config`` with only the keys its provider's configuration needs
    (openai: key, api_base, organization; azure: base, key, version and
    endpoint type; bedrock/sagemaker: the AWS credentials and region). Every
    other key would reach litellm as an argument, ``extra_headers`` or an
    endpoint override included. Other providers, and anything not a dict,
    are returned unchanged (``custom`` has ``normalize_custom_model_config``)."""
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


# Sent when a tenant endpoint has no key configured (a keyless local model
# server), so the SDK never falls back to the platform's OPENAI_API_KEY or
# AZURE_OPENAI_API_KEY for a tenant URL; it refuses an empty key.
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
    """(litellm arguments, policy): ``kwargs`` and None without a base URL;
    otherwise the guarded arguments (``guard_litellm_kwargs``) and the
    deployment's policy for the call."""
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
    # Every route gets the tenant's key (or the placeholder): a route that
    # drops the guarded client would otherwise read OPENAI_API_KEY /
    # AZURE_API_KEY from the platform's environment for the tenant's URL.
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
    """litellm arguments for a call: unchanged without a base URL; with one
    (``api_base``/``base_url``, a tenant's model endpoint), the URL checked
    under the deployment's policy, the tenant's key (or a placeholder) set,
    metadata and forwarding headers dropped from ``extra_headers``/
    ``headers``, and a guarded ``client`` for the provider litellm will use.
    Raises EndpointBlocked. A call must also run inside
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

    def __init__(self, message, category, *, status_code=None, cause_type=None):
        super().__init__(message)
        self.category = category
        self.status_code = status_code
        self.cause_type = cause_type


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
    return error_class(
        message, category, status_code=status, cause_type=type(exc).__name__
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


def guarded_completion(**kwargs):
    """``litellm.completion``; with a tenant base URL, through
    ``guard_litellm_kwargs`` inside ``tenant_endpoint_scope``, raising
    EndpointBlocked (refused before any connection) or EndpointCallError."""
    import litellm

    guarded, policy = _guard(kwargs)
    if policy is None:
        return litellm.completion(**kwargs)
    return _call_tenant_endpoint(litellm.completion, guarded, policy)


async def guarded_acompletion(**kwargs):
    """``litellm.acompletion``, guarded like ``guarded_completion``."""
    import litellm

    guarded, policy = _guard(kwargs, is_async=True)
    if policy is None:
        return await litellm.acompletion(**kwargs)
    return await _acall_tenant_endpoint(litellm.acompletion, guarded, policy)


def guarded_image_generation(**kwargs):
    """``litellm.image_generation``, guarded like ``guarded_completion``."""
    import litellm

    guarded, policy = _guard(kwargs, call="image_generation")
    if policy is None:
        return litellm.image_generation(**kwargs)
    return _call_tenant_endpoint(litellm.image_generation, guarded, policy)
