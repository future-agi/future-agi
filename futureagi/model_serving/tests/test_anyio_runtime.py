"""AnyIO 4 behaviour used by the serving web stack, without models or sockets.

Use plain sync test functions so the smoke also runs in an offline scratch
venv without Django, pytest-asyncio or a serving application/model download.
HTTPX's ASGI transport buffers response chunks; this checks streamed content
and generator completion, not wire-level chunk timing or disconnect handling.
"""

import anyio
import httpx
from fastapi import FastAPI
from starlette.responses import StreamingResponse


def test_cancel_scope_and_task_group_cleanup():
    async def smoke():
        reached_after_cancel = False
        with anyio.CancelScope() as scope:
            scope.cancel()
            await anyio.sleep(0)
            reached_after_cancel = True
        assert scope.cancel_called and scope.cancelled_caught
        assert not reached_after_cancel
        cleaned_up = []

        async def worker(*, task_status=anyio.TASK_STATUS_IGNORED):
            try:
                task_status.started()
                await anyio.sleep_forever()
            finally:
                cleaned_up.append(True)

        with anyio.fail_after(5):
            async with anyio.create_task_group() as group:
                await group.start(worker)
                group.cancel_scope.cancel()
        assert cleaned_up == [True]

    anyio.run(smoke, backend="asyncio")


def test_memory_object_stream_and_task_group():
    async def smoke():
        send, receive = anyio.create_memory_object_stream[int](0)
        received = []

        async def producer():
            async with send:
                for value in range(3):
                    await send.send(value)

        with anyio.fail_after(5):
            async with anyio.create_task_group() as group:
                group.start_soon(producer)
                async with receive:
                    async for value in receive:
                        received.append(value)
        assert received == [0, 1, 2]
        assert send.statistics().open_send_streams == 0
        assert receive.statistics().open_receive_streams == 0

    anyio.run(smoke, backend="asyncio")


def test_fastapi_streaming_response_over_asgi():
    async def smoke():
        app = FastAPI()
        yielded = []
        completed = []

        async def chunks():
            try:
                for chunk in (b"alpha\n", b"beta\n", b"gamma\n"):
                    await anyio.sleep(0)
                    yielded.append(chunk)
                    yield chunk
            finally:
                completed.append(True)

        @app.get("/stream")
        async def stream():
            return StreamingResponse(chunks(), media_type="text/plain")

        with anyio.fail_after(5):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://asgi.invalid",
                trust_env=False,
            ) as client:
                async with client.stream("GET", "/stream") as response:
                    assert response.status_code == 200
                    assert response.headers["content-type"].startswith("text/plain")
                    body = b"".join([chunk async for chunk in response.aiter_bytes()])
        assert body == b"alpha\nbeta\ngamma\n"
        assert yielded == [b"alpha\n", b"beta\n", b"gamma\n"]
        assert completed == [True]

    anyio.run(smoke, backend="asyncio")
