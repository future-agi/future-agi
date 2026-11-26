"""What a trial's browser did, as run detail shows it when its entry is opened.

The list is the trial's own world_state file: the one its sealed record names by digest, read whatever the record
says of the world's records. Never found by scenario key. Each picture is a stored screenshot of the same job whose
digest a row names. The file came from a sandbox that ran the customer's code, so its size is decided from the stored
row before any read, its rows are capped, its schema is checked, and nothing of a row is passed through whole.
"""

from __future__ import annotations

import json
import math
import re
import uuid
from collections.abc import Mapping
from typing import Any, Literal, TypedDict
from urllib.parse import urlsplit

import structlog

from simulate.constants.hosted_harness import TRIAL_STATE_KEY
from simulate.models import CallExecution, HostedHarnessArtifact, HostedHarnessJob
from simulate.selectors.call_detail import end_state_artifact
from simulate.services.browser_sites import browser_sites_at_pin
from simulate.services.hosted_harness_ingestion import artifact_kind_kept
from tfc.settings.settings import UPLOAD_BUCKET_NAME
from tfc.utils.storage_client import get_object_url, get_storage_client

logger = structlog.get_logger(__name__)

# A file larger than this is not read. A door world's file also holds its tables and action log.
FILE_BYTES = 16 * 1024 * 1024
# The kit keeps at most 20 screenshots and 500 rows for a trial; the page shows this many and says how many more.
SCREENSHOT_ROWS = 50
BLOCKED_ROWS = 200
TEXT = 2048
SCHEMAS = frozenset({"futureagi.world-state.v1", "futureagi.browser-world-state.v1"})
SCREENSHOT = "screenshot"
_DIGEST = re.compile(r"sha256:([0-9a-f]{64})")
_IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
_NO_BROWSER = frozenset({"not_started"})

BrowserState = Literal["listed", "not_kept", "unreadable", "no_browser"]
BlockedSource = Literal["record", "file", "unreadable"]


class BrowserScreenshot(TypedDict):
    when: str
    url: str
    link: bool
    tab: int | None
    at: float | None
    kept: bool
    picture_url: str | None


class BrowserEvidence(TypedDict, total=False):
    state: BrowserState
    complete: bool
    capped: bool
    level_keeps_none: bool
    screenshots: list[BrowserScreenshot]
    more: int
    blocked_source: BlockedSource
    blocked: list[dict[str, Any]]
    blocked_truncated: bool


class _Unreadable(Exception):
    """The file is stored but could not be read as a world_state file."""


def browser_evidence(call: CallExecution) -> BrowserEvidence:
    """The trial's browser, every state said: its screenshots in the file's order, each with its stored picture when
    kept; and, for a trial with no world door, its blocked list from the same file."""
    metadata = call.call_metadata if isinstance(call.call_metadata, dict) else {}
    state = metadata.get(TRIAL_STATE_KEY)
    state = state if isinstance(state, Mapping) else {}
    # A door trial's blocked lists are kept on its record and shown with its changes; never from both.
    from_record = state.get("recorded") is not False
    # The kit says on the agent's entry when the browser's refusals could not be read: the record then holds none.
    unread = any(
        isinstance(one, Mapping)
        and one.get("who") == "agent"
        and one.get("browser_blocked_unreadable") is True
        for one in state.get("state_changes") or []
    )
    shown: BrowserEvidence = {
        "state": "not_kept",
        "capped": False,
        "level_keeps_none": False,
        "screenshots": [],
        "more": 0,
        "blocked_source": "record" if from_record and not unread else "unreadable",
    }
    # The kit names a file holding only the browser's record as the browser's own, and one with the world's tables as
    # the end state; the Browser tab reads whichever the trial has. Older kits named both as the end state.
    stored = end_state_artifact(
        call, metadata, state, key="browser_state_artifact"
    ) or end_state_artifact(call, metadata, state)
    if stored is None:
        return shown
    try:
        body = _read(stored)
    except _Unreadable as why:
        logger.warning(
            "browser_evidence_unreadable", call_execution_id=str(call.id), why=str(why)
        )
        shown["state"] = "unreadable"
        return shown
    if not from_record:
        shown.update(_blocked(body))
    browser = body.get("browser")
    if not isinstance(browser, Mapping) or browser.get("state") in _NO_BROWSER:
        shown["state"] = "no_browser"
        return shown
    rows = browser.get("screenshots")
    if rows is None and browser.get("state") == "not_used":
        rows = []
    if not isinstance(rows, list):
        shown["state"] = "unreadable"
        return shown
    shown["state"] = "listed"
    if browser.get("complete") is False:
        shown["complete"] = False
    shown["capped"] = browser.get("screenshots_capped") is True
    shown["more"] = max(len(rows) - SCREENSHOT_ROWS, 0)
    rows = [row for row in rows[:SCREENSHOT_ROWS] if isinstance(row, Mapping)]
    pictures = _pictures(stored, rows)
    shown["level_keeps_none"] = not artifact_kind_kept(
        stored.job.artifact_level, SCREENSHOT
    )
    shown["screenshots"] = [_screenshot(row, pictures) for row in rows]
    return shown


def browser_environment(call: CallExecution) -> bool:
    """Whether the trial ran in a browser environment: the job that sent its receipt, in the run's organisation and
    workspace, has sites at its pin. Read through the one function for a job's sites, never a second reader."""
    from simulate.services.hosted_harness import HostedHarnessError

    metadata = call.call_metadata if isinstance(call.call_metadata, dict) else {}
    receipt = metadata.get("hosted_harness_receipt")
    if not isinstance(receipt, Mapping):
        return False
    try:
        job_id = uuid.UUID(str(receipt.get("job_id")))
    except ValueError:
        return False
    run_test = call.test_execution.run_test
    jobs = HostedHarnessJob.no_workspace_objects.filter(
        id=job_id, organization_id=run_test.organization_id
    )
    jobs = (
        jobs.filter(workspace_id=run_test.workspace_id)
        if run_test.workspace_id is not None
        else jobs.filter(workspace_id__isnull=True)
    )
    job = jobs.select_related("environment_version").first()
    if job is None:
        return False
    try:
        return bool(browser_sites_at_pin(job.payload, job.environment_version))
    except HostedHarnessError:
        # A stored list the save would refuse still names a browser environment; the entry says what was kept.
        logger.warning("browser_environment_sites_refused", job_id=str(job.id))
        return True


def _read(stored: HostedHarnessArtifact) -> dict[str, Any]:
    if stored.size > FILE_BYTES:
        raise _Unreadable("too_large")
    try:
        data = _fetch(stored.object_key)
    except Exception as error:  # noqa: BLE001 - storage that cannot give the file is said, never a failed page
        raise _Unreadable("not_fetched") from error
    if len(data) > FILE_BYTES:
        raise _Unreadable("too_large")
    try:
        body = json.loads(data)
    except (ValueError, RecursionError) as error:
        raise _Unreadable("not_json") from error
    if not isinstance(body, dict) or body.get("schema") not in SCHEMAS:
        raise _Unreadable("unknown_schema")
    return body


def _fetch(object_key: str) -> bytes:
    """The stored file, read up to one byte past the cap, however the store gives it."""
    response = get_storage_client().get_object(UPLOAD_BUCKET_NAME, object_key)
    pieces: list[bytes] = []
    held = 0
    try:
        while held <= FILE_BYTES:
            piece = response.read(min(1024 * 1024, FILE_BYTES + 1 - held))
            if not piece:
                break
            pieces.append(piece)
            held += len(piece)
    finally:
        response.close()
        release = getattr(response, "release_conn", None)
        if callable(release):
            release()
    return b"".join(pieces)


def _digest(row: Mapping[str, Any]) -> str | None:
    named = row.get("artifact_id")
    match = _DIGEST.fullmatch(named) if isinstance(named, str) else None
    return match.group(1) if match else None


def _pictures(
    stored: HostedHarnessArtifact, rows: list[Mapping[str, Any]]
) -> dict[str, str]:
    """Each named picture's link: a stored screenshot image of the file's own job."""
    digests = {digest for row in rows if (digest := _digest(row))}
    if not digests:
        return {}
    kept = HostedHarnessArtifact.no_workspace_objects.filter(
        job_id=stored.job_id,
        kind=SCREENSHOT,
        sha256__in=digests,
        content_type__in=_IMAGE_TYPES,
    ).values_list("sha256", "object_key")
    return {digest: _link(object_key) for digest, object_key in kept}


def _link(object_key: str) -> str:
    # The one place a picture is linked, as a recording is.
    return get_object_url(UPLOAD_BUCKET_NAME, object_key)


def _text(value: Any, limit: int = TEXT) -> str:
    return value[:limit] if isinstance(value, str) else ""


def _screenshot(
    row: Mapping[str, Any], pictures: Mapping[str, str]
) -> BrowserScreenshot:
    url = _text(row.get("url"))
    digest = _digest(row)
    picture = pictures.get(digest) if digest else None
    tab, at = row.get("tab"), row.get("at")
    return {
        "when": _text(row.get("when"), 32),
        "url": url,
        "link": urlsplit(url).scheme.lower() in {"http", "https"} if url else False,
        "tab": tab if isinstance(tab, int) and not isinstance(tab, bool) else None,
        "at": _seconds(at),
        "kept": picture is not None,
        "picture_url": picture,
    }


def _seconds(value: Any) -> float | None:
    # The file's JSON reads NaN, Infinity and numbers too large for a float; none of them can be served as JSON.
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        seconds = float(value)
    except OverflowError:
        return None
    return seconds if math.isfinite(seconds) else None


def _blocked(body: Mapping[str, Any]) -> BrowserEvidence:
    """A no-door trial's blocked list from its file, shaped as a trial's own blocked list is: no address is served,
    and each page the agent tried to open counts one."""
    entries = body.get("blocked")
    if body.get("blocked_unreadable") is True or not isinstance(entries, list):
        return {"blocked_source": "unreadable"}
    truncated = any(
        isinstance(one, Mapping) and one.get("truncated") is True for one in entries
    )
    rows = [
        _blocked_row(one)
        for one in entries
        if isinstance(one, Mapping) and isinstance(one.get("host"), str)
    ]
    return {
        "blocked_source": "file",
        "blocked": rows[:BLOCKED_ROWS],
        "blocked_truncated": truncated or len(rows) > BLOCKED_ROWS,
    }


def _blocked_row(entry: Mapping[str, Any]) -> dict[str, Any]:
    row: dict[str, Any] = {"host": _text(entry.get("host"), 253)}
    for key in ("method", "tool", "via"):
        if isinstance(entry.get(key), str) and entry[key]:
            row[key] = _text(entry[key], 64)
    if entry.get("what") in {"navigation", "page_request"}:
        row["what"] = entry["what"]
    count = entry.get("count")
    row["count"] = (
        count
        if isinstance(count, int) and not isinstance(count, bool) and count > 0
        else 1
    )
    return row
