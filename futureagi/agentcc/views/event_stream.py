"""A text/event-stream response that reaches the browser piece by piece under
ASGI as well as WSGI.

Production serves Django through ASGI (granian --interface asgi
tfc.asgi:application). There Django's StreamingHttpResponse.__aiter__ serves
a sync iterator by reading it to the end first (sync_to_async(list)), so the
browser would get the whole answer at once, after the gateway finished. This
response pulls one piece at a time instead, and closes its source when the
send stops (the browser left, or the send failed).

Pieces are read with thread_sensitive=True: under Django's ASGIHandler that
is the request's own thread (ThreadSensitiveContext), so a slow read holds no
thread of the loop's shared pool, and every read and close of the source runs
on that one thread in order. Django closes the response there too
(sync_to_async(response.close)), so a close never meets a read in progress:
when the browser leaves mid-read, the close runs as soon as that read returns.
"""

from asgiref.sync import sync_to_async
from django.http import StreamingHttpResponse

_END = object()


def _next_piece(pieces):
    return next(pieces, _END)


class EventStreamResponse(StreamingHttpResponse):
    """Serves ``events``, a sync iterator of event strings with ``close()``."""

    def __init__(self, events, **kwargs):
        kwargs.setdefault("content_type", "text/event-stream")
        super().__init__(events, **kwargs)
        self._events = events
        self["Cache-Control"] = "no-cache"
        self["X-Accel-Buffering"] = "no"

    async def __aiter__(self):
        pieces = iter(self.streaming_content)
        read = sync_to_async(_next_piece, thread_sensitive=True)
        try:
            while (piece := await read(pieces)) is not _END:
                yield piece
        finally:
            # Django's ASGIHandler also closes the response after a client
            # disconnect, but not when the send itself raised: close here.
            await sync_to_async(self._events.close, thread_sensitive=True)()
