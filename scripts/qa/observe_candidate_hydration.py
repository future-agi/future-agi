"""Exercise actual page-scoped CH25 content/attribute builders in the replay.

Selection alone is not a visible-page latency measurement. This includes the
content SQL and public merge/drift checks, serialized to keep the production
diagnostic to one statement in flight. It is still not an HTTP/serializer or
PG/eval/annotation-cell enrichment qualification.
"""

from contextlib import nullcontext
import json
import sys

import replay_observe_filters as replay


CONTENT_KEYS = (
    "input",
    "output",
    "attributes_extra",
    "attrs_string",
    "attrs_number",
    "attrs_bool",
)


def hydrate_voice_page(reader, builder, payload):
    """Read every selected Voice root's raw content under the existing guards.

    Reuse the public physical-identity builders, finite batches and memory-only
    split policy. Provider formatting and relational enrichments remain a
    separate gate; this diagnostic cannot claim a complete public request.
    """
    if payload.get("query_complete") is not True:
        return payload
    from django.conf import settings
    from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded

    rows = [dict(row) for row in payload.get("table", [])]
    # Validate the complete page before any batch, including cross-batch
    # duplicates and project escape. Never silently truncate identities.
    identities = builder.content_root_identities_for_rows(rows) if rows else []
    if len(set(identities)) != len(rows):
        raise replay.ReplayError("REPLAY_DUPLICATE_VOICE_ROOT_IDENTITY")
    phases, hydrated = [], []
    attempts = 0

    def hydrate_batch(batch):
        nonlocal attempts
        if attempts >= settings.VOICE_CONTENT_MAX_QUERY_ATTEMPTS:
            raise ReadDeadlineExceeded("voice content hydration query budget exceeded")
        remaining = reader.remaining_read_ms()
        if remaining < 25:
            raise ReadDeadlineExceeded("voice content hydration read deadline exceeded")
        batch_identities = builder.content_root_identities_for_rows(batch)
        sql, params = builder.build_content_query(
            [identity[2] for identity in batch_identities],
            root_identities=batch_identities,
        )
        if not sql:
            raise replay.ReplayError("REPLAY_REQUIRED_HYDRATION_QUERY_MISSING")
        attempts += 1
        try:
            content = reader.execute_ch_query(
                sql,
                params,
                timeout_ms=min(remaining, settings.VOICE_CONTENT_MIN_REMAINING_MS),
                settings={
                    "max_result_rows": len(batch_identities),
                    "result_overflow_mode": "throw",
                },
            ).data
        except Exception as exc:
            if getattr(exc, "code", None) != 241 or len(batch) == 1:
                raise
            midpoint = len(batch) // 2
            return hydrate_batch(batch[:midpoint]) + hydrate_batch(batch[midpoint:])
        if not builder.content_root_rows_match(batch, content):
            raise replay.ReplayError("REPLAY_CONTENT_IDENTITY_OR_VERSION_DRIFT")
        required = {
            "provider",
            "span_attributes",
            "attrs_string",
            "attrs_number",
            "attrs_bool",
        }
        if any(not required <= row.keys() for row in content):
            raise replay.ReplayError("REPLAY_VOICE_CONTENT_FIELDS_MISSING")
        phases.append({"name": "content", "rows": len(content)})
        return content

    for start in range(0, len(rows), settings.VOICE_CONTENT_MAX_BATCH_SIZE):
        hydrated.extend(
            hydrate_batch(rows[start : start + settings.VOICE_CONTENT_MAX_BATCH_SIZE])
        )
    if rows and not builder.content_root_rows_match(rows, hydrated):
        raise replay.ReplayError("REPLAY_CONTENT_IDENTITY_OR_VERSION_DRIFT")
    content_by_identity = {
        builder.bounded_filter_page_hydration_identity(row): row for row in hydrated
    }
    for row, identity in zip(rows, identities):
        content = content_by_identity[identity]
        # Preserve actual NULLs, long values and all fields returned by the
        # public content builder. Only that builder excludes bulky call_logs.
        row.update(content)
    return {
        **payload,
        "table": rows,
        "hydration_phases": phases,
        "query_layer_coverage": {
            "contract": "CH25_voice_physical_root_content.v1",
            "selection": True,
            "content": True,
            "execution": "serial_read_only_candidate_diagnostic_guards",
            "omitted_public_phases": [
                "provider_attribute_normalization_and_formatting",
                "eval_query_and_cell_enrichment",
                "annotation_query_and_cell_enrichment",
                "PG_configuration",
                "HTTP_serializer_and_transport",
            ],
            "full_public_request": False,
        },
    }


def hydrate_session_queries(reader, builder, payload, *, remaining_ms):
    """Time all three real Session hydration builders on either selector route.

    This preserves their raw phase results, not public formatting/PG/Score
    enrichment. A selection-only fallback must not earn a full-page timing.
    """
    if payload.get("query_complete") is not True:
        return payload
    ids = [str(row["session_id"]) for row in payload.get("table", [])]
    phases = {}
    if ids:
        for name, method in (
            ("metrics", builder.build_page_metrics_query),
            ("content", builder.build_content_query),
            ("attributes", builder.build_span_attributes_query),
        ):
            sql, params = method(ids)
            phases[name] = reader.execute_ch_query(
                sql, params, timeout_ms=remaining_ms()
            ).data
    return {
        **payload,
        "query_phases": phases,
        "serial_enrichment_not_http_or_pg_overlay": True,
    }


def requested_attribute_keys(request):
    raw = (request.get("params") or {}).get("attribute_keys", "[]")
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError) as exc:
        raise replay.ReplayError("REPLAY_ATTRIBUTE_KEYS_INVALID") from exc
    if not isinstance(value, list) or any(
        not isinstance(key, str) or not key for key in value
    ):
        raise replay.ReplayError("REPLAY_ATTRIBUTE_KEYS_INVALID")
    return list(dict.fromkeys(value))


def hydrate_page(reader, builder, payload, *, entity, request, timing=None):
    """Return the same selected identities with verified current content.

    A missing/changed content row invalidates this diagnostic completion, just
    as the public page rejects hydration drift; it is never filled with fake
    empty values. Raw content stays in process and only its hash enters the
    replay ledger.
    """
    if entity not in {"traces", "spans"}:
        raise replay.ReplayError("REPLAY_HYDRATION_ENTITY_UNSUPPORTED")
    if payload.get("query_complete") is not True:
        return payload
    from tracer.services.clickhouse.v2.span_selectors import merge_content_rows

    rows = [dict(row) for row in payload.get("table", [])]
    phases = []
    coverage = {
        "contract": "CH25_content_and_requested_attributes.v1",
        "selection": True,
        "content": not rows,
        "requested_attributes": not rows or entity == "spans",
        "execution": "serial_read_only_not_public_parallel_scheduling",
        "omitted_public_phases": [
            "eval_cell_enrichment",
            "annotation_cell_enrichment",
            "end_user_labels",
            "PG_configuration",
            "HTTP_serializer_and_transport",
        ],
        "full_public_request": False,
    }

    def execute(name, query_and_params):
        sql, params = query_and_params
        if not sql:
            raise replay.ReplayError("REPLAY_REQUIRED_HYDRATION_QUERY_MISSING")
        result = reader.execute_ch_query(sql, params).data
        phases.append({"name": name, "rows": len(result)})
        return result

    if rows:
        if entity == "spans":
            from tracer.views.observation_span import _merge_span_page_content

            identities = [builder.bounded_filter_row_identity(row) for row in rows]
            ids = list(dict.fromkeys(str(row["id"]) for row in rows))
            content = execute(
                "content", builder.build_content_query(ids, span_identities=identities)
            )
            if not _merge_span_page_content(
                rows, content, builder=builder, keys=CONTENT_KEYS
            ):
                raise replay.ReplayError("REPLAY_CONTENT_IDENTITY_OR_VERSION_DRIFT")
        else:
            with timing(
                "trace_view_lazy_import",
                module_already_loaded="tracer.views.trace" in sys.modules,
            ) if timing is not None else nullcontext():
                from tracer.views.trace import _decode_projected_trace_attribute_value

            identities = builder.content_root_identities_for_rows(rows)
            ids = list(dict.fromkeys(str(row["trace_id"]) for row in rows))
            content = execute(
                "content", builder.build_content_query(ids, root_identities=identities)
            )
            if not builder.content_root_rows_match(rows, content):
                raise replay.ReplayError("REPLAY_CONTENT_IDENTITY_OR_VERSION_DRIFT")
            merge_content_rows(
                rows,
                content,
                id_key=("project_id", "trace_id"),
                # Preserve every raw trace-content field for independent
                # comparison. HTTP metadata parsing remains a separate gate.
                keys=(*CONTENT_KEYS, "trace_tags", "metadata"),
            )
            keys = requested_attribute_keys(request)
            if keys:
                trace_identities = tuple(
                    (str(row["project_id"]), str(row["trace_id"])) for row in rows
                )
                if len(set(trace_identities)) != len(rows):
                    raise replay.ReplayError("REPLAY_DUPLICATE_TRACE_IDENTITY")
                attributes = execute(
                    "requested_attributes",
                    builder.build_span_attributes_query(
                        ids,
                        attribute_keys=keys,
                        trace_identities=trace_identities,
                    ),
                )
                # One row per requested trace/key is the current public query
                # contract, not a latency-driven cap or a truncated population.
                if len(attributes) > len(rows) * len(keys):
                    raise replay.ReplayError(
                        "REPLAY_ATTRIBUTE_RESULT_IDENTITY_OVERFLOW"
                    )
                projected, seen = {}, set()
                for attribute in attributes:
                    identity = (
                        str(attribute.get("project_id") or ""),
                        str(attribute.get("trace_id") or ""),
                    )
                    key = attribute.get("attribute_key")
                    if identity not in trace_identities or key not in keys:
                        raise replay.ReplayError("REPLAY_ATTRIBUTE_SCOPE_ESCAPE")
                    coordinate = (*identity, key)
                    if coordinate in seen:
                        raise replay.ReplayError("REPLAY_ATTRIBUTE_DUPLICATE")
                    seen.add(coordinate)
                    projected.setdefault(identity, {})[key] = [
                        _decode_projected_trace_attribute_value(
                            attribute.get("attribute_value_json")
                        )
                    ]
                for row in rows:
                    identity = (str(row["project_id"]), str(row["trace_id"]))
                    row["replay_requested_attributes"] = projected.get(identity, {})
            coverage["requested_attributes"] = True
        coverage["content"] = True
    return {
        **payload,
        "table": rows,
        "query_layer_coverage": coverage,
        "hydration_phases": phases,
    }
