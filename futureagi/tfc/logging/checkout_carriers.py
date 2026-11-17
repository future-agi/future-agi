"""Trusted storage readers for the standard containers that can carry a URL.

Checkout-value discovery must not run code owned by the carrier: no repr/str,
properties, overridden mapping or iteration methods, response streams,
traceback frames, lazy objects or metaclass hooks. Every supported base is
read through storage that the BASE owns (built-in container C methods called
unbound, or the base's instance-dict and slot descriptors), so a subclass
cannot substitute what is inspected. Unknown types are skipped, which is also
why streams and iterators are never read. The caller owns traversal, cycle
detection and scrubbing; scrubbing replaces the carrier in an exported copy
(a telemetry or Sentry payload), never the application's object.
"""

import gc
import sys
from collections import ChainMap, UserDict, UserList, UserString, deque
from types import (
    GetSetDescriptorType,
    MappingProxyType,
    MemberDescriptorType,
    SimpleNamespace,
)

from django.http import HttpRequest
from django.http.response import HttpResponseBase
from django.template.context import BaseContext
from django.urls import ResolverMatch
from django.utils.datastructures import CaseInsensitiveMapping, OrderedSet
from django.utils.functional import LazyObject, Promise

# type.__mro__ read through type's own descriptor: no metaclass hook and no
# ABC subclass hook or virtual-subclass registration can change the answer.
_mro = type.__dict__["__mro__"].__get__


def derives(kind, base):
    """issubclass for real inheritance only, compared by identity."""
    return any(entry is base for entry in _mro(kind))


def _storage_descriptors(base):
    # Only descriptors defined in the TRUSTED base's own MRO; a subclass's
    # __dict__ property or attribute override is never consulted.
    found = []
    for cls in _mro(base):
        for name, descriptor in vars(cls).items():
            if name == "__weakref__":
                continue
            if type(descriptor) is MemberDescriptorType or (
                name == "__dict__" and type(descriptor) is GetSetDescriptorType
            ):
                found.append(descriptor)
    return tuple(found)


def instance_storage(value, base):
    """The base-owned instance dictionary of value, or an empty dict."""
    for descriptor in _storage_descriptors(base):
        if descriptor.__name__ == "__dict__":
            stored = descriptor.__get__(value)
            if type(stored) is dict:
                return stored
    return {}


def _stored_fields(descriptors, value):
    for descriptor in descriptors:
        try:
            yield descriptor.__get__(value)
        except AttributeError:
            # Unset slots and optional native fields (characters_written).
            pass


def _storage_reader(base):
    descriptors = _storage_descriptors(base)
    return lambda value: tuple(_stored_fields(descriptors, value))


def _path_reader(base):
    read = _storage_reader(base)

    def parts(value):
        stored = read(value)
        fields = []
        for field in stored:
            fields.extend(dict.values(field) if type(field) is dict else (field,))
        joined = []
        for field in fields:
            # PurePath stores path segments (_parts on 3.11, _raw_paths on
            # 3.12+). Scan them together as well as separately; never call
            # str/fspath, which may evaluate subclass hooks or cache a result.
            kind = type(field)
            if (kind is list or kind is tuple) and all(type(p) is str for p in field):
                joined.append("/".join(field).replace("\\", "/"))
        return (*stored, *joined)

    return parts


def _base_iteration(method):
    return lambda value: tuple(method(value))


def _mapping_storage(value):
    return (*dict.keys(value), *dict.values(value))


def _storage_and_mapping(base):
    read = _storage_reader(base)
    return lambda value: (*read(value), *_mapping_storage(value))


def _mapping_proxy(value):
    # The wrapped mapping, by reference; it is traversed by its own real type.
    return tuple(gc.get_referents(value))


# Native fields of trusted exception bases (args, cause, context, filename,
# SyntaxError text/location, Unicode object/reason, ...), never subclass
# attributes. The instance dictionary carries notes and library attributes
# such as an HTTP client error's stored request or response.
_EXCEPTION_FIELDS = tuple(
    (
        base,
        tuple(
            descriptor
            for name, descriptor in vars(base).items()
            if type(descriptor) in (GetSetDescriptorType, MemberDescriptorType)
            and name not in {"__dict__", "__traceback__"}
        ),
    )
    for base in (
        BaseException,
        BaseExceptionGroup,
        OSError,
        BlockingIOError,
        SyntaxError,
        ImportError,
        AttributeError,
        NameError,
        StopIteration,
        SystemExit,
        UnicodeDecodeError,
        UnicodeEncodeError,
        UnicodeTranslateError,
    )
)


def _exception_parts(error):
    parts = [instance_storage(error, BaseException)]
    for base, descriptors in _EXCEPTION_FIELDS:
        if derives(type(error), base):
            parts.extend(_stored_fields(descriptors, error))
    return tuple(parts)


# Readers keyed by id() of the trusted base: integer lookups, so a hostile
# metaclass __hash__/__eq__ cannot run. Bases are module-level classes kept
# alive here, so their ids are stable.
_READERS = {}
_SUPPORTED = []


def _register(base, reader):
    _READERS[id(base)] = (base, reader)
    _SUPPORTED.append(f"{base.__module__}.{base.__qualname__}")


for _base, _method in (
    (list, list.__iter__),
    (tuple, tuple.__iter__),
    (set, set.__iter__),
    (frozenset, frozenset.__iter__),
    (deque, deque.__iter__),
):
    _register(_base, _base_iteration(_method))
_register(dict, _mapping_storage)
_register(MappingProxyType, _mapping_proxy)
_register(BaseException, _exception_parts)
for _base in (
    SimpleNamespace,
    UserDict,
    UserList,
    UserString,
    ChainMap,
    HttpRequest,
    HttpResponseBase,
    BaseContext,
    CaseInsensitiveMapping,
    ResolverMatch,
    OrderedSet,
    # Stored arguments of a lazy value (reverse_lazy) and an already
    # evaluated lazy object's wrapped value. Never evaluated here.
    Promise,
    LazyObject,
):
    _register(_base, _storage_reader(_base))

# Library and optional stdlib carriers. A carrier instance can only exist once
# its defining module was imported, so resolve these from sys.modules on use
# instead of importing (and paying for) every optional client library here.
_LAZY = {
    "argparse": ("Namespace",),
    "logging": ("LogRecord",),
    "traceback": ("TracebackException", "FrameSummary"),
    "email.message": ("Message",),
    "email.header": ("Header",),
    "django.core.files.base": ("File",),
    "http.client": ("HTTPResponse",),
    "http.cookies": ("Morsel",),
    "http.cookiejar": ("Cookie", "CookieJar"),
    "pathlib": ("PurePath",),
    "urllib.request": ("Request",),
    "wsgiref.headers": ("Headers",),
    "rest_framework.request": ("Request",),
    "httpx": (
        "URL",
        "Request",
        "Response",
        "Headers",
        "QueryParams",
        "Cookies",
        "Proxy",
    ),
    "requests.models": ("Request", "PreparedRequest", "Response"),
    "requests.structures": ("CaseInsensitiveDict",),
    "urllib3.response": ("BaseHTTPResponse",),
    "urllib3._collections": ("HTTPHeaderDict",),
    "urllib3.util.retry": ("Retry",),
    "starlette.datastructures": (
        "URL",
        "Headers",
        "ImmutableMultiDict",
        "State",
        "UploadFile",
    ),
    "starlette.requests": ("HTTPConnection",),
    "starlette.responses": ("Response",),
}
# Dict subclasses whose own instance storage also holds rendered data.
_WITH_MAPPING = {("http.cookies", "Morsel")}
_pending = dict(_LAZY)


def _resolve_pending():
    for module_name in tuple(_pending):
        module = sys.modules.get(module_name)
        names = None if module is None else _pending.pop(module_name, None)
        if names is None:
            continue
        missing = []
        for name in names:
            base = vars(module).get(name)
            if not isinstance(base, type):
                # Module still initializing (or a version without it).
                missing.append(name)
            elif (module_name, name) == ("pathlib", "PurePath"):
                _register(base, _path_reader(base))
            elif (module_name, name) in _WITH_MAPPING:
                _register(base, _storage_and_mapping(base))
            else:
                _register(base, _storage_reader(base))
        if missing:
            _pending[module_name] = tuple(missing)


# Standard-library metadata objects whose representation renders stored
# arguments: a partial's arguments, signature defaults and bound arguments,
# email addresses, enum member values, a logger's name or an adapter's extra
# mapping. They are not URL containers, so supported_carriers() (the URL
# carrier registry and its rendering suite) does not list them; the output
# boundary suites cover them. Read through base-owned storage like the rest.
_METADATA = {
    "functools": ("partial", "partialmethod"),
    "inspect": ("BoundArguments", "Parameter", "Signature"),
    "email.headerregistry": ("Address", "Group"),
    "enum": ("Enum",),
    "logging": ("Logger", "LoggerAdapter"),
}
_METADATA_READERS = {}
_metadata_pending = dict(_METADATA)


def _resolve_metadata():
    for module_name in tuple(_metadata_pending):
        module = sys.modules.get(module_name)
        names = None if module is None else _metadata_pending.pop(module_name, None)
        if names is None:
            continue
        missing = []
        for name in names:
            base = vars(module).get(name)
            if isinstance(base, type):
                _METADATA_READERS[id(base)] = (base, _storage_reader(base))
            else:
                missing.append(name)
        if missing:
            _metadata_pending[module_name] = tuple(missing)


_TEXT_LEAVES = tuple(
    f"builtins.{kind.__qualname__}" for kind in (str, bytes, bytearray, memoryview)
)


def supported_carriers():
    """Qualified names of every carrier base, resolving imported libraries."""
    _resolve_pending()
    return _TEXT_LEAVES + tuple(_SUPPORTED)


def stored_text(value):
    """An exact str for text-like leaves, read without the value's methods."""
    kind = type(value)
    if kind is str:
        return value
    if derives(kind, str):
        return str.__str__(value)
    if derives(kind, bytes):
        return bytes.decode(value, "utf-8", "replace")
    if derives(kind, bytearray):
        return bytearray.decode(value, "utf-8", "replace")
    if kind is memoryview:
        try:
            return memoryview.tobytes(value).decode("utf-8", "replace")
        except ValueError:  # released buffer
            return None
    return None


def checkout_carrier_parts(value):
    """Return a supported carrier's stored parts, or None for other objects."""
    if _pending:
        _resolve_pending()
    if _metadata_pending:
        _resolve_metadata()
    for entry in _mro(type(value)):
        found = _READERS.get(id(entry))
        if found is None:
            found = _METADATA_READERS.get(id(entry))
        if found is not None and found[0] is entry:
            return found[1](value)
    return None
