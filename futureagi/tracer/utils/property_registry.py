"""Stable logical property identities shared by discovery and filter APIs.

The registry names a property definition; it does not replace the native fact
store.  Callers decode an identity to the existing system/eval/annotation/
attribute/dataset adapter and keep authorization scope in the request.
"""

PROPERTY_KIND_TO_METRIC_TYPE = {
    "system_attribute": "system_metric",
    "custom_attribute": "custom_attribute",
    "eval_config": "eval_metric",
    "eval_template": "eval_metric",
    # Read-only compatibility for identities emitted before config and
    # template UUIDs were split into distinct registry kinds. New discovery
    # responses never emit this kind, and exact graph requests reject it.
    "eval": "eval_metric",
    "annotation": "annotation_metric",
    "dataset_column": "custom_column",
}

PROPERTY_KIND_TO_FILTER_COLUMN_TYPE = {
    "system_attribute": "SYSTEM_METRIC",
    "custom_attribute": "SPAN_ATTRIBUTE",
    "eval_config": "EVAL_METRIC",
    "eval_template": "EVAL_METRIC",
    "eval": "EVAL_METRIC",
    "annotation": "ANNOTATION",
    "dataset_column": "CUSTOM_COLUMN",
}

_PROPERTY_KIND_ALLOWED_SOURCES = {
    "custom_attribute": frozenset({"traces"}),
    "eval_config": frozenset(
        {"traces", "sessions", "datasets", "simulation", "all", "both"}
    ),
    "eval_template": frozenset(
        {"traces", "sessions", "datasets", "simulation", "all", "both"}
    ),
    "eval": frozenset({"traces", "sessions", "datasets", "simulation", "all", "both"}),
    "annotation": frozenset({"traces", "sessions", "datasets", "both", "all"}),
    "dataset_column": frozenset({"datasets", "dataset_column"}),
}

_LOGICAL_SOURCE_TO_TRANSPORT = {
    "spans": "traces",
    "users": "sessions",
    "voice_calls": "traces",
    "voiceCalls": "traces",
    "prompts": "traces",
    "dataset": "datasets",
}

_SYSTEM_DEFINITION_ALLOWED_TRANSPORTS = {
    "traces": frozenset({"traces"}),
    "spans": frozenset({"traces"}),
    "sessions": frozenset({"sessions"}),
    "users": frozenset({"sessions"}),
    "datasets": frozenset({"dataset", "datasets"}),
    "dataset": frozenset({"dataset", "datasets"}),
    "simulation": frozenset({"simulation"}),
    "voice_calls": frozenset({"traces"}),
    # Prompt metrics are computed from trace facts.  ``prompts`` is the
    # catalog namespace, not a separate native filter transport.
    "prompts": frozenset({"traces"}),
    "all": frozenset(
        {
            "all",
            "both",
            "traces",
            "sessions",
            "datasets",
            "simulation",
        }
    ),
    "both": frozenset({"both", "traces", "datasets"}),
}

_SYSTEM_FILTER_COLUMN_ALIASES = {
    ("sessions", "session"): frozenset({"session", "session_id"}),
    ("sessions", "project"): frozenset({"project", "project_id"}),
    ("sessions", "user"): frozenset({"user", "user_id"}),
    ("traces", "project"): frozenset({"project", "project_id"}),
    ("traces", "session"): frozenset({"session", "session_id"}),
    ("traces", "user"): frozenset({"user", "user_id"}),
    ("spans", "project"): frozenset({"project", "project_id"}),
    ("spans", "session"): frozenset({"session", "session_id"}),
    ("spans", "user"): frozenset({"user", "user_id"}),
    ("users", "project"): frozenset({"project", "project_id"}),
    ("users", "session"): frozenset({"session", "session_id"}),
    ("users", "user"): frozenset({"user", "user_id"}),
}

# There is one logical definition for an identifier even where older list and
# filter payloads use the physical-column spelling (``*_id``).  Keep accepting
# those spellings at the native adapter boundary, but never mint a second
# catalog definition for them.  This mapping deliberately excludes similarly
# named fields such as ``tag``/``tags``: those are distinct values.
_SYSTEM_ATTRIBUTE_CANONICAL_NAMES = {
    ("traces", "project_id"): "project",
    ("traces", "session_id"): "session",
    ("traces", "user_id"): "user",
    ("spans", "project_id"): "project",
    ("spans", "session_id"): "session",
    ("spans", "user_id"): "user",
    ("sessions", "project_id"): "project",
    ("sessions", "session_id"): "session",
    ("sessions", "user_id"): "user",
    ("users", "project_id"): "project",
    ("users", "session_id"): "session",
    ("users", "user_id"): "user",
}


def canonical_system_attribute_name(definition_source: str, metric_name: str) -> str:
    """Return the one public identity for a system field alias.

    Native readers still receive the original column spelling.  This function
    is intentionally pure so the checked-in manifest and legacy list helpers
    can share the same identity rule without a database dependency.
    """

    source = str(definition_source or "").strip()
    name = str(metric_name or "").strip()
    return _SYSTEM_ATTRIBUTE_CANONICAL_NAMES.get((source, name), name)


def property_value_transport_source(source: str) -> str:
    """Translate a logical catalog namespace to its native value adapter.

    Public discovery keeps logical sources such as ``spans`` and ``prompts``.
    The established value readers are physically partitioned by trace/session
    transport, so normalize once at the API boundary before cursor identity and
    adapter dispatch.
    """

    normalized = str(source or "").strip()
    return _LOGICAL_SOURCE_TO_TRANSPORT.get(normalized, normalized)


def normalize_custom_attribute_source(
    source: str | None, *, allow_blank: bool = False
) -> str:
    """Return the one native source supported by custom attributes.

    Discovery accepts logical namespaces such as ``spans``, ``voice_calls``,
    and ``prompts`` while exact value reads operate on trace-backed span
    attributes.  Keeping this admission rule beside the shared transport map
    prevents the definition and value APIs from silently disagreeing.
    """

    normalized = str(source or "").strip()
    if not normalized:
        if allow_blank:
            return ""
        raise ValueError("property source must be a non-empty string")
    transport_source = property_value_transport_source(normalized)
    if transport_source not in _PROPERTY_KIND_ALLOWED_SOURCES["custom_attribute"]:
        raise ValueError("custom_attribute is not compatible with source")
    return transport_source


def parse_property_registry_id(property_id: str) -> dict[str, str]:
    """Decode one public registry identity into its native adapter identity."""

    normalized = str(property_id or "").strip()
    property_kind, separator, remainder = normalized.partition(":")
    if not separator or property_kind not in PROPERTY_KIND_TO_METRIC_TYPE:
        raise ValueError("invalid property_id")
    definition_source = ""
    if property_kind == "system_attribute":
        definition_source, separator, metric_name = remainder.partition(":")
        if not separator or not definition_source or not metric_name:
            raise ValueError("invalid system property_id")
        metric_name = canonical_system_attribute_name(definition_source, metric_name)
        normalized = f"{property_kind}:{definition_source}:{metric_name}"
    else:
        metric_name = remainder
        if not metric_name:
            raise ValueError("invalid property_id")
    return {
        "property_id": normalized,
        "property_kind": property_kind,
        "metric_name": metric_name,
        "metric_type": PROPERTY_KIND_TO_METRIC_TYPE[property_kind],
        "filter_column_type": PROPERTY_KIND_TO_FILTER_COLUMN_TYPE[property_kind],
        "definition_source": definition_source,
        "identity_version": "legacy" if property_kind == "eval" else "current",
    }


def validate_property_source_binding(
    decoded: dict[str, str], source: str | None
) -> dict[str, str]:
    """Reject a registry identity routed through an incompatible adapter.

    Source is optional only for historical filter payloads whose compiler
    already fixes the native surface. Whenever a caller supplies it, it is a
    security-relevant part of the binding and may not disagree with the
    registry definition.
    """

    if source is None:
        return decoded
    normalized_source = str(source or "").strip()
    if not normalized_source:
        raise ValueError("property source must be a non-empty string")

    property_kind = decoded["property_kind"]
    if property_kind == "custom_attribute":
        normalized_source = normalize_custom_attribute_source(normalized_source)
        allowed_sources = _PROPERTY_KIND_ALLOWED_SOURCES[property_kind]
    elif property_kind == "system_attribute":
        normalized_source = property_value_transport_source(normalized_source)
        definition_source = decoded["definition_source"]
        allowed_sources = _SYSTEM_DEFINITION_ALLOWED_TRANSPORTS.get(
            definition_source,
            frozenset({definition_source}),
        )
    else:
        normalized_source = property_value_transport_source(normalized_source)
        allowed_sources = _PROPERTY_KIND_ALLOWED_SOURCES[property_kind]
    if normalized_source not in allowed_sources:
        raise ValueError("property_id is not compatible with source")
    return decoded


def validate_property_filter_binding(
    property_id: str,
    *,
    column_id: str,
    column_type: str | None,
    source: str | None = None,
) -> dict[str, str]:
    """Reject a namespaced identity that disagrees with its native filter.

    Annotation and eval choices retain the native
    ``<definition UUID>**<choice>`` adapter shape, but the UUID prefix must
    match the registry identity exactly.
    Annotator is a second explicit adapter: its historical trace compiler uses
    the dedicated SYSTEM_METRIC pseudo-column.
    """

    decoded = parse_property_registry_id(property_id)
    validate_property_source_binding(decoded, source)
    normalized_column_id = str(column_id or "")
    allowed_column_ids = _SYSTEM_FILTER_COLUMN_ALIASES.get(
        (decoded["definition_source"], decoded["metric_name"]),
        frozenset({decoded["metric_name"]}),
    )
    native_column_id, separator, native_subfield = normalized_column_id.partition("**")
    is_definition_subfield_adapter = (
        decoded["property_kind"]
        in {"annotation", "eval_config", "eval_template", "eval"}
        and bool(separator)
        and native_column_id == decoded["metric_name"]
        and bool(native_subfield)
    )
    if (
        normalized_column_id not in allowed_column_ids
        and not is_definition_subfield_adapter
    ):
        raise ValueError("property_id does not match column_id")
    expected_column_type = decoded["filter_column_type"]
    if column_type and column_type != expected_column_type:
        is_annotator_adapter = (
            decoded["property_kind"] == "annotation"
            and decoded["metric_name"] == "annotator"
            and column_type == "SYSTEM_METRIC"
        )
        if not is_annotator_adapter:
            raise ValueError("property_id does not match filter col_type")
    return decoded


def validate_property_metric_binding(
    property_id: str,
    *,
    metric_name: str,
    metric_type: str,
    source: str | None = None,
) -> dict[str, str]:
    """Bind a saved dashboard metric/breakdown to one registry definition."""

    decoded = parse_property_registry_id(property_id)
    validate_property_source_binding(decoded, source)
    if decoded["metric_name"] != str(metric_name or ""):
        if not (
            decoded["property_kind"] == "system_attribute"
            and str(metric_name or "")
            in _SYSTEM_FILTER_COLUMN_ALIASES.get(
                (decoded["definition_source"], decoded["metric_name"]),
                frozenset(),
            )
        ):
            raise ValueError("property_id does not match metric identity")
    if decoded["metric_type"] != str(metric_type or ""):
        raise ValueError("property_id does not match metric type")
    return decoded


def validate_property_graph_binding(
    property_id: str,
    *,
    metric_name: str,
    graph_type: str,
    source: str,
) -> dict[str, str]:
    """Bind an exact graph request to one unambiguous registry definition."""

    expected = {
        "SYSTEM_METRIC": ("system_metric", frozenset({"system_attribute"})),
        "EVAL": ("eval_metric", frozenset({"eval_config"})),
        "ANNOTATION": ("annotation_metric", frozenset({"annotation"})),
    }.get(graph_type)
    if expected is None:
        raise ValueError("invalid graph property type")
    metric_type, allowed_kinds = expected
    decoded = validate_property_metric_binding(
        property_id,
        metric_name=metric_name,
        metric_type=metric_type,
        source=source,
    )
    if decoded["property_kind"] not in allowed_kinds:
        raise ValueError("property_id kind is not valid for exact graph requests")
    return decoded


def validate_property_graph_namespace(
    property_id: str | None, *, expected_definition_source: str
) -> None:
    """Bind a system graph identity to the logical endpoint being queried."""

    if not property_id:
        return
    decoded = parse_property_registry_id(property_id)
    if (
        decoded["property_kind"] == "system_attribute"
        and decoded["definition_source"] != expected_definition_source
    ):
        raise ValueError("property_id is not valid for this graph endpoint")
