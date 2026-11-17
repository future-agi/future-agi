"""Source-derived transport census (no application imports, database or network).

Component roots are not a list of sends: AST traversal discovers sends, aliases,
client constructors and retry waits in every source, including local imports.
Unknown transports/dependencies fail closed rather than gaining admission from
an earlier guard.check() that a blocking operation could invalidate.
"""

import ast
import runpy
from functools import lru_cache
from pathlib import Path

_aliases = runpy.run_path(str(Path(__file__).with_name("falcon_send_aliases.py")))
LocalAliases = _aliases["LocalAliases"]
TransportStores = _aliases["TransportStores"]
CENSUS_CONTRACT = _aliases["CENSUS_CONTRACT"]
scan_safely, unscannable = _aliases["scan_safely"], _aliases["unscannable"]
STRUCTURAL_EXCEPTIONS = runpy.run_path(
    str(Path(__file__).with_name("falcon_send_store_exceptions.py"))
)["STRUCTURAL_EXCEPTIONS"]

# Added by the integration: original frozen exception table remains byte-exact.
# These exact counted sites are configuration/protocol forwarding, never sends.
STRUCTURAL_EXCEPTIONS = {
    **STRUCTURAL_EXCEPTIONS,
    (
        "ee.agenthub.traceerroragent.background_provider",
        "",
        "transport-store httpx <- 'httpx'",
    ): (
        1,
        "Module import of the HTTPX capability, not an escaped client; constructor and post are independently guarded.",
    ),
    (
        "ee.agenthub.traceerroragent.background_gateway",
        "sdk_completion",
        "transport-store scoped._make_status_error_from_response <- MethodType(_reject_sdk_status, scoped)",
    ): (
        1,
        "Private zero-retry SDK copy gets a body-independent terminal error constructor; installed SDK error controls prove no evidence/body consumption.",
    ),
    (
        "tracer.services.analysis_result_completion",
        "RecordingLLM.__getattr__",
        "dynamic-attribute getattr(self._client, name)",
    ): (
        1,
        "Inherited compatibility proxy; production model operations use explicit witnessed overrides, raw construction pinned by completion suites.",
    ),
}

ROOT = Path(__file__).resolve().parents[2]
ROOTS = (
    "ee.falcon_ai.agent",
    "ai_tools.background",
    "tracer.services.background_transport",
    "tracer.queries.error_analysis",
    "agentic_eval.core.embeddings.embedding_manager",
    "tracer.services.clickhouse.client",
)
# These are application dependency boundaries, NOT transport-send exemptions.
# ORM/worker state and unrelated interactive registry/telemetry are outside this
# transport oracle. Four allowed tools are independently seeded from the policy.
BOUNDARIES = {
    "agentic_eval.core.llm.llm": "BackgroundLLM inherits payload/configuration helpers only; all model-send/retry entrypoints are overridden and independently exercised by the one-shot/witness suites.",
    "tracer.services.analysis_result_lock": "PostgreSQL advisory publication lock, not HTTP/provider/native transport; r3b lock-retention suite covers it.",
    "tracer.services.analysis_result_binding": "Generation/ORM publication fence; transport-free current_attempt is consumed here, lifecycle and persistence are independently tested by r3b.",
    "ai_tools.registry": "dynamic interactive registry; immutable background allowlist tested separately",
    "ee.falcon_ai.mcp_tools": "background tool policy denies MCP before dispatch",
    "ee.falcon_ai.modes": "interactive tool discovery; background uses background_tools",
    "ee.agenthub.cluster_rca.falcon_bridge": "interactive agent delegation, denied in background",
    "ee.licensing.validator": "hash_key is local hashing, not a transport",
    "ee.usage.services.gateway_llm_client": "only _get_setting imported by managed_ai",
    "ee.usage.deployment": "deployment mode selection, not dispatch",
    "analytics.mixpanel_util": "interactive analytics, not embedding failure webhook",
    "accounts.utils": "organization ORM helper, not an HTTP transport",
    "tfc.utils.storage": "image/audio utilities are not used by background text embedding",
    "tfc.telemetry": "thread instrumentation, not application model/embedding transport",
    "tfc.telemetry.instrumentation": "client instrumentation; real underlying clients are audited",
    "tfc.utils.tenant_scope": "organization context manager, ORM scope",
    "tfc.middleware.workspace_context": "workspace context manager, ORM scope",
    "tfc.deployment_telemetry.config": "local deployment version lookup",
    "tfc.deployment_telemetry.state": "local installation identity lookup",
    "tracer.queries.deep_analysis_state": "Redis worker terminal state belongs to r3b; no HTTP/provider/native transport",
}
TRANSPORT_MODULES = {
    "requests",
    "httpx",
    "httpcore",
    "urllib3",
    "urllib",
    "http",
    "aiohttp",
    "openai",
    "anthropic",
    "litellm",
    "boto3",
    "botocore",
    "clickhouse_driver",
    "clickhouse_connect",
    "slack_sdk",
    "google",
    "cohere",
    "mistralai",
    "socket",
}
VERBS = {
    "post",
    "put",
    "patch",
    "delete",
    "head",
    "options",
    "request",
    "stream",
    "send",
    "sendall",
    "urlopen",
    "urlretrieve",
    "execute",
    "execute_iter",
    "query",
    "query_df",
    "query_np",
    "command",
    "insert",
    "raw_query",
    "raw_insert",
    "invoke_model",
    "invoke_model_with_response_stream",
    "converse",
    "converse_stream",
    "completion",
    "acompletion",
    "predict",
    "generate_content",
    "generate_content_stream",
    "embed_content",
    "send_query",
    "send_data",
    "send_external_tables",
    "send_hello",
    "send_addendum",
    "send_cancel",
    "ping",
    "handle_request",
    "handle_async_request",
    "sleep",
    "_get_completion_content",
}
WRAPPERS = {
    "requests_send",
    "httpx_post",
    "chat_completion",
    "stream_chat_completion",
    "dispatch_managed_request",
    "dispatch_managed_stream",
    "call_managed_service",
    "stream_managed_service",
}


def text(node):
    return ast.unparse(node)


def load_sources(root=ROOT):
    pending, sources, boundaries = list(ROOTS), {}, set()
    policy = ast.parse((root / "ai_tools/background.py").read_text())
    allowlist = next(
        n.value
        for n in policy.body
        if isinstance(n, ast.Assign)
        and any(text(t) == "BACKGROUND_TOOL_ALLOWLIST" for t in n.targets)
    )
    pending.extend(v.rsplit(".", 1)[0] for v in ast.literal_eval(allowlist).values())
    while pending:
        name = pending.pop()
        if name in sources:
            continue
        if name in BOUNDARIES:
            boundaries.add(name)
            continue
        # Never open settings, secret-bearing files, ORM models or r3b workers.
        if set(name.split(".")) & {
            "settings",
            "models",
            "migrations",
            "tasks",
            "secrets",
            "credentials",
            "values",
        }:
            continue
        path = root / (name.replace(".", "/") + ".py")
        if not path.is_file():
            path = root / name.replace(".", "/") / "__init__.py"
        if not path.is_file():
            continue  # external dependency; calls are still censused at caller
        source = path.read_text()
        sources[name] = source
        package = name if path.name == "__init__.py" else name.rpartition(".")[0]
        try:
            tree = ast.parse(source)
        except Exception:
            # Already retained in sources: census emits the source rejection.
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                pending.extend(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    base = ".".join(
                        package.split(".")[: len(package.split(".")) - node.level + 1]
                        + ([base] if base else [])
                    )
                pending.append(base)
                pending.extend(base + "." + a.name for a in node.names)
    return sources, boundaries


def send_sites(tree, parents, aliases):
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        name = aliases.resolve(n.func)
        names = aliases.names(n.func)
        leaves = {candidate.rsplit(".", 1)[-1] for candidate in names}
        dynamic = isinstance(n.func, ast.Call) and text(n.func.func) == "getattr"
        transport_receiver = aliases.transport_receiver(n.func)
        provider_chain = any(
            part in candidate.split(".")
            for candidate in names
            for part in {"completions", "responses", "embeddings", "messages"}
        )
        if not (
            leaves & VERBS
            or leaves & WRAPPERS
            or (
                leaves
                & {
                    "get",
                    "create",
                    "Client",
                    "AsyncClient",
                    "Session",
                    "get_client",
                    "client",
                    "connect",
                    "create_connection",
                }
                and (transport_receiver or provider_chain)
            )
            or dynamic
            or name == "send"
        ):
            continue
        owner, cur = [], n
        while cur in parents:
            cur = parents[cur]
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                owner.append(cur.name)
        yield {
            "node": n,
            "parents": parents,
            "owner": ".".join(reversed(owner)),
            "call": text(n.func),
            "resolved": name,
            "line": n.lineno,
            "tree": tree,
            "dynamic": dynamic,
        }


def structural_site(node, violation, tree, parents):
    owner, cur = [], node
    while cur in parents:
        cur = parents[cur]
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            owner.append(cur.name)
    site = {
        "node": node,
        "parents": parents,
        "owner": ".".join(reversed(owner)),
        "call": violation,
        "resolved": violation,
        "line": node.lineno,
        "tree": tree,
        "dynamic": False,
        "structural": True,
    }
    if hasattr(node, "unscannable"):
        site["unscannable"] = node.unscannable
    return site


@lru_cache(maxsize=64)
def census(source):
    tree, parents = None, {}

    def failure(exc):
        return structural_site(*unscannable(exc), tree, parents)

    try:
        tree = ast.parse(source)
        parents = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
        aliases = LocalAliases(tree, parents, TRANSPORT_MODULES)
    except Exception as exc:
        return [failure(exc)]
    sites = list(scan_safely(lambda: send_sites(tree, parents, aliases), failure))
    for node, violation in scan_safely(
        lambda: TransportStores(tree, aliases).violations(), unscannable
    ):
        sites.append(structural_site(node, violation, tree, parents))
    return sites


def ancestors(site):
    node = site["node"]
    while node in site["parents"]:
        parent = site["parents"][node]
        yield node, parent
        node = parent


def guarded_httpx(site):
    if site["call"] not in {"client.post", "client.stream"}:
        return False
    for _, parent in ancestors(site):
        if isinstance(parent, ast.AsyncWith):
            for item in parent.items:
                c = item.context_expr
                if (
                    text(item.optional_vars) == "client"
                    and isinstance(c, ast.Call)
                    and text(c.func) == "httpx.AsyncClient"
                ):
                    return any(
                        k.arg is None and text(k.value) == "async_client_options()"
                        for k in c.keywords
                    )
    return False


def interactive(site):
    # An optional guard=None argument does not erase the ambient ContextVar.
    # Only a dominating binding to that ambient guard proves this branch safe.
    ambient = False
    for node, parent in ancestors(site):
        for values in vars(parent).values():
            if isinstance(values, list) and node in values:
                ambient |= any(
                    isinstance(stmt, ast.Assign)
                    and text(stmt) == "guard = guard or current_guard()"
                    for stmt in values[: values.index(node)]
                )
    for node, parent in ancestors(site):
        if isinstance(parent, ast.If):
            cond = text(parent.test)
            if node in parent.body and cond == "current_guard() is None":
                return True
            if ambient and (
                (node in parent.body and cond == "guard is None")
                or (node in parent.orelse and cond == "guard is not None")
            ):
                return True
    return False


def audit(sources, admissions, exceptions, exception_counts=None):
    """Exact, occurrence-limited exceptions; new sites cannot inherit a waiver."""
    structural = {k: v for k, v in STRUCTURAL_EXCEPTIONS.items() if k[0] in sources}
    exceptions = {**{k: v[1] for k, v in structural.items()}, **exceptions}
    exception_counts = {
        **{k: v[0] for k, v in structural.items()},
        **(exception_counts or {}),
    }
    found, errors, used = [], [], {}
    for module, source in sources.items():
        # Also guard the driver boundary, including replaced/synthetic scanners.
        def failure(exc):
            return structural_site(*unscannable(exc), None, {})

        for site in scan_safely(lambda source=source: iter(census(source)), failure):
            key = (module, site["owner"], site["call"])
            found.append((module, site))
            if site.get("unscannable"):
                errors.append(
                    f"unscannable: {site['unscannable']} at {module}:{site['line']}"
                )
                continue  # No admission or exact exception can waive a scan failure.
            # Stores/reflection require their own exact counted exception;
            # interactive branches and earlier before_send calls are not waivers.
            try:
                reason = (
                    None
                    if site.get("structural")
                    else admissions(module, site, sources)
                )
            except Exception as exc:
                errors.append(
                    f"unscannable: {type(exc).__name__} at {module}:{site['line']}"
                )
                continue
            if reason:
                continue
            used[key] = used.get(key, 0) + 1
            if key not in exceptions or used[key] > (exception_counts or {}).get(
                key, 1
            ):
                errors.append(
                    f"{module}:{site['line']} {site['owner']} {site['call']}: unadmitted"
                )
    stale = {
        key
        for key in exceptions
        if used.get(key, 0) != (exception_counts or {}).get(key, 1)
    }
    errors.extend(f"stale/count-changed exception: {key}" for key in sorted(stale))
    return found, errors
