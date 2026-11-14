"""Activities that need ffmpeg fail once, not on every retry, when it is missing.

A backend image built without ffmpeg (``FFMPEG_FLAVOR=none``) raises
``MediaToolUnavailable`` from every feature that needs it. Retrying the
activity cannot install the binary, so this interceptor turns that error, or
any error it caused, into a non-retryable ``ApplicationError`` of type
``MediaToolUnavailable`` carrying the same message. Every other error keeps the
activity's retry policy. Registered by every worker builder:
``start_temporal_worker``, the embedded worker and ``common/worker.py``.
"""

from temporalio.worker import (
    ActivityInboundInterceptor,
    ExecuteActivityInput,
    Interceptor,
)

from tfc.utils.media_tools import raise_non_retryable_if_media_tool_missing


class MediaToolActivityInbound(ActivityInboundInterceptor):
    async def execute_activity(self, input: ExecuteActivityInput):
        try:
            return await super().execute_activity(input)
        except Exception as exc:
            raise_non_retryable_if_media_tool_missing(exc)
            raise


class MediaToolActivityInterceptor(Interceptor):
    def intercept_activity(self, next):
        return MediaToolActivityInbound(next)
