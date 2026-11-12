"""One provider's live smoke (scaling.md §5 L4), against its test account.

For a vendor MCP server (K1, Streamable HTTP):
- session: connect at the manifest's era and list the tools;
- toolsHash: the live tools hash (M17's ToolsHash) against
  tools.snapshot.toolsHash; a difference is drift, reported with the live
  hash, count and each live tool's own hash, so a maintainer can review it
  and update the snapshot. Nothing is pushed and no PR is opened;
- annotations: a read-only tool whose live annotations say it writes;
- verify: auth.verify, a tool (under policy.py) or an HTTP check;
- call: each recorded tools/call of the provider's MCP cassettes, repeated
  live under policy.py; the live result's key paths must cover the
  cassette's (keypaths.py). A tool the keys entry gives arguments for is
  called too.

For our REST connector (K2): verify when it is an HTTP check, and spec, the
vendor's API description against upstream.spec.sha256. Its tools are served
by agentcc-connectors, reachable only from the gateway, so they are not
listed or called here. Partner (K3) and local stdio (K4) providers are
skipped with a named reason.

A pass needs a check that carried the test account's credential (report.py,
ProviderResult.finish): the K1 session, or verify. The K2 spec is fetched
without it, so a K2 provider whose verify is a tool is never a pass here.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from . import (
    keypaths,
    mcp_client,
    placeholders,
    policy,
    secretscan,
    toolshash,
    transport,
)
from .credentials import Skip, Unsupported, resolve
from .report import ProviderResult, skipped

MAX_CALLS = 25
SPEC_MAX_BYTES = 32 << 20
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_ERA = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
# The eras the MCP client speaks over Streamable HTTP (mcp_client.Session).
ERAS = ("auto", "", mcp_client.VERSION_2026, *mcp_client.LEGACY_ERAS)


@dataclass(frozen=True)
class Options:
    allow_writes: bool = False
    timeout: float = transport.DEFAULT_TIMEOUT
    net: transport.Net | None = None
    max_calls: int = MAX_CALLS


def name_ref(name) -> str:
    """A live tool name as the report may hold it: the name when it is a
    plain tool name, else its hash (vendor text is data)."""
    if (
        isinstance(name, str)
        and _SAFE_NAME.match(name)
        and not secretscan.find_shapes(name)
    ):
        return name
    text = name if isinstance(name, str) else repr(name)
    return (
        "unsafe-name:sha256:"
        + hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()
    )


def unsupported(provider) -> str | None:
    """Why the runner does not smoke this provider at all, or None."""
    kind, t = provider.kind, provider.transport
    if kind == "partner":
        return "partner_not_smoked"
    if kind == "vendor_local" or t.get("kind") == "stdio":
        return "transport_stdio_not_run"
    if kind not in ("vendor_mcp", "rest"):
        return f"kind_unsupported:{kind}"
    if t.get("variant", "streamable") != "streamable":
        return "transport_sse_not_smoked"
    era = t.get("era", "auto")
    if kind == "vendor_mcp" and era not in ERAS:
        # an L0-valid era the client does not speak: named, never a failure
        return "era_unsupported:" + (era if _ERA.match(str(era)) else "unknown")
    return None


def _is_error(result) -> bool:
    return isinstance(result, dict) and result.get("isError") is True


def _check_reply(result: ProviderResult, check: str, reply, **detail) -> bool:
    if reply.error is not None:
        result.add(check, "fail", reason="jsonrpc_error", **detail)
        return False
    if _is_error(reply.result):
        result.add(check, "fail", reason="live_is_error", **detail)
        return False
    return True


def _hash(result: ProviderResult, provider, live: list, era: str) -> None:
    try:
        live_hash = toolshash.tools_hash(live)
    except toolshash.ToolsHashError:
        result.add(
            "toolsHash", "fail", reason="live_tools_unhashable", liveCount=len(live)
        )
        return
    snap = provider.snapshot
    detail = {"liveHash": live_hash, "liveCount": len(live), "liveEra": era}
    if not snap:
        result.add("toolsHash", "skipped", reason="no_snapshot", **detail)
        return
    detail.update(
        snapshotHash=snap.get("toolsHash"),
        snapshotCount=snap.get("count"),
        snapshotEra=snap.get("era"),
    )
    if snap.get("toolsHash") == live_hash:
        result.add("toolsHash", "pass", **detail)
        return
    declared = provider.manifest_tools()
    live_names = {t.get("name") for t in live if isinstance(t, dict)}
    result.add(
        "toolsHash",
        "drift",
        **detail,
        notInManifest=sorted(name_ref(n) for n in live_names if n not in declared),
        missingLive=sorted(n for n in declared if n not in live_names),
        liveTools=sorted(
            (
                {
                    "name": name_ref(t.get("name") if isinstance(t, dict) else None),
                    "hash": toolshash.tool_hash(t),
                }
                for t in live
            ),
            key=lambda d: (d["name"], d["hash"]),
        ),
    )


def _annotations(result: ProviderResult, declared: dict, live_by_name: dict) -> None:
    read_only = [n for n, t in declared.items() if policy.manifest_read_only(t)]
    if not read_only:
        result.add("annotations", "skipped", reason="no_read_only_tools_declared")
        return
    if not any(n in live_by_name for n in read_only):
        result.add("annotations", "skipped", reason="no_read_only_tools_live")
        return
    wide = policy.widened(declared, live_by_name)
    result.add("annotations", "drift" if wide else "pass", widened=wide)


def _verify_http(result: ProviderResult, spec: dict, cred, opts: Options) -> None:
    try:
        url = cred.url(spec.get("url", ""))
    except Skip as s:  # the entry lacks a variable the check's URL needs
        result.add("verify", "fail", reason=s.reason)
        return
    method = spec.get("method", "GET")
    try:
        resp = transport.request(
            method,
            url,
            cred.headers,
            b"" if method == "POST" else None,
            timeout=opts.timeout,
            net=opts.net,
        )
    except transport.TransportError as err:
        result.add("verify", "fail", reason=err.code, http=method)
        return
    resp.close()  # the answer's body is never read or kept
    if 200 <= resp.status < 300:
        result.add("verify", "pass", http=method, httpStatus=resp.status)
    elif resp.status in (401, 403):
        result.add(
            "verify",
            "fail",
            reason="credential_refused",
            http=method,
            httpStatus=resp.status,
        )
    elif 300 <= resp.status < 400:
        result.add(
            "verify",
            "fail",
            reason="redirect_refused",
            http=method,
            httpStatus=resp.status,
        )
    else:
        result.add(
            "verify",
            "fail",
            reason=f"http_{resp.status}",
            http=method,
            httpStatus=resp.status,
        )


def _verify_tool(result, session, name, declared, live_by_name, entry, opts) -> None:
    d = policy.decide(name, declared, live_by_name, opts.allow_writes, entry.sandbox)
    if not d.allowed:
        result.add("verify", "skipped", tool=name, reason=d.reason)
        return
    reply = session.call_tool(name, entry.arguments.get(name, {}))
    if _check_reply(result, "verify", reply, tool=name):
        result.add("verify", "pass", tool=name, write=d.write)


def _plan_calls(provider, entry) -> list:
    """(tool, cassette, recorded call or None) to make, cassettes first."""
    plan = [(c.tool, c.cassette, c) for c in provider.calls]
    recorded = {c.tool for c in provider.calls}
    plan += [
        (name, None, None) for name in sorted(entry.arguments) if name not in recorded
    ]
    return plan


def _calls(result, session, provider, declared, live_by_name, entry, opts) -> None:
    made = 0
    for name, cassette, call in _plan_calls(provider, entry):
        detail = {"tool": name, "cassette": cassette}
        if call is not None and (call.result is None or _is_error(call.result)):
            result.add("call", "skipped", reason="recorded_error", **detail)
            continue
        d = policy.decide(
            name, declared, live_by_name, opts.allow_writes, entry.sandbox
        )
        if not d.allowed:
            result.add("call", "skipped", reason=d.reason, **detail)
            continue
        if name in entry.arguments:
            args = entry.arguments[name]
        elif placeholders.holds_placeholder(call.arguments):
            result.add("call", "skipped", reason="arguments_scrubbed", **detail)
            continue
        else:
            args = call.arguments
        if made >= opts.max_calls:
            result.add("call", "skipped", reason="max_calls_reached", **detail)
            continue
        made += 1
        reply = session.call_tool(name, args)
        if not _check_reply(result, "call", reply, write=d.write, **detail):
            continue
        if call is None:
            result.add("call", "pass", keys="no_cassette", write=d.write, **detail)
            continue
        found = keypaths.compare(call.result, reply.result)
        status = "drift" if found["missing"] else "pass"
        result.add("call", status, write=d.write, **detail, **found)


def _mcp(
    result: ProviderResult, provider, entry, cred, url: str, opts: Options
) -> None:
    session = mcp_client.Session(
        url, cred.headers, provider.transport.get("era", "auto"), opts.timeout, opts.net
    )
    try:
        era = session.connect()
        result.era = era
        live = session.list_tools()
        live_by_name = {
            t["name"]: t
            for t in live
            if isinstance(t, dict) and isinstance(t.get("name"), str)
        }
        declared = provider.manifest_tools()
        result.add("session", "pass", era=era)
        _hash(result, provider, live, era)
        _annotations(result, declared, live_by_name)
        verify = provider.auth.get("verify") or {}
        if verify.get("tool"):
            _verify_tool(
                result, session, verify["tool"], declared, live_by_name, entry, opts
            )
        elif verify.get("http"):
            _verify_http(result, verify["http"], cred, opts)
        _calls(result, session, provider, declared, live_by_name, entry, opts)
    except mcp_client.MCPError as err:
        result.add("session", "fail", reason=err.code)
    finally:
        session.close()


def _spec(result: ProviderResult, spec: dict, opts: Options) -> None:
    try:
        resp = transport.request(
            "GET",
            spec.get("url", ""),
            {"Accept": "*/*"},
            None,
            timeout=opts.timeout,
            max_bytes=SPEC_MAX_BYTES,
            net=opts.net,
        )
    except transport.TransportError as err:
        result.add("spec", "fail", reason=err.code)
        return
    try:
        body = resp.read() if resp.status == 200 else None
    except transport.TransportError as err:
        result.add("spec", "fail", reason=err.code)
        return
    finally:
        resp.close()
    if body is None:
        result.add("spec", "fail", reason=f"http_{resp.status}")
        return
    live = hashlib.sha256(body).hexdigest()
    result.add(
        "spec",
        "pass" if live == spec.get("sha256") else "drift",
        liveSha256=live,
        pinnedSha256=spec.get("sha256"),
        bytes=len(body),
    )


def _rest(result: ProviderResult, provider, entry, cred, opts: Options) -> None:
    verify = provider.auth.get("verify") or {}
    if verify.get("http"):
        _verify_http(result, verify["http"], cred, opts)
    elif verify.get("tool"):
        result.add(
            "verify",
            "skipped",
            tool=verify["tool"],
            reason="tool_served_by_agentcc_connectors",
        )
    if entry.arguments:
        result.add("call", "skipped", reason="tool_served_by_agentcc_connectors")
    if provider.http_cassettes:
        result.add(
            "cassetteKeys",
            "skipped",
            reason="http_cassettes_have_no_vendor_host",
            cassettes=len(provider.http_cassettes),
        )


def run(provider, entry, opts: Options) -> ProviderResult:
    """Smoke one provider with its keys entry. Never raises for a provider's
    own failure; every outcome is a check in the result."""
    reason = unsupported(provider)
    if reason:
        return skipped(provider, reason)
    result = ProviderResult(
        app=provider.app,
        provider=provider.id,
        kind=provider.kind,
        test_account=entry.test_account,
        sandbox=entry.sandbox,
    )
    try:
        cred = resolve(provider, entry)
        url = (
            None
            if provider.kind == "rest"
            else cred.url(provider.transport.get("url", ""))
        )
    except Unsupported as s:
        return skipped(provider, s.reason)
    except Skip as s:  # the keys entry does not fit the manifest: nothing is sent
        result.add("credential", "fail", reason=s.reason)
        return result.finish()
    if provider.invalid_cassettes:
        result.add(
            "cassettes",
            "fail",
            reason="cassette_unreadable",
            cassettes=sorted(provider.invalid_cassettes),
        )
    if provider.kind == "rest":
        _rest(result, provider, entry, cred, opts)
    else:
        _mcp(result, provider, entry, cred, url, opts)
    spec = provider.upstream_spec
    if spec:
        _spec(result, spec, opts)
    return result.finish()
