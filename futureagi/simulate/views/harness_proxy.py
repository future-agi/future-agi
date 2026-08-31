import asyncio
import json

import httpx
from asgiref.sync import sync_to_async
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from rest_framework.negotiation import BaseContentNegotiation
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from simulate.services import harness_links
from simulate.services.harness_client import NON_STREAMING_TIMEOUT, resolve_harness_internal_url

# SSE responses; everything else is JSON (or a recording, passed through as-is).
STREAMING_PATHS = frozenset({"say", "run"})
# Well under the shortest idle timeout of any hop in front of this response.
HEARTBEAT_SECONDS = 15


class _PassthroughNegotiation(BaseContentNegotiation):
    """The upstream harness decides the response shape, so the Accept header
    must not be able to 406 a request before it is even forwarded."""

    def select_parser(self, request, parsers):
        return parsers[0] if parsers else None

    def select_renderer(self, request, renderers, format_suffix=None):
        return (renderers[0], renderers[0].media_type)


class HarnessProxyView(APIView):
    """One authenticated door to every harness endpoint.

    The harness has no auth and no notion of platform entities, so this view
    supplies both: platform auth in front, and run_test/execution ids attached
    on the way back out. Forwarded verbatim otherwise — the FE contract in
    api_contracts/harness/frontend-contract.md is the harness's own API.
    """

    permission_classes = [IsAuthenticated]
    content_negotiation_class = _PassthroughNegotiation

    def get(self, request, path=""):
        return self._forward(request, path)

    def post(self, request, path=""):
        return self._forward(request, path)

    def delete(self, request, path=""):
        return self._forward(request, path)

    def _forward(self, request, path):
        path = path.strip("/")
        if not path or ".." in path or "%" in path:
            return JsonResponse({"error": "unknown harness path"}, status=404)
        url = f"{resolve_harness_internal_url()}/api/{path}"
        body, links = self._body_and_links(request, path)
        if path in STREAMING_PATHS:
            if request.method != "POST":
                return JsonResponse({"error": "method not allowed"}, status=405)
            return self._stream(url, body)
        try:
            answered = httpx.request(
                request.method,
                url,
                params=request.GET.dict(),
                json=body,
                timeout=NON_STREAMING_TIMEOUT,
            )
        except httpx.HTTPError:
            return JsonResponse({"error": "harness unreachable"}, status=502)
        content_type = answered.headers.get("content-type", "")
        if "application/json" not in content_type:
            # Recordings and anything else binary pass through untouched.
            return HttpResponse(
                answered.content, status=answered.status_code, content_type=content_type
            )
        payload = answered.json()
        if answered.status_code < 400:
            self._remember_links(path, links, payload)
            self._enrich(path, payload)
        return JsonResponse(payload, status=answered.status_code, safe=False)

    def _body_and_links(self, request, path):
        """The forwardable body, with platform ids stripped out of session creation."""
        if request.method != "POST":
            return None, {}
        try:
            body = json.loads(request.body) if request.body else {}
        except ValueError:
            return None, {}
        links = {}
        if path == "sessions" and isinstance(body, dict):
            links = {
                "run_test_id": body.pop("run_test_id", None),
                "execution_id": body.pop("execution_id", None),
            }
        return body, links

    def _remember_links(self, path, links, payload):
        if path != "sessions" or not any(links.values()):
            return
        session = (payload or {}).get("session") or {}
        if session.get("id"):
            harness_links.remember(
                session["id"], links.get("run_test_id"), links.get("execution_id")
            )

    def _enrich(self, path, payload):
        if not isinstance(payload, dict):
            return
        if "session" in payload:
            session = payload.get("session") or {}
            self._attach(payload, session.get("id"))
        elif path == "sessions":
            self._attach_all(payload.get("sessions") or [], "id")
        elif path == "environments":
            self._attach_all(payload.get("environments") or [], "session_id")

    def _attach_all(self, rows, key):
        for one in rows:
            self._attach(one, one.get(key))

    def _attach(self, target, session_id):
        # A session opened from the platform was told which run test it belongs to, and that
        # link wins. A session started on the harness was not, but it reports where its own
        # runs went — so fall back to that rather than overwriting it with nothing, which is
        # how a session with runs behind it ends up looking like one that has never been run.
        link = harness_links.lookup(session_id)
        for field in ("run_test_id", "execution_id"):
            if link.get(field):
                target[field] = link[field]
            else:
                target.setdefault(field, None)

    def _stream(self, url, body):
        # No read timeout: a stage or a suite legitimately streams for minutes.
        client = httpx.Client(timeout=httpx.Timeout(10.0, read=None))
        stream = client.stream("POST", url, json=body)
        try:
            upstream = stream.__enter__()
        except httpx.HTTPError:
            client.close()
            return JsonResponse({"error": "harness unreachable"}, status=502)
        if upstream.status_code >= 400:
            content = upstream.read()
            stream.__exit__(None, None, None)
            client.close()
            return HttpResponse(
                content,
                status=upstream.status_code,
                content_type=upstream.headers.get("content-type", "application/json"),
            )

        done = object()

        def read_all(push):
            try:
                for chunk in upstream.iter_bytes():
                    push(chunk)
            finally:
                push(done)
                stream.__exit__(None, None, None)
                client.close()

        async def relay():
            # A dedicated reader feeds a queue so the relay can wake up on its
            # own clock: a long tool call emits nothing for minutes, and any
            # hop's idle timer would kill the quiet stream. The heartbeat is an
            # SSE comment — every parser ignores it, every timer resets.
            queue = asyncio.Queue()
            loop = asyncio.get_running_loop()

            def push(item):
                loop.call_soon_threadsafe(queue.put_nowait, item)

            # Off the thread-sensitive executor, so one long suite cannot
            # serialize every other request on the worker.
            reader = asyncio.ensure_future(
                sync_to_async(read_all, thread_sensitive=False)(push)
            )
            try:
                while True:
                    try:
                        chunk = await asyncio.wait_for(queue.get(), HEARTBEAT_SECONDS)
                    except asyncio.TimeoutError:
                        yield b": keep-alive\n\n"
                        continue
                    if chunk is done:
                        break
                    yield chunk
            finally:
                # Closing the response unblocks a reader mid-iter_bytes; its own
                # finally then releases the stream and the client.
                await sync_to_async(upstream.close, thread_sensitive=False)()
                reader.cancel()

        response = StreamingHttpResponse(relay(), content_type="text/event-stream")
        # A buffering reverse proxy in front would reintroduce exactly the
        # problem this generator exists to avoid.
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response
