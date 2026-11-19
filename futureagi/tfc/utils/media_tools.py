"""One place to ask whether ffmpeg and ffprobe are installed.

The backend shells out to ``ffmpeg`` (audio format detection and MP3
conversion, video thumbnails, LiveKit recording splits) and pydub runs
``ffmpeg``/``ffprobe`` for it. Both come from the backend image
(``futureagi/Dockerfile.oss``, build arg ``FFMPEG_FLAVOR``); an image built
with ``FFMPEG_FLAVOR=none`` has neither.

Call :func:`require_ffmpeg` before running either tool. Without it the
feature fails with :class:`MediaToolUnavailable`, whose message names the
missing tool, the feature and the image to use, instead of a
``FileNotFoundError`` that callers report as a corrupt file or "try again
later". Callers that catch broad exceptions re-raise it first; Temporal
activities turn it into a non-retryable ``ApplicationError`` with
:func:`raise_non_retryable_if_media_tool_missing`, because retrying cannot
install a binary.
"""

import shutil

FFMPEG_UNAVAILABLE = "ffmpeg_unavailable"


def _install_hint(tool: str, feature: str) -> str:
    return (
        f"{tool} is not installed in this backend image, so {feature} is "
        f"unavailable. Use the default futureagi/future-agi image or rebuild "
        f"the backend with --build-arg FFMPEG_FLAVOR=minimal (INSTALLATION.md, "
        f"'Optional feature extras')."
    )


class MediaToolUnavailable(ValueError):
    """ffmpeg or ffprobe is not on PATH, so ``feature`` cannot run.

    A ``ValueError`` so the many ``except ValueError: raise`` paths that pass
    a storage error's message to the user keep doing so."""

    code = FFMPEG_UNAVAILABLE

    def __init__(self, feature: str, tool: str = "ffmpeg"):
        self.feature = feature
        self.tool = tool
        super().__init__(_install_hint(tool, feature))


def ffmpeg_available(*, ffprobe: bool = False) -> bool:
    """True when ``ffmpeg`` (and ``ffprobe`` if asked) is on PATH."""
    tools = ("ffmpeg", "ffprobe") if ffprobe else ("ffmpeg",)
    return all(shutil.which(tool) for tool in tools)


def require_ffmpeg(feature: str, *, ffprobe: bool = False) -> str:
    """Return the path of ``ffmpeg``, or raise :class:`MediaToolUnavailable`.

    ``feature`` completes "so <feature> is unavailable", e.g. "audio format
    detection". Pass ``ffprobe=True`` where pydub decodes a file: it probes
    with ``ffprobe`` before it runs ``ffmpeg``."""
    path = shutil.which("ffmpeg")
    if path is None:
        raise MediaToolUnavailable(feature, "ffmpeg")
    if ffprobe and shutil.which("ffprobe") is None:
        raise MediaToolUnavailable(feature, "ffprobe")
    return path


def raise_non_retryable_if_media_tool_missing(exc: BaseException) -> None:
    """In a Temporal activity: raise a non-retryable ``ApplicationError`` when
    ``exc`` is, or was caused by, a :class:`MediaToolUnavailable`.

    Retrying cannot install ffmpeg, so the workflow fails at once with the
    message instead of retrying until its timeout. Does nothing otherwise, so
    the caller re-raises ``exc`` with its usual retry policy."""
    missing = _find_media_tool_unavailable(exc)
    if missing is None:
        return
    from temporalio.exceptions import ApplicationError

    raise ApplicationError(
        str(missing), type="MediaToolUnavailable", non_retryable=True
    ) from exc


def _find_media_tool_unavailable(
    exc: BaseException | None,
) -> MediaToolUnavailable | None:
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, MediaToolUnavailable):
            return exc
        seen.add(id(exc))
        # Python's own chaining rule: an explicit cause, else the implicit
        # context unless `raise ... from None` suppressed it.
        if exc.__cause__ is not None:
            exc = exc.__cause__
        elif exc.__suppress_context__:
            exc = None
        else:
            exc = exc.__context__
    return None
