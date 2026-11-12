"""Admission proofs for source-censused background transport sites.

Receiver families are capabilities backed by factory checks, not line-number
allowlists. Any additional raw HTTP/SDK/native constructor remains unadmitted.
"""

import ast
import runpy
from functools import lru_cache
from pathlib import Path

c = runpy.run_path(str(Path(__file__).with_name("falcon_send_census.py")))
text, ancestors, guarded_httpx, interactive = (
    c[k] for k in ("text", "ancestors", "guarded_httpx", "interactive")
)

# Exact non-background sends in conservatively included helper modules. No
# general "interactive" waiver: gates in reachable modules are proven below.
EXCEPTIONS = {
    (
        "agentic_eval.core.utils.functions",
        "download_image_to_base64",
        "requests.get",
    ): "Image-only download; ingestion uses model_manager.text_model directly, never image helpers.",
    (
        "agentic_eval.core.utils.functions",
        "download_audio_to_base64",
        "requests.get",
    ): "Audio-only download; background trace ingestion embeds text only.",
    (
        "agentic_eval.core.utils.functions",
        "detect_input_type.detect_single_item",
        "requests.get",
    ): "Two image/audio URL probes in generic batch input detection; text_model closure bypasses detection.",
    (
        "tracer.utils.sql_queries",
        "SQL_query_handler.execute_query",
        "cursor.execute",
    ): "Two PostgreSQL execute branches; Django/DB admission belongs to r3b, not HTTP/provider/native transport.",
}
# These generic evaluation helpers share a module with detect_input_type but
# are never called by the four background tools or the text-model closure.
# Each entry binds one exact owner/callee and allows exactly one occurrence.
for _owner in (
    "get_summary_judgement",
    "get_summary_judgement_ragrank",
    "get_summary_judgement_ragrank_v2",
    "expand_eval_instructions",
    "generate_correct_answer",
    "generate_summary",
    "generate_fixes_for_answer",
    "apply_fixes_to_answer",
    "generate_combined_criteria",
    "calculate_score",
    "get_score_from_subcriterias",
    "generate_combined_subcriterias",
):
    EXCEPTIONS[
        ("agentic_eval.core.utils.functions", _owner, "client._get_completion_content")
    ] = "Generic evaluation LLM helper, not reachable from background text embedding or the allowed tools."
EXCEPTIONS[
    (
        "agentic_eval.core.utils.functions",
        "get_qualitative_eval_parameter_prompt_v2",
        "llm_client._get_completion_content",
    )
] = "Generic evaluation rubric generation, not reachable from background text embedding."
EXCEPTION_COUNTS = {
    (
        "agentic_eval.core.utils.functions",
        "detect_input_type.detect_single_item",
        "requests.get",
    ): 2,
    (
        "tracer.utils.sql_queries",
        "SQL_query_handler.execute_query",
        "cursor.execute",
    ): 2,
}


def calls(node):
    return [n for n in ast.walk(node) if isinstance(n, ast.Call)]


def function(site):
    return next(
        p
        for _, p in ancestors(site)
        if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef))
    )


def has_call(node, name):
    return any(text(n.func) == name for n in calls(node))


def factory_assignments(source, target, permitted):
    return _factory_assignments(source, target, tuple(sorted(permitted)))


@lru_cache(maxsize=64)
def _factory_assignments(source, target, permitted):
    values = [
        n.value
        for n in ast.walk(ast.parse(source))
        if isinstance(n, ast.Assign) and any(text(t) == target for t in n.targets)
    ]
    live = [v for v in values if not (isinstance(v, ast.Constant) and v.value is None)]
    return bool(live) and all(
        isinstance(v, ast.Call) and text(v.func) in permitted for v in live
    )


def refused(site):
    # An abort/return must dominate the site in a sibling prefix, not merely
    # appear somewhere in the function or in a conditional unrelated branch.
    for node, parent in ancestors(site):
        for values in vars(parent).values():
            if not isinstance(values, list) or node not in values:
                continue
            for stmt in values[: values.index(node)]:
                if (
                    isinstance(stmt, ast.If)
                    and text(stmt.test) == "current_guard() is not None"
                ):
                    if any(isinstance(s, ast.Return) for s in stmt.body):
                        return True
                    if any(
                        isinstance(s, ast.Expr)
                        and isinstance(s.value, ast.Call)
                        and text(s.value.func) == "current_guard().abort"
                        for s in stmt.body
                    ):
                        return True
    return False


def immediate_gate(site):
    # Only unconditional statements in dominating blocks count. A gate in a
    # sibling if/try/loop must never admit a later send on a bypassing path.
    for node, parent in ancestors(site):
        for values in vars(parent).values():
            if not isinstance(values, list) or node not in values:
                continue
            for stmt in values[: values.index(node)]:
                if isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.Expr)) and has_call(
                    stmt, "before_send"
                ):
                    return True
    return False


# Keep outer API admission, but never treat adjacency as wire-boundary proof.
# Every site also binds to installed-stack behavioral tests and owned wire hooks.
wire_proof = runpy.run_path(
    str(Path(__file__).with_name("falcon_witness_wire_proof.py"))
)["wire_proof"]
WITNESSED_SENDS = {
    (
        "ee.agenthub.traceerroragent.background_provider",
        "direct_completion",
        "client.invoke_model",
    ),
    (
        "ee.agenthub.traceerroragent.background_provider",
        "direct_completion",
        "client.post",
    ),
    (
        "ee.agenthub.traceerroragent.background_gateway",
        "managed_completion",
        "client.post",
    ),
    (
        "ee.agenthub.traceerroragent.background_gateway",
        "sdk_completion",
        "completions.with_raw_response.create",
    ),
    ("ee.agenthub.traceerroragent.background_llm", "BackgroundLLM._attempt", "send"),
}


def adjacent_gate(site):
    for node, parent in ancestors(site):
        for values in vars(parent).values():
            if (
                isinstance(node, ast.stmt)
                and isinstance(values, list)
                and node in values
            ):
                index = values.index(node)
                if index:
                    statement = values[index - 1]
                    return isinstance(statement, (ast.Assign, ast.Expr)) and has_call(
                        statement, "before_send"
                    )
                return False
    return False


def admission(module, site, sources):
    call, owner, leaf = site["call"], site["owner"], site["resolved"].rsplit(".", 1)[-1]
    source = sources[module]
    if (module, owner, call) in WITNESSED_SENDS or (
        module
        in {
            "ee.agenthub.traceerroragent.background_provider",
            "ee.agenthub.traceerroragent.background_gateway",
        }
        and call in {"httpx.Client", "boto3.client"}
    ):
        proof = wire_proof(module, owner, call, sources)
        if adjacent_gate(site) and proof:
            return (
                "GUARDED: instance-local actual-wire admission; behavioral proof: "
                + proof
            )
        return None
    if (module, owner, call) == (
        "ee.agenthub.traceerroragent.background_llm",
        "BackgroundLLM.call_llm",
        "self._get_completion_content",
    ):
        return "Delegates to the overridden witnessed one-shot content method; no base LLM retry transport"
    if module == "tracer.services.analysis_result_completion" and (owner, call) in {
        ("RecordingLLM.stream_with_retry", "stream"),
        (
            "RecordingLLM._get_completion_content",
            "self._client._get_completion_content",
        ),
    }:
        return "Non-provider compatibility protocol; production Falcon construction uses raw BackgroundFalconClient and BackgroundLLM, independently pinned by witness suites"
    if (module, owner, call) == (
        "ee.agenthub.traceerroragent.background_payload_tools",
        "run_background_tool",
        "tool.execute",
    ):
        if has_call(function(site), "background_tool_denial") and has_call(
            function(site), "consume_tool_payload"
        ):
            return "Validated allowlisted tool dispatch, not wire I/O; recursively audited tools retain their own sends"
    if interactive(site) or refused(site):
        return "structurally unreachable with a background guard"
    if guarded_httpx(site):
        return (
            "request and httpcore trace admission on the enclosing private AsyncClient"
        )
    if leaf == "AsyncClient" and site["resolved"] == "httpx.AsyncClient":
        if any(
            k.arg is None and text(k.value) == "async_client_options()"
            for k in site["node"].keywords
        ):
            return "private HTTPX client with per-request/wire gates"
    if leaf in c["WRAPPERS"] and site["resolved"].rsplit(".", 1)[0] in {
        "tracer.services.background_transport",
        "ee.licensing.managed_ai",
        "ee.licensing.activation_client",
    }:
        return "delegates to recursively audited transport helper"
    if module == "ee.licensing.activation_client" and call in {
        "dispatch_managed_request",
        "dispatch_managed_stream",
    }:
        return "local recursively audited managed transport helper"
    if module == "tracer.services.background_transport":
        if (owner, call) in {
            ("_guarded_pool_classes.GuardedConnection.send", "super().send"),
            ("requests_send.GuardedAdapter.send", "super().send"),
            ("guarded_http_pool.GuardedPool.urlopen", "super().urlopen"),
            ("guard_native_client.wrap.guarded", "send"),
            ("guard_native_client.wrap.guarded", "connection.connect"),
        } and immediate_gate(site):
            return "per-request/bounded-chunk admission; real driver boundary tests cover gates"
        if owner == "guard_native_client.insert" and call == "send_insert":
            if immediate_gate(site) and all(
                part in text(function(site))
                for part in (
                    "'insert_block_size': min(settings['insert_block_size'], NATIVE_CHUNK_ROWS)",
                    "connection.context.client_settings = settings",
                )
            ):
                return "driver insert loop capped to bounded blocks; connection re-admits each"
        if owner == "httpx_post" and call in {"httpx.Client", "client.post"}:
            if "event_hooks={'request': [_sync_httpx_request]}" in text(function(site)):
                return "private HTTPX request/wire hook"
        if owner == "requests_send" and call in {
            "requests.Session",
            "getattr(guarded, method)",
        }:
            body = text(function(site))
            if all(
                s in body
                for s in (
                    "GuardedAdapter(max_retries=0)",
                    "guarded.mount('http://', adapter)",
                    "guarded.mount('https://', adapter)",
                    "before_send(kwargs.get('timeout'))",
                )
            ):
                return "private zero-retry session; adapter and connection each recheck"
    if leaf in {"Client", "CHDriver"} and site["resolved"].startswith(
        "clickhouse_driver."
    ):
        if any(
            isinstance(p, ast.Call) and text(p.func) == "guard_native_client"
            for _, p in ancestors(site)
        ):
            return "private native factory installs seven wire gates"
    if module == "agentic_eval.core.database.ch_vector":
        if factory_assignments(source, "self.client", {"guard_native_client"}):
            if call == "self.client.execute" and owner.startswith(
                "ClickHouseVectorDB."
            ):
                return "native client handle minted only by guarded factory"
            if (
                call in {"client.execute", "connection.connect"}
                and owner == "ClickHouseVectorDB._execute_native_read_with_deadline"
            ):
                return "read helper receives private native client with gated handshake/query"
    if (
        module == "ee.agenthub.traceerroragent.error_cluster"
        and call == "db.client.execute"
    ):
        if any(
            isinstance(p, ast.FunctionDef)
            and factory_assignments(text(p), "db", {"ClickHouseVectorDB"})
            for _, p in ancestors(site)
        ):
            return "cluster helper delegates to guarded vector factory"
    if module == "agentic_eval.core.embeddings.embedding_manager":
        if call == "self.db_client.client.execute" and factory_assignments(
            source, "self.db_client", {"ClickHouseVectorDB"}
        ):
            return "vector factory owns guarded native handle"
    if module == "tracer.services.clickhouse.client":
        if "guard_native_client" in source and "return self._create_client()" in source:
            if call in {"self.execute", "self.insert"} and owner.startswith(
                "ClickHouseClient."
            ):
                return "delegates to audited native query method"
            if (
                call == "connection.connect"
                and owner
                == "ClickHouseClient._execute_native_read_with_remaining_timeout"
            ):
                return "private native connection; send_hello/addendum gates handshake"
            if call in {"client.execute", "client.execute_iter"}:
                if (
                    owner
                    == "ClickHouseClient._execute_native_read_with_remaining_timeout"
                    or has_call(function(site), "self._get_client")
                ):
                    return "private background native client; wrapper gates each retry/block"
            if (
                call == "self._client.execute_iter"
                and owner == "_ManagedNativeReadStream.__enter__"
            ):
                if has_call(function(site), "self._owner._get_client"):
                    return "stream uses private guarded native factory"
            if call == "time.sleep" and owner == "ClickHouseClient._execute_read":
                return "retry returns through guarded native wire methods, never reuses admission"
    if (
        module == "tracer.services.clickhouse.application_read_transport"
        and call == "clickhouse_connect.get_client"
    ):
        body = text(function(site))
        if all(
            s in body
            for s in (
                "if current_guard() is not None:",
                "pool = guarded_http_pool()",
                "kwargs['pool_mgr'] = pool",
                "kwargs['query_retries'] = 0",
            )
        ):
            return "private HTTP pool installed before bootstrap; every retry separately gated"
    if (
        module == "tracer.services.clickhouse.v2.span_reader"
        and call == "self._client.query"
    ):
        if factory_assignments(
            source,
            "self._client",
            {
                "ServerEnforcedReadOnlyNativeClient",
                "create_application_read_http_client",
            },
        ):
            return "reader handle bound only to audited native/HTTP factories"
    if (
        module == "agentic_eval.core.embeddings.serving_client"
        and owner == "ModelServingClient._create_session"
        and call == "requests.Session"
    ):
        if "return requests_send(self.session, method, url, **kwargs)" in source:
            return "cached interactive session is not used to send background requests"
    if module == "ee.falcon_ai.llm_client" and call == "asyncio.sleep":
        for _, p in ancestors(site):
            if (
                isinstance(p, ast.For)
                and p.body
                and text(p.body[0]) == "await before_async_send()"
            ):
                return "retry loop rechecks on each iteration; nested wire hooks also gate sends"
    if module == "ai_tools.base" and call == "self.execute":
        if has_call(function(site), "background_tool_denial"):
            return "tool dispatch, not outbound I/O; independent capability checker verifies dominance"
    return None
