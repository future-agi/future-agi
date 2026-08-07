import pytest

from ee.falcon_ai.llm_client import FalconLLMClient


class EmptyStreamClient(FalconLLMClient):
    def __init__(self):
        super().__init__(provider="openai", model="gpt-4o")
        self.calls = 0

    async def stream_completion(self, messages, tools=None):
        self.calls += 1
        if False:
            yield {}


@pytest.mark.asyncio
async def test_stream_with_retry_retries_empty_stream_before_failing(monkeypatch):
    client = EmptyStreamClient()

    async def no_sleep(delay):
        return None

    monkeypatch.setattr("ee.falcon_ai.llm_client.asyncio.sleep", no_sleep)

    with pytest.raises(RuntimeError, match="empty stream"):
        async for _chunk in client.stream_with_retry(
            [{"role": "user", "content": "hello"}],
            max_retries=2,
        ):
            pass

    assert client.calls == 2
