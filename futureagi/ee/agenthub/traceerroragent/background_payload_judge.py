"""Judge consumes only schema-checked original arguments in background mode."""

from ee.agenthub.traceerroragent.background_payloads import refuse, require_payload


def execute_judge_tool(judge, name, payload):
    witness = judge.completion_witness
    data = require_payload(payload, witness, "judge-tool:" + name)
    try:
        if name == "submit_finding":
            with judge._counter_lock:
                judge._error_counter += 1
                number = judge._error_counter
            error = {**data, "error_id": f"E{number:03}", "cluster_id": f"C{number:02}"}
            judge.errors.append(error)
            return {"status": "accepted", "error_id": error["error_id"]}
        if name == "submit_scores":
            judge.scores = data
            return {"status": "accepted"}
        if name == "submit_summary":
            if data["error_count"] != len(judge.errors):
                refuse(witness)
            judge.summary = data
            judge._investigation_complete = True
            return {"status": "accepted", "investigation_complete": True}
        handlers = {
            "read_span": judge._tool_read_span,
            "read_span_exact": judge._tool_read_span_exact,
            "get_children": judge._tool_get_children,
            "get_spans_by_type": judge._tool_get_spans_by_type,
            "search_spans": judge._tool_search_spans,
        }
        if name not in handlers:
            refuse(witness)
        result = handlers[name](**data)
        if not result or "error" in result or result.get("status") == "rejected":
            refuse(witness)
        return result
    except BaseException:
        witness.failed = True
        raise
