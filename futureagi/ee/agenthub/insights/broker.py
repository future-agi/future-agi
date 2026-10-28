"""Run-scoped access to unit evidence for the Insights agent.

An insights-agent/v2 run's units are read by the agent through Django's
internal evidence endpoints (``tracer/views/insights_evidence.py``), never
through ClickHouse or a URL of its own. Each agent request carries a
capability signed here for that one call: the run, which of its units, which
operations, until when, and how many bytes and calls. Every evidence call
checks it again together with the run (the same organization and project,
still running, the unit one of its own), so a run that ends stops serving at
once.

Capability (PROTOCOL_V2 section 3)::

    ie1.<b64url(claims)>.<b64url(HMAC-SHA256(secret, "ie1." + b64url(claims)))>

with the claims as compact JSON with sorted keys, base64url without padding,
signed with ``INSIGHTS_EVIDENCE_SECRET`` (``INSIGHTS_EVIDENCE_SECRET_PREVIOUS``
is still accepted while a new secret rolls out).

A unit's snapshot (``build``) is the unit as it was ingested when the run
was created, with that time as its ``built_at``, so every build of it has the
same digest. The worker builds a batch's snapshots before the agent is asked
to read it (``prebuild``) and puts them in the run's evidence store
(``evidence_store``), and every call here is served from that store. A unit
the store lacks is built here only on a 'local' store, whose documents stay
on the host that wrote them, or for a running run's unit the worker could
not store; few at a time per process, one per unit. No customer content is
stored in the database.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import io
import json
import math
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import structlog

from ee.agenthub.insights import evidence_store
from ee.agenthub.insights.evidence_store import Cached

logger = structlog.get_logger(__name__)

TOKEN_VERSION = "ie1"
OPS = ("manifest", "fields", "media")
# Read-units reads at most eight units a call; a report or a question gives
# its investigators up to 64, in a longer token.
STAGES = {"read": (8, 4_096), "report": (64, 16_384), "ask": (64, 16_384)}
# What a capability allows per unit: every field once (a 32 MiB snapshot plus
# JSON escaping), its manifest and four 5 MiB media views; and calls for the
# agent's tool budget, field pages and media views, with room to spare.
UNIT_BYTES = 64 * 1024 * 1024
UNIT_CALLS = 64
# A report's or a question's investigators open a few of the units they may,
# so their capability is also capped as a whole.
STAGE_LIMITS = {"report": (512 * 1024 * 1024, 1_024), "ask": (256 * 1024 * 1024, 512)}
# How long a capability outlives the agent call it was minted for.
EXPIRY_SLACK_S = 60

MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_FIELD_ITEMS = 2_000
MAX_MEDIA_BYTES = 5 * 1024 * 1024
MAX_IMAGE_SIDE = 1_568
MAX_IMAGE_PIXELS = 50_000_000
# Bytes fetched for one media view, before an image is downscaled.
MAX_MEDIA_FETCH_BYTES = 25 * 1024 * 1024

# A unit's build, on the worker before the agent is asked or here.
BUILD_DEADLINE_MS = 60_000
# Builds and image decodes this process runs at once; a call waits this long
# for a turn, then is refused (503 broker_busy).
MAX_BUILDS = 2
MAX_DECODES = 2
SLOT_WAIT_S = 10.0
# Builds one pre-build (a reading batch's calls share one) runs at once, and
# how long a question waits for its units before it is asked without the
# rest.
PREBUILD_WORKERS = 2
ASK_PREBUILD_S = 30.0
_TOKEN = re.compile(r"ie1\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")

# The protocol's media codes for the snapshot's and the fetch policy's own
# reasons. Anything unlisted is fetch_failed.
_MEDIA_CODES = {
    "too_large": "oversize",
    "unit_media_budget": "oversize",
    "invalid_base64": "unsupported_mime",
    "unsupported_data_uri": "unsupported_mime",
    "unexpected_content_type": "unsupported_mime",
    "own_storage_read_failed": "missing",
    "own_storage_out_of_scope": "not_exported",
    "source_truncated": "missing",
    "http_404": "missing",
    "http_410": "missing",
    "http_401": "expired",
    "http_403": "expired",
    "external_fetch_disabled": "external_fetch_disabled",
    "blocked_by_ssrf_guard": "forbidden_host",
    "not_https": "forbidden_host",
    "redirected_off_host": "redirect_refused",
    "provider_file_reference": "not_exported",
    "unsupported_scheme": "not_exported",
    "unsupported_reference": "not_exported",
    "unsupported_source": "not_exported",
    "invalid_url": "not_exported",
}
# Statuses for a refused media view. Never 401/403/409/410: the agent reads
# those as the capability refused, not as this one item.
_MEDIA_STATUS = {"oversize": 413, "missing": 404, "expired": 404, "not_exported": 404}
_IMAGE_FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}


class BrokerError(Exception):
    """A refused evidence call: its HTTP status and typed code, nothing else."""

    def __init__(self, status: int, code: str):
        self.status = status
        self.code = code
        super().__init__(code)


def _setting(name: str, default: Any = None) -> Any:
    from django.conf import settings

    value = getattr(settings, name, None)
    return default if value in (None, "") else value


# ─── Capabilities ───────────────────────────────────────────────────────────
def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _signature(secret: str, body: str) -> str:
    digest = hmac.new(secret.encode(), body.encode("ascii"), hashlib.sha256)
    return _b64(digest.digest())


def configured(*, probe: bool = False) -> bool:
    """Whether capabilities can be signed here and the evidence store starts
    (the v2 engine needs both). ``probe`` asks the bucket too, as a run's
    creation does: one whose policy lets anyone in refuses to start
    (``evidence_store.probe``)."""
    if not _setting("INSIGHTS_EVIDENCE_SECRET"):
        return False
    refused = evidence_store.probe() if probe else evidence_store.configuration_error()
    if refused:
        logger.error("insight_evidence_store_misconfigured", reason=refused)
        return False
    return True


def unit_kind(unit: str) -> str:
    return {"traces": "trace", "sessions": "session", "users": "user"}[unit]


def mint(run, unit_keys, *, stage: str, ttl_s: float, now: float | None = None):
    """A capability for ``unit_keys`` of ``run`` (InsightUnit pks) for one
    agent call that may take ``ttl_s`` seconds. Without a secret (removed
    while a v2 run was under way) the step fails rather than read another
    way."""
    secret = _setting("INSIGHTS_EVIDENCE_SECRET")
    if not secret:
        from ee.agenthub.insights.llm import InsightModelError

        raise InsightModelError(
            "Insights can't read this report's items: its evidence broker "
            "is not configured."
        )
    max_units, max_chars = STAGES[stage]
    keys = list(dict.fromkeys(str(key) for key in unit_keys))
    if not keys or len(keys) > max_units:
        raise ValueError(f"a {stage} capability covers 1 to {max_units} units")
    claims = {
        "v": 1,
        "run": str(run.id),
        "org": str(run.organization_id),
        "project": str(run.project_id),
        "kind": unit_kind(run.unit),
        "units": keys,
        "ops": list(OPS),
        "stage": stage,
        "exp": int((time.time() if now is None else now) + ttl_s + EXPIRY_SLACK_S),
    }
    stage_bytes, stage_calls = STAGE_LIMITS.get(stage, (math.inf, math.inf))
    claims["max_bytes"] = int(min(UNIT_BYTES * len(keys), stage_bytes))
    claims["max_calls"] = int(min(UNIT_CALLS * len(keys), stage_calls))
    payload = json.dumps(claims, sort_keys=True, separators=(",", ":"))
    body = f"{TOKEN_VERSION}.{_b64(payload.encode())}"
    token = f"{body}.{_signature(secret, body)}"
    if len(token) > max_chars:
        raise ValueError("the capability is too long")
    return token


def verify(token: str, *, now: float | None = None) -> dict:
    """The claims of a capability signed here and not expired. The signature
    is checked before the claims are read."""
    longest = max(chars for _, chars in STAGES.values())
    if not isinstance(token, str) or len(token) > longest:
        raise BrokerError(401, "capability_invalid")
    if not _TOKEN.fullmatch(token):
        raise BrokerError(401, "capability_invalid")
    version, payload, signature = token.split(".")
    if version != TOKEN_VERSION:
        raise BrokerError(401, "capability_invalid")
    body = f"{version}.{payload}"
    secrets = [
        _setting(name)
        for name in ("INSIGHTS_EVIDENCE_SECRET", "INSIGHTS_EVIDENCE_SECRET_PREVIOUS")
    ]
    if not any(
        hmac.compare_digest(_signature(secret, body), signature)
        for secret in secrets
        if secret
    ):
        raise BrokerError(401, "capability_invalid")
    try:
        claims = json.loads(_unb64(payload))
    except (binascii.Error, ValueError):
        raise BrokerError(401, "capability_invalid") from None
    if not isinstance(claims, dict) or claims.get("v") != 1:
        raise BrokerError(401, "capability_invalid")
    expires = claims.get("exp")
    if not isinstance(expires, int) or isinstance(expires, bool):
        raise BrokerError(401, "capability_invalid")
    if expires <= (time.time() if now is None else now):
        raise BrokerError(401, "capability_expired")
    return claims


def bearer(header: str | None) -> str:
    scheme, _, token = str(header or "").partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""


# ─── The run and its unit ───────────────────────────────────────────────────
def _load_run(run_id: str):
    from tracer.models.insights import InsightRun

    # The agent calls outside any request's workspace: the claims scope it.
    return InsightRun.no_workspace_objects.filter(id=run_id).first()


def _load_unit(run, key: str):
    from tracer.models.insights import InsightUnit

    return InsightUnit.objects.filter(run_id=run.id, pk=int(key)).first()


def authorize(claims: dict, op: str, unit: str):
    """The run and unit a call may read: the op and unit are in the claims,
    the run is the claims' (organization, project, unit kind), the unit is
    one of its own, and the run still running (a question's capability reads
    a completed report)."""
    if op not in (claims.get("ops") or ()):
        raise BrokerError(403, "op_not_allowed")
    if not isinstance(unit, str) or unit not in (claims.get("units") or ()):
        raise BrokerError(403, "unit_not_in_capability")
    try:
        run = _load_run(str(uuid.UUID(str(claims.get("run")))))
    except ValueError:
        run = None
    if run is None:
        raise BrokerError(403, "run_not_found")
    if (
        str(run.organization_id) != claims.get("org")
        or str(run.project_id) != claims.get("project")
        or unit_kind(run.unit) != claims.get("kind")
    ):
        raise BrokerError(403, "scope_mismatch")
    wanted = "completed" if claims.get("stage") == "ask" else "running"
    if run.status != wanted:
        if run.status != "running":
            forget_run(run.id)
        raise BrokerError(409, "run_not_running")
    pk = unit.isascii() and unit.isdigit() and len(unit) <= 18
    row = _load_unit(run, unit) if pk else None
    if row is None:
        raise BrokerError(403, "unit_not_in_run")
    return run, row


# ─── Capability budgets ─────────────────────────────────────────────────────
def _budget_key(token: str, what: str) -> str:
    return f"insight-evidence-{what}:{hashlib.sha256(token.encode()).hexdigest()}"


def _ttl(claims: dict) -> int:
    return max(1, int(claims["exp"] - time.time()))


def take_call(token: str, claims: dict) -> int:
    """Count a call against the capability; returns the bytes it has left.
    Shared across pods through the cache. Fails closed."""
    from django.core.cache import cache

    calls, spent = _budget_key(token, "calls"), _budget_key(token, "bytes")
    try:
        cache.add(calls, 0, timeout=_ttl(claims))
        made = cache.incr(calls)
        used = int(cache.get(spent) or 0)
    except Exception:
        logger.warning("insight_evidence_budget_unavailable", exc_info=True)
        raise BrokerError(503, "budget_unavailable") from None
    left = int(claims.get("max_bytes") or 0) - used
    if made > int(claims.get("max_calls") or 0) or left <= 0:
        raise BrokerError(429, "capability_budget_spent")
    return left


def spend_bytes(token: str, claims: dict, size: int) -> None:
    from django.core.cache import cache

    key = _budget_key(token, "bytes")
    try:
        cache.add(key, 0, timeout=_ttl(claims))
        cache.incr(key, size)
    except Exception:
        logger.warning("insight_evidence_budget_unavailable", exc_info=True)


# ─── Snapshots ──────────────────────────────────────────────────────────────
@dataclass
class _Flight:
    """One build of a unit that other calls for it wait on."""

    done: threading.Event = field(default_factory=threading.Event)
    result: Cached | None = None
    error: tuple[int, str] | None = None


_flights: dict[str, _Flight] = {}
_flights_lock = threading.Lock()
_builds = threading.BoundedSemaphore(MAX_BUILDS)
_decodes = threading.BoundedSemaphore(MAX_DECODES)


def forget_run(run_id) -> None:
    """Drop what this process and this host hold of a run that is no longer
    served (the bucket's documents go in ``delete_run`` or the sweep)."""
    evidence_store.release(run_id)


def sweep() -> None:
    """Sweep the evidence store, at most once a minute per process."""
    evidence_store.sweep()


def _analytics():
    from tracer.services.clickhouse.v2.query_service import V2AnalyticsQueryService

    return V2AnalyticsQueryService()


def _lookup_ids(kind: str, key: str) -> list[str]:
    from ee.agenthub.insights.evidence import resolve_lookup_ids

    return resolve_lookup_ids(kind, key)


def _window(run) -> tuple[datetime | None, datetime | None]:
    """The run's window bounds a user's traces, as its prepare stage reads."""
    window = (run.scope or {}).get("window") or []
    if run.unit != "users" or len(window) != 2:
        return None, None
    from ee.agenthub.insights.pipeline import _parse_time

    return _parse_time(window[0]), _parse_time(window[1])


def build(run, row):
    """The unit's snapshot, under its run key: ``manifest.unit.key`` is the
    InsightUnit pk the agent asks for, ``unit_id`` the unit it reads. It is
    the unit as ingested when the run was created, whenever and wherever it
    is built. A data URI too large to view is not kept (its view is
    ``oversize``), and a source's lone surrogates are replaced, as the
    rendered text's are, so the document always encodes."""
    from ee.agenthub.insights import evidence

    kind = unit_kind(run.unit)
    since, until = _window(run)
    snapshot = evidence.build_unit_snapshot(
        analytics=_analytics(),
        project_id=str(run.project_id),
        organization_id=str(run.organization_id),
        kind=kind,
        key=row.unit_id,
        lookup_ids=_lookup_ids(kind, row.unit_id),
        since=since,
        until=until,
        limits=evidence.EvidenceLimits(deadline_ms=BUILD_DEADLINE_MS),
        now=run.created_at,
        as_of=run.created_at,
    )
    manifest = dict(snapshot.manifest)
    unit = manifest["unit"]
    manifest["unit"] = {**unit, "key": str(row.pk), "unit_id": unit["key"]}
    manifest["digest"] = evidence.snapshot_digest(manifest, snapshot.fields)
    sources = {}
    for alias, source in snapshot.media_sources.items():
        value = source.value
        if source.source == "data_uri" and len(value) * 3 // 4 > MAX_MEDIA_FETCH_BYTES:
            value = None
        sources[alias] = [source.source, _text(value), _text(source.mime)]
    return Cached({"manifest": manifest, "fields": snapshot.fields}, sources)


def _text(value):
    """A source's string with its lone surrogates replaced (a URL a span's
    JSON escaped badly): it would not encode as UTF-8 to be stored."""
    from ee.agenthub.insights.evidence import _clean

    if not isinstance(value, str) or value.isascii():
        return value
    return _clean(value)


def snapshot(run, row) -> Cached:
    """The unit's snapshot from the run's evidence store. Built here, once
    however many calls ask for it meanwhile, only when the store lacks it
    and may: always on a 'local' store (each host keeps its own), on a
    shared one only while the run runs (a unit the worker could not store).
    A question's units were stored before it was asked (``prebuild``), so a
    completed run's missing unit is not found."""
    key = str(row.pk)
    try:
        store = evidence_store.current()
        cached = store.get(run.id, key)
    except evidence_store.EvidenceStoreUnavailable as unavailable:
        raise BrokerError(503, unavailable.code) from None
    if cached is not None:
        return cached
    if store.shared and run.status != "running":
        raise BrokerError(404, "unit_not_found")
    built, _ = _once(
        f"{run.id}/{key}",
        lambda: _build_and_store(run, row, store, wait_s=SLOT_WAIT_S, remember=True),
    )
    return built


def _once(key: str, work):
    """``work()`` run by the first caller for ``key``; the others meanwhile
    wait for its result (or its refusal)."""
    with _flights_lock:
        flight = _flights.get(key)
        leader = flight is None
        if leader:
            flight = _flights[key] = _Flight()
    if not leader:
        if not flight.done.wait(BUILD_DEADLINE_MS / 1000 + SLOT_WAIT_S + 5):
            raise BrokerError(503, "broker_busy")
        if flight.error is not None:
            raise BrokerError(*flight.error)
        return flight.result
    try:
        flight.result = work()
        return flight.result
    except BrokerError as refused:
        flight.error = (refused.status, refused.code)
        raise
    except BaseException:
        flight.error = (500, "internal_error")
        raise
    finally:
        with _flights_lock:
            _flights.pop(key, None)
        flight.done.set()


def _build(run, row, wait_s: float | None) -> Cached:
    """``build``, with the builder's failures as the broker's refusals. With
    ``wait_s``, as one of this process's MAX_BUILDS, waiting that long for a
    turn (503 broker_busy past it)."""
    from ee.agenthub.insights.evidence import EvidenceNotFound, EvidenceUnavailable

    if wait_s is not None and not _builds.acquire(timeout=wait_s):
        raise BrokerError(503, "broker_busy")
    try:
        return build(run, row)
    except EvidenceNotFound:
        raise BrokerError(404, "unit_not_found") from None
    except EvidenceUnavailable as exc:
        raise BrokerError(503, exc.code) from None
    except Exception:
        logger.exception("insight_evidence_build_failed", run_id=str(run.id))
        raise BrokerError(500, "internal_error") from None
    finally:
        if wait_s is not None:
            _builds.release()


def _build_and_store(
    run, row, store, *, wait_s: float | None, remember: bool
) -> tuple[Cached, int | None]:
    """The unit's snapshot as this process holds it, else built (``_build``),
    put in the store; with the bytes stored, None when the store did not
    take it. ``remember`` keeps it in memory too, so a unit the store could
    not take is not built again here. A document that cannot be stored at
    all fails that unit alone (500 internal_error)."""
    key = str(row.pk)
    built = evidence_store.recall(run.id, key) or _build(run, row, wait_s)
    try:
        return built, store.put(run.id, key, built, remember=remember)
    except evidence_store.EvidenceStoreUnavailable:
        logger.warning("insight_evidence_store_unavailable", run_id=str(run.id))
        return built, None
    except Exception as exc:
        # The type only: the document being encoded is customer content.
        logger.error(
            "insight_evidence_store_put_failed",
            run_id=str(run.id),
            error=type(exc).__name__,
        )
        raise BrokerError(500, "internal_error") from None


# ─── Building ahead of the agent ────────────────────────────────────────────
@dataclass(frozen=True)
class Prebuilt:
    """The units of a pre-build the agent may read (``ready``, in the order
    asked) and those that could not be built, with the builder's code."""

    ready: list[str]
    failed: dict[str, str]


def prebuild(run, rows, *, stage: str, ttl_s: float = 0.0, pool=None) -> Prebuilt:
    """Build the snapshots of ``rows`` (InsightUnits, or anything with their
    ``pk`` and ``unit_id``) that the run's evidence store lacks and store
    them, before the agent is asked to read them: ``PREBUILD_WORKERS`` at a
    time (on ``pool`` when given, so the calls of one batch share it), each
    within ``BUILD_DEADLINE_MS`` and as ingested when the run was created
    (``build``), so a rebuild has the digest of the first build. A unit is
    built once however many calls of this process want it meanwhile, the
    broker's own included (``_once``), and from what this process holds of
    it when it holds it.

    Reading (``stage="read"``, on the worker) builds on either store, and a
    unit that can't be built is never sent to the agent. A report or a
    question builds only on a shared store (each web pod builds its own on
    'local'). A question, asked on a web pod, builds as the broker does
    there, one of its MAX_BUILDS at a time; it waits for ``ASK_PREBUILD_S``
    at most, leaving out what is not stored by then, and first holds the
    run's documents for that wait and ``ttl_s``, its agent call. A store
    that does not take a unit, or no turn to build it, fails a reading
    (InsightModelError, retried) and leaves the unit out of a report or a
    question. The store is swept first, unless ``pool`` is given: the
    batch's caller sweeps on its own thread, never on the pool's."""
    rows = list(rows)
    keys = [str(row.pk) for row in rows]
    try:
        store = evidence_store.current()
    except evidence_store.EvidenceStoreMisconfigured as refused:
        raise _not_configured(refused) from None
    if not rows or (stage != "read" and not store.shared):
        return Prebuilt(keys, {})
    budget = None
    if stage == "ask":
        budget = ASK_PREBUILD_S
        # Held before the sweep below can look at the completed run.
        evidence_store.hold(run.id, ASK_PREBUILD_S + ttl_s + EXPIRY_SLACK_S)
    own = pool is None
    if own:
        evidence_store.sweep()
    started = time.monotonic()
    pool = pool or ThreadPoolExecutor(max_workers=PREBUILD_WORKERS)
    try:
        futures = [pool.submit(_prebuild_one, store, run, row, stage) for row in rows]
        done, _ = wait(futures, timeout=budget)
    finally:
        if own:
            # A question's late builds finish on their own and are stored
            # for the next one; the ones not started are dropped.
            pool.shutdown(wait=False, cancel_futures=True)
    ready, failed = [], {}
    counts = {"built": 0, "reused": 0, "unstored": 0, "bytes": 0}
    for key, future in zip(keys, futures, strict=True):
        if future not in done:
            continue
        try:
            outcome, size, code = future.result()
        except evidence_store.EvidenceStoreMisconfigured as refused:
            # A bucket found open to anyone on first use.
            raise _not_configured(refused) from None
        except evidence_store.EvidenceStoreUnavailable:
            counts["unstored"] += 1
            continue
        if code is not None:
            failed[key] = code
            continue
        ready.append(key)
        counts[outcome] += 1
        counts["bytes"] += size
    logger.info(
        "insight_evidence_prebuilt",
        run_id=str(run.id),
        stage=stage,
        units=len(keys),
        failed=len(failed),
        late=len(keys) - len(done),
        elapsed_ms=round((time.monotonic() - started) * 1000),
        **counts,
    )
    if counts["unstored"] and stage == "read":
        from ee.agenthub.insights.llm import InsightModelError

        raise InsightModelError(
            "The Insights evidence store is unavailable right now. "
            "Try again shortly.",
            unavailable=True,
        )
    return Prebuilt(ready, failed)


def _not_configured(refused):
    """The step's failure for a store that refuses to start: for good."""
    from ee.agenthub.insights.llm import InsightModelError

    logger.error("insight_evidence_store_misconfigured", reason=refused.reason)
    return InsightModelError(
        "Insights can't read this report's items: its evidence store is not "
        "configured."
    )


def _prebuild_one(store, run, row, stage: str) -> tuple[str, int, str | None]:
    """("reused" or "built", bytes stored, None), or ("failed", 0, the
    builder's code); what another call built meanwhile is reused. Raises
    EvidenceStoreUnavailable when the unit is not stored: the store did not
    take it, or it had no turn to be built."""
    key = str(row.pk)
    if store.has(run.id, key):
        return "reused", 0, None
    # The worker's pre-builds are bounded per batch (PREBUILD_WORKERS); a
    # question's, on a web pod, by the broker's own turns there.
    wait_s = SLOT_WAIT_S if stage == "ask" else None
    mine = []

    def work():
        mine.append(True)
        return _build_and_store(run, row, store, wait_s=wait_s, remember=False)

    try:
        _, size = _once(f"{run.id}/{key}", work)
    except BrokerError as refused:
        if refused.code == "broker_busy":
            raise evidence_store.EvidenceStoreUnavailable() from None
        return "failed", 0, refused.code
    if size is None:
        raise evidence_store.EvidenceStoreUnavailable()
    return ("built", size, None) if mine else ("reused", 0, None)


# ─── Operations ─────────────────────────────────────────────────────────────
def manifest_body(cached: Cached, unit: str) -> bytes:
    manifest = cached.document["manifest"]
    if manifest["unit"]["key"] != unit:
        raise BrokerError(500, "manifest_mismatch")
    return _json({"manifest": manifest})


def fields_body(cached: Cached, items, max_bytes, remaining: int) -> bytes:
    """Field texts in the order asked, until the next would take the body
    past min(``max_bytes``, 16 MiB, the capability's bytes left). Items left
    out are the agent's to ask for again."""
    if not isinstance(items, list) or not 0 < len(items) <= MAX_FIELD_ITEMS:
        raise BrokerError(400, "invalid_request")
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 1:
        raise BrokerError(400, "invalid_request")
    texts = cached.document["fields"]
    wanted = []
    for item in items:
        if not isinstance(item, dict):
            raise BrokerError(400, "invalid_request")
        span, field = item.get("span"), item.get("field")
        text = None
        if isinstance(span, str) and isinstance(field, str):
            text = texts.get(span, {}).get(field)
        if not isinstance(text, str):
            raise BrokerError(400, "unknown_field")
        wanted.append((span, field, text))
    asked = min(max_bytes, MAX_RESPONSE_BYTES)
    limit = min(asked, remaining)
    out: dict[str, dict[str, str]] = {}
    size = len(b'{"fields":{}}')
    for span, field, text in wanted:
        # An upper bound on what the item adds: its span's key, colon,
        # braces and comma (counted again for a span already there), and its
        # own key, colon, text and comma.
        cost = len(_json(span)) + len(_json(field)) + len(_json(text)) + 6
        if size + cost > limit:
            if out:
                break
            if size + cost <= asked:
                raise BrokerError(429, "capability_budget_spent")
            raise BrokerError(413, "field_too_large")
        out.setdefault(span, {})[field] = text
        size += cost
    return _json({"fields": out})


def _json(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


@dataclass(frozen=True)
class Media:
    content: bytes
    mime: str
    original: tuple[int, int] | None = None


def media(cached: Cached, alias, *, project_id: str) -> Media:
    """One media item's bytes, fetched by this host under the D6 policy (the
    platform's storage only for trace media of ``project_id``); an image
    downscaled and re-encoded, a few at a time. Never its URL."""
    from ee.agenthub.insights import evidence

    entry = next(
        (
            item
            for item in cached.document["manifest"]["media"]
            if item["alias"] == alias
        ),
        None,
    )
    if entry is None:
        raise BrokerError(400, "unknown_media")
    stored = cached.media_sources.get(alias)
    if not entry["accessible"] or not stored:
        raise _refused(entry.get("reason") or "unsupported_source")
    if entry["kind"] not in ("image", "audio"):
        raise _refused("unexpected_content_type")
    if stored[1] is None:
        # A data URI past what a view may fetch was not kept (``build``).
        raise _refused("too_large")
    policy = evidence.MediaFetchPolicy(
        max_item_bytes=MAX_MEDIA_FETCH_BYTES,
        max_unit_bytes=MAX_MEDIA_FETCH_BYTES,
        fetch_external=bool(_setting("INSIGHTS_EVIDENCE_FETCH_EXTERNAL_MEDIA", False)),
        own_storage_project=str(project_id),
    )
    source = evidence.MediaSource(source=stored[0], value=stored[1], mime=stored[2])
    try:
        content, mime = evidence.fetch_media(entry, source, policy)
    except evidence.MediaRefused as refused:
        raise _refused(refused.reason) from None
    if entry["kind"] == "image":
        if not _decodes.acquire(timeout=SLOT_WAIT_S):
            raise BrokerError(503, "broker_busy")
        try:
            return downscale(content)
        finally:
            _decodes.release()
    if len(content) > MAX_MEDIA_BYTES:
        raise _refused("too_large")
    if not (mime or "").startswith("audio/"):
        raise _refused("unexpected_content_type")
    return Media(content, mime)


def _refused(reason: str) -> BrokerError:
    code = _MEDIA_CODES.get(reason, "fetch_failed")
    return BrokerError(_MEDIA_STATUS.get(code, 422), code)


def cited_image(run, row, alias) -> Media:
    """An image ``row``'s evidence cites (a ``kind: media`` item naming
    ``alias``), for the report page: the view the agent had of it. A run's
    snapshots go when it ends, so the unit is built again as ingested when
    the run was created (the same aliases), one of this process's
    MAX_BUILDS, and never stored. Anything the evidence does not cite, or
    that is not an image, is not served."""
    cited = any(
        item.get("kind") == "media" and item.get("media") == alias
        for item in (row.facets or {}).get("evidence") or []
        if isinstance(item, dict)
    )
    if not cited:
        raise BrokerError(404, "unknown_media")
    key = str(row.pk)
    cached = evidence_store.recall(run.id, key) or _once(
        f"cited/{run.id}/{key}", lambda: _build(run, row, SLOT_WAIT_S)
    )
    entry = next(
        (m for m in cached.document["manifest"]["media"] if m["alias"] == alias),
        None,
    )
    if entry is not None and entry["kind"] != "image":
        raise _refused("unexpected_content_type")
    return media(cached, alias, project_id=str(run.project_id))


def downscale(content: bytes) -> Media:
    """An image the agent may view: its first frame, the longest side at most
    ``MAX_IMAGE_SIDE``, re-encoded without metadata (PNG, JPEG and WebP keep
    their format, anything else becomes PNG; past ``MAX_MEDIA_BYTES`` it
    becomes JPEG, smaller if it must). Refuses an image over
    ``MAX_IMAGE_PIXELS`` before decoding it, and any it cannot decode."""
    from PIL import Image, ImageOps

    try:
        with Image.open(io.BytesIO(content)) as image:
            width, height = image.size
            if width * height > MAX_IMAGE_PIXELS:
                raise _refused("too_large")
            source_format = image.format
            image.seek(0)
            # Scaled before it is turned upright: a JPEG then decodes at a
            # fraction of its size, and only the small copy is rotated.
            image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE), Image.Resampling.LANCZOS)
            frame = ImageOps.exif_transpose(image)
        image_format = source_format if source_format in _IMAGE_FORMATS else "PNG"
        data = _encode(frame, image_format)
        while len(data) > MAX_MEDIA_BYTES:
            if image_format != "JPEG":
                image_format = "JPEG"
            elif max(frame.size) > 256:
                frame = frame.resize((frame.width * 3 // 4, frame.height * 3 // 4))
            else:
                raise _refused("too_large")
            data = _encode(frame, image_format)
    except BrokerError:
        raise
    except Image.DecompressionBombError:
        raise _refused("too_large") from None
    except Exception:
        # A corrupt file raises more than Pillow documents (a bad TIFF or
        # EXIF block raises SyntaxError, among others).
        raise _refused("unexpected_content_type") from None
    return Media(data, _IMAGE_FORMATS[image_format], (width, height))


def _encode(frame, image_format: str) -> bytes:
    from PIL import Image

    if image_format == "JPEG" and frame.mode != "RGB":
        rgba = frame.convert("RGBA")
        flat = Image.new("RGB", rgba.size, (255, 255, 255))
        flat.paste(rgba, mask=rgba.getchannel("A"))
        frame = flat
    elif image_format == "WEBP" and frame.mode not in ("RGB", "RGBA"):
        frame = frame.convert("RGBA")
    elif image_format == "PNG" and frame.mode not in ("1", "L", "LA", "RGB", "RGBA"):
        # A palette image's transparency lives in its info, dropped below.
        frame = frame.convert("RGBA")
    # A fresh copy carries no EXIF, ICC or text chunks into the encoder.
    clean = frame.copy()
    clean.info = {}
    buffer = io.BytesIO()
    options = {"quality": 85} if image_format in ("JPEG", "WEBP") else {}
    clean.save(buffer, format=image_format, **options)
    return buffer.getvalue()
