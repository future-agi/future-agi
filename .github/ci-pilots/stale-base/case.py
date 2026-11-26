"""Fixed task and receipt checks. No candidate-defined selectors or commands."""

import hashlib
import json
import re

METHOD = {
    "acceptance": "test_pull_request_plans_from_the_merge_base_tip_not_a_stale_event_base",
    "preservation": "test_loader_uses_base_policy_and_bootstraps_conservatively",
}
WORKFLOWS = ("backend-ci.yml", "e2e-ci.yml", "update-ci.yml")
ORACLE = "eb5804ad55d2093c66267f6ca5e5b1a7bf0f0446d555bcf8bd90f7e4ccf39e4d"
POLICY = "f026e1c3c886e19ba78a7924a5caddf5a3ef03651e68f2b78f2349321e00d2c5"
SUBJECT = {
    "broken": {
        "commit": "ae0bf483a052580a2e57da454d51b5a43b90fbc2",
        "tree": "cc4f5c151073af40a9a867a72a524cfa28efe3ac",
        "bootstrap": "be06959d6e6c2e496a570c80a47e33e883af6791bb34ca866e20b2ad1cb50acc",
        "executed": "a94cd64575b127d810c89d107512f46b93769368bbffb0d859d22ee0669f1258",
        "workflows": [
            "1bcca1d33fd5398468c4cfddf22cf172e263831d3002191149f230a16595b7e0",
            "daca81f525a6f40c6bc83a2570aa50a6d53613a39ab78bffd81b276aefb0461e",
            "29ee8226c4572213afa6abc869e206f975aa7422fa2baea8be98836b6e770176",
        ],
    },
    "reference": {
        "commit": "81b6ec549c8e90c97113b1bae5fb43e27e1fc035",
        "tree": "0250436cb624acde739f3afda4d708bf8f8290d7",
        "bootstrap": "c438bb74a0d89bc84733043fbf6ba9ab3db5bb2269644e01d127a64b60d2e743",
        "executed": "df612162e7717400c4e148f4b3b0aad3748ad72252c2fde4662f217163fb2ad6",
        "workflows": [
            "7ee284d858de294ff2ef016605fcce1f455d7543dc2f8a59debb301a7ce627a0",
            "7e924a3844503b80681142ecd6b50502c304c8fe296d3f785d43ad58df5e43a2",
            "52c65acb445c7a11d07ce8e2bf4a0efb921bf9ce02b60e69b73f4f7de004b83a",
        ],
    },
}
MATRIX = {
    "broken": {"acceptance": "expected-red", "preservation": "pass"},
    "reference": {"acceptance": "pass", "preservation": "pass"},
}
TASK = {
    "id": "scope-pr-base-v1",
    "acceptance": "Plan from the actual PR merge tip when the event base predates it.",
    "preservation": "Bootstrap fully without published policy; prefer base over candidate policy.",
    "edit_boundary": "PR parent check in three historical bootstrap copies only; no edits during replay.",
    "prohibited": "Product, policy, grader, repository workflows, required checks and queue rules.",
    "subjects": SUBJECT,
    "workflow_order": WORKFLOWS,
    "oracle_revision": SUBJECT["reference"]["commit"],
    "test_sha256": ORACLE,
    "policy_sha256": POLICY,
    "methods": METHOD,
    "expected": MATRIX,
}


class Invalid(ValueError):
    """Evidence is missing or does not identify the expected behavior."""


def require(condition, reason):
    if not condition:
        raise Invalid(reason)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encode(record):
    return (json.dumps(record, indent=2, sort_keys=True) + "\n").encode()


def oid(value):
    return (
        isinstance(value, str)
        and re.fullmatch("[0-9a-f]{40}", value)
        and value != "0" * 40
    )


def full_id(role):
    return "test_ci_scope.CandidateScope." + METHOD[role]


def check_outer(o):
    require(
        o["event"] == "pull_request" and o["ref"] == f"refs/pull/{o['pr']}/merge",
        "outer PR ref",
    )
    require(
        all(
            oid(o[k])
            for k in (
                "advertised_base",
                "head",
                "event_sha",
                "tested_commit",
                "tested_tree",
            )
        ),
        "outer OID",
    )
    require(o["tested_commit"] == o["event_sha"], "outer checkout")
    require(
        len(o["parents"]) == 2 and all(oid(p) for p in o["parents"]), "outer parents"
    )
    require(
        o["parents"] == [o["effective_base"], o["head"]] and o["ancestor_rc"] == 0,
        "outer endpoints",
    )
    for key in ("run_id", "run_attempt", "pr"):
        require(re.fullmatch(r"[1-9][0-9]*", str(o[key])), "missing " + key)


def check_call(call, subject, event):
    require(call["event"] == event, "fixture event")
    for key in ("base", "head", "tested_commit", "tested_tree", "event_sha"):
        require(oid(call[key]), "fixture " + key)
    require(call["event_sha"] == call["tested_commit"], "fixture checkout")
    require(call["event_tree"] == call["tested_tree"], "fixture tree")
    marker = call["stderr_marker"]
    require(
        marker is None or (isinstance(marker, list) and len(marker) == 2),
        "unexpected loader diagnostic",
    )
    expected_stderr = (
        b"" if marker is None else f"PILOT_ERR {marker[0]} {marker[1]}\n".encode()
    )
    require(
        call["stderr_sha256"] == digest(expected_stderr), "loader diagnostic digest"
    )
    parents = call["parents"]
    require(
        len(parents) == (2 if event == "pull_request" else 1)
        and all(oid(p) for p in parents),
        "fixture parents",
    )
    require(
        call["original_sha256"] == subject["bootstrap"]
        and call["executed_sha256"] == subject["executed"],
        "executed body",
    )


def rejected(call, line):
    require(
        call["returncode"] == 1
        and call["stderr_marker"] == [line, 1]
        and call["plan"] is None,
        "wrong rejection site",
    )


def planned(call, policy_base, diff_base):
    require(call["returncode"] == 0 and call["stderr_marker"] is None, "loader failed")
    p = call["plan"]
    require(isinstance(p, dict), "missing plan")
    for key, expected in {
        "version": 1,
        "mode": "qualified",
        "event": call["event"],
        "policy_sha256": POLICY,
        "policy_base": policy_base,
        "diff_base": diff_base,
        "diff_head": call["head"],
        "tested_commit": call["tested_commit"],
        "tested_tree": call["tested_tree"],
        "path_count": 1,
    }.items():
        require(
            type(p.get(key)) is type(expected) and p[key] == expected, "plan " + key
        )


def check_result(arm, role, receipt, returncode, binding):
    expected = MATRIX[arm][role]
    subject = SUBJECT[arm]
    require(
        isinstance(receipt, dict) and "error" not in receipt,
        "missing or incomplete child receipt",
    )
    require(
        receipt["binding"] == binding
        and receipt["arm"] == arm
        and receipt["role"] == role,
        "child binding",
    )
    require(
        receipt["source"] == dict(subject, oracle=ORACLE, policy=POLICY), "source pins"
    )
    for key in ("bootstrap", "executed"):
        body = receipt["bodies"][key]
        require(
            isinstance(body, str) and digest(body.encode()) == subject[key],
            "body bytes: " + key,
        )
    test_id = full_id(role)
    require(
        receipt["selected"] == receipt["started"] == receipt["stopped"] == [test_id],
        "test inventory",
    )
    red = expected == "expected-red"
    terminal = (
        {
            "id": test_id,
            "status": "failure",
            "exception": "AssertionError",
            "oracle_frames": [[METHOD[role], 327]],
        }
        if red
        else {"id": test_id, "status": "success"}
    )
    require(
        receipt["terminal"] == [terminal] and returncode == int(red),
        "terminal result or raw status",
    )
    calls = receipt["calls"]
    for c in calls:
        check_call(
            c, subject, "pull_request" if role == "acceptance" else "merge_group"
        )
    if role == "preservation":
        require(len(calls) == 2, "preservation loader count")
        legacy, published = calls
        for c in calls:
            require(
                c["parents"] == [c["base"]] and c["head"] == c["tested_commit"],
                "group endpoints",
            )
        require(
            legacy["returncode"] == 0
            and legacy["stderr_marker"] is None
            and legacy["plan"]
            == {
                "version": 0,
                "mode": "full",
                "reason": "legacy policy base",
                "tested_commit": legacy["head"],
                "policy_base": legacy["base"],
            },
            "legacy plan",
        )
        require(published["base"] == legacy["head"], "published base")
        planned(published, published["base"], published["base"])
    else:
        require(len(calls) == (1 if red else 4), "acceptance loader count")
        stale = calls[0]
        tip, head = stale["parents"]
        root = stale["merge_base"]
        require(
            oid(root) and len({root, tip, head, stale["tested_commit"]}) == 4,
            "not a diverged fixture",
        )
        require(
            (stale["base"], stale["head"], stale["ancestor_rc"]) == (root, head, 0),
            "not the stale first invocation",
        )
        if red:
            rejected(stale, 13)
        else:
            current, wrong, unrelated = calls[1:]
            for c in calls[1:]:
                require(
                    all(
                        c[k] == stale[k]
                        for k in (
                            "tested_commit",
                            "tested_tree",
                            "parents",
                            "merge_base",
                        )
                    ),
                    "changed fixture",
                )
            require(
                (current["base"], current["head"], current["ancestor_rc"])
                == (tip, head, 0),
                "current endpoints",
            )
            for c in (stale, current):
                planned(c, tip, root)
            require(
                (wrong["base"], wrong["head"], wrong["ancestor_rc"]) == (root, tip, 0),
                "wrong-head control",
            )
            require(
                unrelated["head"] == head and unrelated["ancestor_rc"] == 1,
                "unrelated-base control",
            )
            rejected(wrong, 18)
            rejected(unrelated, 19)
    return expected
