"""One private thread for each activity, instead of asgiref's process-wide one.

Outside a ``ThreadSensitiveContext``, every ``sync_to_async(thread_sensitive=True)`` call in a
process runs on one thread, so a slow call in one activity stalls the database work of all the
others, and their heartbeats hide it.
"""

from asgiref.sync import ThreadSensitiveContext, sync_to_async
from django.db import close_old_connections
from temporalio.worker import (
    ActivityInboundInterceptor,
    ExecuteActivityInput,
    Interceptor,
)


class OwnThreadActivityInbound(ActivityInboundInterceptor):
    async def execute_activity(self, input: ExecuteActivityInput):
        async with ThreadSensitiveContext():
            try:
                return await super().execute_activity(input)
            finally:
                # The context's private thread dies with the context; close the DB
                # connection it opened instead of leaving it to GC.
                await sync_to_async(close_old_connections, thread_sensitive=True)()


class OwnThreadActivityInterceptor(Interceptor):
    def intercept_activity(self, next):
        return OwnThreadActivityInbound(next)
