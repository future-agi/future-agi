import grpc
import structlog
from asgiref.sync import sync_to_async
from django.conf import settings
from django_socio_grpc import generics
from opentelemetry.proto.collector.trace.v1 import (
    trace_service_pb2,
    trace_service_pb2_grpc,
)
from opentelemetry.proto.collector.trace.v1.trace_service_pb2_grpc import (
    TraceServiceServicer,
)
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from rest_framework.permissions import IsAuthenticated

logger = structlog.get_logger(__name__)
from tfc.middleware.workspace_context import get_current_organization
from tfc.utils.error_codes import get_error_message
from tfc.utils.payload_storage import PAYLOAD_DEFAULT_TTL, payload_storage
from tracer.utils.trace_ingestion import bulk_create_observation_span_task


class ObservationSpanService(generics.GenericService, TraceServiceServicer):
    """
    gRPC Service for Observation Span related actions.
    This service implements the OTLP/gRPC protocol for traces.
    """

    permission_classes = [IsAuthenticated]

    class Meta:
        pb2_grpc_module = trace_service_pb2_grpc
        registration_function = "add_TraceServiceServicer_to_server"

    @sync_to_async
    def _get_user_details(self, user):
        if not user or not hasattr(user, "organization"):
            return None, None
        return user.id, (get_current_organization() or user.organization).id

    async def Export(
        self,
        request: trace_service_pb2.ExportTraceServiceRequest,
        context: grpc.aio.RpcContext,
    ) -> trace_service_pb2.ExportTraceServiceResponse:
        """
        gRPC method to create ObservationSpans from OTEL data.
        """
        try:
            user = context.user
            # Workspace is resolved during authentication and stored on context
            # or in thread-local storage.
            from tfc.middleware.workspace_context import get_current_workspace

            workspace = getattr(context, "workspace", None) or get_current_workspace()
            workspace_id = str(workspace.id) if workspace else None

            user_id, organization_id = (
                user.id,
                (get_current_organization() or user.organization).id,
            )

            if not user_id or not organization_id:
                raise PermissionDenied("User has no organization.")

            # Ingestion rate limit check
            try:
                from ee.usage.services.rate_limiter import RateLimiter
            except ImportError:
                RateLimiter = None

            if RateLimiter is not None:
                rl_result = await sync_to_async(RateLimiter.check)(
                    str(organization_id), "ingestion"
                )
                if not rl_result.allowed:
                    await context.abort(
                        grpc.StatusCode.RESOURCE_EXHAUSTED, rl_result.reason
                    )

            request_bytes = request.SerializeToString()

            payload_key = payload_storage.store(request_bytes, ttl=PAYLOAD_DEFAULT_TTL)

            logger.info(
                "trace_payload_stored_in_redis",
                payload_key=payload_key,
                payload_size=len(request_bytes),
                source="grpc",
            )

            bulk_create_observation_span_task.apply_async(
                args=[payload_key, organization_id, user_id, workspace_id, "protobuf"],
                queue="trace_ingestion",
            )

            return trace_service_pb2.ExportTraceServiceResponse()

        except (AuthenticationFailed, PermissionDenied) as e:
            await context.abort(grpc.StatusCode.UNAUTHENTICATED, str(e))
        except Exception as e:
            logger.exception(f"Error in creating observation span (gRPC): {str(e)}")
            await context.abort(
                grpc.StatusCode.INTERNAL,
                get_error_message("FAILED_CREATION_OBSERVATION_SPAN"),
            )
