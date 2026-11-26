"""
Let traceai-litellm stream wrappers pass chunks that carry no choices.

traceai-litellm releases before 0.1.12 read ``chunk["choices"][0]`` on every
streamed chunk to build the span output. LiteLLM passes some chunks through
with an empty ``choices`` list, for example the prompt-filter chunk Azure
sends before any content, and some providers' usage-only chunks. On those
chunks the wrapper raised ``IndexError: list index out of range`` and the
caller's whole stream failed.

``patch_traceai_litellm_stream_wrappers`` wraps the two iterator wrappers so
such a chunk is handed to the caller unchanged (with any usage it carries), which is what
traceai-litellm 0.1.12 does. Any other exception still propagates. On
releases that already handle empty choices the wrapper never raises, so the
patch does nothing there.
"""

from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_PATCHED_ATTR = "_fi_empty_choices_patched"


def _has_empty_choices(chunk: Any) -> bool:
    """True when the chunk's dict form has an empty ``choices`` list."""
    try:
        if hasattr(chunk, "to_dict"):
            chunk_dict = chunk.to_dict()
        elif hasattr(chunk, "__dict__"):
            chunk_dict = chunk.__dict__
        else:
            chunk_dict = chunk
    except Exception:  # noqa: BLE001 - a chunk that cannot be read is not the empty-choices case
        return False
    if not isinstance(chunk_dict, dict):
        return False
    choices = chunk_dict.get("choices", [{}])
    return isinstance(choices, (list, tuple)) and len(choices) == 0


class _LastChunkIterator:
    """Iterator proxy that remembers the chunk it most recently produced."""

    def __init__(self, iterator: Any):
        self._iterator = iterator
        self.last = None
        self.fresh = False

    def __iter__(self):
        return self

    def __next__(self):
        chunk = next(self._iterator)
        self.last = chunk
        self.fresh = True
        return chunk

    def __getattr__(self, name: str) -> Any:
        if name == "_iterator":
            raise AttributeError(name)
        return getattr(self._iterator, name)


class _LastChunkAsyncIterator:
    """Async iterator proxy that remembers the chunk it most recently produced."""

    def __init__(self, iterator: Any):
        self._iterator = iterator
        self.last = None
        self.fresh = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        chunk = await self._iterator.__anext__()
        self.last = chunk
        self.fresh = True
        return chunk

    def __getattr__(self, name: str) -> Any:
        if name == "_iterator":
            raise AttributeError(name)
        return getattr(self._iterator, name)


def _patch_sync_wrapper(wrapper_cls: Any) -> None:
    original_next = wrapper_cls.__next__

    def __next__(self):
        tracker = getattr(self, "iterator", None)
        if tracker is None:
            return original_next(self)
        if not isinstance(tracker, _LastChunkIterator):
            tracker = _LastChunkIterator(tracker)
            self.iterator = tracker
        tracker.fresh = False
        try:
            return original_next(self)
        except IndexError:
            if tracker.fresh and _has_empty_choices(tracker.last):
                return tracker.last
            raise

    wrapper_cls.__next__ = __next__
    setattr(wrapper_cls, _PATCHED_ATTR, True)


def _patch_async_wrapper(wrapper_cls: Any) -> None:
    original_anext = wrapper_cls.__anext__

    async def __anext__(self):
        tracker = getattr(self, "async_iterator", None)
        if tracker is None:
            return await original_anext(self)
        if not isinstance(tracker, _LastChunkAsyncIterator):
            tracker = _LastChunkAsyncIterator(tracker)
            self.async_iterator = tracker
        tracker.fresh = False
        try:
            return await original_anext(self)
        except IndexError:
            if tracker.fresh and _has_empty_choices(tracker.last):
                return tracker.last
            raise

    wrapper_cls.__anext__ = __anext__
    setattr(wrapper_cls, _PATCHED_ATTR, True)


def patch_traceai_litellm_stream_wrappers() -> bool:
    """
    Patch traceai-litellm's stream wrappers to pass empty-choices chunks through.

    Safe to call more than once. Returns True when both wrappers are patched.
    """
    try:
        import traceai_litellm
    except ImportError:
        return False

    patched = True
    for class_name, patcher in (
        ("StreamingIteratorWrapper", _patch_sync_wrapper),
        ("AsyncStreamingIteratorWrapper", _patch_async_wrapper),
    ):
        wrapper_cls = getattr(traceai_litellm, class_name, None)
        if wrapper_cls is None:
            patched = False
            continue
        if getattr(wrapper_cls, _PATCHED_ATTR, False):
            continue
        try:
            patcher(wrapper_cls)
        except Exception as exc:  # noqa: BLE001 - tracing must never block startup
            patched = False
            logger.warning(
                "traceai_litellm_stream_patch_failed",
                wrapper=class_name,
                error=str(exc),
            )
    return patched
