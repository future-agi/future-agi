"""What a spoken run leaves behind, beyond whether it passed.

ALK measures a great deal about a call and writes it into its own report: seventeen metrics per
case, what the simulated caller cost and which model it ran on, why the call ended, which LiveKit
room it happened in, four separate audio tracks, and a declaration of what each evidence source
can actually prove. None of that was reaching the harness, which kept one boolean and a wav.

So this reads that report and carries it through. It does not compute anything: everything here
was already measured by the thing that placed the call, and recomputing it would be a second
opinion nobody asked for.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import ARTIFACTS_ROOT

# Where the voice runner writes. Its own directory, because the call belongs to it.
ACCEPTANCE = ARTIFACTS_ROOT / "simulation-acceptance"

# Which recording to prefer, best first. Both voices on one track beats either alone, because
# the questions asked of a call are mostly about the interaction: whether the agent talked over
# the caller, how long it left them waiting, whether what it heard was what was said.
TRACKS = (
    ("stereo", "audio_stereo_path"),
    ("combined", "audio_combined_path"),
    ("caller", "audio_input_path"),
    ("agent", "audio_output_path"),
)


def newest_report(started: float) -> dict[str, Any]:
    """The voice runner's report for the call that just happened, or nothing.

    Only a report written after this run began counts. The newest file on disk is otherwise last
    week's call wearing today's verdict, which is the kind of mistake that is never noticed
    because the numbers look plausible.
    """
    if not ACCEPTANCE.exists():
        return {}
    newest: tuple[float, Path] | None = None
    for report in ACCEPTANCE.glob("run_*/*/report.json"):
        written = report.stat().st_mtime
        if written >= started and (newest is None or written > newest[0]):
            newest = (written, report)
    if newest is None:
        return {}
    try:
        loaded = json.loads(newest[1].read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    cases = loaded.get("results") or []
    return cases[0] if cases else {}


def tracks_in(case: dict[str, Any]) -> list[dict[str, str]]:
    """Every recording of this call that exists, best first.

    Several are written and any of them can be missing: a provider that did not return its own
    copy, a track that never carried audio, a run that stopped early. Offering the list rather
    than one path is what lets the page fall back instead of showing a broken player.
    """
    found: list[dict[str, str]] = []
    for label, key in TRACKS:
        path = case.get(key)
        if path and Path(path).exists():
            found.append({"label": label, "path": str(path)})
    # The provider's own recording, which survives when the room's tracks do not.
    for artifact in (case.get("metadata") or {}).get("provider_artifacts") or []:
        path = artifact.get("path")
        if artifact.get("type") == "audio" and path and Path(path).exists():
            found.append({"label": f"{artifact.get('artifact_id', 'provider')}", "path": str(path)})
    return found


def metrics_in(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Every metric ALK computed, with why it came out that way.

    The averages alone were a trap. A metric with nothing to measure scores 1.0 and says so in
    its reason: "No required browser trace keys provided", "No expected multi-agent coordination
    checks provided". Twenty-four of thirty-eight are that, so a page reading only the numbers
    showed a wall of perfect greens for browser safety and multi-agent coordination on a phone
    call that had neither. That is the same vacuity the harness refuses to tolerate in its own
    checks, and it has no business in the report either.

    ALK already separates them with ``applicable``. Carrying the reason as well is what lets a
    reader tell "scanned three steps and found nothing" from "there was nothing to scan".
    """
    found = report.get("metrics")
    if isinstance(found, list) and found:
        return [
            {
                "name": one.get("name"),
                "score": one.get("score"),
                "reason": one.get("reason") or "",
                # Absent means applicable: an older report that never carried the flag was
                # measuring something, or it would not have been asked for.
                "applicable": bool(one.get("applicable", True)),
            }
            for one in found
            if isinstance(one, dict) and one.get("name")
        ]
    # A report shaped before metrics carried their reasons. Averages are all there is, and every
    # one is treated as applicable rather than silently dropped.
    averages = (report.get("summary") or {}).get("metric_averages") or {}
    return [
        {"name": name, "score": score, "reason": "", "applicable": True}
        for name, score in averages.items()
    ]


def spoken_times(case: dict[str, Any]) -> list[dict[str, Any]]:
    """When each turn was actually spoken, as milliseconds from the start of the call.

    The runner times its own speech but only records when the agent's words arrived, so a turn
    it did not measure carries no times rather than guessed ones. Everything downstream treats
    a missing time as unknown and a present one as observed, and the difference matters: a
    fabricated millisecond is indistinguishable from a measured one once it has been averaged.
    """
    messages = case.get("messages") or []
    starts = [m.get("started_speaking_at") for m in messages if m.get("started_speaking_at")]
    if not starts:
        return []
    origin = min(float(one) for one in starts)

    def offset(at: Any) -> int | None:
        return None if not at else max(0, int((float(at) - origin) * 1000))

    return [
        {
            "role": str(one.get("role") or ""),
            "start_time_ms": offset(one.get("started_speaking_at")),
            "end_time_ms": offset(one.get("stopped_speaking_at")),
            "interrupted": bool(one.get("interrupted")),
        }
        for one in messages
    ]


def measured(case: dict[str, Any]) -> dict[str, Any]:
    """What ALK measured about this call, in the shape the page reads.

    Everything optional, because a report from a run that failed early has most of it missing and
    a page that assumes otherwise shows nothing at all rather than the part that is there.
    """
    metadata = case.get("metadata") or {}
    report = (case.get("evaluation") or {}).get("agent_report") or metadata.get(
        "agent_report_summary"
    ) or {}
    usage = metadata.get("simulator_model_usage") or []
    first = usage[0] if isinstance(usage, list) and usage else {}
    return {
        "score": report.get("score"),
        "threshold": report.get("threshold"),
        "scored_pass": report.get("passed"),
        "metrics": metrics_in(report),
        "stop_reason": metadata.get("stop_reason"),
        "status": metadata.get("status"),
        "room": metadata.get("room_name"),
        "provider": metadata.get("target_provider"),
        "call_id": metadata.get("vapi_call_id") or metadata.get("provider_call_id"),
        "simulator": {
            "model": first.get("model"),
            "provider": first.get("provider"),
            "input_tokens": first.get("input_tokens"),
            "cached_tokens": first.get("input_cached_tokens"),
            "output_tokens": first.get("output_tokens"),
        },
        # What each source claims it can prove. Worth showing: a metric derived from a source
        # that does not report latency is not a measurement, and the report says which is which.
        "evidence": [
            {
                "source": one.get("source_id"),
                "adapter": one.get("adapter"),
                "available": one.get("available"),
                "proves": sorted(
                    key for key, held in (one.get("capabilities") or {}).items() if held
                ),
            }
            for one in metadata.get("evidence") or []
        ],
    }
