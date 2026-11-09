"""payloads.json (DESIGN.md sections 7.6, 14 and 22.2; contract sections 5.5 and 7)."""

from __future__ import annotations

import copy

from . import reference as R
from .cases_fanout import fan_out_case
from .cases_payloads_more import header_cases, more_build_cases
from .cases_payloads_run import run_cases
from .common import (
    API,
    APP,
    ASHA,
    CONFORMANCE_VERSION,
    DOC_FACTS,
    L_PROD,
    L_STAGE,
    ORG,
    P1,
    V6,
    V7,
    WS,
    body_fields,
    check,
    dump,
    sha,
)


def prompt_links(pid):
    return {
        "app": f"{APP}/dashboard/prompt/{pid}",
        "api": f"{API}/model-hub/prompt-templates/{pid}/",
    }


def payloads_file() -> bytes:
    cases = []

    def add(cid, kind, inp, variant, schema, note, expect_doc=None):
        if kind == "prompt.version.created":
            env = R.build_prompt_version_created(inp, variant)
        elif kind == "prompt.label.changed":
            env = R.build_prompt_label_changed(inp, variant)
        else:
            env = R.build_deployment_run(inp)
        body = R.canonical(env)
        if expect_doc:
            f = DOC_FACTS[expect_doc]
            check(
                len(body) == f["bytes"] and sha(body) == f["sha256"],
                f"payload {cid} vs {expect_doc}",
            )
        cases.append(
            {
                "id": cid,
                "builder": kind,
                "input": inp,
                **({"variant": variant} if variant else {}),
                "schema": schema,
                "expected": body_fields(body),
                "note": note,
            }
        )

    thin = {"payload_mode": "thin", "include_people_names": True}
    thin_noname = {"payload_mode": "thin", "include_people_names": False}
    full = {"payload_mode": "full", "include_people_names": True}
    run_schema = {
        "completed": "urn:futureagi:webhooks:v1:deployment.run.completed",
        "failed": "urn:futureagi:webhooks:v1:deployment.run.failed",
    }

    n1 = run_cases(add, run_schema)

    version = {
        "id": V7,
        "version": "v7",
        "commit_message": "Tighten the refund wording",
        "is_default": False,
        "labels": [],
        "created_at": "2026-10-22T09:41:07.200Z",
        "created_by": {"id": ASHA, "name": "Asha Rao"},
    }
    pvc = {
        "event_id": "evt_" + R.ulid(1792662067200, "pvc"),
        "organization_id": ORG,
        "workspace_id": WS,
        "prompt": {"id": P1, "name": "support-reply"},
        "version": version,
        "previous_version": {"id": V6, "version": "v6"},
        "links": prompt_links(P1),
        "content": {
            "messages": [
                {"role": "system", "text": "You answer refund questions."},
                {"role": "user", "text": "{{question}}"},
            ],
            "variables": ["question"],
            "model": "gpt-4o-mini",
        },
    }
    schema_pvc = "urn:futureagi:webhooks:v1:prompt.version.created"
    add(
        "pvc-thin",
        "prompt.version.created",
        pvc,
        thin,
        schema_pvc,
        "DESIGN.md section 14.2, thin mode",
    )
    add(
        "pvc-thin-no-names",
        "prompt.version.created",
        pvc,
        thin_noname,
        schema_pvc,
        "include_people_names = false: people are {id} only",
    )
    noname = copy.deepcopy(pvc)
    noname["version"]["created_by"] = {"id": ASHA, "name": None}
    noname["previous_version"] = None
    noname["version"]["labels"] = [
        {"id": L_PROD, "name": "Production", "type": "system"}
    ]
    add(
        "pvc-no-display-name-first-version",
        "prompt.version.created",
        noname,
        thin,
        schema_pvc,
        "no display name is null, never an email; the first version has previous_version null; a label at creation",
    )
    longmsg = copy.deepcopy(pvc)
    longmsg["version"]["commit_message"] = "Préciser le remboursement ✓ " + "x" * 1200
    add(
        "pvc-thin-truncated",
        "prompt.version.created",
        longmsg,
        thin,
        schema_pvc,
        "thin: the first 200 code points, commit_message_truncated true; raw UTF-8",
    )
    add(
        "pvc-full-truncated",
        "prompt.version.created",
        longmsg,
        full,
        schema_pvc,
        "full: the first 1,000 code points, plus the content allowlist",
    )
    add(
        "pvc-full",
        "prompt.version.created",
        pvc,
        full,
        schema_pvc,
        "full mode: content allowlist",
    )
    secret_content = copy.deepcopy(pvc)
    secret_content["content"]["messages"][0]["text"] = (
        "Use key sk-proj-abcdefghijkl for lookups."
    )
    add(
        "pvc-full-content-secret",
        "prompt.version.created",
        secret_content,
        full,
        schema_pvc,
        "a secret shape in the content: content left out, content_omitted secret_shape",
    )
    nullmsg = copy.deepcopy(pvc)
    nullmsg["version"]["commit_message"] = None
    nullmsg["version"]["created_by"] = None
    add(
        "pvc-null-message-system",
        "prompt.version.created",
        nullmsg,
        thin,
        schema_pvc,
        "no commit message; created by the system (null)",
    )

    plc = {
        "event_id": "evt_01JBF3Q7Z8X4N6R2T5V9W0YB1C",
        "organization_id": ORG,
        "workspace_id": WS,
        "prompt": {"id": P1, "name": "support-reply"},
        "label": {"id": L_PROD, "name": "Production", "type": "system"},
        "revision": 12,
        "from_version": {"id": V6, "version": "v6", "commit_message": "Older wording"},
        "to_version": {
            "id": V7,
            "version": "v7",
            "commit_message": "Tighten the refund wording",
        },
        "changed_at": "2026-10-22T09:59:58.120Z",
        "changed_by": {"id": ASHA, "name": "Asha Rao"},
        "links": prompt_links(P1),
    }
    schema_plc = "urn:futureagi:webhooks:v1:prompt.label.changed"
    add(
        "plc-moved",
        "prompt.label.changed",
        plc,
        thin,
        schema_plc,
        "DESIGN.md section 14.3: one moved event (from_version carries no commit_message)",
    )
    assigned = copy.deepcopy(plc)
    assigned.update(
        from_version=None,
        revision=1,
        event_id="evt_" + R.ulid(1792663000000, "assigned"),
    )
    add(
        "plc-assigned",
        "prompt.label.changed",
        assigned,
        thin,
        schema_plc,
        "assigned: from_version null",
    )
    replaced = copy.deepcopy(plc)
    replaced.update(
        label={"id": L_STAGE, "name": "Staging", "type": "system"},
        to_version=None,
        cause="replaced",
        revision=4,
        event_id="evt_" + R.ulid(1792663000001, "replaced"),
    )
    add(
        "plc-removed-replaced",
        "prompt.label.changed",
        replaced,
        thin,
        schema_plc,
        "a set-replace dropped Staging: removed / replaced (O2)",
    )
    deleted = copy.deepcopy(plc)
    deleted.update(
        to_version=None,
        cause="version_deleted",
        revision=13,
        changed_by=None,
        event_id="evt_" + R.ulid(1792663000002, "deleted"),
    )
    add(
        "plc-removed-version-deleted",
        "prompt.label.changed",
        deleted,
        thin_noname,
        schema_plc,
        "version soft-deleted while holding the label; no actor",
    )
    custom = copy.deepcopy(plc)
    custom.update(
        label={
            "id": "66666666-6666-4666-8666-666666666666",
            "name": "canary-eu",
            "type": "custom",
        },
        revision=2,
        event_id="evt_" + R.ulid(1792663000003, "custom"),
    )
    custom["to_version"]["commit_message"] = "y" * 205
    add(
        "plc-custom-label-truncated",
        "prompt.label.changed",
        custom,
        thin,
        schema_plc,
        "a custom label; to_version.commit_message cut to 200 with commit_message_truncated",
    )
    test = copy.deepcopy(plc)
    test.update(
        test=True,
        event_id="evt_test_" + R.ulid(1792663000004, "test"),
        links=prompt_links(R.NIL_UUID),
    )
    add(
        "plc-test-delivery",
        "prompt.label.changed",
        test,
        thin,
        schema_plc,
        "a test: test true in the signed body, nil UUIDs for prompt, version and label ids (links too, so a receiver "
        "that fetches them gets 404), names prefixed '[Test] '",
    )

    more_build_cases(
        add, n1=n1, pvc=pvc, plc=plc, thin=thin, full=full, run_schema=run_schema
    )

    fan_case = fan_out_case()

    rules = {
        "serialization": "UTF-8 JSON, separators ',' and ':', no whitespace, no trailing newline, non-ASCII as raw "
        "UTF-8 (never \\u escapes), the escapes of RFC 8259 with lower-case hex for other control "
        "characters, '/' not escaped, keys in the builder order below, no NaN or Infinity. Numbers "
        "are IEEE-754 doubles written exactly as ECMAScript Number::toString writes them (what "
        "JSON.stringify emits): integers below 2^53 in full, 1.0 -> 1, 0.00001, 1.5e-7, 1e+21, "
        "2^60 -> 1152921504606847000, -0.0 -> 0. JSON.stringify(JSON.parse(body)) reproduces every body. Every "
        "string is valid Unicode and every number finite: intake refuses a notice whose webhook_context holds a lone "
        "surrogate or a non-finite number (b8/fixtures.json rules.context), so no body ever needs one.",
        "envelope_order": [
            "id",
            "type",
            "api_version",
            "timestamp",
            "organization_id",
            "workspace_id",
            "test (tests only)",
            "data",
        ],
        "prompt.version.created": "data: prompt{id,name}, version{id,version,commit_message,"
        "[commit_message_truncated],is_default,labels[{id,name,type}],created_at,created_by}, "
        "previous_version{id,version}|null, links{app,api}, then content or content_omitted "
        "(full mode only); timestamp = version.created_at",
        "prompt.label.changed": "data: action, cause, prompt{id,name}, label{id,name,type}, revision, "
        "from_version{id,version}|null, to_version{id,version,commit_message,"
        "[commit_message_truncated]}|null, changed_at, changed_by, links{app,api}, then content "
        "(full mode only); action is assigned when from is null, removed when to is null, else "
        "moved; timestamp = changed_at",
        "deployment.run": "B8-EVENT-CONTRACT.md section 7.1, keys in the order of section 12.5; context_dropped "
        "follows context; error keys type, code, http_status, host (present ones only); only the "
        "named notice fields are copied. run.context keys are the customer's, so they are written in the order "
        "JSON.parse gives them (ECMA-262 OrdinaryOwnPropertyKeys): keys that are array indexes (0 or a decimal "
        "integer without a leading zero, below 2^32 - 1) ascending by value first, then the other keys in received "
        "order. data.deployment.name is X-Omega-Deployment-Name decoded by the deployment_name_header rule",
        "people": "{id, name} with name the display name or null; {id} when include_people_names is false; null "
        "when no person acted",
        "commit_message": "scrub, then cut. Scrub: every match of the secret-shape pattern "
        "sk-[A-Za-z0-9_-]{8,}|whsec_[A-Za-z0-9+/=]{8,} (one alternation, leftmost first, non-overlapping) is "
        "replaced by the 10 characters [redacted], in thin and full mode, in version.commit_message and "
        "to_version.commit_message. Cut: thin keeps the first 200 Unicode code points of the scrubbed text, full "
        "the first 1,000; commit_message_truncated: true right after it when cut; no ellipsis. Cutting first could "
        "leave the head of a key (sk-p) too short to match",
        "test_events": "id evt_test_<ulid>, test: true after workspace_id, nil UUID for prompt, version and label "
        "ids, names prefixed '[Test] ', dep-0000000000000000 for a deployment",
        "secret_shapes": "the vectors use only 'sk-' + 8 or more of [A-Za-z0-9_-] and 'whsec_' + 8 or more base64 "
        "characters; FA's scrubber may know more shapes (owned by the scrubber, not these vectors), and masks each "
        "match with [redacted]. A shape in run context drops the whole context (context_dropped); in full-mode "
        "content it omits the content (content_omitted); in a commit message it is masked in place",
        "deployment_name_header": "X-Omega-Deployment-Name (percent-encoded UTF-8, contract section 4.3): the wire "
        "value must be visible ASCII (U+0020-U+007E); each %XX with two hex digits (either case) is one byte, any "
        "other character is its own byte (a '%' without two hex digits stays as it is, '+' is not a space); the bytes "
        "must be strict UTF-8 (no invalid, truncated, overlong or surrogate sequence; a U+FEFF is kept). Otherwise "
        "intake answers 400 invalid_header, field X-Omega-Deployment-Name (contract section 5.1 step 3). Do not use "
        "a decoder that throws on a lone '%' (decodeURIComponent) or replaces bad bytes (unquote's default)",
        "full_mode": "content{messages[{role,text}] or template, variables[], model}; nothing else (P3)",
        "fan_out": "events in acceptance order; endpoints in id order; an endpoint takes the event when its "
        "workspace matches (or it is org-wide) and any subscription matches the type (exact or group "
        "'x.*') and its filters; a targeted endpoint takes it without a subscription, or through its "
        "subscriptions' filters minus the deployment filter, and never twice; deployment.run.failed "
        "subscriptions default statuses to [failed, interrupted]; every non-test delivery takes the "
        "endpoint's next sequence number; an endpoint that is not enabled gets paused deliveries until "
        "paused_count reaches 10,000, then skipped (backlog_cap), both numbered; tests go only to their "
        "endpoint, with seq null",
    }
    return dump(
        {
            "conformance_version": CONFORMANCE_VERSION,
            "file": "payloads",
            "rules": rules,
            "build": cases,
            "deployment_name_header": header_cases(),
            "fan_out": fan_case,
        }
    )
