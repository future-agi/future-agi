"""One attempt under one wall-clock deadline: URL rules again, resolve, vet every answer, sign at send time, POST to a
vetted address, classify the answer (DESIGN.md sections 5.2, 6.1, 6.2, 7.2, 8.1 and 13.1).

Everything the attempt touches outside this module is injected: the resolver, the transport, the clock, the PSL and
the scrubber. Nothing here reads settings.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from . import schedule, signing
from .addresses import (
    CODE_HOST_NOT_ALLOWED,
    CODE_UNSAFE,
    PublicSuffix,
    redirect_log_host,
    url_decision,
)
from .ipclass import PUBLIC, Profile, address_decision, classify
from .payloads import scrub as default_scrub
from .ports import (
    AbortHandle,
    Claim,
    Clock,
    PinnedPost,
    ResolveError,
    Resolver,
    Transport,
)

DNS_BUDGET_S = 2.0
EXCERPT_STORE_BYTES = 2048
RESPONSE_READ_BYTES = 4096
KEPT_RESPONSE_HEADERS = (
    "content-type",
    "content-length",
    "retry-after",
    "date",
    "server",
)
# C0 controls except tab and newline, DEL, C1 controls, the bidi marks (ALM, LRM, RLM), embeddings, overrides and
# isolates (S9).
_INERT = re.compile(
    "[\x00-\x08\x0b-\x1f\x7f-\x9f\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]"
)


def inert(text: str) -> str:
    """Receiver-controlled text with control characters and bidi marks and overrides removed (S9)."""
    return _INERT.sub("", text)


def cut_utf8(text: str, max_bytes: int) -> str:
    """``text`` cut to at most ``max_bytes`` of UTF-8, never inside a character."""
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    return raw[:max_bytes].decode("utf-8", "ignore")


def excerpt(raw: bytes, scrub: Callable[[str], str] = default_scrub) -> str:
    """At most 2 KB (UTF-8 bytes) of what was read (at most 4 KB), scrubbed and inert."""
    return cut_utf8(
        inert(scrub(raw[:RESPONSE_READ_BYTES].decode("utf-8", "replace"))),
        EXCERPT_STORE_BYTES,
    )


def kept_headers(headers: Mapping[str, str]) -> dict:
    low = {k.lower(): v for k, v in headers.items()}
    return {k: inert(str(low[k]))[:256] for k in KEPT_RESPONSE_HEADERS if k in low}


@dataclass
class AttemptRecord:
    """What one attempt writes to ``webhooks_attempt`` and what the schedule reads."""

    n: int
    trigger: str
    started_at_ms: int
    settled_at_ms: int = 0
    t: int | None = None
    signature_key_tails: list[str] = field(default_factory=list)
    signature_header: str | None = field(default=None, repr=False)
    request_headers: dict = field(default_factory=dict)
    """What was sent, redacted for the log: signatures and custom header values masked (section 8.1)."""
    dns_ms: int | None = None
    ttfb_ms: int | None = None
    duration_ms: int | None = None
    resolved_ip: str | None = None
    ip_family: int | None = None
    response_status: int | None = None
    response_headers: dict = field(default_factory=dict)
    response_body_excerpt: str | None = None
    retry_after: str | None = None
    outcome: str = ""
    error_detail: str | None = None
    staff_detail: str | None = None
    policy_blocked: bool = False
    """The stored URL is outside the org or workspace ``allowed_hosts`` now (section 6.3). Nothing was sent; for a
    real delivery the endpoint becomes ``blocked_by_policy`` and the delivery ``paused``, with no attempt row."""

    def answer(self) -> dict:
        """The schedule's view: ``{status, retry_after}`` or ``{error}``, plus ``settled_at_ms``."""
        if self.response_status is not None:
            a = {"status": self.response_status, "settled_at_ms": self.settled_at_ms}
            if self.retry_after is not None:
                a["retry_after"] = self.retry_after
            return a
        return {"error": self.outcome, "settled_at_ms": self.settled_at_ms}


@dataclass(frozen=True)
class Sender:
    """The attempt machinery for one install: its profile and its injected ports."""

    profile: Profile
    public_suffix: PublicSuffix
    resolver: Resolver
    transport: Transport
    clock: Clock
    scrub: Callable[[str], str] = default_scrub
    address_guard: Callable[[str], bool] | None = None
    """An install's extra check on every address about to be connected to, after the normative tables (hosted:
    ``ip.is_global`` and ``ssrf_guard.classify_ip``, the belt-and-braces of section 6.2). False blocks the attempt."""

    def attempt(self, claim: Claim, abort: AbortHandle | None = None) -> AttemptRecord:
        start_mono = self.clock.monotonic()
        deadline = start_mono + claim.endpoint.timeout_ms / 1000.0
        rec = AttemptRecord(
            n=claim.attempt_n, trigger=claim.trigger, started_at_ms=self.clock.now_ms()
        )
        try:
            self._run(claim, rec, deadline, abort)
        finally:
            rec.settled_at_ms = self.clock.now_ms()
            rec.duration_ms = max(0, int((self.clock.monotonic() - start_mono) * 1000))
        return rec

    def _run(
        self,
        claim: Claim,
        rec: AttemptRecord,
        deadline: float,
        abort: AbortHandle | None,
    ) -> None:
        ep = claim.endpoint
        url = url_decision(ep.url, self.profile, self.public_suffix, ep.allowed_hosts)
        if not url.allow:
            # A stored URL that no longer passes (policy, deny list, a new rule) never connects.
            rec.outcome = "ssrf_blocked"
            rec.error_detail = "The URL is not allowed: " + (url.rule or "")
            if url.code != CODE_UNSAFE:
                rec.error_detail = "The URL is not allowed (" + (url.code or "") + ")."
            # A host outside the allowlist is a policy matter, not an unsafe address: it never counts toward the
            # unsafe_address rule (sections 6.3 and 7.7).
            rec.policy_blocked = url.code == CODE_HOST_NOT_ALLOWED
            return
        if url.ip_literal is not None:
            answers = [url.ip_literal]
            rec.dns_ms = 0
        else:
            t0 = self.clock.monotonic()
            budget = min(DNS_BUDGET_S, deadline - t0)
            if budget <= 0:
                rec.outcome = "timeout"
                return
            try:
                answers = list(self.resolver.resolve(url.host, budget))
            except ResolveError as exc:
                rec.dns_ms = int((self.clock.monotonic() - t0) * 1000)
                rec.outcome = "dns_error"
                rec.error_detail = f"{url.host} did not resolve."
                rec.staff_detail = str(exc)[:200]
                return
            rec.dns_ms = int((self.clock.monotonic() - t0) * 1000)
        decision = address_decision(answers, url.port, self.profile)
        if not decision.allow:
            rec.outcome = decision.outcome or "ssrf_blocked"
            if rec.outcome == "dns_error":
                rec.error_detail = f"{url.host} did not resolve."
            else:
                # A tenant never sees a non-public address (section 6.2): the address goes to staff_detail only.
                rec.error_detail = (
                    f"{url.host} resolves to a private or internal address."
                )
                rec.staff_detail = ",".join(
                    a["address"] + "=" + a["rule"] for a in decision.answers
                )[:500]
            return
        if self.address_guard is not None:
            refused = [a for a in decision.connect if not self.address_guard(a)]
            if refused:
                rec.outcome = "ssrf_blocked"
                rec.error_detail = (
                    f"{url.host} resolves to a private or internal address."
                )
                rec.staff_detail = ",".join(a + "=addr.guard" for a in refused)[:500]
                return
        if self.clock.monotonic() >= deadline:
            rec.outcome = "timeout"
            return

        rec.t = self.clock.now_ms() // 1000
        # Local names carry "secret" so an error reporter that scrubs frame variables by name redacts them (K2).
        signing_secrets = ep.secrets_newest_first
        rec.signature_header = signing.signature_header(
            signing_secrets, claim.event_id, rec.t, claim.body
        )
        rec.signature_key_tails = [signing.secret_tail(s) for s in signing_secrets]
        headers = signing.attempt_headers(
            msg_id=claim.event_id,
            timestamp=rec.t,
            signature=rec.signature_header,
            event_type=claim.event_type,
            delivery_id=claim.delivery_id,
            attempt=claim.attempt_n,
            sequence=claim.seq,
            test=claim.is_test,
        )
        secret_header_pairs = tuple(headers.items()) + tuple(ep.custom_headers)
        rec.request_headers = {
            **headers,
            "webhook-signature": signing.mask_signature_header(rec.signature_header),
            **{k: signing.mask_header_value(v) for k, v in ep.custom_headers},
        }
        res = self.transport.post(
            PinnedPost(
                scheme=url.scheme or "https",
                host=url.host or "",
                port=url.port or 443,
                target=url.target or "/",
                addresses=tuple(decision.connect),
                headers=secret_header_pairs,
                body=claim.body,
                deadline=deadline,
                max_read=RESPONSE_READ_BYTES,
                abort=abort,
            )
        )
        if res.connected_ip and classify(res.connected_ip) == PUBLIC:
            rec.resolved_ip = res.connected_ip
            rec.ip_family = 6 if ":" in res.connected_ip else 4
        rec.ttfb_ms = res.ttfb_ms
        if res.status is None and res.error == "aborted":
            # The lease was lost mid-attempt: from this sender's view the outcome is unknown, and its settle will be
            # written as superseded.
            rec.outcome = "worker_lost"
            rec.error_detail = (
                "The lease moved to another worker; this sender closed its socket."
            )
            return
        if res.status is None:
            rec.outcome = res.error or "connect_error"
            rec.error_detail = {
                "timeout": f"No complete answer within {ep.timeout_ms} ms.",
                "tls_error": "The TLS handshake failed.",
            }.get(rec.outcome, "Could not connect.")
            rec.staff_detail = (res.detail or "")[:200] or None
            return
        rec.response_status = res.status
        rec.response_headers = kept_headers(res.headers)
        rec.retry_after = {k.lower(): v for k, v in res.headers.items()}.get(
            "retry-after"
        )
        rec.response_body_excerpt = excerpt(res.body, self.scrub)
        rec.outcome = schedule.classify_answer(
            rec.answer(), rec.settled_at_ms or self.clock.now_ms()
        )["outcome"]
        if rec.outcome == "redirect_refused":
            loc = {k.lower(): v for k, v in res.headers.items()}.get("location")
            rec.error_detail = (
                f"Redirected to {redirect_log_host(loc)}; update the URL."
            )
        elif rec.outcome == "rejected" and res.status != 413:
            rec.error_detail = f"Your endpoint rejected this event ({res.status}). Fix the receiver, then redeliver."
        elif rec.outcome == "rejected":
            rec.error_detail = (
                "Your endpoint refused the body as too large (413); it is not retried."
            )


TEST_FAILED = {
    "action": "give_up",
    "delivery_status": "failed",
    "final_outcome": "test_not_retried",
}


def next_action_for(claim: Claim, rec: AttemptRecord, scale: float = 1.0) -> dict:
    """The schedule over this delivery's whole history plus this attempt.

    Tests are never retried and never change the endpoint: a test that does not succeed ends ``failed`` with
    ``test_not_retried`` (a 410 or a policy block included; the attempt row keeps the real outcome). A real delivery
    whose URL is outside the policy now pauses and blocks the endpoint (``block_by_policy``)."""
    if rec.outcome == "superseded":
        raise ValueError("a superseded attempt never drives the schedule")
    if rec.policy_blocked:
        if claim.is_test:
            return dict(TEST_FAILED)
        return {
            "action": "block_by_policy",
            "delivery_status": "paused",
            "disabled_status": "blocked_by_policy",
            "disabled_reason": "policy",
        }
    history = list(claim.history) + [rec.answer()]
    delivery = {
        "jitter_seed": claim.delivery_id,
        "created_at_ms": claim.created_at_ms,
        "max_age_s": claim.endpoint.max_age_s,
    }
    action = schedule.next_action(delivery, history, scale=scale)
    if claim.is_test and action["action"] in ("retry", "disable_endpoint"):
        kept = {k: action[k] for k in ("outcome", "retry_class") if k in action}
        action = {**kept, **TEST_FAILED}
    return action
