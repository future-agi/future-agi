"""Delegated gateway transport: sign the exact bytes sent, once per attempt."""

import json
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field

from accounts.services.envelope_keys import current_kid
from accounts.services.service_grants import issue_person_grant
from tfc.service_envelope import sign_request

CHAT_TIMEOUT = 30
STREAM_TIMEOUT = 300
CHAT_ROUTE = "/-/delegated/v1/chat/completions"


@dataclass(frozen=True)
class Delegation:
    token: str = field(repr=False)
    claims: dict
    deadline: float | None = field(default=None, repr=False)


def for_chat(request, *, kind, streaming=False):
    timeout = STREAM_TIMEOUT if streaming else CHAT_TIMEOUT
    deadline = time.monotonic() + timeout
    token, claims, _ = issue_person_grant(
        kind=kind,
        org=request.organization.pk,
        user=request.user.pk,
        workspace=getattr(getattr(request, "workspace", None), "pk", None),
        sub="chat:" + str(uuid.uuid4()),
        ttl=timeout + 60,
    )
    return Delegation(token, claims, deadline=deadline)


def _headers(client, delegation, path, raw, *, method="POST"):
    c = delegation.claims
    kid = current_kid()
    token = sign_request(
        keys={("fa", "gw-delegated", kid): client.admin_token.encode()},
        kid=kid,
        issuer="fa",
        audience="gw-delegated",
        method=method,
        route=path,
        body=raw,
        org=c["org"],
        workspace=c["workspace"],
        user=c["user"],
        via="grant:" + c["id"],
        scope=c["kind"],
        grant=delegation.token,
    )
    return {
        **client._headers(),
        "X-FA-Envelope": token,
        "X-FA-Grant": delegation.token,
        "Cache-Control": "no-store",
    }


@contextmanager
def delegated_response(client, delegation, path, payload, *, streaming=False):
    from agentcc.services.delegated_http import bounded_response

    timeout = STREAM_TIMEOUT if streaming else CHAT_TIMEOUT
    deadline = delegation.deadline
    if deadline is None:
        deadline = time.monotonic() + timeout
    raw = json.dumps(
        payload, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    with bounded_response(
        client.base_url + path,
        raw,
        lambda: _headers(client, delegation, path, raw),
        deadline,
    ) as response:
        if not streaming:
            response.read()
        yield response


def chat(client, delegation, *, prompt, model, system_prompt=None, params=None):
    body = client.chat_body(prompt, model, system_prompt, params)
    with delegated_response(client, delegation, CHAT_ROUTE, body) as response:
        return (
            response.status_code,
            response.json() if response.content else {},
            dict(response.headers),
        )


@contextmanager
def stream_chat(client, delegation, *, prompt, model, system_prompt=None, params=None):
    body = client.chat_body(prompt, model, system_prompt, params, stream=True)
    with delegated_response(
        client, delegation, CHAT_ROUTE, body, streaming=True
    ) as response:
        yield response


def submit_batch(client, delegation, requests, max_concurrency):
    from agentcc.services.gateway_client import GatewayClientError

    body = {"requests": requests, "max_concurrency": max_concurrency}
    with delegated_response(client, delegation, "/-/batches", body) as response:
        if response.status_code >= 400:
            raise GatewayClientError(
                "Delegated batch submission refused", status_code=response.status_code
            )
        return response.json()
