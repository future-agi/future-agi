"""A message waits a limited time for its links, and a slow page holds up
nothing else.

Falcon AI fetches the links of a message on a few threads of their own, within
a time limit. A message whose links take longer goes on with one line per link
saying it could not be fetched. A message with no link uses no thread at all.

Network-free and database-free: the fetch itself is replaced.
"""

import asyncio
import threading
import time

import pytest

from ee.falcon_ai import url_fetcher

FIRST_LINK = "https://news.example.com/latest"
SECOND_LINK = "https://www.example.org/notes"
MESSAGE = f"Compare {FIRST_LINK} with {SECOND_LINK}"


@pytest.fixture
def slow_fetch(monkeypatch):
    """A fetch that does not come back until the test is over. It records the
    name of the thread that ran it."""
    release = threading.Event()
    threads = []

    def fetch(message):
        threads.append(threading.current_thread().name)
        release.wait(timeout=10)
        return "late"

    monkeypatch.setattr(url_fetcher, "fetch_urls_from_message", fetch)
    yield threads
    release.set()


def test_links_slower_than_the_limit_give_one_line_each(slow_fetch):
    started = time.monotonic()

    context = asyncio.run(url_fetcher.fetch_urls_for_chat(MESSAGE, time_limit=0.05))

    assert time.monotonic() - started < 5
    assert context == (
        f"[This link could not be fetched: {FIRST_LINK}]\n\n"
        f"[This link could not be fetched: {SECOND_LINK}]"
    )


def test_links_fetched_in_time_come_back_as_they_are(monkeypatch):
    def fetch(message):
        return f"[URL: {FIRST_LINK}]\nPlain notes"

    monkeypatch.setattr(url_fetcher, "fetch_urls_from_message", fetch)

    context = asyncio.run(url_fetcher.fetch_urls_for_chat(MESSAGE))

    assert context == f"[URL: {FIRST_LINK}]\nPlain notes"


def test_a_slow_fetch_runs_on_its_own_thread_and_other_work_goes_on(slow_fetch):
    async def scenario():
        other_work = []
        waiting = asyncio.ensure_future(
            url_fetcher.fetch_urls_for_chat(MESSAGE, time_limit=0.3)
        )
        await asyncio.sleep(0.05)
        other_work.append(threading.current_thread().name)
        await waiting
        return other_work

    other_work = asyncio.run(scenario())

    assert other_work == [threading.current_thread().name]
    assert len(slow_fetch) == 1
    assert slow_fetch[0].startswith("falcon-link-fetch")


def test_a_message_with_no_link_uses_no_thread(slow_fetch):
    context = asyncio.run(url_fetcher.fetch_urls_for_chat("No link in this one."))

    assert context == ""
    assert slow_fetch == []
