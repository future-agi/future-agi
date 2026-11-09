"""Every supported URL carrier, through every dispatch boundary, at the sinks.

The factory table is keyed by the redaction registry, so adding a carrier
without a rendering case fails the guard test below. Ruling 681 addendum 5:
each scenario is observed in what the configured sinks emit (checkout_sinks),
not in the in-memory record.
"""

import gc
import http.client
import io
import logging
import traceback
import types
import urllib.request
import wsgiref.headers
from argparse import Namespace
from collections import ChainMap, Counter, UserDict, UserList, UserString, deque
from contextlib import contextmanager
from email.header import Header
from email.message import Message
from http.cookiejar import Cookie, CookieJar
from http.cookies import Morsel
from pathlib import PurePosixPath

import httpx
import pytest
import requests
import starlette.datastructures
import starlette.requests
import starlette.responses
import urllib3
from django.core.files.base import File
from django.core.handlers.wsgi import WSGIRequest
from django.http import HttpResponseRedirect, StreamingHttpResponse
from django.template import Context
from django.test import RequestFactory
from django.urls import ResolverMatch, resolve, reverse_lazy
from django.utils.datastructures import CaseInsensitiveMapping, OrderedSet
from django.utils.functional import SimpleLazyObject, lazy
from rest_framework.request import Request as FrameworkRequest
from urllib3.util.retry import RequestHistory

from tfc.tests.checkout_sinks import configured_sinks, dispatch, native_emit

pytestmark = pytest.mark.integration
SUPPLIED = "synthetic-registry-session-value"
BOUNDARIES = ["logger", "handler", "formatter"]
PLACEMENTS = ["extra", "args", "exc_info"]


def target(checkout):
    prefix = "/agentcc/managed-credits/checkout" if checkout else "/ordinary"
    return prefix + "/" + SUPPLIED + "/"


def absolute(path):
    return "http://localhost" + path


class StoredTargetError(Exception):
    """A library-style error that renders an attribute rather than its args."""

    def __str__(self):
        return "late failure for " + str(vars(self).get("target"))


class FakeSocket:
    def __init__(self, data):
        self.data = data

    def makefile(self, mode):
        return io.BytesIO(self.data)


def _http_client_response(cls, path):
    raw = f"HTTP/1.1 302 Found\r\nLocation: {path}\r\nContent-Length: 0\r\n\r\n"
    response = cls(FakeSocket(raw.encode()))
    response.begin()
    return response


def _stored_target_error(cls, path):
    error = cls("ordinary message")
    error.target = path
    return error


def _wsgi_request(cls, path):
    request = RequestFactory().get(path)
    if type(request) is not cls:
        request.__class__ = cls
    return request


def _message(cls, path):
    message = cls()
    message["Location"] = path
    return message


def _morsel(cls, path):
    morsel = cls()
    morsel.set("next", path, path)
    return morsel


def _cookie(cls, path):
    return cls(
        0,
        "next",
        path,
        None,
        False,
        "localhost",
        False,
        False,
        "/",
        True,
        False,
        None,
        True,
        None,
        None,
        {},
        False,
    )


def _jar(cls, path):
    jar = cls()
    jar.set_cookie(_cookie(Cookie, path))
    return jar


def _prepared(cls, path):
    prepared = cls()
    prepared.prepare(method="GET", url=absolute(path))
    return prepared


def _requests_response(cls, path):
    response = cls()
    response.url = absolute(path)
    response.status_code = 302
    return response


def _framework_request(cls, path):
    return cls(RequestFactory().get(path))


def _view(request, **kwargs):
    return None


# registry name -> (class to instantiate, builder(cls, path)). The class is
# the registered base unless a concrete subclass is the realistic carrier.
FACTORIES = {
    "builtins.str": (str, lambda cls, p: cls(p)),
    "builtins.bytes": (bytes, lambda cls, p: cls(p.encode())),
    "builtins.bytearray": (bytearray, lambda cls, p: cls(p.encode())),
    "builtins.memoryview": (memoryview, lambda cls, p: cls(p.encode())),
    "builtins.list": (list, lambda cls, p: cls([p])),
    "builtins.tuple": (tuple, lambda cls, p: cls((p,))),
    "builtins.set": (set, lambda cls, p: cls({p})),
    "builtins.frozenset": (frozenset, lambda cls, p: cls({p})),
    "collections.deque": (deque, lambda cls, p: cls([p])),
    "builtins.dict": (dict, lambda cls, p: cls(target=p)),
    "builtins.mappingproxy": (
        types.MappingProxyType,
        lambda cls, p: cls({"target": p}),
    ),
    "builtins.BaseException": (StoredTargetError, _stored_target_error),
    "types.SimpleNamespace": (types.SimpleNamespace, lambda cls, p: cls(target=p)),
    "collections.UserDict": (UserDict, lambda cls, p: cls({"target": p})),
    "collections.UserList": (UserList, lambda cls, p: cls([p])),
    "collections.UserString": (UserString, lambda cls, p: cls(p)),
    "collections.ChainMap": (ChainMap, lambda cls, p: cls({"target": p})),
    "django.http.request.HttpRequest": (WSGIRequest, _wsgi_request),
    "django.http.response.HttpResponseBase": (
        HttpResponseRedirect,
        lambda cls, p: cls(p),
    ),
    "django.template.context.BaseContext": (Context, lambda cls, p: cls({"target": p})),
    "http.cookiejar.Cookie": (Cookie, _cookie),
    "http.cookiejar.CookieJar": (CookieJar, _jar),
    "pathlib.PurePath": (PurePosixPath, lambda cls, p: cls(p)),
    "httpx.Cookies": (httpx.Cookies, lambda cls, p: cls({"next": p})),
    "starlette.datastructures.State": (
        starlette.datastructures.State,
        lambda cls, p: cls({"target": p}),
    ),
    "django.utils.datastructures.CaseInsensitiveMapping": (
        CaseInsensitiveMapping,
        lambda cls, p: cls({"Location": p}),
    ),
    "django.urls.resolvers.ResolverMatch": (
        ResolverMatch,
        lambda cls, p: cls(_view, (), {"target": p}, url_name="ordinary-target"),
    ),
    "django.utils.datastructures.OrderedSet": (OrderedSet, lambda cls, p: cls([p])),
    "django.utils.functional.Promise": (
        type(lazy(str, str)("")),
        lambda cls, p: cls((p,), {}),
    ),
    "django.utils.functional.LazyObject": (
        SimpleLazyObject,
        lambda cls, p: _evaluated(cls(lambda: p)),
    ),
    "logging.LogRecord": (
        logging.LogRecord,
        lambda cls, p: cls("carried", 10, __file__, 1, "nested %s", (p,), None),
    ),
    "traceback.TracebackException": (
        traceback.TracebackException,
        lambda cls, p: cls(ValueError, ValueError(p), None),
    ),
    "traceback.FrameSummary": (
        traceback.FrameSummary,
        lambda cls, p: cls("module.py", 1, "handler", line=p),
    ),
    "argparse.Namespace": (Namespace, lambda cls, p: cls(target=p)),
    "email.header.Header": (Header, lambda cls, p: cls(p)),
    "django.core.files.base.File": (
        File,
        lambda cls, p: cls(io.BytesIO(b"body"), name=p),
    ),
    "httpx.Proxy": (httpx.Proxy, lambda cls, p: cls(absolute(p))),
    "starlette.datastructures.UploadFile": (
        starlette.datastructures.UploadFile,
        lambda cls, p: cls(io.BytesIO(b"body"), filename=p),
    ),
    "email.message.Message": (Message, _message),
    "http.client.HTTPResponse": (http.client.HTTPResponse, _http_client_response),
    "http.cookies.Morsel": (Morsel, _morsel),
    "urllib.request.Request": (
        urllib.request.Request,
        lambda cls, p: cls(absolute(p)),
    ),
    "wsgiref.headers.Headers": (
        wsgiref.headers.Headers,
        lambda cls, p: cls([("Location", p)]),
    ),
    "rest_framework.request.Request": (FrameworkRequest, _framework_request),
    "httpx.URL": (httpx.URL, lambda cls, p: cls(absolute(p))),
    "httpx.Request": (httpx.Request, lambda cls, p: cls("GET", absolute(p))),
    "httpx.Response": (
        httpx.Response,
        lambda cls, p: cls(302, request=httpx.Request("GET", absolute(p))),
    ),
    "httpx.Headers": (httpx.Headers, lambda cls, p: cls({"location": p})),
    "httpx.QueryParams": (httpx.QueryParams, lambda cls, p: cls({"next": p})),
    "requests.models.Request": (
        requests.Request,
        lambda cls, p: cls("GET", absolute(p)),
    ),
    "requests.models.PreparedRequest": (requests.PreparedRequest, _prepared),
    "requests.models.Response": (requests.Response, _requests_response),
    "requests.structures.CaseInsensitiveDict": (
        requests.structures.CaseInsensitiveDict,
        lambda cls, p: cls({"Location": p}),
    ),
    "urllib3.response.BaseHTTPResponse": (
        urllib3.HTTPResponse,
        lambda cls, p: cls(
            body=b"",
            headers={"Location": p},
            status=302,
            request_url=absolute(p),
            preload_content=False,
        ),
    ),
    "urllib3._collections.HTTPHeaderDict": (
        urllib3.HTTPHeaderDict,
        lambda cls, p: cls({"Location": p}),
    ),
    "urllib3.util.retry.Retry": (
        urllib3.util.Retry,
        lambda cls, p: cls(history=(RequestHistory("GET", absolute(p), None, 302, p),)),
    ),
    "starlette.datastructures.URL": (
        starlette.datastructures.URL,
        lambda cls, p: cls(absolute(p)),
    ),
    "starlette.datastructures.Headers": (
        starlette.datastructures.Headers,
        lambda cls, p: cls(headers={"location": p}),
    ),
    "starlette.datastructures.ImmutableMultiDict": (
        starlette.datastructures.QueryParams,
        lambda cls, p: cls([("next", p), ("next", "ordinary-final-value")]),
    ),
    "starlette.requests.HTTPConnection": (
        starlette.requests.Request,
        lambda cls, p: cls(
            {"type": "http", "path": p, "query_string": b"", "headers": []}
        ),
    ),
    "starlette.responses.Response": (
        starlette.responses.RedirectResponse,
        lambda cls, p: cls(absolute(p)),
    ),
}
UNSUBCLASSABLE = {"builtins.memoryview", "builtins.mappingproxy"}
HOOKED_METHODS = (
    "__repr__",
    "__str__",
    "__format__",
    "__iter__",
    "__len__",
    "__bool__",
    "__getitem__",
    "__contains__",
    "__eq__",
    "__hash__",
    "__getattribute__",
    "__reduce_ex__",
    "keys",
    "values",
    "items",
    "get",
    "decode",
    "replace",
    "find",
    "__bytes__",
)


def _evaluated(lazy):
    str(lazy)  # an already-evaluated lazy object stores its wrapped value
    return lazy


def build(name, checkout, cls=None):
    base, builder = FACTORIES[name]
    return builder(cls or base, target(checkout))


@contextmanager
def capture(otlp=True):
    with configured_sinks("checkout.registry.nonpropagating.sink", otlp=otlp) as (
        source,
        sinks,
    ):
        yield source, sinks


class CarrierError(Exception):
    def __str__(self):
        return "late failure with " + str(vars(self).get("payload"))


def make_record(source, carrier, placement):
    if placement == "extra":
        return source.makeRecord(
            source.name,
            40,
            __file__,
            1,
            "late diagnostic",
            (),
            None,
            extra={"payload": carrier},
        )
    if placement == "args":
        return source.makeRecord(
            source.name, 40, __file__, 1, "late diagnostic %s", (carrier,), None
        )
    error = CarrierError("late failure")
    error.payload = carrier
    return source.makeRecord(
        source.name, 40, __file__, 1, "late failure", (), (CarrierError, error, None)
    )


OPAQUE = (
    type,
    types.ModuleType,
    types.FunctionType,
    types.CodeType,
    types.FrameType,
    types.BuiltinFunctionType,
    types.MethodType,
)


def reachable_text(*roots, limit=20000):
    """Independent oracle: every str/bytes reachable through C-level references.

    Uses gc referents (instance dicts, slots, container items), not the
    redaction registry, and never calls an object's own methods.
    """
    found, pending, seen = [], list(roots), set()
    while pending and len(seen) < limit:
        value = pending.pop()
        kind = type(value)
        if id(value) in seen or issubclass(kind, OPAQUE):
            continue
        seen.add(id(value))
        if issubclass(kind, (str, bytes, bytearray)):
            found.append(value)
            continue
        pending.extend(gc.get_referents(value))
    return found


def test_every_registered_carrier_has_a_rendering_case():
    from tfc.logging.checkout_carriers import supported_carriers

    assert set(supported_carriers()) == set(FACTORIES)


@pytest.mark.parametrize("boundary", BOUNDARIES)
@pytest.mark.parametrize("placement", PLACEMENTS)
@pytest.mark.parametrize("name", sorted(FACTORIES))
def test_checkout_carrier_never_reaches_a_configured_sink(name, placement, boundary):
    carrier = build(name, checkout=True)
    if placement == "extra":
        # Native rendering may fill the carrier's own lazy caches (parsed path
        # parts, decoded headers, URL components). Let it, through the native
        # emit, then require that the boundary's pass changes nothing more.
        with native_emit(), capture() as (source, sinks):
            dispatch(source, sinks, make_record(source, carrier, placement), boundary)
    before = sorted(map(repr, reachable_text(carrier)))
    with capture() as (source, sinks):
        record = make_record(source, carrier, placement)
        dispatch(source, sinks, record, boundary)
    assert sinks.emitted(), "a configured sink wrote nothing"
    assert SUPPLIED not in sinks.outputs()["record"], (
        "checkout session value reached the full-record sink"
    )
    assert sinks.leaks(SUPPLIED) == [], (
        "checkout session value reached a configured sink"
    )
    assert sinks.structured(), "a production line is not one JSON document"
    if placement == "extra":
        # The sink boundary never touches application objects.
        after = sorted(map(repr, reachable_text(carrier)))
        assert after == before, "logging changed the application carrier"


@pytest.mark.parametrize("boundary", BOUNDARIES)
@pytest.mark.parametrize("placement", PLACEMENTS)
@pytest.mark.parametrize("name", sorted(FACTORIES))
def test_unrelated_carrier_is_delivered_unchanged(name, placement, boundary):
    carrier = build(name, checkout=False)
    with capture() as (source, observer):
        record = make_record(source, carrier, placement)
        dispatch(source, observer, record, boundary)
    delivered = observer.records[-1]
    assert not getattr(delivered, "_checkout_redacted", False)
    if placement == "extra":
        assert vars(delivered)["payload"] is carrier
    elif placement == "args":
        assert carrier is delivered.args or carrier in delivered.args
    else:
        assert vars(delivered.exc_info[1])["payload"] is carrier


@pytest.mark.parametrize("boundary", BOUNDARIES)
@pytest.mark.parametrize("name", sorted(set(FACTORIES) - UNSUBCLASSABLE))
def test_sink_boundary_runs_no_extra_carrier_or_metaclass_hook(name, boundary):
    base, _ = FACTORIES[name]
    calls, armed = [], []

    def hook(method_name, original):
        def counted(self, *args, **kwargs):
            if armed:
                calls.append(method_name)
            return original(self, *args, **kwargs)

        return counted

    namespace = {}
    for method_name in HOOKED_METHODS:
        original = getattr(base, method_name, None)
        if callable(original):
            namespace[method_name] = hook(method_name, original)

    def spoofed_class(self):
        if armed:
            calls.append("__class__")
            return str
        return object.__dict__["__class__"].__get__(self)

    namespace["__class__"] = property(spoofed_class)
    real_dict = next(
        (vars(c)["__dict__"] for c in base.__mro__ if "__dict__" in vars(c)), None
    )
    if real_dict is not None:

        def spoofed_dict(self):
            if armed:
                calls.append("__dict__")
            return real_dict.__get__(self)

        namespace["__dict__"] = property(spoofed_dict)
    meta_base = type(base)

    def meta_hook(method_name):
        original = getattr(meta_base, method_name)

        def counted(cls, *args):
            if armed:
                calls.append("metaclass " + method_name)
            return original(cls, *args)

        return counted

    meta = type(
        "UntrustedMeta",
        (meta_base,),
        {m: meta_hook(m) for m in ("__eq__", "__hash__", "__instancecheck__")},
    )
    hostile_type = meta("Untrusted" + base.__name__, (base,), namespace)

    def run():
        # The OTLP sink is not attached: the SDK's own attribute check honours
        # a spoofed __class__ and then cannot encode the value, equally with
        # or without the boundary, so it would only add noise to the counts.
        carrier = build(name, checkout=True, cls=hostile_type)
        with capture(otlp=False) as (source, sinks):
            record = make_record(source, carrier, "extra")
            armed.append(True)
            try:
                dispatch(source, sinks, record, boundary)
            finally:
                armed.clear()
        observed = Counter(calls)
        calls.clear()
        return sinks, observed

    sinks, with_boundary = run()
    with native_emit():
        _, natively = run()
    assert with_boundary == natively, (
        f"the sink boundary changed which carrier code ran: {with_boundary}"
        f" versus {natively} natively"
    )
    assert sinks.emitted(), "a configured sink wrote nothing"
    assert sinks.leaks(SUPPLIED) == [], "checkout session value reached a sink"


@pytest.mark.parametrize("checkout", [True, False])
def test_resolved_route_and_lazy_reverse_keep_the_bare_session(checkout):
    if checkout:
        match = resolve(target(True))
        lazy_url = reverse_lazy(
            "agentcc-managed-credit-checkout-status", kwargs={"session_id": SUPPLIED}
        )
    else:
        match = ResolverMatch(_view, (), {"slug": SUPPLIED}, url_name="ordinary")
        lazy_url = lazy(lambda slug: "/ordinary/" + slug + "/", str)(slug=SUPPLIED)
    for carrier in (match, lazy_url):
        with capture() as (source, sinks):
            source.error("late route", extra={"payload": carrier})
        record = sinks.records[-1]
        if checkout:
            assert sinks.emitted(), "a configured sink wrote nothing"
            assert SUPPLIED not in sinks.outputs()["record"]
            assert sinks.leaks(SUPPLIED) == []
        else:
            assert vars(record)["payload"] is carrier


@pytest.mark.parametrize(
    "build_request",
    [
        lambda path: RequestFactory().get("/ordinary/", {"next": path}),
        lambda path: RequestFactory().get("/ordinary/", HTTP_REFERER=absolute(path)),
        lambda path: RequestFactory().post("/ordinary/", {"return_to": path}),
    ],
    ids=["query-string", "referer", "body"],
)
@pytest.mark.parametrize("checkout", [True, False])
def test_request_metadata_beyond_the_path(build_request, checkout):
    request = build_request(target(checkout))
    assert request.POST is not None  # parsed form data is cached on the request
    with capture() as (source, sinks):
        source.error("late request", extra={"payload": request})
    record = sinks.records[-1]
    if checkout:
        assert sinks.emitted(), "a configured sink wrote nothing"
        assert SUPPLIED not in sinks.outputs()["record"]
        assert sinks.leaks(SUPPLIED) == []
    else:
        assert vars(record)["payload"] is request


def test_streams_and_lazy_values_are_never_consumed():
    consumed = []

    def chunks():
        consumed.append("http-client-stream")
        yield b"body"

    def body():
        consumed.append("response-iterator")
        yield b"body"

    def evaluate():
        consumed.append("lazy-evaluation")
        return target(True)

    def run():
        client_request = httpx.Request("POST", absolute(target(True)), content=chunks())
        streaming = StreamingHttpResponse(body())
        streaming["Location"] = target(True)
        lazy = SimpleLazyObject(evaluate)
        observed = []
        for carrier in (client_request, streaming):
            with capture() as (source, sinks):
                source.error("late stream", extra={"payload": carrier, "lazy": lazy})
            observed.append(sinks)
        return observed

    observed = run()
    with_boundary = list(consumed)
    consumed.clear()
    with native_emit():
        run()
    # The boundary reads only final text: no stream or lazy value is consumed
    # beyond what the same sinks consume natively.
    assert with_boundary == consumed
    for sinks in observed:
        assert sinks.emitted(), "a configured sink wrote nothing"
        assert sinks.leaks(SUPPLIED) == [], "checkout session value reached a sink"
