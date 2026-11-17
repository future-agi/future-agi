"""The Hub's object-store credentials (ops/iam/*.json, RUNBOOK "Permissions" and "Approved bundles
stay locked").

The app credential keeps ``s3:DeleteObjectVersion``: erasure and retention delete every version of a
raw upload, a cast, or a removed entry's bundle once its lock has lapsed (services/storage.py
``purge``). What keeps an approved pin safe is the GOVERNANCE lock, renewed while the entry is live
(services/bundle_locks.py), plus the app holding no ``s3:BypassGovernanceRetention``: it can neither
delete a locked version nor shorten its lock. These tests pin that split so a policy edit cannot
quietly widen it.
"""

import json
from pathlib import Path

OPS = Path(__file__).resolve().parent.parent / "ops"
BUCKET = "arn:aws:s3:::omega-hub"
OBJECTS = f"{BUCKET}/*"

# Never on the app credential: each would let it remove or weaken the lock on an approved pin, or
# reshape the bucket itself.
APP_FORBIDDEN = {
    "s3:BypassGovernanceRetention",
    "s3:PutObjectLegalHold",
    "s3:PutBucketObjectLockConfiguration",
    "s3:PutBucketVersioning",
    "s3:PutLifecycleConfiguration",
    "s3:PutBucketPolicy",
    "s3:DeleteBucketPolicy",
    "s3:CreateBucket",
    "s3:DeleteBucket",
}


def _statements(name: str) -> list[dict]:
    policy = json.loads((OPS / "iam" / name).read_text())
    assert policy["Version"] == "2012-10-17"
    return policy["Statement"]


def _actions(statements) -> set[str]:
    return {action for statement in statements for action in statement["Action"]}


def test_every_statement_allows_named_actions_on_the_hub_bucket_only():
    for name in ("app-policy.json", "provision-policy.json"):
        for statement in _statements(name):
            assert statement["Effect"] == "Allow", name
            assert "NotAction" not in statement and "NotResource" not in statement, name
            assert all("*" not in action for action in statement["Action"]), (name, statement["Action"])
            assert set(statement["Resource"]) <= {BUCKET, OBJECTS}, (name, statement["Resource"])


def test_the_app_credential_cannot_bypass_or_weaken_a_bundle_lock_or_reshape_the_bucket():
    actions = _actions(_statements("app-policy.json"))
    assert not actions & APP_FORBIDDEN, sorted(actions & APP_FORBIDDEN)
    assert not {action for action in actions if action.startswith("s3:PutBucket")}


def test_version_deletes_and_retention_writes_are_object_scoped():
    for statement in _statements("app-policy.json"):
        scoped = {"s3:DeleteObjectVersion", "s3:DeleteObject", "s3:PutObjectRetention"} & set(statement["Action"])
        if scoped:
            assert statement["Resource"] == [OBJECTS], (scoped, statement["Resource"])
    actions = _actions(_statements("app-policy.json"))
    # Needed: purge erases every version (raw uploads, casts, removed entries' lapsed bundles), and
    # the lock renewal extends retention on each retained version.
    assert {"s3:DeleteObjectVersion", "s3:PutObjectRetention", "s3:GetObjectRetention"} <= actions


def test_the_provision_credential_only_configures_the_bucket():
    statements = _statements("provision-policy.json")
    assert all(statement["Resource"] == [BUCKET] for statement in statements)
    actions = _actions(statements)
    assert "s3:BypassGovernanceRetention" not in actions
    assert not {action for action in actions if "Object" in action and action != "s3:PutBucketObjectLockConfiguration"}


def test_the_runbook_states_why_the_app_keeps_version_deletes():
    runbook = (OPS / "RUNBOOK.md").read_text()
    assert "s3:BypassGovernanceRetention" in runbook
    assert "s3:DeleteObjectVersion" in runbook
