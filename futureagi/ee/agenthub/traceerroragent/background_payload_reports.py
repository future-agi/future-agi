"""Typed model-report boundaries, shared by Falcon tools and Judge/Chauffeur."""

from ee.agenthub.traceerroragent.background_payload_schemas import (
    CHAUFFEUR,
    JUDGE_ARGUMENTS,
    JUDGE_REPORT,
    TEXT,
)
from ee.agenthub.traceerroragent.background_payloads import (
    ValidatedPayload,
    refuse,
    require_payload,
    validate_payload,
)


def chauffeur_report(raw, owner, *, decode=False, span_ids=None):
    if isinstance(raw, ValidatedPayload):
        data = require_payload(raw, owner, "chauffeur")
    else:
        checked = validate_payload(
            raw, CHAUFFEUR, owner, "chauffeur-source", decode=decode
        )
        data = require_payload(checked, owner, "chauffeur-source")
        if "span_groups" in data:
            groups = data["span_groups"]
            data = {
                "sub_flows": [
                    {
                        "name": g["signal"],
                        "summary": g["signal"],
                        "spans": g["spans"],
                        "flags": [],
                    }
                    for g in groups
                ],
                "trace_overview": "\n".join(g["signal"] for g in groups),
            }
    if span_ids is not None:
        covered = {sid for flow in data["sub_flows"] for sid in flow["spans"]}
        if covered != set(span_ids):
            refuse(owner)
    return validate_payload(data, CHAUFFEUR, owner, "chauffeur")


def reader_report(raw, owner):
    checked = validate_payload(raw, TEXT, owner, "reader")
    return require_payload(checked, owner, "reader")


def judge_arguments(name, raw, owner, *, decode=False):
    schema = JUDGE_ARGUMENTS.get(name)
    if schema is None:
        refuse(owner)
    return validate_payload(raw, schema, owner, "judge-tool:" + name, decode=decode)


def judge_report(raw, owner):
    report = validate_payload(raw, JUDGE_REPORT, owner, "judge")
    data = require_payload(report, owner, "judge")
    if data["summary"]["error_count"] != len(data["errors"]):
        refuse(owner)
    return report


def publication_result(result, report, owner):
    """Compose only from a previously checked Judge report, never a raw dict."""
    data = require_payload(report, owner, "judge")
    if any(result.get(key) != data[key] for key in ("errors", "scores", "summary")):
        refuse(owner)
    # Metadata is host-produced. Only validated report members can reach the
    # service; keep them sealed while carrying the host's trace/memory fields.
    import json

    from ee.agenthub.traceerroragent.background_payloads import _SEAL, ValidatedPayload

    return ValidatedPayload(
        json.dumps({**result, **data}, allow_nan=False), owner, "publication", _SEAL
    )


def falcon_report(result, owner):
    """Falcon's published model report is its text, not host usage metadata.

    Tool arguments enter the same validator at each canonical tool.run. The
    AgentLoop envelope also carries host IDs, metrics and tool execution logs;
    those are not allowed to substitute for a missing/invalid model report.
    """
    if type(result) is not dict or "content" not in result:
        refuse(owner)
    from ee.agenthub.traceerroragent.background_payload_wire import require_falcon_text

    return require_falcon_text(result["content"], owner)


def validated_errors(agent):
    report = getattr(agent, "_validated_model_result", None)
    if not isinstance(report, ValidatedPayload):
        refuse(None)
    from ee.agenthub.traceerroragent.background_payload_consumers import evidence

    return evidence(report, report._witness, "judge").errors


def require_publication(result):
    from tracer.services.analysis_result_binding import current_attempt

    if current_attempt() is None:
        return result
    if not isinstance(result, ValidatedPayload):
        refuse(None)
    if getattr(result._witness, "payload_identity", None) != current_attempt():
        refuse(result._witness)
    result._witness.require()
    return require_payload(result, result._witness, "publication")
