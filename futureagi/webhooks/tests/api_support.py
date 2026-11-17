"""Shared pieces of the WH-2 API tests: people at each level with their own API keys, a local receiver, and a real
dispatcher loop on a thread (the worker).

**People** authenticate the way the SDK and CLI do: an ``OrgApiKey`` of type ``user`` in ``X-Api-Key`` and
``X-Secret-Key``. Requests go through FA's real authentication class, which resolves the organization and the
workspace, so the RBAC rows test the production path, not ``force_authenticate``.

**The worker** is the production ``Dispatcher`` on the Postgres ``OrmStore``, with the real signing, URL and address
rules and the real pinned transport. Two test seams, both outside the rules under test: the resolver answers a public
address (``93.184.215.14``) for the receiver's name, and the transport connects to 127.0.0.1 where the receiver
listens (``LoopbackTransport``), so no request leaves the machine.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import hmac
import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from django.db import close_old_connections, connection, transaction
from rest_framework.test import APIClient

from tfc.constants.levels import Level
from webhooks import config
from webhooks.core.attempt import Sender
from webhooks.core.dispatch import Dispatcher
from webhooks.core.signing import verify
from webhooks.core.tests.fakes import FakeResolver
from webhooks.models import Delivery
from webhooks.publish import publish
from webhooks.store import OrmStore
from webhooks.tests.factories import T0, label_event_data
from webhooks.transport import PinnedTransport, SystemClock

PUBLIC = "93.184.215.14"
EP = "/webhooks/endpoints/"


def person(org, ws, org_level: int, ws_level: int | None = None, label: str = "p"):
    """A user with an active org membership at ``org_level`` and, when given, a workspace membership."""
    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.user import User
    from accounts.models.workspace import WorkspaceMembership

    u = User.objects.create_user(
        email=f"{label}-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="test-password-123",
        name=label.title(),
        organization=org,
        organization_role=Level.LEVEL_TO_ORG_STRING[org_level],
    )
    om, _ = OrganizationMembership.no_workspace_objects.update_or_create(
        user=u,
        organization=org,
        defaults={
            "role": Level.LEVEL_TO_ORG_STRING[org_level],
            "level": org_level,
            "is_active": True,
        },
    )
    if ws_level is not None:
        WorkspaceMembership.no_workspace_objects.update_or_create(
            user=u,
            workspace=ws,
            defaults={
                "role": Level.LEVEL_TO_WS_ROLE[ws_level],
                "level": ws_level,
                "is_active": True,
                "organization_membership": om,
            },
        )
    return u


def workspace_in(org, name: str, creator):
    from accounts.models.workspace import Workspace

    return Workspace.no_workspace_objects.create(
        name=name,
        organization=org,
        is_default=False,
        is_active=True,
        created_by=creator,
    )


class KeyClient(APIClient):
    """Each request starts and ends with no thread-local workspace context, as FA's middleware does in production,
    so FA's authentication resolves the organization and workspace from the key and the headers alone."""

    def generic(self, *args, **kwargs):
        from tfc.middleware.workspace_context import clear_workspace_context

        clear_workspace_context()
        try:
            return super().generic(*args, **kwargs)
        finally:
            clear_workspace_context()


def client_for(user, org, ws=None, *, pinned: bool = False) -> APIClient:
    """An API client with the user's own key; ``pinned`` binds the key to ``ws`` (a workspace-pinned key). The
    thread-local context is cleared first: FA's auto-assignment signal would otherwise pin the key to it."""
    from accounts.tests.personal_keys import personal_key_for
    from tfc.middleware.workspace_context import clear_workspace_context

    clear_workspace_context()
    key = personal_key_for(
        user,
        org,
        name=f"wh2-{uuid.uuid4().hex[:6]}",
        workspace=ws if pinned else None,
    )
    c = KeyClient()
    headers = {"HTTP_X_API_KEY": key.api_key, "HTTP_X_SECRET_KEY": key.secret_key}
    if ws is not None:
        headers["HTTP_X_WORKSPACE_ID"] = str(ws.id)
    c.credentials(**headers)
    return c


def verify_ten_lines(secret: str, headers, body: bytes, tolerance_s: int = 300) -> bool:
    """DESIGN.md section 5.4 recipe 3, as published (stdlib only)."""
    key = base64.b64decode(secret.removeprefix("whsec_"))
    msg_id, ts = headers["webhook-id"], headers["webhook-timestamp"]
    if abs(time.time() - int(ts)) > tolerance_s:
        return False
    mac = hmac.new(key, f"{msg_id}.{ts}.".encode() + body, hashlib.sha256).digest()
    expected = base64.b64encode(mac).decode()
    return any(
        s.startswith("v1,") and hmac.compare_digest(s[3:], expected)
        for s in headers["webhook-signature"].split(" ")
    )


class Receiver:
    """A plain HTTP receiver on 127.0.0.1. ``answers`` is a list of (status, body[, delay_s]) used in order (the last
    repeats);
    every request is recorded with lower-cased headers and the raw body."""

    def __init__(self, answers=((200, b'{"ok":true}'),)):
        self.answers = list(answers)
        self.seen: list[dict] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def do_POST(self):  # noqa: N802
                n = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(n)
                outer.seen.append(
                    {
                        "path": self.path,
                        "headers": {k.lower(): v for k, v in self.headers.items()},
                        "body": body,
                        "at": time.time(),
                    }
                )
                i = min(len(outer.seen) - 1, len(outer.answers) - 1)
                status, payload, *delay = outer.answers[i]
                if delay:
                    time.sleep(delay[0])  # a slow receiver: (status, body, seconds)
                self.send_response(status)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(payload)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://hooks.acme.ai:{self.port}/hook"

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def json_bodies(self) -> list[dict]:
        return [json.loads(r["body"]) for r in self.seen]

    def verified_by_both(self, secret: str) -> list[bool]:
        now = int(time.time())
        return [
            verify(secret, r["headers"], r["body"], now) == "ok"
            and verify_ten_lines(secret, r["headers"], r["body"])
            for r in self.seen
        ]


class LoopbackTransport(PinnedTransport):
    """The real pinned transport, connected to the receiver on 127.0.0.1 instead of the vetted public address."""

    def post(self, request):
        return super().post(dataclasses.replace(request, addresses=("127.0.0.1",)))


class Worker:
    """The production dispatcher, ticking on a thread until stopped (``with Worker(): ...``)."""

    def __init__(self, tick_s: float = 0.1):
        sender = Sender(
            config.profile(),
            config.public_suffix,
            FakeResolver([PUBLIC]),
            LoopbackTransport(),
            SystemClock(),
        )
        self.dispatcher = Dispatcher(
            OrmStore(), sender, SystemClock(), after_send=close_old_connections
        )
        self.tick_s = tick_s
        self.stop = threading.Event()
        self.errors: list[BaseException] = []
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        try:
            while not self.stop.is_set():
                try:
                    self.dispatcher.tick()
                except BaseException as exc:  # noqa: BLE001 - surfaced by the test
                    self.errors.append(exc)
                self.stop.wait(self.tick_s)
            self.dispatcher.drain(10)
        finally:
            connection.close()

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.thread.join(30)
        return False


def wait_for(predicate, timeout_s: float = 15.0, step_s: float = 0.1):
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(step_s)
    return predicate()


def make(owner, url, **kw):
    b = {
        "name": f"hook-{time.time_ns()}",
        "url": url,
        "subscriptions": [{"event_type": "prompt.*"}],
    }
    b.update(kw)
    r = owner.post(EP, b, format="json")
    assert r.status_code == 201, r.content
    res = r.json()["result"]
    return res["endpoint"]["id"], res["secret"]


def pub(org, ws, key, **kw):
    with transaction.atomic():
        return publish(
            type="prompt.label.changed",
            organization_id=org.id,
            workspace_id=ws.id,
            data=label_event_data(**kw),
            source_key=key,
            source_hash="h",
            producer="test",
            occurred_at=T0,
            subject_kind="prompt",
            subject_id=kw.get("prompt_id", "p1"),
        ).event_id


def statuses(ep_id):
    return list(
        Delivery.objects.filter(endpoint_id=ep_id).values_list("status", flat=True)
    )
