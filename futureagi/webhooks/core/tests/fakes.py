"""Fakes for the core send-path tests (and the Django store tests): a clock, a resolver and a transport that
never touch the network, plus small builders. Standard library only."""

from __future__ import annotations

from webhooks.core.attempt import Sender
from webhooks.core.ipclass import Profile
from webhooks.core.memstore import MemEndpoint, MemStore
from webhooks.core.ports import Claim, EndpointConfig

SECRET_A = "whsec_AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
SECRET_B = "whsec_ICEiIyQlJicoKSorLC0uLzAxMjM0NTY3ODk6Ozw9Pj8="
ORG = "11111111-1111-4111-8111-111111111111"
WS = "22222222-2222-4222-8222-222222222222"
T0 = 1_792_663_200_000


def psl(host: str):
    return "ai" if host.endswith(".ai") else ("com" if host.endswith(".com") else None)


class FakeClock:
    def __init__(self, ms: int = T0):
        self.ms = ms
        self.mono = 1000.0

    def now_ms(self) -> int:
        return self.ms

    def monotonic(self) -> float:
        return self.mono

    def advance(self, ms: int) -> None:
        self.ms += ms
        self.mono += ms / 1000


class FakeResolver:
    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def resolve(self, host, timeout_s):
        self.calls.append((host, timeout_s))
        if isinstance(self.answers, Exception):
            raise self.answers
        return list(self.answers)


class FakeTransport:
    def __init__(self, *results):
        self.results = list(results)
        self.requests = []

    def post(self, request):
        self.requests.append(request)
        r = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        return r(request) if callable(r) else r


def endpoint(**kw) -> EndpointConfig:
    base = {
        "id": "whep_A",
        "organization_id": ORG,
        "url": "https://hooks.acme.ai/omega?team=7",
        "secrets_newest_first": (SECRET_A,),
    }
    base.update(kw)
    return EndpointConfig(**base)


def claim(ep=None, **kw) -> Claim:
    base = {
        "delivery_id": "dlv_01JBF3QAAAAAAAAAAAAAAAAAAA",
        "lease_token": "tok-1",
        "lease_until_ms": T0 + 40_000,
        "event_id": "evt_01JBF3Q7Z8X4N6R2T5V9W0YB1C",
        "event_type": "prompt.label.changed",
        "seq": 7,
        "is_test": False,
        "attempt_n": 1,
        "trigger": "auto",
        "created_at_ms": T0,
        "body": b'{"id":"x"}',
        "endpoint": ep or endpoint(),
    }
    base.update(kw)
    return Claim(**base)


def sender(resolver, transport, clock=None, profile=None) -> Sender:
    return Sender(
        profile or Profile(mode="hosted"),
        psl,
        resolver,
        transport,
        clock or FakeClock(),
    )


def make_store(clock, status="enabled", **ep_kw) -> MemStore:
    st = MemStore()
    st.endpoints["whep_A"] = MemEndpoint(
        config=endpoint(status=status, **ep_kw),
        workspace_id=WS,
        subscriptions=[{"event_type": "prompt.*"}],
    )
    st.endpoints["whep_B"] = MemEndpoint(
        config=endpoint(id="whep_B", status=status),
        workspace_id=WS,
        subscriptions=[{"event_type": "deployment.run.*"}],
    )
    return st


def publish(st: MemStore, n: int, clock: FakeClock) -> str:
    eid = f"evt_01JBF3Q7Z8X4N6R2T5V9W0YB{n:02d}"
    st.publish(
        organization_id=ORG,
        source_key=f"k{n}",
        source_hash="h",
        event={
            "id": eid,
            "type": "prompt.label.changed",
            "workspace_id": WS,
            "timestamp": "2026-10-22T09:59:58.120Z",
            "data": {
                "action": "moved",
                "label": {"name": "Production"},
                "prompt": {"id": "p1"},
            },
        },
    )
    return eid
