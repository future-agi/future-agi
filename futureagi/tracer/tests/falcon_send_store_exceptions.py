"""Exact, counted structural exceptions for existing production stores only.

These do not admit sends. The original 17 send exceptions are unchanged.
Keys bind module, full function owner, and target + RHS (or full reflection
expression), not just an attribute name. Counts fail on added/removed stores.
The conservative rule intentionally also flags scalar/configuration results
read from clients; each such existing occurrence is justified below.
"""

BG = "tracer.services.background_transport"
CH = "tracer.services.clickhouse.client"
VECTOR = "agentic_eval.core.database.ch_vector"
HTTP = "tracer.services.clickhouse.application_read_transport"

# Values are (exact occurrence count, justification).
STRUCTURAL_EXCEPTIONS = {
    (
        VECTOR,
        "ClickHouseVectorDB.is_clustered",
        "transport-store cls._is_clustered_cached <- bool(rows and rows[0][0])",
    ): (
        1,
        "Caches a bool computed from cluster-count query rows, not a transport handle.",
    ),
    (
        VECTOR,
        "_vector_native_client_read_lock",
        "transport-store _VECTOR_NATIVE_CLIENT_LOCKS[client] <- lock",
    ): (
        1,
        "Stores a newly created threading.Lock in the weak-key client mutex map; the value is not the client.",
    ),
    (
        BG,
        "guard_native_client",
        "transport-store connection.connect_timeout <- before_send(connection.connect_timeout)",
    ): (
        1,
        "Narrows the private native connection's numeric connect timeout to the admitted budget.",
    ),
    (
        BG,
        "guard_native_client",
        "transport-store connection.send_receive_timeout <- before_send(connection.send_receive_timeout)",
    ): (
        1,
        "Narrows the private native connection's numeric send/receive timeout to the admitted budget.",
    ),
    (
        BG,
        "guard_native_client.insert",
        "transport-store connection.context.client_settings <- {**settings, 'insert_block_size': min(settings['insert_block_size'], NATIVE_CHUNK_ROWS)}",
    ): (
        1,
        "Temporarily installs a copied settings dictionary capped to 64-row native blocks; unchanged chunk tests cover this.",
    ),
    (
        BG,
        "guard_native_client.insert",
        "transport-store connection.context.client_settings <- settings",
    ): (
        1,
        "Restores the original client-settings dictionary in the insert wrapper's finally block.",
    ),
    (
        BG,
        "guard_native_client",
        "transport-store setattr(connection, name, wrap(getattr(connection, name))) <- wrap(getattr(connection, name))",
    ): (
        1,
        "Installs admission wrappers on the seven literal native wire-method names of a PRIVATE client; real-driver chunk tests cover the wrappers.",
    ),
    (
        BG,
        "guard_native_client",
        "dynamic-attribute getattr(connection, name)",
    ): (
        1,
        "Reads each of the same seven literal native wire methods solely to install its admission wrapper.",
    ),
    (
        BG,
        "guarded_http_pool.GuardedPool.urlopen",
        "transport-store kwargs['timeout'] <- Timeout(total=remaining, connect=connect, read=read)",
    ): (
        1,
        "Stores an urllib3 timeout configuration object, not a connection; every pool send separately checks admission.",
    ),
    (
        BG,
        "guard_native_client.wrap.guarded",
        "transport-store connection.connect_timeout <- _budget(guard, connection.connect_timeout)",
    ): (1, "Caps the numeric connect timeout at each native wire admission."),
    (
        BG,
        "guard_native_client.wrap.guarded",
        "transport-store connection.send_receive_timeout <- _budget(guard, connection.send_receive_timeout)",
    ): (1, "Caps the numeric send/receive timeout at each native wire admission."),
    (
        BG,
        "guard_native_client.wrap.guarded",
        "transport-store connection.sync_request_timeout <- _budget(guard, connection.sync_request_timeout)",
    ): (1, "Caps the numeric ping timeout at each native wire admission."),
    (
        BG,
        "guard_native_client.wrap.guarded",
        "transport-store connection.sync_request_timeout <- sync_timeout",
    ): (
        1,
        "Restores the saved numeric ping timeout in finally after the gated wire operation.",
    ),
    (
        BG,
        "guard_native_client.wrap.guarded",
        "transport-store connection.sync_request_timeout <- timeout",
    ): (
        1,
        "Caps the ping timeout by the current budget, operation timeout and socket timeout before the gated send.",
    ),
    (
        BG,
        "requests_send",
        "dynamic-attribute getattr(session if session is not None else requests, method)",
    ): (
        1,
        "Interactive-only dispatch under current_guard() is None; the background branch constructs its own gated session.",
    ),
    (
        BG,
        "requests_send",
        "dynamic-attribute getattr(guarded, method)",
    ): (
        1,
        "Dispatches on the private zero-retry session with GuardedAdapter and wire gates; the existing send census separately proves that admission.",
    ),
    (
        "tracer.services.clickhouse.server_readonly",
        "ServerEnforcedReadOnlyNativeClient.__getattr__",
        "dynamic-attribute getattr(self._client, name)",
    ): (
        1,
        "Existing read-only facade delegates after denying mutation methods; native client construction and sends retain their separate admission proofs.",
    ),
    (
        CH,
        "ClickHouseClient._execute_read.progress_metric",
        "dynamic-attribute getattr(progress, name)",
    ): (
        1,
        "Reads last_query.progress rows/bytes metrics into int; this is result metadata, not method dispatch.",
    ),
    (
        HTTP,
        "create_application_read_http_client",
        "transport-store client.timeout <- Timeout(connect=int(connect), read=None)",
    ): (
        1,
        "Installs an urllib3 timeout configuration on the dedicated read client; the private guarded pool still enforces each background send.",
    ),
    (
        HTTP,
        "application_native_read_transport",
        "transport-store connection.send_receive_timeout <- original_receive",
    ): (
        1,
        "Restores the original numeric/None receive timeout in the native read context manager's finally block.",
    ),
    (
        "tracer.services.clickhouse.v2.span_reader",
        "CHSpanReader.aggregate_by_session_ids",
        "transport-store out[str(sid)] <- metrics",
    ): (
        1,
        "Rekeys aggregate result dictionaries (counts, cost and timestamps) by input session id; no client is stored.",
    ),
}

# Two existing native read helpers narrow scalar timeouts, then restore them.
# Enumerate exact owners, exact RHS expressions and exact multiplicities; this
# is not a module-wide or attribute-name waiver.
for _module, _owner in (
    (VECTOR, "ClickHouseVectorDB._execute_native_read_with_deadline"),
    (CH, "ClickHouseClient._execute_native_read_with_remaining_timeout"),
):
    for _store, _count, _reason in (
        (
            "connection.send_receive_timeout <- min(float(original_send_receive_timeout), remaining)",
            2,
            "Narrows the numeric receive timeout before connect and before query execution.",
        ),
        (
            "connection.connect_timeout <- min(float(original_connect_timeout), remaining)",
            1,
            "Narrows the numeric connect timeout to the remaining read deadline.",
        ),
        (
            "connection.connect_timeout <- original_connect_timeout",
            1,
            "Restores the saved numeric connect timeout in finally.",
        ),
        (
            "connection.send_receive_timeout <- original_send_receive_timeout",
            1,
            "Restores the saved numeric receive timeout in finally.",
        ),
    ):
        STRUCTURAL_EXCEPTIONS[(_module, _owner, "transport-store " + _store)] = (
            _count,
            _reason,
        )

# Module imports are namespace bindings, not plain function locals.
# Each canonical import is separately counted; this grants no call admission.
for _module, _target, _canonical, _reason in (
    (
        VECTOR,
        "clickhouse_driver",
        "clickhouse_driver",
        "Existing native-driver module import; every constructor/send still needs its independent admission proof.",
    ),
    (
        CH,
        "CHDriver",
        "clickhouse_driver.Client",
        "Existing native-driver class import used by the separately guarded client construction.",
    ),
    (
        CH,
        "CHError",
        "clickhouse_driver.errors.Error",
        "Existing exception-class import used for catching native-driver errors, not a transport instance.",
    ),
    (
        "analytics.utils",
        "WebhookClient",
        "slack_sdk.WebhookClient",
        "Existing webhook client class import; the background webhook send retains its separate gate.",
    ),
    (
        "agentic_eval.core.utils.functions",
        "urlparse",
        "urllib.parse.urlparse",
        "Pure URL parser import; conservative urllib module seed is not a network call.",
    ),
    (
        "agentic_eval.core.utils.functions",
        "requests",
        "requests",
        "Existing HTTP module import; each image-download send retains its exact send policy.",
    ),
    (
        "agentic_eval.core.embeddings.serving_client",
        "requests",
        "requests",
        "Existing HTTP module import; serving session creation and sends remain independently audited.",
    ),
    (
        "agentic_eval.core.embeddings.serving_client",
        "HTTPAdapter",
        "requests.adapters.HTTPAdapter",
        "Existing adapter class import for serving sessions; no live transport stored by the import.",
    ),
    (
        "ee.falcon_ai.llm_client",
        "httpx",
        "httpx",
        "Existing HTTP module import; managed and direct provider dispatch retain their separate admission checks.",
    ),
):
    STRUCTURAL_EXCEPTIONS[
        (_module, "", f"transport-store {_target} <- {_canonical!r}")
    ] = (1, _reason)

STRUCTURAL_EXCEPTIONS.update(
    {
        (
            "tracer.services.clickhouse.v2.span_reader",
            "CHSpanReader.root_ids_by_trace_ids",
            "transport-store result[tid] <- (str(sid), _norm(pid))",
        ): (
            1,
            "Rekeys string span/project identifiers from query result rows; no client or transport is stored.",
        ),
        (
            "tracer.services.clickhouse.v2.span_reader",
            "CHSpanReader.trace_session_ids_by_trace_ids",
            "transport-store result[tid] <- _norm(tsid)",
        ): (
            1,
            "Stores normalized string/None session identifiers from query result rows, not transport handles.",
        ),
        (
            "tracer.services.clickhouse.v2.span_reader",
            "CHSpanReader.per_trace_root_span_start_times",
            "transport-store result[tid] <- st",
        ): (
            1,
            "Stores minimum root-span datetime/None values obtained by iterating query rows.",
        ),
        (
            "ee.falcon_ai.llm_client",
            "FalconLLMClient._stream_openai",
            "transport-store chunk['usage'] <- usage",
        ): (
            1,
            "Normalizes streamed response token-count metadata into a result dictionary; no transport handle is stored.",
        ),
        (
            "ee.falcon_ai.llm_client",
            "FalconLLMClient._stream_anthropic",
            "transport-store current_tool_use['arguments'] <- current_tool_use['arguments'] += partial",
        ): (
            1,
            "Appends partial JSON text from parsed response events to tool-argument output, not a transport handle.",
        ),
    }
)

# One existing context-manager identity return, not a blanket waiver
# for __enter__ methods or bare-self stores. All original 29+14 keys are unchanged.
STRUCTURAL_EXCEPTIONS[
    (
        "tracer.services.clickhouse.v2.span_reader",
        "CHSpanReader.__enter__",
        "bare-self-store return self",
    )
] = (
    1,
    "Existing CHSpanReader context-manager identity return; __exit__ closes its client. "
    "This returns the reader wrapper, not its _client, and admits no send. "
    "Client construction and query execution retain their independent transport admission checks.",
)
