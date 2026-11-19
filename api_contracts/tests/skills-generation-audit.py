"""Audit the branch's Skills Swagger and generated MCP delta against its fork point."""

import argparse
import hashlib
import json
import subprocess
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SWAGGER = "api_contracts/openapi/swagger.json"
MANIFEST = "futureagi/mcp_server/catalog/tools.generated.json"
SESSION = "/tracer/trace/list_traces_of_session/"
INCLUDE = "include_skill_links"
MISSING = object()

# These additions live *outside* the Skills namespace. Pin their complete, new
# JSON shapes (canonical SHA-256) rather than allowing a Trace/Runtime prefix or
# duplicating the generated schemas. Existing definitions never use this rule.
PROJECTION = {
    "/tracer/trace/skill-projection/": "9ce82967bfd9ab2d094850db68a7586c0c7d582f2b044093218a61188421e2e7",
}
RUNTIME_DEFS = {
    "CohortCost": "53ddc4a78fdf60f7cb47ea158a5d8bb0bc8c1d5747f490d8236b1702df77514c",
    "CohortLatency": "a12b50ec67c978f1e27f2e5932b32f08c01be1ac14649654e51213bdb947ee99",
    "FailedRequests": "2f60ee2e0319b22bcf774deb49bc07589b77ddeaa80b7ad4e52cc0a48467bdf3",
    "FailedSkillLoad": "5a7528625f9fd0418d3dc40521b42f3ecf2d23f826da90504f326a3f20b932ef",
    "LatestFetch": "c2628283e4d12bbcbf814eb2d75a5fac2a5cc2cb64d2af10388dc754d79e7694",
    "LatestLoad": "6c3394b6959d0b79d74d7c2395cb7b3477cbad374cf4f645d88ebd16146dee34",
    "LatestLoadResult": "e641a64a9bfa32126e4e62a09eb455306f3b1da85c89e0b8f50b6323d902442d",
    "LoadAgent": "f0477bd35c4998c3e3929689e3c466667574998e6e340f4a42280ad061d2253b",
    "LoadProject": "e9806c92de3b4b810463506961dee377a86e4a233dfa6adba928a48a41d45051",
    "RuntimeComparisonResult": "a3238311d97d531b5edd3d928f894a7e7a31d9b601d6d68b556df35be6d4c7f4",
    "RuntimeFilterValue": "30c866bdb277c88758581175674117c59f56770daad53fc02ff71f9a8ffaa73d",
    "RuntimeFilterValuesResult": "b8d468d50631251f3acb783f51d990fb61caa675b91962ec70e9564f2807dec7",
    "RuntimeUsageResult": "41782c3e89ffc51afb0337b62ac3adaab42a9a9281ce4604268d4e1d31236825",
    "SkillVersionGraphPoint": "a39e4ee259b4a45232370c788cbc074bc0b0c44256ff4261e6ad319d5a575b00",
    "SkillVersionGraphSeries": "2f7ccf38019e4054ec8902e9bf12961941bedd6302c456d39e8bb1cb7e384514",
    "TraceCohortSummary": "427ca9d2d25009657a60398dacfe6c3834cd584b7b10c714b802b782b937af1b",
    "TraceContext": "c2268a0db6638aa66ef16cd677a791613baa442527d30abbd48f5b847145417d",
    "TraceScopeCoverage": "773ce829676613ef1047e466ea06cdf92aef46b66f7d15208c2dafb51326517b",
    "TraceSkill": "cc974fff6bd0e50114fbf1059865806dc04865c4a55bfdbf941b628706f80b76",
    "TraceSkillEntry": "a7fd00b3d0c29491a98d40be0f6419bf8958b1a61079828a01446ab34553ceda",
    "TraceSkillsRequest": "8be0a9063240799814d7339abbd6d44a841cd989734641f40be0c346dadfc4fb",
    "TraceSkillsResult": "63928a8b230f26c9dc164bf24718239f987ec624b484872687666c52a8b41a79",
}

# No prior shape exists for these tools. Pin their names and complete new
# contracts (method/route, input types and requiredness, and response policy).
SKILLS_TOOLS = {
    "get_agent_context": "17cdd058141ce925f81f98874fb66213758901b323983c1c9978eb723f3996e0",
    "get_agent_context_snapshot": "171ce3dda9081296436fdd669654a6bb43005603f54ab7ae97c0ceeea8ab632e",
    "get_latest_skill_load": "1540f065db9b8602c71ed001cb0ac2accf87bd49ab2402591a4747f32bc8660c",
    "get_skill": "f00414ff86489b232d0b3db05e8377ee5f07f0463f3e82082200b15ec0a94b88",
    "get_skill_usage": "182ae449403008552e0920f9ad6204c3d306c2998ae28a66293f365721caf414",
    "list_agent_context_versions": "0e44a0db0f11fb610a213ef2f51a97729e1d0d330f07b783803ac273084b2baf",
    "list_agent_contexts": "f37e3d189a2e39e17fac2a5092d7850522f2c994051f537d5bf7c7170da3e36b",
    "list_skill_versions": "d488562eb8d5efa13d27de4102d4000f2ad1d93e56ff5096295b25740e4432f7",
    "list_skills": "cb0ad7004ca2f79ebfc5e3fd68e9c7c25c192582c5d2acdf14cedd4bdfef78fd",
    "publish_skill": "570f9ec6ad140ff05c5a2e80215c056510b73f123c73c8638bd61a584d52e25c",
    "read_skill_file": "5565d6efa2df7f096bdd3361d3cbc8434121319e5c388dc3fa94938830d67ee4",
    "set_skill_label": "50111d0cddd88a6b1b992ce2ad5608eba793b21213f75f613786b2b017146e88",
    "update_skill_tags": "e1d0f5ca6e6cdb56fdf1d412aa9962b55053b50c49248638d0821c975df85cd1",
}


class Drift(ValueError):
    pass


def check(condition, message):
    if not condition:
        raise Drift(message)


def canonical(value):
    # Unlike Python ==, this distinguishes JSON false from 0, and true from 1.
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def fingerprint(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def comparison_base(ref="origin/dev", *, root=ROOT):
    return subprocess.check_output(
        ["git", "merge-base", ref, "HEAD"], cwd=root, text=True
    ).strip()


def baseline(path, ref):
    return json.loads(
        subprocess.check_output(["git", "show", f"{ref}:{path}"], cwd=ROOT)
    )


def current(path):
    return json.loads((ROOT / path).read_bytes())


def field(obj, key, new, old=MISSING):
    """Apply a reviewed transition to a copy of the baseline; also accept an integrated base."""
    value = obj.get(key, MISSING)
    if value is not MISSING and canonical(value) == canonical(new):
        return
    check(
        value is old
        or (
            old is not MISSING
            and value is not MISSING
            and canonical(value) == canonical(old)
        ),
        f"unexpected old value at {key}",
    )
    obj[key] = new


def optional(schema, key, value):
    check(key not in schema.get("required", []), f"{key} must be optional")
    field(schema["properties"], key, value)


def prepend(items, value, matching):
    hits = [i for i, item in enumerate(items) if matching(item)]
    if hits:
        check(
            hits == [0] and canonical(items[0]) == canonical(value),
            f"unexpected old {INCLUDE}",
        )
    else:
        items.insert(0, value)


OLD_FAMILY = "Column family such as SYSTEM_METRIC, SPAN_ATTRIBUTE, EVAL_METRIC, ANNOTATION, or NORMAL."
NEW_FAMILY = OLD_FAMILY.replace("ANNOTATION,", "ANNOTATION, SKILL,")
GROUP = {
    "type": "integer",
    "minimum": 0,
    "maximum": 2147483647,
    "description": "SKILL row-correlation group; omitted means 0. Groups are ANDed.",
}
SPLIT = {"title": "Split by", "type": "string", "enum": ["skill_version"]}
FILTERS = {
    "HarnessEnvironmentEvalEdit",
    "SimulateEvalConfigResponse",
    "EvalConfigDefinition",
    "EvalConfigUpdateRequest",
    "DashboardMetric",
    "DashboardQuery",
    "InstantEvalEstimateRequest",
    "InstantEvalStartRequest",
    "ObserveGraphDataRequest",
    "ProjectUserGraphDataRequest",
    "ProjectUserMetricsRequest",
    "ProjectUsersAggregateGraphDataRequest",
    "TraceSessionGraphDataRequest",
    "TraceGraphDataRequest",
}


def filter_delta(schema, key="filters"):
    item = schema["properties"][key]["items"]
    family = item["properties"]["filter_config"]["properties"]["col_type"]
    field(family, "description", NEW_FAMILY, OLD_FAMILY)
    optional(item, "group", GROUP)


def eval_filters(schema):
    for key in ("filters", "span_attributes_filters"):
        filter_delta(schema, key)


def definition_delta(name, schema):
    if name in FILTERS:
        filter_delta(schema)
    elif name in {"AutomationRuleConditions", "Selection"}:
        filter_delta(schema, "filter")
    elif name in {"EvalTask", "EvalTaskUpdateRequest", "SamplingPreviewRequest"}:
        eval_filters(schema["properties"]["filters"])
    elif name == "EvalTaskDetailsResult":
        eval_filters(schema["properties"]["filters_applied"])

    if name in {"ObserveGraphDataResult", "ObserveGraphDataErrorResult"}:
        old = ["count", "sum", "mean", "percentage"]
        field(schema["properties"]["metric_statistic"], "enum", [*old, "p95"], old)
        optional(
            schema,
            "series",
            {
                "type": "array",
                "items": {"$ref": "#/definitions/SkillVersionGraphSeries"},
            },
        )
        optional(
            schema,
            "totals_note",
            {"title": "Totals note", "type": "string", "minLength": 1},
        )
        optional(schema, "split_by", SPLIT)
    elif name == "TraceGraphDataRequest":
        optional(schema, "split_by", SPLIT)
    elif name == "TraceObserveListQuery":
        optional(schema, INCLUDE, {"title": "Include skill links", "type": "boolean"})
    elif name in {"TraceSessionListMetadata", "TraceObserveListMetadata"}:
        optional(
            schema,
            "skill_links_exist",
            {"title": "Skill links exist", "type": "boolean"},
        )


def path_delta(name, route):
    if name == SESSION:
        param = {"name": INCLUDE, "in": "query", "required": False, "type": "boolean"}
        prepend(route["get"]["parameters"], param, lambda p: p["name"] == INCLUDE)


def tool_delta(name, tool):
    if name == "search_traces":
        optional(tool["inputSchema"], INCLUDE, {"type": "boolean"})
        prepend(tool["request"]["parameters"]["query"], INCLUDE, lambda p: p == INCLUDE)
    elif name in {"create_eval_task", "update_eval_task"}:
        eval_filters(tool["inputSchema"]["properties"]["filters"]["anyOf"][0])
    elif name == "create_simulation_test":
        filter_delta(tool["inputSchema"]["definitions"]["EvalConfigDefinition"])
    elif name == "query_dashboard_metrics":
        filter_delta(tool["inputSchema"])
        filter_delta(tool["inputSchema"]["definitions"]["DashboardMetric"])


def existing(group, before, after, apply, billing):
    changed = reconciled = 0
    for name, old in before.items():
        label = f"Changed existing {group}: {name}"
        check(name in after, label + " (removed)")
        new = after[name]
        if name in billing and canonical(billing[name]) == canonical(
            {"before": old, "after": new}
        ):
            reconciled += 1
            continue
        expected = deepcopy(old)
        try:
            apply(name, expected)
        except (LookupError, TypeError, Drift) as exc:
            raise Drift(f"{label} ({exc})") from exc
        check(canonical(new) == canonical(expected), label)
        changed += canonical(old) != canonical(new)
    print(
        f"PASS: {group}: {len(before) - changed - reconciled} unchanged; {changed} reviewed field deltas; "
        f"{reconciled} exact EE billing reconciliations"
    )


def audit_unchanged_shared_routes(before, after):
    """A frozen operation may change through its refs even with identical route JSON."""
    def references(value):
        if isinstance(value, list):
            for item in value:
                yield from references(item)
        elif isinstance(value, dict):
            ref = value.get("$ref", "")
            if ref.startswith("#/definitions/"):
                yield ref.removeprefix("#/definitions/")
            for item in value.values():
                yield from references(item)

    checked = set()
    for path, route in before["paths"].items():
        if path.startswith("/model-hub/skills/") or not any(
            term in path.lower() for term in ("dataset", "experiment", "compare", "/develops/")
        ):
            continue
        check(canonical(after["paths"].get(path)) == canonical(route), f"Changed protected route: {path}")
        todo = list(references(route))
        while todo:
            name = todo.pop()
            if name in checked:
                continue
            checked.add(name)
            schema = before["definitions"][name]
            check(canonical(after["definitions"].get(name)) == canonical(schema),
                  f"Changed protected definition: {name} ({path})")
            todo.extend(references(schema))
    print(f"PASS: {len(checked)} reachable protected definitions byte-equivalent as JSON")


def audit_swagger(before, after, billing):
    audit_unchanged_shared_routes(before, after)
    # Apart from the two collections below, the only new Swagger metadata is
    # the exact context credential scheme. Preserve all previous auth schemes.
    metadata = {
        k: deepcopy(v) for k, v in before.items() if k not in ("paths", "definitions")
    }
    field(
        metadata["securityDefinitions"],
        "ContextCredential",
        {
            "type": "apiKey",
            "in": "header",
            "name": "Authorization",
            "description": "Context <credential-id>.<secret>; never combine with API/secret keys.",
        },
    )
    check(
        canonical(metadata)
        == canonical(
            {k: v for k, v in after.items() if k not in ("paths", "definitions")}
        ),
        "Changed Swagger metadata",
    )
    for group, apply, pins, prefix in (
        ("paths", path_delta, PROJECTION, "/model-hub/skills/"),
        ("definitions", definition_delta, RUNTIME_DEFS, "Skills"),
    ):
        old, new = before[group], after[group]
        existing(group, old, new, apply, billing[group])
        missing = pins.keys() - new.keys()
        check(not missing, f"Missing reviewed {group}: {sorted(missing)}")
        added = new.keys() - old.keys()
        for name in added:
            if not name.startswith(prefix):
                check(
                    pins.get(name) == fingerprint(new[name]),
                    f"Unexpected added {group}: {name}",
                )
        print(
            f"PASS: {group}: {len(added)} additions ({len(added & pins.keys())} pinned outside Skills)"
        )


def tool_index(manifest):
    tools = {t["name"]: t for t in manifest["tools"]}
    check(len(tools) == len(manifest["tools"]), "Duplicate MCP tool name")
    check(
        canonical(len(tools)) == canonical(manifest["tool_count"]),
        "Invalid MCP tool_count",
    )
    return tools


def audit_mcp(before, after):
    old, new = tool_index(before), tool_index(after)
    existing("MCP tools", old, new, tool_delta, {})
    expected = old.keys() | SKILLS_TOOLS.keys()
    check(
        expected == new.keys(),
        f"Unexpected MCP tool set: {sorted(expected ^ new.keys())}",
    )
    check(
        list(old) == [name for name in new if name in old],
        "Changed existing MCP tool order",
    )
    for name in new.keys() - old.keys():
        check(
            fingerprint(new[name]) == SKILLS_TOOLS[name],
            f"Changed new Skills MCP tool: {name}",
        )
    metadata = {k: v for k, v in before.items() if k != "tools"}
    metadata.update(
        tool_count=len(new),
        source_contract_sha256=hashlib.sha256(
            (ROOT / SWAGGER).read_bytes()
        ).hexdigest(),
    )
    check(
        canonical(metadata)
        == canonical({k: v for k, v in after.items() if k != "tools"}),
        "Changed MCP metadata",
    )
    print(
        f"PASS: {len(new.keys() - old.keys())} named Skills MCP additions; {len(new)} unique tools"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/dev", help="PR target ref")
    ref = comparison_base(parser.parse_args().base)
    print(f"Comparison merge base: {ref}")
    billing = current("api_contracts/tests/ee-autoreload-contract-delta.json")[
        "swagger"
    ]
    audit_swagger(baseline(SWAGGER, ref), current(SWAGGER), billing)
    audit_mcp(baseline(MANIFEST, ref), current(MANIFEST))


if __name__ == "__main__":
    try:
        main()
    except Drift as error:
        raise SystemExit(str(error)) from None
