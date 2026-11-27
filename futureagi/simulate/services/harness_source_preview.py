"""Bounded source details that an authorized reviewer can inspect."""

import re
from pathlib import PurePath

STATEMENT_PREVIEW_LIMIT = 2000


def safe_diagnostics(items: list[dict]) -> list[dict]:
    return [
        {
            "code": item["code"],
            "severity": item["severity"],
            "span": {
                key: span[key] for key in ("source_id", "page", "row") if key in span
            },
        }
        for item in items
        if isinstance(item, dict)
        and re.fullmatch(r"[a-z_]{1,100}", str(item.get("code") or ""))
        and item.get("severity") in {"warning", "blocking"}
        and isinstance(span := item.get("span") or {}, dict)
    ]


def _rejected_row_reason(message):
    """Describe parser findings without copying field names or record values."""
    width = re.fullmatch(r"Row [0-9]+ has ([0-9]+) cells; expected ([0-9]+)", message)
    if width:
        return f"Found {width[1]} cells, expected {width[2]}."
    if message == "The CSV body contains a malformed row":
        return "Malformed CSV row."
    if re.fullmatch(r"JSON item [0-9]+ is not an object", message):
        return "Expected a JSON object."
    if message.startswith("JSON item ") and message.endswith(
        " is nested; flatten it before upload"
    ):
        return "The row contains a nested object or array. Flatten it before uploading."
    return "The row could not be read."


_RULE_TITLES = {
    "contains": "Responses must include “{}”",
    "not_contains": "Responses must not include “{}”",
    "equals": "Responses must be exactly “{}”",
}


def _rule_title(rule: dict) -> str | None:
    """A short, plain title for a checkable rule, built from its check."""
    value = (rule.get("check_arguments") or {}).get("value")
    template = _RULE_TITLES.get(rule.get("check_type"))
    if rule.get("status") != "supported" or not template or not isinstance(value, str):
        return None
    return template.format(value[:160])


def _statement_preview(statement, pages, withheld):
    span = statement.get("span") or {}
    result = {"statement_id": statement["statement_id"], "page": span.get("page")}
    if withheld:
        return result
    text = statement.get("text")
    page = pages.get(span.get("page"))
    if not text or not page:
        return result
    start, end = span.get("start"), span.get("end")
    if start is None and end is None:
        matches = page == text
    else:
        matches = (
            isinstance(start, int)
            and isinstance(end, int)
            and 0 <= start <= end <= len(page)
            and end - start == len(text)
            and page.startswith(text, start, end)
        )
    if matches:
        result.update(
            text=text[:STATEMENT_PREVIEW_LIMIT],
            text_truncated=len(text) > STATEMENT_PREVIEW_LIMIT,
        )
    return result


def safe_source_preview(draft: dict, origins: list[dict]) -> dict:
    identity = {
        item["source_id"]: {
            key: item[key]
            for key in ("kind", "document_id", "kb_id", "file_id")
            if key in item
        }
        for item in origins
    }
    names = {item["source_id"]: item.get("original_name") for item in origins}
    withheld = {
        (issue.get("span") or {}).get("source_id")
        for issue in draft.get("diagnostics", [])
        if issue.get("code") in {"source_privacy_unresolved", "source_content_flagged"}
    }
    withheld.update(
        sid
        for sid, origin in identity.items()
        if origin.get("kind") == "knowledge_base"
    )
    display = []
    for item in draft.get("sources", []):
        pin = item["pin"]
        sid = pin["source_id"]
        name = str(names.get(sid) or item.get("filename") or "").replace("\\", "/")
        filename = "".join(c for c in PurePath(name).name if c.isprintable())[:255]
        pages = {page["page"]: page["text"] for page in item.get("pages", [])}
        safe_fields = {
            field["source_field"]
            for field in item.get("fields", [])
            if field.get("personal_data_suspected") is False
        }
        samples = []
        if sid not in withheld:
            for row in item.get("rows", [])[:5]:
                values = row.get("values") or []
                pairs = values.items() if isinstance(values, dict) else values
                samples.append(
                    {
                        pair[0]: str(pair[1])[:160] if pair[1] is not None else None
                        for pair in pairs
                        if isinstance(pair, (list, tuple))
                        and len(pair) == 2
                        and pair[0] in safe_fields
                    }
                )
        display.append(
            {
                "pin": {
                    key: pin[key]
                    for key in ("source_id", "source_sha256", "parser_revision", "role")
                    if key in pin
                },
                "filename": filename,
                "sample_rows": samples,
                "rejected_count": len(item.get("rejected_records", [])),
                "rejected_rows": [
                    r["source_row"] for r in item.get("rejected_records", [])[:100]
                ],
                "rejected_row_details": [
                    {
                        "row": record["source_row"],
                        "reasons": list(
                            dict.fromkeys(
                                _rejected_row_reason(reason)
                                for reason in record.get("reasons", [])
                                if isinstance(reason, str)
                            )
                        ),
                    }
                    for record in item.get("rejected_records", [])[:100]
                ],
                "origin": identity.get(pin["source_id"]),
                "parse_state": item.get("parse_state"),
                "row_count": len(item.get("rows", [])),
                "fields": [
                    {
                        key: field[key]
                        for key in (
                            "source_field",
                            "inferred_type",
                            "null_count",
                            "unique_count",
                            "suggested_target_field",
                            "suggested_stable_id",
                            "personal_data_suspected",
                        )
                        if key in field
                    }
                    for field in item.get("fields", [])
                ],
                "statements": [
                    _statement_preview(statement, pages, sid in withheld)
                    for statement in item.get("statements", [])
                ],
            }
        )
    proposals = draft.get("proposals") or {}
    hidden_statements = {
        statement["statement_id"]
        for item in draft.get("sources", [])
        if item["pin"]["source_id"] in withheld
        for statement in item.get("statements", [])
    }
    return {
        "draft_digest": draft["draft_digest"],
        "sources": display,
        "destination_schema": draft.get("destination_schema"),
        "diagnostics": safe_diagnostics(draft.get("diagnostics", [])),
        "proposals": {
            "field_mappings": proposals.get("field_mappings", []),
            "relationships": [
                {"relationship": item["relationship"], "confidence": item["confidence"]}
                for item in proposals.get("relationships", [])
            ],
            "policy_rules": [
                {
                    **{
                        key: item[key]
                        for key in (
                            "rule_id",
                            "statement_id",
                            "kind",
                            "check_type",
                            "conflict_group",
                            "status",
                        )
                        if key in item
                    },
                    # The literal comes from the file, so withheld files get no title.
                    **(
                        {"title": title}
                        if item.get("statement_id") not in hidden_statements
                        and (title := _rule_title(item))
                        else {}
                    ),
                }
                for item in proposals.get("policy_rules", [])
            ],
        },
    }


def public_source_preview(preview: dict) -> dict:
    """Older stored previews may include excerpts from a knowledge base."""
    if not preview or not any(
        (source.get("origin") or {}).get("kind") == "knowledge_base"
        for source in preview.get("sources", [])
    ):
        return preview
    sources = []
    for source in preview["sources"]:
        if (source.get("origin") or {}).get("kind") == "knowledge_base":
            source = {
                **source,
                "sample_rows": [],
                "statements": [
                    {
                        key: statement[key]
                        for key in ("statement_id", "page")
                        if key in statement
                    }
                    for statement in source.get("statements", [])
                ],
            }
        sources.append(source)
    return {**preview, "sources": sources}
