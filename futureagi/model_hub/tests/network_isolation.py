"""Keep tests on loopback and the configured local test services.

Importing this module installs pre-native socket adapters and an audit hook.
While a ``watch()`` scope is open (the ``no_external_lookups`` fixture opens
one per test), the guard refuses and counts resolutions and transport aimed
at other hosts before CPython can resolve their hostnames. The fixture
fails the test at teardown when anything was recorded, even when the code
under test swallowed the refusal.

Allowed hosts are loopback and explicitly named local fixture services.
Configuration never grants arbitrary DNS access. Service resolution runs
inside the watch; every answer must be a fixture address before the name
or any of its addresses is allowed. Metadata destinations are never allowed.

``stub_eval_recommender`` replaces the model client that the enterprise eval
recommender builds, so a dataset creation test never reaches a model provider.
"""

from __future__ import annotations

import importlib
import json
import socket
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from model_hub.tests.network_local_identity import own_host_snapshot, write_event
from model_hub.tests.network_service_hosts import (
    SERVICE_HOST_SETTINGS as SERVICE_HOST_SETTINGS,
)
from model_hub.tests.network_service_hosts import (
    configured_service_hosts,
    is_fixture_address,
    is_loopback_host,
)
from model_hub.tests.network_service_hosts import host_ip as _ip
from model_hub.tests.network_service_hosts import host_text as _host_text
from model_hub.tests.network_socket_layer import install_socket_layer

LOOKUP_EVENTS = frozenset(
    {
        "socket.getaddrinfo",
        "socket.gethostbyname",
        "socket.gethostbyname_ex",
        "socket.gethostbyaddr",
        "socket.getnameinfo",
    }
)
TRANSPORT_EVENTS = frozenset({"socket.connect", "socket.sendto", "socket.sendmsg"})


class ExternalNetworkRefused(PermissionError):
    """Raised in place of a network operation that would leave the test host."""


@dataclass(frozen=True)
class Attempt:
    event: str
    host: str
    port: object = None

    def __str__(self) -> str:
        suffix = "" if self.port in (None, 0, "") else f":{self.port}"
        return f"{self.event} {self.host}{suffix}"


def _event_target(event: str, args: tuple) -> tuple[object, object] | None:
    if event in TRANSPORT_EVENTS:
        address = args[1] if len(args) > 1 else None
        if not isinstance(address, tuple) or not address:
            return None
        return address[0], address[1] if len(address) > 1 else None
    if event == "socket.getnameinfo":
        sockaddr = args[0] if args else None
        if not isinstance(sockaddr, tuple) or not sockaddr:
            return None
        return sockaddr[0], sockaddr[1] if len(sockaddr) > 1 else None
    if event in LOOKUP_EVENTS:
        if not args:
            return None
        port = args[1] if event == "socket.getaddrinfo" and len(args) > 1 else None
        return args[0], port
    return None


class NetworkGuard:
    """Process-wide socket guard; refuses and records only inside ``watch()``."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._scopes: list[list[Attempt]] = []
        self._service_names: frozenset[str] = frozenset()
        self._service_addresses: frozenset[str] = frozenset()
        self._service_answers: dict[str, frozenset[str]] = {}
        self._own_names, self._own_addresses = own_host_snapshot()
        self._resolving = threading.local()
        self._installed = False

    def install(self) -> None:
        with self._lock:
            if not self._installed:
                install_socket_layer(self)
                sys.addaudithook(self._hook)
                self._installed = True

    @property
    def active(self) -> bool:
        return bool(self._scopes)

    def _refresh_services(self) -> None:
        # Recheck each outer scope: never reuse a previous scope's DNS answers.
        self._service_names = self._service_addresses = frozenset()
        self._service_answers = {}
        names, addresses = set(), set()
        for name in configured_service_hosts():
            if _ip(name) is not None:
                addresses.add(name)
                continue
            self._resolving.host = name
            try:
                infos = socket.getaddrinfo(name, None)
            except OSError:
                continue
            finally:
                self._resolving.host = None
            resolved = {_host_text(info[4][0]) for info in infos}
            unsafe = {host for host in resolved if not is_fixture_address(host)}
            if unsafe:
                for host in sorted(unsafe):
                    self._record(Attempt("socket.getaddrinfo", host))
                continue
            if resolved:
                names.add(name)
                addresses.update(resolved)
                self._service_answers[name] = frozenset(resolved)
        self._service_names = frozenset(names)
        self._service_addresses = frozenset(addresses)

    def is_own_host(self, host: object) -> bool:
        # No suffix/prefix matching or normalization: only the startup snapshot.
        return isinstance(host, str) and (
            host in self._own_names or host in self._own_addresses
        )

    def resolving_service(self, host: object) -> bool:
        return _host_text(host) == getattr(self._resolving, "host", None)

    def numeric_hosts(self, host: object, family: int) -> tuple[str, ...]:
        text = _host_text(host)
        if _ip(text) is not None:
            values = (text,)
        elif self.is_own_host(host):
            values = tuple(sorted(self._own_addresses))
        elif is_loopback_host(host):
            values = ("127.0.0.1", "::1")
        else:
            values = tuple(sorted(self._service_answers.get(text, ())))
        version = {socket.AF_INET: 4, socket.AF_INET6: 6}.get(family)
        return tuple(v for v in values if version is None or _ip(v).version == version)

    def local_name(self, host: object) -> str:
        if isinstance(host, str) and host in self._own_names:
            return host
        if self.is_own_host(host):
            return sorted(self._own_names)[0]
        if is_loopback_host(host):
            return "localhost"
        text = _host_text(host)
        for name, addresses in self._service_answers.items():
            if text == name or text in addresses:
                return name
        return text

    def allowed(self, host: object) -> bool:
        if is_loopback_host(host) or self.is_own_host(host):
            return True
        text = _host_text(host)
        if _ip(text) is not None:
            return is_fixture_address(text) and text in self._service_addresses
        return text in self._service_names

    def _record(self, attempt: Attempt) -> None:
        with self._lock:
            for scope in self._scopes:
                scope.append(attempt)
        write_event("refused", attempt.event, attempt.host, attempt.port)
        # Observers must see pre-native refusals too, not just CPython's events.
        sys.audit("test.network.refused", attempt.event, attempt.host, attempt.port)

    def check_target(self, event, host, port, *, audit=False):
        if event == "socket.getaddrinfo" and self.resolving_service(host):
            return
        if self.is_own_host(host):
            write_event("own-host", event, host, port)
            return
        text = _host_text(host)
        if self.allowed(host) and (
            not audit
            or event in LOOKUP_EVENTS
            or _ip(text) is not None
            or is_loopback_host(host)
        ):
            return
        attempt = Attempt(event, text, port)
        self._record(attempt)
        raise ExternalNetworkRefused(
            f"Refused before transport by the test network guard: {attempt}"
        )

    def _hook(self, event: str, args: tuple) -> None:
        if not self._scopes or (
            event not in LOOKUP_EVENTS and event not in TRANSPORT_EVENTS
        ):
            return
        target = _event_target(event, args)
        if target is not None:
            self.check_target(event, *target, audit=True)

    @contextmanager
    def watch(self) -> Iterator[list[Attempt]]:
        self.install()
        attempts: list[Attempt] = []
        with self._lock:
            outermost = not self._scopes
            self._scopes.append(attempts)
        try:
            if outermost:
                self._refresh_services()
            yield attempts
        finally:
            with self._lock:
                self._scopes[:] = [
                    scope for scope in self._scopes if scope is not attempts
                ]


GUARD = NetworkGuard()
GUARD.install()


def fail_on_attempts(attempts: list[Attempt]) -> None:
    if attempts:
        listed = ", ".join(str(attempt) for attempt in attempts)
        pytest.fail(
            f"{len(attempts)} network attempt(s) outside the test host, "
            f"refused before transport: {listed}",
            pytrace=False,
        )


@pytest.fixture
def no_external_lookups():
    """Refuse and count every non-local network attempt; fail if any."""
    with GUARD.watch() as attempts:
        yield attempts
    fail_on_attempts(attempts)


RECOMMENDER_MODULE = "ee.agenthub.eval_recommendation.eval_recommendation"
DEFAULT_RECOMMENDATIONS = ("Context Adherence", "Toxicity")
ALWAYS_RECOMMENDED = "Deterministic Evals"


@dataclass
class RecommenderStub:
    """What the eval recommender's model client received."""

    installed: bool
    recommended: tuple[str, ...]
    clients: list[dict] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)

    @property
    def stored_recommendations(self) -> list[str]:
        if not self.installed:
            return [ALWAYS_RECOMMENDED]
        return [*self.recommended, ALWAYS_RECOMMENDED]

    def assert_reached(self, *, columns, times: int = 1) -> None:
        """The recommender built its client and asked it once per dataset.

        Without the enterprise package the recommender is a stub that raises
        before any model client exists, so nothing can be reached.
        """
        if not self.installed:
            assert self.clients == [] and self.prompts == []
            return
        assert len(self.clients) == times, self.clients
        assert len(self.prompts) == times, len(self.prompts)
        for prompt in self.prompts:
            assert str(list(columns)) in prompt


def stub_eval_recommender(
    monkeypatch, recommended: tuple[str, ...] = DEFAULT_RECOMMENDATIONS
) -> RecommenderStub:
    """Answer the eval recommender from a local stub instead of a model.

    ``EvalRecommender.recommend_evals`` calls
    ``self.llm._get_completion_content``; ``self.llm`` is built from the
    ``LLM`` name in the recommender module. Replacing that name means neither
    the client set-up nor the provider retry and fallback chain runs.
    """
    try:
        module = importlib.import_module(RECOMMENDER_MODULE)
    except ImportError:
        return RecommenderStub(installed=False, recommended=tuple(recommended))
    stub = RecommenderStub(installed=True, recommended=tuple(recommended))
    answer = json.dumps({"recommended_evals": list(recommended)})

    class RecommenderModelClient:
        def __init__(self, *args, **kwargs):
            stub.clients.append(dict(kwargs, args=args))

        def _get_completion_content(self, messages, **kwargs):
            stub.prompts.append(str(messages[0]["content"]))
            return answer

    monkeypatch.setattr(module, "LLM", RecommenderModelClient)
    return stub


def stub_url_resolution(monkeypatch, answers: dict[str, list[str]], module: str):
    """Resolve the named hosts locally for ``module``'s ``socket.getaddrinfo``.

    An unknown host fails as the system resolver would for a missing name.
    Returns the list of hosts the module asked for.
    """
    asked: list[str] = []

    def getaddrinfo(host, port, *args, **kwargs):
        asked.append(host)
        if host not in answers:
            raise socket.gaierror(socket.EAI_NONAME, "unknown host in the test")
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                (ip, port or 0),
            )
            for ip in answers[host]
        ]

    resolver = SimpleNamespace(
        **{
            name: getattr(socket, name)
            for name in dir(socket)
            if not name.startswith("__")
        }
    )
    resolver.getaddrinfo = getaddrinfo
    monkeypatch.setattr(f"{module}.socket", resolver)
    return asked
