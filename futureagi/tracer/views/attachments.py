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

import structlog
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from drf_yasg import openapi
from rest_framework.exceptions import APIException, MethodNotAllowed
from rest_framework.negotiation import BaseContentNegotiation
from rest_framework.parsers import BaseParser
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from tfc.utils.api_contracts import validated_request
from tracer.models.attachment import AttachmentScope
from tracer.serializers.attachments import (
    AttachmentBootstrapRequestSerializer,
    AttachmentBootstrapResponseSerializer,
    AttachmentCompleteRequestSerializer,
    AttachmentEmptyRequestSerializer,
    AttachmentErrorSerializer,
    AttachmentImportRequestSerializer,
    AttachmentImportStatusResponseSerializer,
    AttachmentLinkRequestSerializer,
    AttachmentOccurrencePageSerializer,
    AttachmentOccurrenceSerializer,
    AttachmentRecordingManifestSerializer,
    AttachmentResolveRequestSerializer,
    AttachmentResolveResponseSerializer,
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


def storage():
    from tracer.services.attachments.storage import from_settings

    return from_settings(settings)


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
        project = principal.project_scope(project_id)
        return principal.project_viewer(principal.identity(request), project)

    def writer(self, request, project_id):
        project = principal.project_scope(project_id)
        return principal.project_writer(principal.identity(request), project)


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
            writer, scope_id=scope.id, body=body, key=key
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

        document = uploads.transfer(
            token,
            scope_id=scope.id,
            upload_id=_uuid(upload_id),
            chunks=chunks(),
            storage=storage(),
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
        responses={202: openapi.Response("Import accepted"), **ERRORS},
    )
    def post(self, request, project_id):
        """Admit a completed-recording import with ORIGINAL credentials only.

        A write capability never reaches this route (``principal.identity``
        accepts app sessions and API keys only). The source is an opaque
        envelope or a registered provider output: no URL, path or credential
        is ever in the body. Admission (scope ``allow_import`` plus deny-wins
        privacy, exact policy and deletion generation, atomic reservations)
        runs before any byte is read; the worker does the transfer.

        A single-attachment import has no trusted resolver deployed and is
        refused identically for every well-formed request.
        """
        self.enabled()
        self.key(request)
        writer, scope = self.writer(request, project_id)
        body = self.body(request, "ImportRequest")
        broker.check_writer(writer, scope)
        if body.get("mode") != "completed_recording":
            raise AttachmentError("source_unavailable", 403)
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
        request_serializer=AttachmentResolveRequestSerializer,
        strict_request_validation=False,
        responses={200: AttachmentResolveResponseSerializer, **ERRORS},
    )
    def post(self, request, project_id):
        self.enabled()
        viewer = self.viewer(request, project_id)
        body = self.body(request, "ResolveRequest")
        return json_response(
            {"items": playback.resolve(viewer, authority(), body["items"])}
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
        request_serializer=AttachmentEmptyRequestSerializer,
        strict_request_validation=False,
        responses={204: openapi.Response("Media cookie set", NO_CONTENT), **ERRORS},
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
        if response.status_code != 204:
            raise AttachmentError("not_found", 404)
        return response


class AttachmentMediaView(AttachmentView):
    """Cookie-authenticated byte gateway. Errors carry no body."""

    @validated_request(
        operation_id="getAttachmentMedia",
        tags=[TAG],
        responses={
            200: openapi.Response("Media bytes"),
            206: openapi.Response("Partial media bytes"),
            304: openapi.Response("Not modified"),
            404: openapi.Response("Not found"),
            416: openapi.Response("Range not satisfiable"),
            429: openapi.Response("Egress limit"),
        },
    )
    def get(self, request, occurrence_id, grant_id):
        return self._serve(request, occurrence_id, grant_id)

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
    def head(self, request, occurrence_id, grant_id):
        return self._serve(request, occurrence_id, grant_id)

    def _serve(self, request, occurrence_id, grant_id):
        origin = media_origin()
        if not getattr(settings, "FI_ATTACHMENTS_ENABLED", False) or not origin:
            return attachment_media.private(HttpResponse(status=404))
        return attachment_media.media_response(
            request._request,
            occurrence_id=occurrence_id,
            grant_token=grant_id,
            authority=authority(),
            storage=storage(),
            origin=origin,
        )

    def handle_exception(self, exc):
        logger.warning("attachment_media_failed", error_type=type(exc).__name__)
        return attachment_media.private(HttpResponse(status=404))
