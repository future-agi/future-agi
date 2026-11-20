"""The 17 F01 attachment operations (traceAI ``spec/attachments/v1/openapi.json``).

Each view authenticates with its own credential class (see
``tracer.services.attachments.principal``): the eligible original identity,
the upload-bound write capability, or the media cookie. None uses the generic
``APIKeyAuthentication``. Every error body is exactly ``{code, request_id}``
with an F01 ``Reason``; 429 always carries ``Retry-After``. Every response is
``private, no-store``. All routes stay dark until ``FI_ATTACHMENTS_ENABLED``.
"""

import uuid
from collections.abc import Sequence
from functools import wraps

import structlog
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework.exceptions import APIException, MethodNotAllowed
from rest_framework.negotiation import BaseContentNegotiation
from rest_framework.parsers import BaseParser
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from tfc.utils.api_contracts import validated_request
from tracer.models.attachment import AttachmentScope, AttachmentUpload
from tracer.serializers.attachments import (
    AttachmentBootstrapRequestSerializer,
    AttachmentBootstrapResponseSerializer,
    AttachmentCompleteRequestSerializer,
    AttachmentEmptyRequestSerializer,
    AttachmentErrorSerializer,
    AttachmentImportRequestSerializer,
    AttachmentImportResponseSerializer,
    AttachmentImportStatusResponseSerializer,
    AttachmentLinkRequestSerializer,
    AttachmentLiveKitEventRequestSerializer,
    AttachmentOccurrencePageSerializer,
    AttachmentOccurrenceSerializer,
    AttachmentProviderRegistrationRequestSerializer,
    AttachmentProviderRegistrationResponseSerializer,
    AttachmentRecordingManifestSerializer,
    AttachmentResolveRequestSerializer,
    AttachmentResolveResponseSerializer,
    AttachmentSourceEnvelopeRequestSerializer,
    AttachmentSourceEnvelopeResponseSerializer,
    AttachmentStatusResponseSerializer,
    AttachmentUploadRequestSerializer,
    AttachmentUploadResponseSerializer,
)
from tracer.services.attachments import (
    bootstrap as broker,
)
from tracer.services.attachments import (
    imports,
    manifests,
    occurrences,
    playback,
    principal,
    retention,
    uploads,
    wire,
)
from tracer.services.attachments.authority import LiveAuthority
from tracer.services.attachments.contract import AttachmentError
from tracer.views import attachment_media

logger = structlog.get_logger(__name__)

TAG = "Attachments"
NO_STORE = "private, no-store"
READ_BYTES = wire.MAX_JSON_BYTES
# A 204 has no body. Declaring it as an empty string keeps generated clients on
# the 204 entry instead of falling back to the default error schema.
NO_CONTENT = openapi.Schema(type=openapi.TYPE_STRING, max_length=0, x_nullable=True)
ISSUANCE_HEADERS = [
    openapi.Parameter(
        "Origin",
        openapi.IN_HEADER,
        type=openapi.TYPE_STRING,
        required=True,
        description="Exact configured HTTPS media origin; no ambient application-cookie authentication.",
    ),
    openapi.Parameter(
        "Authorization",
        openapi.IN_HEADER,
        type=openapi.TYPE_STRING,
        required=True,
        description="Explicit application Bearer access token (original AuthToken row).",
    ),
]
MEDIA_SESSION_HEADER = openapi.Parameter(
    playback.SESSION_HEADER,
    openapi.IN_HEADER,
    type=openapi.TYPE_STRING,
    format="uuid",
    required=True,
    description="Non-secret session UUID returned by this caller's exchange; no latest-session fallback.",
)
IDEMPOTENCY_HEADER = openapi.Parameter(
    "Idempotency-Key",
    openapi.IN_HEADER,
    type=openapi.TYPE_STRING,
    required=True,
    description="F01 Token; the same key with an identical body replays.",
)


def _query(name, required, description):
    return openapi.Parameter(
        name,
        openapi.IN_QUERY,
        type=openapi.TYPE_STRING,
        required=required,
        description=description,
    )


PARENT_QUERY = [
    _query("trace_id", True, "Expected parent trace (F01 TraceInput)."),
    _query("span_id", True, "Expected parent span (F01 SpanID)."),
]
OCCURRENCE_QUERY = [
    _query("trace_id", True, "Parent trace (F01 TraceInput)."),
    _query("span_id", False, "Parent span (F01 SpanID)."),
    _query("cursor", False, "Opaque F01 Token cursor."),
    openapi.Parameter(
        "limit", openapi.IN_QUERY, type=openapi.TYPE_INTEGER, minimum=1, maximum=100
    ),
]
ERRORS = {
    status: openapi.Response("F01 Error", AttachmentErrorSerializer)
    for status in (400, 401, 403, 404, 409, 413, 429)
}


def authority():
    """The production read authority. Tests replace this single seam."""
    return LiveAuthority()


def admission_binding(*, operation):
    from tracer.services.attachments.readiness import require_new_admission

    return require_new_admission(operation=operation, settings=settings)


def storage_for_upload(upload_id, *, scope_id, operation):
    """Existing content always resolves its protected historical binding.

    Call only after request authorization. Status/metadata never call this.
    New-owner selection belongs inside initialize/submit after replay lookup.
    """
    from tracer.services.attachments.storage import for_binding

    upload = (
        AttachmentUpload.objects.select_related("attachment")
        .filter(pk=upload_id, scope_id=scope_id)
        .first()
    )
    if upload is None:
        raise AttachmentError("not_found", 404)
    if operation == "write" and upload.producer_kind != "authenticated_stream":
        raise AttachmentError("not_found", 404)
    if upload.attachment.storage_binding_id is None:
        raise AttachmentError("storage_unverified", 503)
    return for_binding(
        upload.attachment.storage_binding_id, operation=operation, settings=settings
    )


def media_origin():
    return getattr(settings, "FI_ATTACHMENTS_MEDIA_ORIGIN", "")


def _request_id(request):
    supplied = request.headers.get("X-Request-Id", "")
    try:
        value = uuid.UUID(supplied)
        if str(value) == supplied and value.int:
            return supplied
    except ValueError:
        pass
    return str(uuid.uuid4())


def _private(response):
    response["Cache-Control"] = NO_STORE
    response["X-Content-Type-Options"] = "nosniff"
    response["Referrer-Policy"] = "no-referrer"
    return response


def error_response(request, error):
    status, code = wire.public_error(error)
    response = JsonResponse(
        {"code": code, "request_id": _request_id(request)}, status=status
    )
    if status == 429:
        response["Retry-After"] = "1"
    if status == 401:
        response["WWW-Authenticate"] = "Bearer"
    return _private(response)


def json_response(document, status=200):
    return _private(JsonResponse(document, status=status))


def empty_response(status):
    return _private(HttpResponse(status=status))


class StrictJSONParser(BaseParser):
    """Bounded JSON that rejects duplicate members and non-JSON numbers."""

    media_type = "application/json"

    def parse(self, stream, media_type=None, parser_context=None):
        view = (parser_context or {}).get("view")
        limit = getattr(view, "max_body_bytes", READ_BYTES)
        raw = stream.read(limit + 1) if stream is not None else b""
        if len(raw) > limit:
            raise AttachmentError("too_large", 413)
        return wire.strict_json(raw)


class OctetStreamParser(BaseParser):
    """Documentation only; content bodies are read from the raw stream."""

    media_type = "application/octet-stream"

    def parse(self, stream, media_type=None, parser_context=None):
        return None


class NoNegotiation(BaseContentNegotiation):
    """Responses are built explicitly; a media Accept header must not 406."""

    def select_parser(self, request, parsers):
        return parsers[0] if parsers else None

    def select_renderer(self, request, renderers, format_suffix=None):
        return renderers[0], renderers[0].media_type


class AttachmentView(APIView):
    """Shared F01 envelope: own authentication, fixed errors, no-store."""

    authentication_classes = ()
    permission_classes = (AllowAny,)
    content_negotiation_class = NoNegotiation
    parser_classes: Sequence[type[BaseParser]] = (StrictJSONParser,)
    max_body_bytes = READ_BYTES

    def handle_exception(self, exc):
        if isinstance(exc, AttachmentError):
            return error_response(self.request, exc)
        if isinstance(exc, MethodNotAllowed):
            response = error_response(self.request, AttachmentError("invalid_request"))
            response.status_code = 405
            return response
        if isinstance(exc, APIException):
            return error_response(self.request, AttachmentError("invalid_request"))
        logger.warning(
            "attachment_request_failed",
            view=type(self).__name__,
            error_type=type(exc).__name__,
        )
        return error_response(self.request, AttachmentError("internal", 503))

    def initial(self, request, *args, **kwargs):
        # Dark routes answer 404 before any body is parsed or validated.
        super().initial(request, *args, **kwargs)
        self.enabled()

    def enabled(self):
        if not getattr(settings, "FI_ATTACHMENTS_ENABLED", False):
            raise AttachmentError("not_found", 404)

    def body(self, request, name):
        """The strictly parsed JSON body, validated against the F01 schema.

        F01 schema validation runs after authentication. Transport-level parse
        errors (content type, duplicate members, size) can surface earlier as
        a 400 with a fixed code; they reveal nothing about stored state.
        """
        content_type = request.META.get("CONTENT_TYPE", "").split(";")[0].strip()
        if content_type != "application/json":
            raise AttachmentError("invalid_request")
        return wire.validate(name, request.data)

    def key(self, request):
        return wire.idempotency_key(request.headers.get("Idempotency-Key"))

    def parent_query(self, request):
        trace_id = request.query_params.get("trace_id")
        span_id = request.query_params.get("span_id")
        if not wire.is_valid("TraceInput", trace_id) or not wire.is_valid(
            "SpanID", span_id
        ):
            raise AttachmentError("invalid_request")
        return {"trace_id": trace_id, "span_id": span_id}

    def viewer(self, request, project_id):
        identity = principal.identity(request)
        principal.require_application_session(identity)
        return principal.project_viewer(identity, principal.project_scope(project_id))

    def writer(self, request, project_id):
        identity = principal.identity(request)
        return principal.project_writer(identity, principal.project_scope(project_id))


class AttachmentSessionView(AttachmentView):
    @validated_request(
        operation_id="bootstrapAttachmentSession",
        tags=[TAG],
        manual_parameters=[IDEMPOTENCY_HEADER],
        request_serializer=AttachmentBootstrapRequestSerializer,
        strict_request_validation=False,
        responses={200: AttachmentBootstrapResponseSerializer, **ERRORS},
    )
    def post(self, request):
        self.enabled()
        self.key(request)
        identity = principal.identity(request)
        body = self.body(request, "BootstrapRequest")
        writer = principal.bootstrap_writer(request, identity, body)
        _, document = broker.bootstrap(writer, body)
        return json_response(document)


class AttachmentUploadView(AttachmentView):
    @validated_request(
        operation_id="initializeAttachmentUpload",
        tags=[TAG],
        manual_parameters=[IDEMPOTENCY_HEADER],
        request_serializer=AttachmentUploadRequestSerializer,
        strict_request_validation=False,
        responses={201: AttachmentUploadResponseSerializer, **ERRORS},
    )
    def post(self, request, project_id):
        self.enabled()
        key = self.key(request)
        writer, scope = self.writer(request, project_id)
        body = self.body(request, "UploadRequest")
        upload, token, expires_at = uploads.initialize(
            writer, scope_id=scope.id, body=body, key=key, binding_id=None
        )
        return json_response(
            uploads.upload_response(upload, token, expires_at), status=201
        )


def _upload_scope(project_id):
    project = principal.project_scope(project_id)
    scope = AttachmentScope.objects.filter(project_id=project.pk).first()
    if scope is None:
        raise AttachmentError("not_found", 404)
    return scope


def _uuid(value):
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise AttachmentError("not_found", 404) from None


class AttachmentContentView(AttachmentView):
    parser_classes = (OctetStreamParser,)

    @validated_request(
        operation_id="streamAttachmentContent",
        tags=[TAG],
        request_body=openapi.Schema(type=openapi.TYPE_STRING, format="binary"),
        responses={200: AttachmentStatusResponseSerializer, **ERRORS},
    )
    def put(self, request, project_id, upload_id):
        self.enabled()
        token = principal.capability_token(request)
        scope = _upload_scope(project_id)
        length = request.META.get("CONTENT_LENGTH", "")
        if not length.isdigit():
            raise AttachmentError("length_mismatch")
        content_type = request.META.get("CONTENT_TYPE", "").split(";")[0].strip()
        if content_type != "application/octet-stream":
            raise AttachmentError("invalid_request")
        stream = request._request

        def chunks():
            remaining = int(length)
            while remaining > 0:
                chunk = stream.read(min(uploads.CHUNK_BYTES, remaining))
                if not chunk:
                    return
                remaining -= len(chunk)
                yield chunk
            if stream.read(1):
                raise AttachmentError("too_large", 413)

        broker.authorize(
            token, operation="upload", scope_id=scope.id, upload_id=_uuid(upload_id)
        )
        document = uploads.transfer(
            token,
            scope_id=scope.id,
            upload_id=_uuid(upload_id),
            chunks=chunks(),
            storage=storage_for_upload(
                _uuid(upload_id), scope_id=scope.id, operation="write"
            ),
            content_length=int(length),
        )
        return json_response(document)


class AttachmentCompleteView(AttachmentView):
    @validated_request(
        operation_id="completeAttachmentUpload",
        tags=[TAG],
        manual_parameters=[IDEMPOTENCY_HEADER],
        request_serializer=AttachmentCompleteRequestSerializer,
        strict_request_validation=False,
        responses={202: AttachmentStatusResponseSerializer, **ERRORS},
    )
    def post(self, request, project_id, upload_id):
        self.enabled()
        self.key(request)
        token = principal.capability_token(request)
        scope = _upload_scope(project_id)
        body = self.body(request, "CompleteRequest")
        upload = uploads.complete(
            token, scope_id=scope.id, upload_id=_uuid(upload_id), body=body
        )
        return json_response(uploads.status_document(upload), status=202)


class AttachmentAbortView(AttachmentView):
    @validated_request(
        operation_id="abortAttachmentUpload",
        tags=[TAG],
        manual_parameters=[IDEMPOTENCY_HEADER],
        request_serializer=AttachmentCompleteRequestSerializer,
        strict_request_validation=False,
        responses={200: AttachmentStatusResponseSerializer, **ERRORS},
    )
    def post(self, request, project_id, upload_id):
        self.enabled()
        self.key(request)
        token = principal.capability_token(request)
        scope = _upload_scope(project_id)
        body = self.body(request, "CompleteRequest")
        upload = uploads.abort(
            token, scope_id=scope.id, upload_id=_uuid(upload_id), body=body
        )
        return json_response(uploads.status_document(upload))


class AttachmentUploadStatusView(AttachmentView):
    @validated_request(
        operation_id="attachmentUploadStatus",
        tags=[TAG],
        responses={200: AttachmentStatusResponseSerializer, **ERRORS},
    )
    def get(self, request, project_id, upload_id):
        self.enabled()
        token = principal.capability_token(request)
        scope = _upload_scope(project_id)
        return json_response(
            uploads.status(token, scope_id=scope.id, upload_id=_uuid(upload_id))
        )


class AttachmentImportView(AttachmentView):
    @validated_request(
        operation_id="importAttachment",
        tags=[TAG],
        manual_parameters=[IDEMPOTENCY_HEADER],
        request_serializer=AttachmentImportRequestSerializer,
        strict_request_validation=False,
        responses={202: AttachmentImportResponseSerializer, **ERRORS},
    )
    def post(self, request, project_id):
        """Admit a completed-recording import with ORIGINAL credentials only.

        A write capability never reaches this route (``principal.identity``
        accepts app sessions and API keys only). The source is an opaque
        envelope or a registered provider output: no URL, path or credential
        is ever in the body. Admission (scope ``allow_import`` plus deny-wins
        privacy, exact policy and deletion generation, atomic reservations)
        runs before any byte is read; the worker does the transfer.

        Both canonical forms use metadata-only source resolution before owner
        capacity reservation. They share the same original-identity checks.
        """
        self.enabled()
        self.key(request)
        writer, scope = self.writer(request, project_id)
        body = self.body(request, "ImportRequest")
        broker.check_writer(writer, scope)
        document, _ = imports.submit(
            imports.Importer(writer.kind, writer.id), scope_id=scope.id, body=body
        )
        return json_response(document, status=202)


class AttachmentImportStatusView(AttachmentView):
    @validated_request(
        operation_id="getAttachmentImportStatus",
        tags=[TAG],
        responses={200: AttachmentImportStatusResponseSerializer, **ERRORS},
    )
    def get(self, request, project_id, job_id):
        self.enabled()
        writer, scope = self.writer(request, project_id)
        broker.check_writer(writer, scope)
        # Content-free status, visible only to the original importer.
        document = imports.status(
            imports.Importer(writer.kind, writer.id),
            scope_id=scope.id,
            job_id=_uuid(job_id),
        )
        return json_response(document)


class AttachmentOccurrencesView(AttachmentView):
    @validated_request(
        operation_id="listAttachmentOccurrences",
        tags=[TAG],
        manual_parameters=OCCURRENCE_QUERY,
        responses={200: AttachmentOccurrencePageSerializer, **ERRORS},
    )
    def get(self, request, project_id):
        self.enabled()
        viewer = self.viewer(request, project_id)
        params = request.query_params
        trace_id = params.get("trace_id")
        span_id = params.get("span_id")
        cursor = params.get("cursor")
        limit = params.get("limit", "50")
        if (
            not wire.is_valid("TraceInput", trace_id)
            or (span_id is not None and not wire.is_valid("SpanID", span_id))
            or (cursor is not None and not wire.is_valid("Token", cursor))
            or not limit.isdigit()
        ):
            raise AttachmentError("invalid_request")
        page = occurrences.list_occurrences(
            viewer,
            authority(),
            trace_id=trace_id,
            parent_span_id=span_id,
            limit=int(limit),
            cursor=cursor,
        )
        return json_response(page)

    @validated_request(
        operation_id="linkAttachmentOccurrence",
        tags=[TAG],
        manual_parameters=[IDEMPOTENCY_HEADER],
        request_serializer=AttachmentLinkRequestSerializer,
        strict_request_validation=False,
        responses={201: AttachmentOccurrenceSerializer, **ERRORS},
    )
    def post(self, request, project_id):
        self.enabled()
        self.key(request)
        viewer = self.viewer(request, project_id)
        body = self.body(request, "LinkRequest")
        return json_response(occurrences.link(viewer, authority(), body), status=201)


class AttachmentOccurrenceView(AttachmentView):
    @validated_request(
        operation_id="deleteAttachmentOccurrence",
        tags=[TAG],
        manual_parameters=PARENT_QUERY,
        responses={204: openapi.Response("Deleted", NO_CONTENT), **ERRORS},
    )
    def delete(self, request, project_id, occurrence_id):
        self.enabled()
        viewer = self.viewer(request, project_id)
        parent = occurrences.Parent.parse(self.parent_query(request))
        retention.delete_occurrence(
            viewer, authority(), occurrence_id=occurrence_id, parent=parent
        )
        return empty_response(204)


class AttachmentResolveView(AttachmentView):
    @validated_request(
        operation_id="resolveAttachmentOccurrences",
        tags=[TAG],
        manual_parameters=[*ISSUANCE_HEADERS, MEDIA_SESSION_HEADER],
        request_serializer=AttachmentResolveRequestSerializer,
        strict_request_validation=False,
        responses={200: AttachmentResolveResponseSerializer, **ERRORS},
    )
    def post(self, request, project_id):
        self.enabled()
        viewer = self.viewer(request, project_id)
        attachment_media.same_origin(request, media_origin(), issuance=True, csrf=False)
        body = self.body(request, "ResolveRequest")
        return json_response(
            {
                "items": playback.resolve(
                    viewer,
                    authority(),
                    body["items"],
                    media_session_id=request.headers.get(playback.SESSION_HEADER),
                )
            }
        )


class AttachmentManifestView(AttachmentView):
    max_body_bytes = wire.MAX_MANIFEST_BYTES

    @validated_request(
        operation_id="updateRecordingManifest",
        tags=[TAG],
        manual_parameters=[IDEMPOTENCY_HEADER],
        request_serializer=AttachmentRecordingManifestSerializer,
        strict_request_validation=False,
        responses={204: openapi.Response("Accepted", NO_CONTENT), **ERRORS},
    )
    def put(self, request, project_id, recording_id):
        self.enabled()
        key = self.key(request)
        token = principal.capability_token(request)
        scope = _upload_scope(project_id)
        body = self.body(request, "RecordingManifest")
        manifests.put_manifest(
            token,
            scope_id=scope.id,
            recording_id=_uuid(recording_id),
            body=body,
            key=key,
        )
        return empty_response(204)

    @validated_request(
        operation_id="readRecordingManifest",
        tags=[TAG],
        manual_parameters=PARENT_QUERY,
        responses={200: AttachmentRecordingManifestSerializer, **ERRORS},
    )
    def get(self, request, project_id, recording_id):
        self.enabled()
        viewer = self.viewer(request, project_id)
        return json_response(
            manifests.read_manifest(
                viewer,
                authority(),
                recording_id=recording_id,
                parent=self.parent_query(request),
            )
        )


class AttachmentMediaSessionView(AttachmentView):
    @validated_request(
        operation_id="exchangeAttachmentMediaSession",
        tags=[TAG],
        manual_parameters=ISSUANCE_HEADERS,
        request_serializer=AttachmentEmptyRequestSerializer,
        strict_request_validation=False,
        responses={
            204: openapi.Response(
                "Independent session cookie set; fixed expiry, no predecessor revocation",
                NO_CONTENT,
                headers={
                    playback.SESSION_HEADER: {
                        "type": "string",
                        "format": "uuid",
                        "description": "Required non-secret handle for resolve.",
                    },
                    playback.EXPIRY_HEADER: {
                        "type": "string",
                        "format": "date-time",
                        "description": "Required fixed session expiry.",
                    },
                    "Set-Cookie": {
                        "type": "string",
                        "description": "Host-only fi_media_session_v2; Secure; HttpOnly; SameSite=Strict; Path=/tracer/v1/attachment-media/{media_session_id}/. Secret never appears in JSON or selector headers.",
                    },
                },
            ),
            **ERRORS,
        },
    )
    def post(self, request):
        self.enabled()
        origin = media_origin()
        if not origin:
            raise AttachmentError("not_found", 404)
        viewer = principal.media_viewer(request, principal.identity(request))
        response = attachment_media.exchange_response(
            request, viewer, authority(), origin=origin
        )
        if response.status_code == 429:
            response = error_response(request, AttachmentError("throttled", 429))
            response["Retry-After"] = "60"
        elif response.status_code != 204:
            raise AttachmentError("not_found", 404)
        return response


class AttachmentMediaView(AttachmentView):
    """Cookie-authenticated byte gateway. Errors carry no body."""

    @validated_request(
        operation_id="getAttachmentMedia",
        tags=[TAG],
        responses={
            200: openapi.Response(
                "Media bytes", openapi.Schema(type=openapi.TYPE_FILE)
            ),
            206: openapi.Response(
                "Partial media bytes", openapi.Schema(type=openapi.TYPE_FILE)
            ),
            304: openapi.Response("Not modified"),
            404: openapi.Response("Not found"),
            416: openapi.Response("Range not satisfiable"),
            429: openapi.Response("Egress limit"),
        },
    )
    def get(self, request, media_session_id, occurrence_id, grant_id):
        return self._serve(request, media_session_id, occurrence_id, grant_id)

    @validated_request(
        operation_id="headAttachmentMedia",
        tags=[TAG],
        responses={
            200: openapi.Response("Media headers"),
            304: openapi.Response("Not modified"),
            404: openapi.Response("Not found"),
            429: openapi.Response("Egress limit"),
        },
    )
    def head(self, request, media_session_id, occurrence_id, grant_id):
        return self._serve(request, media_session_id, occurrence_id, grant_id)

    def _serve(self, request, media_session_id, occurrence_id, grant_id):
        origin = media_origin()
        if not getattr(settings, "FI_ATTACHMENTS_ENABLED", False) or not origin:
            return attachment_media.private(HttpResponse(status=404))
        return attachment_media.media_response(
            request._request,
            media_session_id=media_session_id,
            occurrence_id=occurrence_id,
            grant_token=grant_id,
            authority=authority(),
            storage=None,  # media_response resolves the authorized asset's binding
            origin=origin,
        )

    def handle_exception(self, exc):
        logger.warning("attachment_media_failed", error_type=type(exc).__name__)
        return attachment_media.private(HttpResponse(status=404))


def _invalid_control_body(_errors):
    raise AttachmentError("invalid_request")


def _authorized_control_writer(post):
    """The shared validator reads request.data; check the writer before entering it."""

    @wraps(post)
    def authorized(view, request, project_id):
        writer, scope = view.writer(request, project_id)
        broker.check_writer(writer, scope)
        return post(view, request, project_id, writer=writer, scope=scope)

    return authorized


class AttachmentProviderRegistrationView(AttachmentView):
    """Product control route; not an addition to F01 import payloads."""

    @_authorized_control_writer
    @validated_request(
        request_serializer=AttachmentProviderRegistrationRequestSerializer,
        reject_unknown_fields=True,
        validation_error_response=_invalid_control_body,
        responses={201: AttachmentProviderRegistrationResponseSerializer},
    )
    def post(self, request, project_id, *, writer, scope):
        from tracer.services.attachments import provider_recordings

        body = request.data
        required = {
            "connection_id",
            "egress_id",
            "room_name",
            "recording_id",
            "association",
            "direction",
        }
        if (
            not isinstance(body, dict)
            or not required <= set(body)
            or set(body) - required - {"dispatch_mode"}
        ):
            raise AttachmentError("invalid_request")
        row = provider_recordings.register(
            writer,
            scope_id=scope.id,
            connection_id=_uuid(body["connection_id"]),
            egress_id=body["egress_id"],
            room_name=body["room_name"],
            recording_id=body["recording_id"],
            association=body["association"],
            direction=body["direction"],
            dispatch_mode=body.get("dispatch_mode", "auto"),
        )
        return json_response(
            provider_recordings.registration_document(
                row, registrar=writer, scope_id=scope.id
            ),
            status=201,
        )


class AttachmentProviderLookupView(AttachmentView):
    @swagger_auto_schema(
        responses={200: AttachmentProviderRegistrationResponseSerializer}
    )
    def get(self, request, project_id, registered_output_id):
        from tracer.services.attachments import provider_recordings

        writer, scope = self.writer(request, project_id)
        broker.check_writer(writer, scope)
        row = provider_recordings.lookup(
            writer, scope_id=scope.id, registered_output_id=_uuid(registered_output_id)
        )
        return json_response(
            provider_recordings.registration_document(
                row, registrar=writer, scope_id=scope.id
            )
        )


class AttachmentSourceEnvelopeView(AttachmentView):
    """Resolve a registered source to its sealed reference; never accept a URL."""

    @_authorized_control_writer
    @validated_request(
        request_serializer=AttachmentSourceEnvelopeRequestSerializer,
        reject_unknown_fields=True,
        validation_error_response=_invalid_control_body,
        responses={200: AttachmentSourceEnvelopeResponseSerializer},
    )
    def post(self, request, project_id, *, writer, scope):
        from tracer.models.attachment import CaptureSession
        from tracer.services.attachments import provider_recordings, source_providers
        from tracer.services.attachments.interfaces import SourceRef

        body = request.data
        if not isinstance(body, dict) or set(body) != {"source"}:
            raise AttachmentError("invalid_request")
        source = wire.validate("ImportSource", body["source"])
        if source["kind"] == "RemoteUrlEnvelope":
            return json_response(
                provider_recordings.inspect_envelope(
                    writer, scope_id=scope.id, envelope_id=_uuid(source["envelope_id"])
                )
            )
        row = provider_recordings.completed_output(
            writer,
            scope,
            connection_id=_uuid(source["connection_id"]),
            registered_output_id=_uuid(source["registered_output_id"]),
        )
        session = CaptureSession.objects.filter(
            scope_id=scope.id,
            client_id=row.capture_session_client_id,
            correlation_nonce=row.correlation_nonce,
            writer_kind=writer.kind,
            writer_id=writer.id,
        ).first()
        if session is None:
            raise AttachmentError("source_unavailable", 403)
        source_providers.resolve(
            source=SourceRef("ProviderFile", row.id),
            scope_id=scope.id,
            importer=writer,
            capture_session_id=session.id,
        )
        return json_response(
            {
                "source": {
                    "kind": "RemoteUrlEnvelope",
                    "envelope_id": str(row.output_envelope_id),
                }
            }
        )


class _VerifiedWebhookRequest:
    """Validate the verifier's exact event, not a second parse of the request.

    Keep the rest of the DRF request available to the common validator without
    setting its private parser caches or attempting to rewind the raw stream.
    """

    def __init__(self, request, event):
        self._original = request
        self.data = event

    def __getattr__(self, name):
        return getattr(self._original, name)


def _verified_livekit_request(post):
    @wraps(post)
    def verified(view, request):
        from tracer.services.attachments import provider_recordings

        raw = request._request.read(provider_recordings.WEBHOOK_MAX_BYTES + 1)
        connection, event = provider_recordings.verify_livekit_webhook(
            raw, request.headers.get("Authorization")
        )
        return post(
            view, _VerifiedWebhookRequest(request, event), connection=connection
        )

    return verified


class AttachmentLiveKitEventView(AttachmentView):
    parser_classes = (OctetStreamParser,)

    @_verified_livekit_request
    @validated_request(
        request_serializer=AttachmentLiveKitEventRequestSerializer,
        validation_error_response=_invalid_control_body,
        responses={204: ""},  # Do not infer a response from the request schema.
        operation_description=(
            "Signed LiveKit provider JSON (at most 65536 original bytes). "
            "The Authorization JWT signature and SHA-256 body claim are verified "
            "before strict JSON decoding; never normalize or reserialize the body. "
            "Only matching registered egress events advance recording state."
        ),
    )
    def post(self, request, *, connection):
        from tracer.services.attachments import provider_recordings

        # Extensions are signed, too: dispatch the verified event, never DRF's
        # coerced/filtered serializer output or a reserialization of the body.
        provider_recordings.process_livekit_event(connection, request.data)
        return empty_response(204)
