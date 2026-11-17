"""Async, cancellable I/O behind the console's synchronous response interface.

Both sides use the same monotonic deadline. Slow headers, a busy SSE stream,
retry backoff and consumer backpressure all spend that budget. The queue holds
at most two chunks; closing a response cancels the upstream task.
"""

import asyncio
import queue
import threading
import time
from contextlib import contextmanager

import httpx


class Exchange:
    def __init__(self, url, raw, headers, deadline):
        self.url, self.raw, self.headers, self.deadline = url, raw, headers, deadline
        self.messages = queue.Queue(maxsize=2)
        self.stopped = threading.Event()
        self.loop = None
        self.task = None
        self.thread = threading.Thread(target=self.run, daemon=True)

    async def put(self, message):
        while not self.stopped.is_set():
            try:
                self.messages.put_nowait(message)
                return
            except queue.Full:
                await asyncio.sleep(0.01)
        raise asyncio.CancelledError

    async def exchange(self):
        async with httpx.AsyncClient(
            timeout=max(0.001, self.deadline - time.monotonic()),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            for attempt in range(2):
                # Signing and retries happen inside the single overall budget.
                headers = self.headers()
                if time.monotonic() >= self.deadline:
                    raise TimeoutError
                try:
                    async with client.stream(
                        "POST", self.url, content=self.raw, headers=headers
                    ) as response:
                        if response.status_code == 401 and attempt == 0:
                            await response.aread()
                            try:
                                body = response.json()
                            except ValueError:
                                body = None
                            error = (
                                body.get("error") if isinstance(body, dict) else None
                            )
                            if error == "service_envelope_rejected" or (
                                isinstance(error, dict)
                                and error.get("code") == "service_envelope_rejected"
                            ):
                                await asyncio.sleep(6)
                                continue
                        response_headers = httpx.Headers(response.headers)
                        if response.is_stream_consumed:
                            # aread decoded the body already; the synchronous
                            # facade must not decompress these bytes a second time.
                            response_headers.pop("content-encoding", None)
                            response_headers.pop("content-length", None)
                        await self.put(
                            ("headers", (response.status_code, response_headers))
                        )
                        # aread above may already have consumed a refused response.
                        if response.is_stream_consumed:
                            await self.put(("chunk", response.content))
                        else:
                            async for chunk in response.aiter_raw():
                                await self.put(("chunk", chunk))
                        await self.put(("done", None))
                        return
                except httpx.ConnectError:
                    if attempt:
                        raise
                await asyncio.sleep(6)

    async def bounded(self):
        try:
            await asyncio.wait_for(
                self.exchange(), timeout=max(0, self.deadline - time.monotonic())
            )
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            # Do not block teardown on a full queue; the reader also checks the
            # deadline, and any queued chunks are consumed before this error.
            await self.put(("error", exc))

    def run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.task = self.loop.create_task(self.bounded())
        try:
            if self.stopped.is_set():
                self.task.cancel()
            self.loop.run_until_complete(self.task)
        except asyncio.CancelledError:
            pass
        finally:
            self.loop.close()

    def receive(self):
        from agentcc.services.gateway_client import GatewayClientError

        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise GatewayClientError("Delegated gateway deadline exceeded")
        try:
            kind, value = self.messages.get(timeout=remaining)
        except queue.Empty as exc:
            raise GatewayClientError("Delegated gateway deadline exceeded") from exc
        if time.monotonic() >= self.deadline or (
            kind == "error" and isinstance(value, TimeoutError)
        ):
            raise GatewayClientError("Delegated gateway deadline exceeded")
        if kind == "error":
            raise GatewayClientError("Delegated gateway request unavailable") from value
        return kind, value

    def close(self):
        self.stopped.set()
        try:
            if self.loop and self.task:
                self.loop.call_soon_threadsafe(self.task.cancel)
        except RuntimeError:
            pass  # the task already completed and closed its loop
        # No join here: a consumer must not wait beyond its budget for cleanup.


class ResponseBody(httpx.SyncByteStream):
    def __init__(self, exchange):
        self.exchange = exchange

    def __iter__(self):
        while True:
            kind, value = self.exchange.receive()
            if kind == "done":
                return
            yield value

    def close(self):
        self.exchange.close()


@contextmanager
def bounded_response(url, raw, headers, deadline):
    exchange = Exchange(url, raw, headers, deadline)
    try:
        # An expired lazy stream must never start a new gateway operation.
        if time.monotonic() >= deadline:
            from agentcc.services.gateway_client import GatewayClientError

            raise GatewayClientError("Delegated gateway deadline exceeded")
        exchange.thread.start()
        kind, value = exchange.receive()
        if kind != "headers":
            raise RuntimeError("Missing delegated response headers")
        status, response_headers = value
        response = httpx.Response(
            status, headers=response_headers, stream=ResponseBody(exchange)
        )
        try:
            yield response
        finally:
            response.close()
    finally:
        exchange.close()
