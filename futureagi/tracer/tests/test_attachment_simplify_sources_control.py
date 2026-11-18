"""Isolated source-control tests: real crypto/wire/read code, fake ORM only.

No database, application startup, network or provider account is used. These
prove deterministic boundaries, not PostgreSQL locking or native qualification.
"""

import base64
import hashlib
import importlib.util
import io
import json
import sys
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import jwt
import pytest

BASE = Path(__file__).resolve().parents[1] / "services" / "attachments"
# Explicit unit-test epoch, not collection-time wall clock. Only the three
# freshly loaded service modules use this clock; Django setup and JWT validation
# retain their own clocks. Each test gets a new clock and can advance it to prove
# that the unchanged production expiry checks still deny access.
NOW = datetime(2026, 1, 1, tzinfo=UTC)


class SourceClock:
    def __init__(self):
        self.value = NOW

    def now(self):
        return self.value

    def advance(self, delta):
        assert delta >= timedelta(0)
        self.value += delta


@pytest.fixture
def source_clock():
    return SourceClock()


class Query:
    def __init__(self, rows=(), calls=None):
        self.rows = list(rows)
        self.calls = calls if calls is not None else []

    def filter(self, **kw):
        self.calls.append(("filter", kw))

        def matches(row):
            for key, value in kw.items():
                field, _, op = key.partition("__")
                actual = getattr(row, "id" if field == "pk" else field, None)
                if op == "isnull":
                    if (actual is None) != value:
                        return False
                elif op == "gt":
                    if actual is None or actual <= value:
                        return False
                elif op == "lte":
                    if actual is None or actual > value:
                        return False
                elif actual != value:
                    return False
            return True

        return Query([r for r in self.rows if matches(r)], self.calls)

    def select_for_update(self, **kw):
        self.calls.append(("lock", kw))
        return self

    def order_by(self, *args):
        return self

    def first(self):
        return self.rows[0] if self.rows else None

    def count(self):
        return len(self.rows)

    def exists(self):
        return bool(self.rows)

    def get(self, **kw):
        row = self.filter(**kw).first() if kw else self.first()
        if row is None:
            raise LookupError
        return row

    def values_list(self, field, flat=False):
        return Query([getattr(row, field) for row in self.rows])

    def update(self, **kw):
        for row in self.rows:
            for key, value in kw.items():
                setattr(row, key, value)
        return len(self.rows)

    def __getitem__(self, item):
        return self.rows[item]

    def __iter__(self):
        return iter(self.rows)


def row(**kw):
    if "id" in kw:
        kw.setdefault("pk", kw["id"])
    return SimpleNamespace(save=Mock(), **kw)


@pytest.fixture
def modules(monkeypatch, source_clock):
    from django.conf import settings
    from django.db import transaction

    if not settings.configured:
        settings.configure(USE_TZ=True)
    monkeypatch.setattr(settings, "FI_ATTACHMENTS_ENABLED", True, raising=False)
    monkeypatch.setattr(
        settings, "FI_ATTACHMENTS_IMPORT_HOSTS", ("example.test",), raising=False
    )
    monkeypatch.setattr(
        settings,
        "FI_ATTACHMENTS_ENVELOPE_KEYS",
        "test:" + base64.urlsafe_b64encode(b"x" * 32).decode(),
        raising=False,
    )
    depth = SimpleNamespace(value=0)

    @contextmanager
    def atomic(*_a, **_kw):
        depth.value += 1
        try:
            yield
        finally:
            depth.value -= 1

    monkeypatch.setattr(transaction, "atomic", atomic)
    monkeypatch.setattr(
        transaction,
        "get_connection",
        lambda: SimpleNamespace(in_atomic_block=depth.value > 0),
    )
    for name in (
        "tracer",
        "tracer.models",
        "tracer.services",
        "tracer.services.attachments",
    ):
        pkg = ModuleType(name)
        pkg.__path__ = []
        monkeypatch.setitem(sys.modules, name, pkg)
    am = ModuleType("tracer.models.attachment")
    rm = ModuleType("tracer.models.attachment_recording")
    for name in ("AttachmentScope", "CaptureSession", "AttachmentWriteGrant"):
        setattr(am, name, SimpleNamespace(objects=Query()))
    for name in (
        "AttachmentSourceEnvelope",
        "AttachmentImportJob",
        "AttachmentProviderConnection",
        "AttachmentProviderRecording",
        "AttachmentProviderEvent",
    ):
        setattr(rm, name, SimpleNamespace(objects=Query()))
    rm.ProviderRecordingState = SimpleNamespace(
        **{
            n.upper(): n
            for n in (
                "registered",
                "active",
                "ending",
                "complete",
                "failed",
                "aborted",
                "limit_reached",
            )
        }
    )
    rm.TERMINAL_PROVIDER_STATES = {"complete", "failed", "aborted", "limit_reached"}
    monkeypatch.setitem(sys.modules, am.__name__, am)
    monkeypatch.setitem(sys.modules, rm.__name__, rm)
    bootstrap = ModuleType("tracer.services.attachments.bootstrap")
    bootstrap.check_session = Mock()
    bootstrap.session_policy = Mock()
    monkeypatch.setitem(sys.modules, bootstrap.__name__, bootstrap)
    loaded = {}
    for name in (
        "contract",
        "interfaces",
        "safe_fetch",
        "envelopes",
        "wire",
        "provider_recordings",
        "source_providers",
    ):
        qual = "tracer.services.attachments." + name
        spec = importlib.util.spec_from_file_location(qual, BASE / (name + ".py"))
        mod = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, qual, mod)
        spec.loader.exec_module(mod)
        if name in {"envelopes", "provider_recordings", "source_providers"}:
            # Replace only this module's clock reference, never Django's global
            # timezone.now or the application's TTL/lease policy.
            monkeypatch.setattr(mod, "timezone", source_clock)
        loaded[name] = mod
        monkeypatch.setattr(
            sys.modules["tracer.services.attachments"], name, mod, raising=False
        )
    return SimpleNamespace(
        **loaded, am=am, rm=rm, bootstrap=bootstrap, depth=depth, settings=settings
    )


def facts(m, **changes):
    value = m.interfaces.SourceFacts(
        "RemoteUrlEnvelope",
        uuid4(),
        "user",
        uuid4(),
        "http_strong_etag",
        '"v1"',
        8,
        "audio/wav",
        1,
        NOW + timedelta(minutes=5),
        0,
        uuid4(),
        "1234567890abcdef",
        "mixed",
        None,
    )
    return replace(value, **changes)


@pytest.mark.parametrize(
    "kind,revision,valid",
    [
        ("http_strong_etag", '"v1"', True),
        ("http_strong_etag", '""', True),
        ("http_strong_etag", 'W/"v1"', False),
        ("http_strong_etag", '"broken', False),
        ("http_strong_etag", '"a\r\nb"', False),
        ("http_strong_etag", None, False),
        ("gcs_generation", "2", True),
        ("gcs_generation", "0", False),
        ("gcs_generation", "²", False),
        ("s3_version", "null", False),
        ("s3_version", "abc123", True),
        ("unknown", "v1", False),
    ],
)
def test_generation_strict(modules, kind, revision, valid):
    assert modules.safe_fetch.valid_generation(kind, revision) is valid


@pytest.mark.parametrize(
    "url",
    [
        "http://example.test/a",
        "https://example.test:444/a",
        "https://127.0.0.1/a",
        "https://u:p@example.test/a",
        "https://example.test/a#secret",
        "https://example.test.attacker.test/a",
        "https://example.test/a\r\nHeader:value",
    ],
)
def test_destination_denials(modules, url):
    with pytest.raises(modules.contract.AttachmentError):
        modules.safe_fetch.parse_destination(url, approved_hosts=("example.test",))


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "10.1.1.1",
        "169.254.169.254",
        "::1",
        "::ffff:127.0.0.1",
        "64:ff9b::a00:1",
        "224.0.0.1",
        "0.0.0.0",
    ],
)
def test_private_address_denied(modules, ip):
    assert not modules.safe_fetch.address_allowed(ip)


class Response:
    status = 206

    def __init__(self, body=b"abcdefgh", headers=None):
        self.body = io.BytesIO(body)
        self.read_sizes = []
        self.headers = {
            "Content-Range": "bytes 0-7/8",
            "Content-Length": "8",
            "ETag": '"v1"',
        }
        self.headers.update(headers or {})

    def getheader(self, key, default=None):
        return self.headers.get(key, default)

    def read(self, size):
        self.read_sizes.append(size)
        return self.body.read(size)


def range_source(m, monkeypatch, response):
    connection = SimpleNamespace(
        request=Mock(), getresponse=lambda: response, close=Mock(), sock=None
    )
    source = m.safe_fetch.RangeSource(
        m.safe_fetch.Destination("example.test", 443, "/fixed?secret=redacted"),
        8,
        8 * 1024 * 1024,
        30,
        8 * 1024 * 1024,
        generation='"v1"',
    )
    monkeypatch.setattr(source, "_connection", lambda: connection)
    return source, connection


def test_exact_bounded_read(modules, monkeypatch):
    response = Response()
    source, conn = range_source(modules, monkeypatch, response)
    sink = io.BytesIO()
    assert source.read_range(0, 8, sink) == 8
    assert sink.getvalue() == b"abcdefgh"
    headers = conn.request.call_args.kwargs["headers"]
    assert headers["If-Match"] == '"v1"'
    assert "Authorization" not in headers and "Cookie" not in headers
    assert max(response.read_sizes) <= 256 * 1024
    conn.close.assert_called_once()


@pytest.mark.parametrize(
    "headers",
    [
        {"ETag": '"v2"'},
        {"ETag": 'W/"v1"'},
        {"Content-Range": "bytes 0-7/9"},
        {"Content-Length": "9"},
        {"Content-Encoding": "gzip"},
        {"Transfer-Encoding": "chunked"},
    ],
)
def test_changed_metadata_never_read(modules, monkeypatch, headers):
    response = Response(headers=headers)
    source, conn = range_source(modules, monkeypatch, response)
    with pytest.raises(modules.contract.AttachmentError):
        source.read_range(0, 8, io.BytesIO())
    assert not response.read_sizes
    conn.close.assert_called_once()


@pytest.mark.parametrize("status", [200, 301, 302, 307, 308, 412, 404])
def test_status_redirects_denied(modules, monkeypatch, status):
    response = Response()
    response.status = status
    source, conn = range_source(modules, monkeypatch, response)
    with pytest.raises(modules.contract.AttachmentError):
        source.read_range(0, 8, io.BytesIO())
    assert not response.read_sizes
    conn.close.assert_called_once()


@pytest.mark.parametrize("body", [b"short", b"abcdefghX"])
def test_short_long_read_denied(modules, monkeypatch, body):
    source, conn = range_source(modules, monkeypatch, Response(body))
    with pytest.raises(modules.contract.AttachmentError):
        source.read_range(0, 8, io.BytesIO())
    conn.close.assert_called_once()


def test_head_reads_no_body(modules, monkeypatch):
    response = Response(headers={"Content-Type": "audio/wav"})
    response.status = 200
    source, conn = range_source(modules, monkeypatch, response)
    assert modules.safe_fetch.inspect_metadata(source) == ('"v1"', 8, "audio/wav")
    assert not response.read_sizes
    assert conn.request.call_args.args[0] == "HEAD"


@pytest.mark.parametrize(
    "kind,target,revision,valid",
    [
        ("s3_version", "/a?versionId=abc", "abc", True),
        ("s3_version", "/a", "abc", False),
        ("s3_version", "/a?versionId=abc&versionId=abc", "abc", False),
        ("gcs_generation", "/a?generation=123", "123", True),
        ("gcs_generation", "/a?generation=124", "123", False),
    ],
)
def test_native_revision_is_addressed(modules, kind, target, revision, valid):
    def fn():
        return modules.safe_fetch.validate_revision_query(
            modules.safe_fetch.Destination("example.test", 443, target), kind, revision
        )

    if valid:
        fn()
    else:
        with pytest.raises(modules.contract.AttachmentError):
            fn()


def signed_event(
    m,
    monkeypatch,
    *,
    body=b'{"id":"evt","event":"egress_ended"}',
    secret=b"s" * 32,
    claims=None,
    algorithm="HS256",
):
    connection = row(
        id=uuid4(),
        scope_id=uuid4(),
        provider="livekit",
        api_key_id="issuer",
        enabled=True,
        revoked_at=None,
        key_id="test",
        secret_nonce=b"",
        secret_ciphertext=b"",
    )
    m.rm.AttachmentProviderConnection.objects = Query([connection])
    monkeypatch.setattr(m.envelopes, "open_bytes", lambda *_a: b"s" * 32)
    value = {
        "iss": "issuer",
        "exp": datetime.now(UTC) + timedelta(minutes=1),
        "sha256": base64.b64encode(hashlib.sha256(body).digest()).decode(),
    }
    value.update(claims or {})
    return body, "Bearer " + jwt.encode(value, secret, algorithm=algorithm), connection


def test_signature_exact_body(modules, monkeypatch):
    body, token, connection = signed_event(modules, monkeypatch)
    assert (
        modules.provider_recordings.verify_livekit_webhook(body, token)[0] is connection
    )
    with pytest.raises(modules.contract.AttachmentError):
        modules.provider_recordings.verify_livekit_webhook(body + b" ", token)


@pytest.mark.parametrize(
    "changes",
    [
        {"secret": b"t" * 32},
        {"claims": {"iss": "foreign"}},
        {"claims": {"exp": 1}},
        {"claims": {"sha256": "different"}},
        {"algorithm": "HS384"},
    ],
)
def test_signature_denials(modules, monkeypatch, changes):
    body, token, _ = signed_event(modules, monkeypatch, **changes)
    with pytest.raises(modules.contract.AttachmentError) as error:
        modules.provider_recordings.verify_livekit_webhook(body, token)
    assert str(error.value) == "not_found"


@pytest.mark.parametrize(
    "body", [b'{"id":"a","id":"b"}', b'{"value":NaN}', b"[]", b"x" * 65537]
)
def test_signed_malformed_body_denied(modules, monkeypatch, body):
    body, token, _ = signed_event(modules, monkeypatch, body=body)
    with pytest.raises(modules.contract.AttachmentError):
        modules.provider_recordings.verify_livekit_webhook(body, token)


def test_seal_is_scoped_and_revision_bound(modules, monkeypatch):
    created = []
    modules.rm.AttachmentSourceEnvelope.objects = SimpleNamespace(
        create=lambda **kw: created.append(row(scope_id=kw["scope"].id, **kw))
    )
    f = facts(modules)
    scope = row(id=uuid4())
    eid = modules.envelopes.seal(
        scope=scope,
        url="https://example.test/fixed?secret=never-durable-plaintext",
        creator_kind=f.creator_kind,
        creator_id=f.creator_id,
        direction=f.direction,
        source_bytes=f.byte_length,
        source_expires_at=f.expires_at,
        generation_kind=f.generation_kind,
        generation=f.generation,
        mime_type=f.mime_type,
        duration_ms=f.duration_ms,
        trace_id=f.parent_trace_id,
        span_id=f.parent_span_id,
        capture_session_id=uuid4(),
    )
    sealed = created[0]
    assert sealed.id == eid and b"never-durable-plaintext" not in sealed.ciphertext
    monkeypatch.setattr(modules.envelopes, "describe", lambda **_kw: sealed)
    assert modules.envelopes.open_url(scope_id=scope.id, envelope_id=eid).startswith(
        "https://example.test/"
    )
    with pytest.raises(modules.contract.AttachmentError):
        modules.envelopes.open_url(scope_id=uuid4(), envelope_id=eid)
    sealed.generation = '"changed"'
    with pytest.raises(modules.contract.AttachmentError):
        modules.envelopes.open_url(scope_id=scope.id, envelope_id=eid)


def setup_source(m, monkeypatch):
    f = facts(m)
    scope = row(
        id=uuid4(),
        enabled=True,
        allow_import=True,
        require_media_redaction=False,
        allow_unredacted_media_with_text_pii=False,
    )
    session = row(
        id=uuid4(),
        scope_id=scope.id,
        writer_kind=f.creator_kind,
        writer_id=f.creator_id,
        client_id=uuid4(),
        correlation_nonce=uuid4(),
    )
    envelope = row(
        id=f.source_id,
        scope_id=scope.id,
        creator_kind=f.creator_kind,
        creator_id=f.creator_id,
        generation_kind=f.generation_kind,
        generation=f.generation,
        source_bytes=f.byte_length,
        mime_type=f.mime_type,
        duration_ms=f.duration_ms,
        expires_at=f.expires_at,
        source_expires_at=f.expires_at,
        authority_generation=0,
        trace_id=f.parent_trace_id,
        span_id=f.parent_span_id,
        direction=f.direction,
        revoked_at=None,
    )
    m.am.CaptureSession.objects = Query([session])
    m.rm.AttachmentSourceEnvelope.objects = Query([envelope])
    monkeypatch.setattr(m.envelopes, "check_capture_session", Mock())
    m.bootstrap.check_session.return_value = scope
    m.bootstrap.session_policy.return_value = m.contract.CapturePolicy(
        enabled=True, allow_import=True
    )
    parent = Mock()
    monkeypatch.setattr(m.source_providers, "_parent", parent)
    return f, scope, session, envelope, parent


def test_resolution_is_metadata_only(modules, monkeypatch):
    f, scope, session, _, parent = setup_source(modules, monkeypatch)
    monkeypatch.setattr(
        modules.envelopes,
        "open_url",
        Mock(side_effect=AssertionError("no source open")),
    )
    monkeypatch.setattr(
        modules.safe_fetch,
        "inspect_metadata",
        Mock(side_effect=AssertionError("no network")),
    )
    assert (
        modules.source_providers.resolve(
            source=modules.interfaces.SourceRef(f.source_kind, f.source_id),
            scope_id=scope.id,
            importer=SimpleNamespace(kind=f.creator_kind, id=f.creator_id),
            capture_session_id=session.id,
        )
        == f
    )
    parent.assert_called_once()


@pytest.mark.parametrize(
    "change", ["tenant", "creator", "session", "revoked", "policy"]
)
def test_resolution_identity_denials(modules, monkeypatch, change):
    f, scope, session, envelope, _ = setup_source(modules, monkeypatch)
    scope_id = scope.id
    if change == "tenant":
        scope_id = uuid4()
    if change == "creator":
        envelope.creator_id = uuid4()
    if change == "session":
        session.writer_id = uuid4()
    if change == "revoked":
        envelope.revoked_at = NOW
    if change == "policy":
        modules.bootstrap.session_policy.return_value = modules.contract.CapturePolicy(
            enabled=True, allow_import=False
        )
    with pytest.raises(modules.contract.AttachmentError):
        modules.source_providers.resolve(
            source=modules.interfaces.SourceRef(f.source_kind, f.source_id),
            scope_id=scope_id,
            importer=SimpleNamespace(kind=f.creator_kind, id=f.creator_id),
            capture_session_id=session.id,
        )


def test_same_length_replacement_fenced(modules, monkeypatch):
    f, scope, session, envelope, _ = setup_source(modules, monkeypatch)
    envelope.generation = '"v2"'
    with pytest.raises(modules.contract.AttachmentError) as error:
        modules.source_providers.revalidate(
            facts=f,
            scope_id=scope.id,
            importer=SimpleNamespace(kind=f.creator_kind, id=f.creator_id),
            capture_session_id=session.id,
        )
    assert error.value.code == "snapshot_changed"


def test_locked_recheck_has_no_network(modules, monkeypatch):
    f, scope, session, _, parent = setup_source(modules, monkeypatch)
    modules.depth.value = 1
    modules.source_providers.revalidate(
        facts=f,
        scope_id=scope.id,
        importer=SimpleNamespace(kind=f.creator_kind, id=f.creator_id),
        capture_session_id=session.id,
    )
    parent.assert_not_called()


def test_provider_connection_revoked_denies_envelope_alias(modules, monkeypatch):
    f, scope, session, envelope, _ = setup_source(modules, monkeypatch)
    connection = row(
        id=uuid4(),
        scope_id=scope.id,
        provider="livekit",
        enabled=False,
        revoked_at=NOW,
        authority_generation=1,
    )
    registration = row(
        id=uuid4(),
        scope_id=scope.id,
        output_envelope_id=envelope.id,
        connection_id=connection.id,
    )
    modules.rm.AttachmentProviderConnection.objects = Query([connection])
    modules.rm.AttachmentProviderRecording.objects = Query([registration])
    with pytest.raises(modules.contract.AttachmentError):
        modules.source_providers.resolve(
            source=modules.interfaces.SourceRef(f.source_kind, f.source_id),
            scope_id=scope.id,
            importer=SimpleNamespace(kind=f.creator_kind, id=f.creator_id),
            capture_session_id=session.id,
        )


def test_bounded_read_discards_revoked_result(modules, monkeypatch):
    f = facts(modules)
    reader = modules.source_providers.SourceReader.__new__(
        modules.source_providers.SourceReader
    )
    reader.facts = f
    checks = Mock(
        side_effect=[None, modules.contract.AttachmentError("source_unavailable", 403)]
    )
    reader.verify_remote = checks
    reader._source = SimpleNamespace(
        read_range=lambda _start, _length, sink: sink.write(b"abcdefgh")
    )
    with pytest.raises(modules.contract.AttachmentError):
        reader.read_range(start=0, end=7)
    assert checks.call_count == 2


@pytest.mark.parametrize(
    "start,end", [(True, 1), (-1, 1), (3, 1), (0, 8), (0, 8 * 1024 * 1024)]
)
def test_invalid_range_before_authority_or_io(modules, start, end):
    reader = modules.source_providers.SourceReader.__new__(
        modules.source_providers.SourceReader
    )
    reader.facts = facts(modules)
    reader.verify_remote = Mock(side_effect=AssertionError)
    with pytest.raises(modules.contract.AttachmentError):
        reader.read_range(start=start, end=end)
    reader.verify_remote.assert_not_called()


@pytest.mark.parametrize("state", ["complete", "failed", "aborted", "limit_reached"])
def test_terminal_event_cannot_regress(modules, state):
    row_ = row(
        provider_state=state,
        room_digest=modules.provider_recordings._digest("room"),
        provider_updated_at=10,
    )
    assert (
        modules.provider_recordings._apply_info(
            row_, {"status": "EGRESS_ACTIVE", "roomName": "room", "updatedAt": 20}
        )
        is False
    )
    assert row_.provider_state == state
    row_.save.assert_not_called()


def test_stale_terminal_event_cannot_override_newer(modules):
    row_ = row(
        provider_state="active",
        room_digest=modules.provider_recordings._digest("room"),
        provider_updated_at=100,
    )
    assert not modules.provider_recordings._apply_info(
        row_, {"status": "EGRESS_COMPLETE", "roomName": "room", "updatedAt": 99}
    )


@pytest.mark.parametrize(
    "results",
    [
        [],
        [{}, {}],
        [None],
        [{"size": True, "duration": 10}],
        [{"size": -1, "duration": 10}],
        [{"size": 1, "duration": 0}],
    ],
)
def test_bad_output_not_fetchable(modules, monkeypatch, results):
    inspect = Mock(side_effect=AssertionError)
    monkeypatch.setattr(modules.safe_fetch, "inspect_metadata", inspect)
    assert (
        modules.provider_recordings._completion_metadata(None, {"fileResults": results})
        is None
    )
    inspect.assert_not_called()


def test_dispatch_survives_capacity_failure_and_bool_return(modules, monkeypatch):
    m = modules
    registrar = SimpleNamespace(kind="user", id=uuid4())
    scope_id, job_id = uuid4(), uuid4()
    session = row(
        id=uuid4(),
        scope_id=scope_id,
        client_id=uuid4(),
        correlation_nonce=uuid4(),
        writer_kind=registrar.kind,
        writer_id=registrar.id,
        policy_version=7,
        deletion_generation=2,
    )
    registration = row(
        id=uuid4(),
        scope_id=scope_id,
        provider_state="complete",
        dispatch_mode="auto",
        source_expires_at=NOW + timedelta(minutes=5),
        egress_id="EG_fixed",
        room_digest=m.provider_recordings._digest("room"),
        source_authority_generation=4,
        import_job_id=None,
        dispatch_due_at=NOW,
        dispatch_attempts=0,
        expires_at=NOW + timedelta(minutes=5),
        registrar_kind=registrar.kind,
        registrar_id=registrar.id,
        capture_session_client_id=session.client_id,
        correlation_nonce=session.correlation_nonce,
        trace_id=uuid4(),
        span_id="1234567890abcdef",
        source_mime_type="audio/wav",
        direction="mixed",
        recording_client_id=uuid4(),
        output_bytes=8,
        output_duration_ms=1,
        connection_id=uuid4(),
        registered_output_id=uuid4(),
    )
    m.rm.AttachmentProviderRecording.objects = Query([registration])
    m.am.CaptureSession.objects = Query([session])
    m.rm.AttachmentImportJob.objects = Query()
    imports = ModuleType("tracer.services.attachments.imports")
    imports.Importer = lambda kind, ident: SimpleNamespace(kind=kind, id=ident)
    # The completed-import seam projects an authorized body, recovers an exact
    # legacy link, then submits outside provider locks. Keep the real projector
    # and bound its external authority/ORM collaborators to this fixture.
    scope = row(id=scope_id)
    m.am.AttachmentScope.objects = Query([scope])
    live = row(id=registration.connection_id, provider="livekit", enabled=True,
               revoked_at=None, authority_generation=4)
    monkeypatch.setattr(m.provider_recordings, "_parent_expiry", lambda *_: registration.expires_at)
    monkeypatch.setattr(m.provider_recordings, "_lock_registrar", lambda *_: session)
    resolved = object()
    monkeypatch.setattr(m.source_providers, "resolve", Mock(return_value=resolved))
    monkeypatch.setattr(m.source_providers, "lock_source", Mock(return_value=(live, None, registration)))
    monkeypatch.setattr(m.source_providers, "revalidate", Mock())
    imports._check_registered_descriptor = Mock()
    imports._registered_job = Mock(return_value=None)
    def recover(candidate, caller):
        assert m.depth.value == 0 and candidate is registration
        assert caller == registrar and registration.import_job_id is None
        assert not m.rm.AttachmentImportJob.objects.exists()
        return None
    imports.recover_auto_dispatch = Mock(side_effect=recover)
    bodies = []

    def submit(_importer, **kw):
        assert m.depth.value == 0
        bodies.append(kw["body"])
        if len(bodies) == 1:
            raise m.contract.AttachmentError("throttled", 429)
        assert _importer == registrar and kw["scope_id"] == scope_id
        linked_job = row(id=job_id, scope_id=scope_id, provider_recording_id=registration.id,
                         importer_kind=_importer.kind, importer_id=_importer.id)
        m.rm.AttachmentImportJob.objects.rows.append(linked_job)
        registration.import_job_id = linked_job.id
        registration.dispatch_due_at = None
        return {"job_id": str(job_id)}, False

    imports.submit = submit
    monkeypatch.setitem(sys.modules, imports.__name__, imports)
    monkeypatch.setattr(
        sys.modules["tracer.services.attachments"], "imports", imports, raising=False
    )
    assert m.provider_recordings.dispatch_completed(now=NOW)["deferred"] == 1
    assert registration.import_job_id is None and registration.dispatch_due_at > NOW
    assert m.provider_recordings.dispatch_completed(now=NOW)["dispatched"] == 0
    assert (
        m.provider_recordings.dispatch_completed(now=registration.dispatch_due_at)[
            "dispatched"
        ]
        == 1
    )
    assert registration.import_job_id == job_id and registration.dispatch_due_at is None
    assert imports.recover_auto_dispatch.call_count == 2
    assert bodies[0] == bodies[1]
    m.wire.validate("RecordingImportRequest", bodies[0])


def test_f01_two_import_forms_are_not_modified(modules):
    schema = json.loads((BASE / "f01/contract.schema.json").read_text())
    assert schema["$defs"]["ImportRequest"]["oneOf"] == [
        {"$ref": "#/$defs/SingleAttachmentImportRequest"},
        {"$ref": "#/$defs/RecordingImportRequest"},
    ]
    assert schema["$defs"]["SingleAttachmentImportResponse"]["required"] == [
        "job_id",
        "upload_id",
        "state",
    ]


def test_view_no_default_storage_and_both_import_forms():
    source = (BASE.parents[1] / "views/attachments.py").read_text()
    assert "from_settings" not in source and "storage=storage()" not in source
    assert 'body.get("mode") != "completed_recording"' not in source
    assert "def storage_for_upload(" in source
    assert "principal.identity" in source
    import ast

    tree = ast.parse(source)
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "verify_livekit_webhook"
    ]
    assert (
        len(calls) == 1
        and isinstance(calls[0].args[0], ast.Name)
        and calls[0].args[0].id == "body"
    )


def test_capture_session_sealed_to_exact_original(modules, monkeypatch):
    m = modules
    f = facts(m)
    capture_id = uuid4()
    scope = row(id=uuid4())
    created = []
    m.rm.AttachmentSourceEnvelope.objects = SimpleNamespace(
        create=lambda **kw: created.append(row(scope_id=scope.id, **kw))
    )
    m.envelopes.seal(
        scope=scope,
        url="https://example.test/fixed",
        creator_kind=f.creator_kind,
        creator_id=f.creator_id,
        direction=f.direction,
        source_bytes=f.byte_length,
        source_expires_at=f.expires_at,
        generation_kind=f.generation_kind,
        generation=f.generation,
        mime_type=f.mime_type,
        duration_ms=f.duration_ms,
        trace_id=f.parent_trace_id,
        span_id=f.parent_span_id,
        capture_session_id=capture_id,
    )
    m.envelopes.check_capture_session(created[0], capture_id)
    with pytest.raises(m.contract.AttachmentError):
        m.envelopes.check_capture_session(created[0], uuid4())


def test_completion_persists_receipt_intent_and_original_session(modules, monkeypatch):
    m = modules
    p = m.provider_recordings
    scope = row(id=uuid4(), enabled=True, allow_import=True)
    registrar_id, capture_id = uuid4(), uuid4()
    connection = row(
        id=uuid4(),
        scope_id=scope.id,
        provider="livekit",
        enabled=True,
        revoked_at=None,
        authority_generation=4,
    )
    session = row(
        id=capture_id,
        scope_id=scope.id,
        client_id=uuid4(),
        correlation_nonce=uuid4(),
        writer_kind="user",
        writer_id=registrar_id,
        expires_at=NOW + timedelta(minutes=5),
    )
    registration = row(
        id=uuid4(),
        scope_id=scope.id,
        connection_id=connection.id,
        egress_id="EG_fixed",
        expires_at=NOW + timedelta(minutes=5),
        registrar_kind="user",
        registrar_id=registrar_id,
        capture_session_client_id=session.client_id,
        correlation_nonce=session.correlation_nonce,
        trace_id=uuid4(),
        span_id="1234567890abcdef",
        provider_state="active",
        provider_updated_at=1,
        dispatch_mode="auto",
        output_envelope_id=None,
        connection=connection,
        room_digest=p._digest("room"),
        source_authority_generation=4,
        direction="mixed",
    )
    m.am.AttachmentScope.objects = Query([scope])
    m.am.CaptureSession.objects = Query([session])
    m.rm.AttachmentProviderConnection.objects = Query([connection])
    m.rm.AttachmentProviderRecording.objects = Query([registration])
    receipts = Query()

    class Event:
        objects = receipts

        def __init__(self, **kw):
            vars(self).update(kw)
            self.scope_id = self.scope.id
            self.connection_id = self.connection.id

        def save(self, *, force_insert=False, update_fields=None):
            assert m.depth.value > 0
            if force_insert:
                assert self not in receipts.rows
                receipts.rows.append(self)
            else:
                assert self in receipts.rows and update_fields

    monkeypatch.setattr(m.rm, "AttachmentProviderEvent", Event)
    monkeypatch.setattr(p, "AttachmentProviderEvent", Event)
    # Run registered callbacks at the OUTER commit boundary, not inline at
    # enqueue time: the preexisting out-of-transaction HEAD assertion matters.
    callbacks = []
    original_atomic = p.transaction.atomic

    @contextmanager
    def atomic(*args, **kwargs):
        with original_atomic(*args, **kwargs):
            yield
        if m.depth.value == 0:
            while callbacks:
                callbacks.pop(0)()

    monkeypatch.setattr(p.transaction, "atomic", atomic)
    monkeypatch.setattr(p.transaction, "on_commit", callbacks.append)
    monkeypatch.setattr(p, "_lock_registrar", lambda *_a: session)
    monkeypatch.setattr(p, "_parent_expiry", lambda *_a: NOW + timedelta(minutes=5))
    m.bootstrap.session_policy.return_value = m.contract.CapturePolicy(
        enabled=True, allow_import=True
    )
    metadata = {
        "url": "https://example.test/fixed",
        "generation_kind": "http_strong_etag",
        "generation": '"v1"',
        "source_bytes": 8,
        "duration_ms": 1,
        "mime_type": "audio/wav",
    }

    def inspect(*_a):
        assert m.depth.value == 0
        return metadata

    observed = Mock(side_effect=inspect)
    monkeypatch.setattr(p, "_completion_metadata", observed)
    sealed = Mock(return_value=uuid4())
    monkeypatch.setattr(m.envelopes, "seal", sealed)
    event = {
        "id": "event-1",
        "event": "egress_ended",
        "egressInfo": {
            "egressId": "EG_fixed",
            "status": "EGRESS_COMPLETE",
            "roomName": "room",
            "updatedAt": 2,
            "fileResults": [{"location": metadata["url"], "size": 8, "duration": 1_000_000}],
        },
    }
    assert p.process_livekit_event(connection, event) is registration
    assert (
        registration.provider_state == "complete"
        and registration.dispatch_due_at is not None
    )
    assert len(receipts.rows) == 1
    assert sealed.call_args.kwargs["capture_session_id"] == capture_id
    assert sealed.call_args.kwargs["creator_id"] == registrar_id
    assert sealed.call_args.kwargs["authority_generation"] == 4
    # Exact receipt replay does not re-HEAD, reseal, dispatch or alter terminal state.
    assert p.process_livekit_event(connection, event) is registration
    assert observed.call_count == sealed.call_count == len(receipts.rows) == 1


@pytest.mark.parametrize("state", ["failed", "aborted", "limit_reached"])
def test_provider_failed_states_never_admit(modules, state):
    m = modules
    scope = row(id=uuid4(), enabled=True)
    connection = row(
        id=uuid4(),
        scope_id=scope.id,
        provider="livekit",
        enabled=True,
        revoked_at=None,
        authority_generation=0,
    )
    importer = row(kind="user", id=uuid4())
    registration = row(
        scope=scope,
        connection_id=connection.id,
        registered_output_id=uuid4(),
        registrar_kind=importer.kind,
        registrar_id=importer.id,
        expires_at=NOW + timedelta(minutes=1),
        provider_state=state,
        output_bytes=8,
        output_envelope_id=uuid4(),
        source_authority_generation=0,
    )
    m.am.AttachmentScope.objects = Query([scope])
    m.rm.AttachmentProviderConnection.objects = Query([connection])
    m.rm.AttachmentProviderRecording.objects = Query([registration])
    with pytest.raises(m.contract.AttachmentError):
        m.provider_recordings.completed_output(
            importer,
            scope,
            connection_id=connection.id,
            registered_output_id=registration.registered_output_id,
        )


def test_provider_generation_change_denies(modules):
    m = modules
    connection = row(
        id=uuid4(),
        scope_id=uuid4(),
        provider="livekit",
        enabled=True,
        revoked_at=None,
        authority_generation=2,
    )
    registration = row(
        connection_id=connection.id,
        scope_id=connection.scope_id,
        provider_state="complete",
        expires_at=NOW + timedelta(minutes=1),
        registrar_kind="user",
        registrar_id=uuid4(),
        source_authority_generation=1,
    )
    m.rm.AttachmentProviderConnection.objects = Query([connection])
    with pytest.raises(m.contract.AttachmentError):
        m.source_providers._provider_facts(
            registration, None, row(kind="user", id=registration.registrar_id)
        )


def job_fixture(m):
    fence = m.interfaces.LeaseFence(
        "import", uuid4(), uuid4(), 3, NOW + timedelta(minutes=1)
    )
    job = row(
        id=fence.owner_id,
        scope_id=uuid4(),
        lease_token=fence.token,
        lease_epoch=3,
        lease_until=fence.expires_at,
        deadline=fence.expires_at,
        state="importing",
        lease_phase="writing",
        finished_at=None,
        storage_binding_id=uuid4(),
        reservation_id=uuid4(),
        reservation_settled_at=None,
        reservation_charge_bytes=1024,
        reservation_expires_at=fence.expires_at,
        policy_version=1,
        deletion_generation=1,
        upload_id=None,
        recording=row(state="importing"),
    )
    m.am.AttachmentScope.objects = Query(
        [row(id=job.scope_id, policy_version=1, deletion_generation=1)]
    )
    return job, fence


@pytest.mark.parametrize(
    "field,value",
    [
        ("reservation_id", None),
        ("reservation_charge_bytes", 0),
        ("reservation_settled_at", NOW),
        ("reservation_expires_at", NOW - timedelta(seconds=1)),
        ("lease_epoch", 2),
        ("lease_token", uuid4()),
        ("lease_phase", "terminal"),
        ("state", "ready"),
        ("finished_at", NOW),
        ("storage_binding_id", None),
        ("policy_version", 2),
        ("deletion_generation", 2),
        ("lease_until", NOW - timedelta(seconds=1)),
    ],
)
def test_source_owner_reservation_and_lease_required(modules, field, value):
    job, fence = job_fixture(modules)
    setattr(job, field, value)
    with pytest.raises(modules.contract.AttachmentError):
        modules.source_providers._check_job(job, fence)


def test_source_open_forbidden_under_db_locks(modules):
    job, fence = job_fixture(modules)
    modules.depth.value = 1
    with pytest.raises(modules.contract.AttachmentError):
        modules.source_providers._check_job(job, fence)


def test_source_owner_valid_is_not_a_default_storage_lookup(modules):
    job, fence = job_fixture(modules)
    modules.source_providers._check_job(job, fence)


@pytest.mark.parametrize(
    "change", ["producer", "settled", "binding", "expired", "empty"]
)
def test_single_import_checks_both_owner_reservations(modules, change):
    job, fence = job_fixture(modules)
    upload = row(
        producer_kind="import",
        reservation_id=uuid4(),
        reservation_settled_at=None,
        reservation_charge_bytes=512,
        reservation_expires_at=fence.expires_at,
        attachment=row(
            storage_binding_id=job.storage_binding_id, state="pending_upload"
        ),
    )
    job.upload_id, job.upload = uuid4(), upload
    if change == "producer":
        upload.producer_kind = "authenticated_stream"
    if change == "settled":
        upload.reservation_settled_at = NOW
    if change == "binding":
        upload.attachment.storage_binding_id = uuid4()
    if change == "expired":
        upload.reservation_expires_at = NOW - timedelta(seconds=1)
    if change == "empty":
        upload.reservation_charge_bytes = 0
    with pytest.raises(modules.contract.AttachmentError):
        modules.source_providers._check_job(job, fence)


def test_watchdog_shuts_detached_http_response_socket(modules, monkeypatch):
    saved = {}

    class Timer:
        def __init__(self, seconds, stop):
            saved.update(seconds=seconds, stop=stop)

        def start(self):
            pass

    monkeypatch.setattr(modules.safe_fetch.threading, "Timer", Timer)
    sock = Mock()
    connection = SimpleNamespace(sock=None, _transport_socket=sock)
    modules.safe_fetch._watchdog(connection, 30)
    saved["stop"]()
    sock.shutdown.assert_called_once()
    sock.close.assert_called_once()


def test_lookup_document_contains_only_opaque_identifiers(modules):
    registration = row(
        connection_id=uuid4(),
        registered_output_id=uuid4(),
        provider_state="complete",
        import_job_id=uuid4(),
    )
    doc = modules.provider_recordings.registration_document(registration)
    assert set(doc) == {"source", "state", "job_id"}
    modules.wire.validate("ImportSource", doc["source"])
    assert "http" not in json.dumps(doc)


def test_dns_timeout_is_bounded(modules):
    import threading

    release = threading.Event()

    def stalled(*_args):
        release.wait(5)
        return []

    try:
        with pytest.raises(modules.contract.AttachmentError):
            modules.safe_fetch.resolve(
                "example.test", 443, getaddrinfo=stalled, timeout_seconds=0.01
            )
    finally:
        release.set()


def test_dns_any_private_answer_denies(modules):
    import socket

    def mixed(*_args):
        return [
            (socket.AF_INET, 1, 6, "", ("8.8.8.8", 443)),
            (socket.AF_INET, 1, 6, "", ("127.0.0.1", 443)),
        ]

    with pytest.raises(modules.contract.AttachmentError):
        modules.safe_fetch.resolve("example.test", 443, getaddrinfo=mixed)


def test_resolve_requires_pre_admission_unlocked_call(modules, monkeypatch):
    f, scope, session, _, parent = setup_source(modules, monkeypatch)
    modules.depth.value = 1
    with pytest.raises(modules.contract.AttachmentError):
        modules.source_providers.resolve(
            source=modules.interfaces.SourceRef(f.source_kind, f.source_id),
            scope_id=scope.id,
            importer=row(kind=f.creator_kind, id=f.creator_id),
            capture_session_id=session.id,
        )
    parent.assert_not_called()


@pytest.mark.parametrize(
    "producer,operation,allowed",
    [
        ("authenticated_stream", "write", True),
        ("import", "write", False),
        ("import", "read", True),
    ],
)
def test_view_upload_storage_is_historical(
    modules, monkeypatch, producer, operation, allowed
):
    import ast

    scope_id, upload_id, binding_id = uuid4(), uuid4(), uuid4()
    upload = row(
        id=upload_id,
        scope_id=scope_id,
        producer_kind=producer,
        attachment=row(storage_binding_id=binding_id),
    )
    query = Query([upload])
    manager = SimpleNamespace(select_related=lambda *_a: query)
    storage = ModuleType("tracer.services.attachments.storage")
    storage.for_binding = Mock(return_value=object())
    monkeypatch.setitem(sys.modules, storage.__name__, storage)
    tree = ast.parse((BASE.parents[1] / "views/attachments.py").read_text())
    fn = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "storage_for_upload"
    )
    namespace = {
        "AttachmentUpload": SimpleNamespace(objects=manager),
        "settings": modules.settings,
        "AttachmentError": modules.contract.AttachmentError,
    }
    exec(
        compile(ast.Module(body=[fn], type_ignores=[]), "view-storage-test", "exec"),
        namespace,
    )
    if allowed:
        namespace["storage_for_upload"](
            upload_id, scope_id=scope_id, operation=operation
        )
        storage.for_binding.assert_called_once_with(
            binding_id, operation=operation, settings=modules.settings
        )
    else:
        with pytest.raises(modules.contract.AttachmentError):
            namespace["storage_for_upload"](
                upload_id, scope_id=scope_id, operation=operation
            )
        storage.for_binding.assert_not_called()


@pytest.mark.parametrize("expiry_field", ["expires_at", "source_expires_at"])
def test_source_expiry_denies_after_clock_advance(
    modules, monkeypatch, source_clock, expiry_field
):
    f, scope, session, envelope, _ = setup_source(modules, monkeypatch)
    # Isolate each stored expiry bound without changing production TTLs.
    setattr(envelope, expiry_field, NOW + timedelta(seconds=30))
    arguments = dict(
        source=modules.interfaces.SourceRef(f.source_kind, f.source_id),
        scope_id=scope.id,
        importer=SimpleNamespace(kind=f.creator_kind, id=f.creator_id),
        capture_session_id=session.id,
    )
    source_clock.advance(timedelta(seconds=30) - timedelta(microseconds=1))
    assert modules.source_providers.resolve(**arguments).source_id == f.source_id
    source_clock.advance(timedelta(microseconds=1))
    with pytest.raises(modules.contract.AttachmentError) as error:
        modules.source_providers.resolve(**arguments)
    assert error.value.code == "source_unavailable"


@pytest.mark.parametrize(
    "expiry_field", ["lease_until", "reservation_expires_at", "deadline", "fence"]
)
def test_source_owner_expiry_denies_after_clock_advance(
    modules, source_clock, expiry_field
):
    job, fence = job_fixture(modules)
    expiry = NOW + timedelta(seconds=30)
    if expiry_field == "fence":
        fence = replace(fence, expires_at=expiry)
    else:
        setattr(job, expiry_field, expiry)
    source_clock.advance(timedelta(seconds=30) - timedelta(microseconds=1))
    modules.source_providers._check_job(job, fence)
    source_clock.advance(timedelta(microseconds=1))
    with pytest.raises(modules.contract.AttachmentError) as error:
        modules.source_providers._check_job(job, fence)
    assert error.value.code == "conflict"
