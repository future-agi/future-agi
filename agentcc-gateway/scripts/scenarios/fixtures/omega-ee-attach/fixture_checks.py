"""Checks on the omega-ee-attach/v1 fixtures that JSON Schema cannot express.

CHECKS run on one fixture (fixtures.json names them per fixture); cross() runs one cross-fixture check
from fixtures.json's cross_checks. Each returns True, or a message saying what is wrong.
"""

PRE_DISPATCH = {
    "auth_required",
    "approval_required",
    "tool_rate_limited",
    "tool_budget_exhausted",
    "tool_denied",
}
DJANGO_TYPE = {
    400: "validation_error",
    401: "authentication_error",
    403: "permission_error",
    404: "not_found",
    409: "conflict",
    422: "client_error",
    503: "service_unavailable",
}
# build_error_envelope's attr is the first field of details that is not one of these (first_error_attr).
GENERIC_DETAIL_KEYS = {"detail", "error", "message", "non_field_errors"}


def denial_parts(doc):
    result = doc["body"]["result"]
    return (
        result,
        result["_meta"]["io.futureagi/error"],
        result["_meta"]["io.futureagi/tool_call"],
    )


def check_vs_url_matches_id(doc):
    r = doc["body"]["result"]
    return r["url"].endswith(
        "/v/" + r["vs_id"]
    ) or "url {} does not end in /v/{}".format(r["url"], r["vs_id"])


def check_django_error_consistent(doc):
    b, status = doc["body"], doc["http"]["status"]
    if not b["detail"] == b["message"] == b["error"] == b["result"]:
        return "detail, message, error and result differ (build_error_envelope sets one message)"
    if b["type"] != DJANGO_TYPE[status]:
        return f"type {b['type']} for HTTP {status}, want {DJANGO_TYPE[status]}"
    fields = [k for k in b.get("details", {}) if k not in GENERIC_DETAIL_KEYS]
    want = fields[0] if fields else None
    return (
        b.get("attr") == want
        or f"attr is {b.get('attr')!r} but details name {want!r} first (build_error_envelope sets attr from details)"
    )


def check_denial_text(doc):
    result, err, _ = denial_parts(doc)
    want = err["message"] + " [" + err["code"] + "]"
    return (
        result["content"][0]["text"] == want
        or f"content text is not {want!r} (denial.go Error())"
    )


def check_pre_dispatch_not_executed(doc):
    _, err, tc = denial_parts(doc)
    if err["code"] in PRE_DISPATCH and (tc["executed"] or tc["billed_usd"] != 0):
        return "{} is refused before dispatch: executed must be false and billed_usd 0".format(
            err["code"]
        )
    return True


def check_protocol_401_shape(doc):
    h, b = doc["http"], doc["body"]
    good = (
        h["status"] == 401
        and "www-authenticate" in h["headers"]
        and b["id"] is None
        and b["error"]["code"] == -32001
        and "data" in b["error"]
    )
    return (
        good
        or "a 401 has WWW-Authenticate, id null, code -32001 and data.code (principal.go unauthorized)"
    )


def check_protocol_404_shape(doc):
    h, e = doc["http"], doc["body"]["error"]
    good = (
        h["status"] == 404
        and "www-authenticate" not in h["headers"]
        and e["code"] == -32600
        and e["message"] == "virtual server not found"
        and "data" not in e
    )
    return (
        good
        or "a 404 is code -32600, message 'virtual server not found', no data (hook_vserver.go)"
    )


def check_token_lifetime(doc):
    life = doc["exp"] - doc["iat"]
    good = 0 < life <= 900 and doc["nbf"] <= doc["exp"]
    return good or f"exp - iat is {life} s; the access-token contract caps it at 900 s"


CHECKS = {
    name[len("check_") :]: fn
    for name, fn in globals().items()
    if name.startswith("check_")
}


def cross(c, load, schema):
    kind = c["check"]
    if kind == "idempotent_repeat":
        a, b = load(c["first"])["body"]["result"], load(c["repeat"])["body"]["result"]
        same = {k: v for k, v in a.items() if k != "created"} == {
            k: v for k, v in b.items() if k != "created"
        }
        return (
            same and a["created"] is True and b["created"] is False
        ) or "the repeat must return the same virtual server with created false"
    if kind == "request_matches_provisioned":
        url = load(c["provisioned"])["body"]["result"]["url"]
        for r in c["requests"]:
            http = load(r)["http"]
            if (
                http["url"] != url
                or http["headers"]["authorization"] != c["authorization"]
            ):
                return "{} must call {} with {}".format(r, url, c["authorization"])
        return True
    if kind == "ensure_echoes_request":
        q, r = load(c["request"])["body"], load(c["response"])["body"]["result"]
        return (
            all(q[k] == r[k] for k in ("mode", "workspace", "connection_id"))
            or "the response must echo mode, workspace and connection_id"
        )
    if kind == "token_audience_is_provisioned_url":
        aud, url = (
            load(c["claims"])["aud"],
            load(c["provisioned"])["body"]["result"]["url"],
        )
        return aud == url or f"aud {aud} is not the provisioned url {url}"
    if kind == "listed_tools_are_called_names":
        listed = {t["name"] for t in load(c["list"])["body"]["result"]["tools"]}
        called = {load(f)["body"]["params"]["name"] for f in c["calls"]}
        return (
            called <= listed or f"called names {sorted(called - listed)} are not listed"
        )
    if kind == "the_404s_are_told_apart_by_body":
        # The session's (no body), the virtual server's (JSON-RPC) and the router's (model-path JSON): each
        # must be its own shape and no other, or EE could not tell them apart (spec section 5.3).
        bodies, wrong = c["bodies"], []
        for b in bodies:
            doc = load(b["file"])
            for other in bodies:
                valid = not schema.errors(doc, {"$ref": "#/$defs/" + other["def"]})
                if valid != (other is b):
                    wrong.append(
                        "{} {} a valid {}".format(
                            b["file"], "is" if valid else "is not", other["def"]
                        )
                    )
        return (
            (len(bodies) == 3 and not wrong)
            or f"the three 404s must each validate as their own shape only: {wrong or bodies}"
        )
    if kind == "agent_key_minted_is_bound":
        # The key the provisioning API mints is the one ensure binds, the agent-mode requests send and the revoke names, all
        # for one workspace, through one Django origin (spec sections 3.1 and 3.7).
        mint_q, mint_r = (
            load(c["mint_request"]),
            load(c["mint_response"])["body"]["result"],
        )
        ensure = load(c["ensure_request"])
        revoke_q = load(c["revoke_request"])
        revoke_r = load(c["revoke_response"])["body"]["result"]
        kid, wrong = mint_r["agent_key_id"], []
        if not (
            mint_q["body"]["workspace"]
            == mint_r["workspace"]
            == ensure["body"]["workspace"]
        ):
            wrong.append("the mint, its answer and ensure name different workspaces")
        if ensure["body"]["agent_key_id"] != kid:
            wrong.append(
                f"ensure binds {ensure['body']['agent_key_id']}, not the minted {kid}"
            )
        bearer = "Bearer " + mint_r["key"]
        wrong += [
            f"{r} does not send the minted key"
            for r in c["requests"]
            if load(r)["http"]["headers"]["authorization"] != bearer
        ]
        if not revoke_q["http"]["url"].endswith(f"/agent-keys/{kid}/revoke/") or (
            revoke_r["agent_key_id"] != kid
        ):
            wrong.append(f"the revoke does not name the minted {kid}")
        origins = {
            load(f)["http"]["url"].split("/agentcc/", 1)[0]
            for f in [c["mint_request"], c["revoke_request"], *c["provisioning"]]
        }
        if len(origins) != 1:
            wrong.append(
                f"the provisioning calls go to more than one origin: {sorted(origins)}"
            )
        return not wrong or "; ".join(wrong)
    if kind == "token_matches_host_identity":
        # The access token's sub and org_id are EE's bound host identity, and its aud the url ensure returned for a
        # connection of that workspace (spec section 4.3, step 5).
        claims, ident = load(c["claims"]), load(c["identity"])
        q, r = (
            load(c["ensure_request"])["body"],
            load(c["provisioned"])["body"]["result"],
        )
        wrong = [
            f"{what} {got!r} is not the host identity's {want!r}"
            for what, got, want in (
                ("sub", claims["sub"], ident["subject"]),
                ("org_id", claims["org_id"], ident["organizationId"]),
                ("the ensure workspace", q["workspace"], ident["workspaceId"]),
                ("the provisioned workspace", r["workspace"], ident["workspaceId"]),
            )
            if got != want
        ]
        if claims["aud"] != r["url"]:
            wrong.append(f"aud {claims['aud']} is not the provisioned url {r['url']}")
        if claims["iss"] == ident["issuer"]:
            wrong.append("the access token's issuer and the host assertion's are one; they differ")
        return not wrong or "; ".join(wrong)
    if kind == "protocol_header_only_with_om2":
        # Until EE repins it names the revision only in initialize; the repin adds the header,
        # with the revision initialize asked for, to every later request.
        want = load(c["initialize"])["body"]["params"]["protocolVersion"]
        wrong = [
            f"{r} sends mcp-protocol-version before OM2"
            for r in c["today"]
            if "mcp-protocol-version" in load(r)["http"]["headers"]
        ]
        wrong += [
            f"{r} must send mcp-protocol-version {want}"
            for r in c["om2"]
            if load(r)["http"]["headers"].get("mcp-protocol-version") != want
        ]
        return (bool(c["today"]) and bool(c["om2"]) and not wrong) or (
            "; ".join(wrong) or "name requests from before and after OM2"
        )
    if kind == "mint_replay_names_the_minted_key":
        # A retry of the mint with its Idempotency-Key names the key that mint made (never the key
        # itself), and EE's find by that Idempotency-Key lists the same key in the same workspace (spec
        # section 3.7).
        mint_q, mint_r = load(c["mint_request"]), load(c["mint_response"])["body"]["result"]
        replayed = load(c["replayed"])["body"]
        find_q = load(c["find_request"])["http"]
        found = load(c["find_response"])["body"]["result"]
        key, wrong = mint_q["http"]["headers"]["idempotency-key"], []
        if replayed["agent_key_id"] != mint_r["agent_key_id"]:
            wrong.append(f"the replay names {replayed['agent_key_id']}, not the minted {mint_r['agent_key_id']}")
        if replayed["expires_at"] != mint_r["expires_at"]:
            wrong.append("the replay's expires_at is not the mint's")
        if not find_q["url"].endswith(f"/workspaces/{mint_r['workspace']}/agent-keys/?mint_idempotency_key={key}"):
            wrong.append("the find does not name the mint's workspace and Idempotency-Key")
        listed = [k for k in found["keys"] if k["agent_key_id"] == mint_r["agent_key_id"]]
        if found["workspace"] != mint_r["workspace"] or len(listed) != 1:
            wrong.append("the find does not list the minted key once, in the mint's workspace")
        elif listed[0]["mint_idempotency_key"] != key:
            wrong.append("the listed key does not carry the mint's Idempotency-Key")
        origins = {
            u.split("/agentcc/", 1)[0] for u in (mint_q["http"]["url"], find_q["url"])
        }
        if len(origins) != 1:
            wrong.append(f"the mint and the find go to more than one origin: {sorted(origins)}")
        return not wrong or "; ".join(wrong)
    raise ValueError(f"unknown cross check {kind}")
