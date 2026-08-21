"""Reporting a run to the platform, so it appears where every other run does.

The platform already has somewhere to put this. Its simulate pages read `RunTest`,
`TestExecution` and `CallExecution`, and the ingestion API that the hosted runner posts to builds
exactly those. So a harness run is not shown by drawing it again somewhere else; it is shown by
walking the same API, and the pages that already exist render it unchanged.

    provision  ──► a RunTest for this session, once
    start      ──► a TestExecution, once per run, so running twice gives two runs
    batch      ──► a CallExecution per scenario
    result     ──► what the scenario did, one call at a time
    recording  ──► the audio, where a spoken run left any

What is deliberately *not* sent: interruption counts, talk ratio, latency, scores. The backend
derives those from the transcript it is given, and a second implementation here would drift from
the one the rest of the platform is measured by. This reports only what the run observed.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Where runs are reported. Its own variables, because reporting and evaluating go to different
# places: the eval templates a run is scored against live on the hosted platform, while the runs
# themselves belong wherever the person is looking at them -- usually the backend beside this
# harness. Sharing FI_* for both means one of the two is always pointed at the wrong host.
# FI_* is the fallback, so a setup that genuinely uses one platform for both still works unchanged.
BASE_URL = ("HARNESS_PLATFORM_URL", "FI_BASE_URL")
API_KEY = ("HARNESS_PLATFORM_API_KEY", "FI_API_KEY")
SECRET_KEY = ("HARNESS_PLATFORM_SECRET_KEY", "FI_SECRET_KEY")


def _setting(names: tuple[str, ...]) -> str:
    """The first of these that is set, so the specific name wins over the shared one."""
    for name in names:
        found = os.environ.get(name, "").strip()
        if found:
            return found
    return ""

INGESTION = "/simulate/api/alk-simulate"

# Django appends a slash and cannot redirect a POST while keeping its body, so every path here
# carries one already. Without it the call fails as a 500 that reads like a server fault.
TIMEOUT_SECONDS = 120.0


class PlatformError(RuntimeError):
    """The platform refused or could not be reached, with enough detail to act on."""


@dataclass
class Reported:
    """Where a run ended up, so a caller can link to it."""

    run_test_id: str = ""
    test_execution_id: str = ""
    calls: dict[str, str] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    @property
    def url(self) -> str:
        """Where this run is on the platform, for anyone wanting to look at it."""
        return f"/dashboard/simulate/test/{self.run_test_id}/runs" if self.run_test_id else ""


def configured() -> str:
    """Why a run cannot be reported, or an empty string when it can."""
    missing = [names[0] for names in (BASE_URL, API_KEY, SECRET_KEY) if not _setting(names)]
    if missing:
        return f"{', '.join(missing)} not set, so this run stays local"
    return ""


class Platform:
    """The ingestion API, as the few calls a run actually makes."""

    def __init__(self, base: str = "", key: str = "", secret: str = "") -> None:
        self.base = (base or _setting(BASE_URL)).rstrip("/")
        self.key = key or _setting(API_KEY)
        self.secret = secret or _setting(SECRET_KEY)

    def _call(self, path: str, payload: dict[str, Any], method: str = "POST") -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.base}{INGESTION}{path}",
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "X-Api-Key": self.key,
                "X-Secret-Key": self.secret,
            },
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as answer:
                body = json.loads(answer.read().decode() or "{}")
        except urllib.error.HTTPError as refused:
            detail = refused.read().decode(errors="replace")[:400]
            raise PlatformError(f"{method} {path} failed ({refused.code}): {detail}") from refused
        except Exception as unreachable:  # noqa: BLE001 - reported, not handled
            raise PlatformError(f"{method} {path} could not be sent: {unreachable}") from unreachable
        # The platform wraps every answer; unwrap it here so callers read the payload itself.
        return body.get("result", body) if isinstance(body, dict) else {}

    def provision(
        self, name: str, personas: list[dict[str, Any]], modality: str = "text"
    ) -> dict[str, Any]:
        return self._call(
            "/run-tests/provision/",
            {"name": name, "personas": personas, "modality": modality},
        )

    def start(self, run_test_id: str) -> dict[str, Any]:
        return self._call(f"/run-tests/{run_test_id}/test-executions/", {})

    def batch(self, test_execution_id: str, count: int) -> dict[str, Any]:
        return self._call(f"/test-executions/{test_execution_id}/batch/", {"count": count})

    def result(self, call_execution_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._call(f"/call-executions/{call_execution_id}/result/", payload, method="PATCH")

    def recording(self, call_execution_id: str, audio: Path) -> dict[str, Any]:
        """Send one call's audio, as the multipart upload the endpoint expects.

        Built by hand rather than with a library: this is the only multipart request the harness
        makes, and a dependency for one boundary string is not worth carrying.
        """
        edge = "----harness" + os.urandom(8).hex()
        head = (
            f"--{edge}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{audio.name}"\r\n'
            "Content-Type: audio/wav\r\n\r\n"
        ).encode()
        tail = f"\r\n--{edge}--\r\n".encode()
        body = head + audio.read_bytes() + tail
        request = urllib.request.Request(
            f"{self.base}{INGESTION}/call-executions/{call_execution_id}/recording/",
            data=body,
            headers={
                "Content-Type": f"multipart/form-data; boundary={edge}",
                "X-Api-Key": self.key,
                "X-Secret-Key": self.secret,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as answer:
                return json.loads(answer.read().decode() or "{}")
        except urllib.error.HTTPError as refused:
            detail = refused.read().decode(errors="replace")[:300]
            raise PlatformError(f"recording upload failed ({refused.code}): {detail}") from refused
        except Exception as unreachable:  # noqa: BLE001 - reported, not handled
            raise PlatformError(f"recording could not be sent: {unreachable}") from unreachable


def persona_of(scenario: Any) -> dict[str, Any]:
    """One scenario as the platform's persona record.

    ``persona`` is carried whole so the simulator prompt's placeholder resolves against the same
    person the scenario was written for, rather than a name reconstructed from it.
    """
    persona = getattr(scenario, "persona", None) or {}
    if hasattr(persona, "model_dump"):
        persona = persona.model_dump()
    elif not isinstance(persona, dict):
        persona = {}
    return {
        "name": str(persona.get("name") or getattr(scenario, "name", "") or "caller")[:255],
        "role": str(persona.get("role") or persona.get("occupation") or "")[:255],
        "situation": str(getattr(scenario, "instruction", "") or ""),
        "outcome": str(getattr(scenario, "tests", "") or ""),
        "persona": persona,
    }


# What the harness calls a speaker, and what a transcript row is called on the platform. Anything
# unrecognised is the person, because the agent's turns are the ones we name.
SPEAKERS = {
    "agent": "assistant",
    "assistant": "assistant",
    "bot": "assistant",
    "system": "system",
    "customer": "user",
    "caller": "user",
    "user": "user",
    "tester": "user",
}


def segments_of(result: Any) -> list[dict[str, Any]]:
    """A run's conversation as transcript rows, tool calls included.

    No timings are invented. A typed run has none to give, and a made-up millisecond would be
    indistinguishable from a measured one to everything downstream that averages them. A spoken
    turn the runner timed carries those times through, because the platform derives duration,
    silence, talk ratio and latency from them and can derive none of it from zeros.
    """
    rows: list[dict[str, Any]] = []
    for turn in getattr(result, "exchanges", None) or []:
        said = str(turn.get("text") or "").strip()
        if not said:
            continue
        row = {
            "speaker_role": SPEAKERS.get(str(turn.get("speaker", "")).lower(), "user"),
            "content": said,
        }
        for when in ("start_time_ms", "end_time_ms"):
            if turn.get(when) is not None:
                row[when] = int(turn[when])
        rows.append(row)
    for call in getattr(result, "calls_detail", None) or []:
        rows.append(
            {
                "speaker_role": "tool_calls",
                "content": f"{call.get('name', '')}({json.dumps(call.get('arguments', {}), default=str)})",
            }
        )
        outcome = call.get("error") or call.get("result") or ""
        rows.append(
            {
                "speaker_role": "tool_call_result",
                "content": ("refused: " if call.get("refused") else "") + str(outcome)[:4000],
            }
        )
    return rows


def evaluations_of(result: Any) -> list[dict[str, Any]]:
    """Everything this run judged, as one list the platform can render per call.

    Two kinds arrive from different places and mean different things, so both are named and
    kept apart rather than averaged into a verdict. A sub-goal is deterministic: the world was
    left in a state, or it was not. A metric is scored: the run placed it somewhere between
    nothing and everything. Reporting only the first is what made a scored run look unjudged.
    """
    judged: list[dict[str, Any]] = []
    for check in getattr(result, "checkpoints", None) or []:
        judged.append(
            {
                "name": getattr(check, "name", ""),
                "kind": getattr(check, "kind", "") or "checkpoint",
                "passed": bool(getattr(check, "passed", False)),
                "reason": str(getattr(check, "detail", ""))[:2000],
            }
        )
    for metric in (getattr(result, "measured", None) or {}).get("metrics") or []:
        if not metric.get("applicable", True):
            continue
        judged.append(
            {
                "name": str(metric.get("name", "")),
                "kind": "metric",
                "score": float(metric.get("score", 0.0) or 0.0),
                "reason": str(metric.get("reason", ""))[:2000],
            }
        )
    return [one for one in judged if one["name"]]


def result_of(result: Any) -> dict[str, Any]:
    """One scenario's outcome, in the shape the ingestion API takes.

    Sub-goals travel in ``call_metadata`` rather than as free text: they are what this run
    actually decided, and a page showing one goal per column needs them named and separate.
    """
    checkpoints = [
        {
            "name": getattr(check, "name", ""),
            "kind": getattr(check, "kind", ""),
            "passed": bool(getattr(check, "passed", False)),
            "detail": str(getattr(check, "detail", ""))[:2000],
        }
        for check in getattr(result, "checkpoints", None) or []
    ]
    problems = list(getattr(result, "problems", None) or [])
    payload: dict[str, Any] = {
        # A scenario that never ran is not a scenario the agent failed, and the two must not
        # arrive as the same status.
        "status": "failed" if problems else "completed",
        "duration_seconds": max(0, int(getattr(result, "seconds", 0) or 0)),
        "ended_reason": (getattr(result, "ended", "") or "")[:10000],
        "call_summary": (getattr(result, "line", lambda: "")() or "")[:2000],
        "transcript": segments_of(result),
        "evaluations": evaluations_of(result),
        "call_metadata": {
            "harness_scenario": getattr(result, "scenario", ""),
            "harness_passed": bool(getattr(result, "passed", False)),
            "harness_met": int(getattr(result, "met", 0) or 0),
            "harness_of": len(checkpoints),
            "harness_checkpoints": checkpoints,
            "harness_spent_usd": round(float(getattr(result, "spent_usd", 0.0) or 0.0), 4),
        },
    }
    if problems:
        payload["error_message"] = "; ".join(problems)[:2000]
    return payload


def report(
    results: list[Any],
    scenarios: list[Any],
    *,
    name: str,
    run_test_id: str = "",
    modality: str = "text",
    platform: Platform | None = None,
) -> Reported:
    """Report one suite run, and say where it landed.

    ``run_test_id`` is reused when the session already has one, so a second run adds a second
    execution to the same test rather than a second test with one run in it.

    ``modality`` decides how the run is rendered: a spoken call reported as text lands in the
    chat view, with no player and no audio, whatever actually happened on it.
    """
    api = platform or Platform()
    reported, ids = begin(
        scenarios,
        name=name,
        run_test_id=run_test_id,
        modality=modality,
        platform=api,
    )

    # Calls come back in the order the scenarios were attached, which is the order they were run
    # in. Zip rather than assume equal length: a suite can be a subset of its own test.
    for call_execution_id, result in zip(ids, results, strict=False):
        send_result(reported, call_execution_id, result, platform=api)
    if len(ids) < len(results):
        reported.problems.append(
            f"the platform allocated {len(ids)} calls for {len(results)} scenarios, "
            "so the rest were not reported"
        )
    return reported


def begin(
    scenarios: list[Any],
    *,
    name: str,
    run_test_id: str = "",
    modality: str = "text",
    platform: Platform | None = None,
) -> tuple[Reported, list[str]]:
    """Create the platform rows before a suite starts, so the run is visible while it runs."""
    api = platform or Platform()
    reported = Reported(run_test_id=run_test_id)
    if not reported.run_test_id:
        provisioned = api.provision(
            name, [persona_of(one) for one in scenarios], modality=modality
        )
        reported.run_test_id = str(provisioned.get("run_test_id", ""))
    if not reported.run_test_id:
        raise PlatformError("the platform returned no run test to report against")

    started = api.start(reported.run_test_id)
    reported.test_execution_id = str(started.get("test_execution_id", ""))
    if not reported.test_execution_id:
        raise PlatformError("the platform returned no test execution for this run")
    claimed = api.batch(reported.test_execution_id, max(1, len(scenarios)))
    return reported, [str(one) for one in claimed.get("call_execution_ids", [])]


def send_result(
    reported: Reported,
    call_execution_id: str,
    result: Any,
    *,
    platform: Platform | None = None,
) -> None:
    """Patch one pre-allocated platform row as soon as its scenario finishes."""
    api = platform or Platform()
    try:
        api.result(call_execution_id, result_of(result))
        reported.calls[getattr(result, "scenario", "")] = call_execution_id
        audio = str(getattr(result, "recording", "") or "")
        if audio and Path(audio).exists():
            try:
                api.recording(call_execution_id, Path(audio))
            except PlatformError as refused:
                reported.problems.append(f"recording not sent: {refused}")
    except PlatformError as failed:
        reported.problems.append(f"{getattr(result, 'scenario', '?')}: {failed}")


def deliver(
    results: list[Any],
    scenarios: list[Any],
    destination: Path | None,
    *,
    modality: str = "text",
) -> tuple[Reported | None, list[str]]:
    """Report a finished run, and say what happened, without ever failing the run.

    Shared by every way a suite can be started, because a run that only appears on the platform
    when it was started from one particular button is worse than one that never appears: which
    runs exist then depends on how they were launched, and nobody can tell that from the page.

    The suite has finished and its results are on disk by the time this is called, so an
    unreachable platform is worth saying out loud and not worth throwing a completed run away
    over. Returns what was reported, if anything, and the lines to show whoever asked.
    """
    blocked = configured()
    if blocked:
        return None, [f"not reported to the platform: {blocked}"]
    try:
        reported = report(
            results,
            scenarios,
            name=(destination.name if destination else "harness run"),
            run_test_id=remembered(destination) if destination else "",
            modality=modality,
        )
    except PlatformError as failed:
        return None, [f"the run finished, but reporting it to the platform failed: {failed}"]
    if destination:
        remember(destination, reported)
    said = [f"partly reported: {problem}" for problem in reported.problems]
    said.append(f"reported to the platform: {reported.url}")
    return reported, said


def remember(destination: Path, reported: Reported) -> None:
    """Keep where a session reports to, so its next run joins the same test."""
    (Path(destination) / "platform.json").write_text(
        json.dumps(
            {
                "run_test_id": reported.run_test_id,
                "test_execution_id": reported.test_execution_id,
                "url": reported.url,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def reported_to(destination: Path | None) -> dict[str, str]:
    """Where this session's runs have been reported, or nothing.

    Read rather than held in memory, because a session outlives the process that reported it:
    reopening one has to be able to find the run it already has.
    """
    kept = Path(destination) / "platform.json" if destination else None
    if kept is None or not kept.exists():
        return {}
    try:
        found = json.loads(kept.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a damaged file just means provisioning again
        return {}
    return found if isinstance(found, dict) else {}


def remembered(destination: Path) -> str:
    """The run test this session already has, or an empty string."""
    return str(reported_to(destination).get("run_test_id", ""))
