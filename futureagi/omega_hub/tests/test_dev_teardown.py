"""scripts/omega-hub/teardown_bucket.py, which dev-down.sh runs: the lane bucket is versioned with object
lock, so teardown deletes every version and delete marker by id (bypassing GOVERNANCE retention), then the
bucket, and reports what it could not remove instead of hiding it."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "omega-hub" / "teardown_bucket.py"
DEV_DOWN = ROOT / "scripts" / "omega-hub" / "dev-down.sh"


def _module():
    spec = importlib.util.spec_from_file_location("omega_hub_teardown_bucket", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeMinio:
    def __init__(self, items, *, exists=True, errors=(), bucket_error=None):
        self.items, self.exists, self.errors, self.bucket_error = items, exists, list(errors), bucket_error
        self.removed, self.bypass, self.bucket_removed, self.errors_consumed = None, None, False, False

    def bucket_exists(self, bucket):
        return self.exists

    def list_objects(self, bucket, *, recursive, include_version):
        assert recursive and include_version
        return [SimpleNamespace(object_name=name, version_id=version, is_delete_marker=marker)
                for name, version, marker in self.items]

    def remove_objects(self, bucket, targets, *, bypass_governance_mode):
        self.removed, self.bypass = list(targets), bypass_governance_mode

        def lazy():  # minio-py deletes only as its error iterator is consumed
            yield from self.errors
            self.errors_consumed = True

        return lazy()

    def remove_bucket(self, bucket):
        if self.bucket_error:
            raise self.bucket_error
        self.bucket_removed = True


def _target(name, version):
    return (name, version)


def test_every_version_and_delete_marker_goes_by_id_with_governance_bypass_then_the_bucket():
    client = FakeMinio([("bundles/a.tar.gz", "v1", False), ("bundles/a.tar.gz", "v2", False),
                        ("bundles/a.tar.gz", "m1", True), ("raw/u.tar.gz", "v3", False)])
    count, errors = _module().empty_and_remove(client, "omega-hub-lane", delete_object=_target)
    assert (count, errors) == (4, [])
    assert client.removed == [("bundles/a.tar.gz", "v1"), ("bundles/a.tar.gz", "v2"), ("bundles/a.tar.gz", "m1"),
                              ("raw/u.tar.gz", "v3")]
    assert client.bypass is True
    assert client.errors_consumed
    assert client.bucket_removed


def test_a_missing_bucket_is_nothing_to_do():
    client = FakeMinio([], exists=False)
    assert _module().empty_and_remove(client, "omega-hub-lane", delete_object=_target) == (0, [])
    assert client.removed is None and not client.bucket_removed


def test_a_version_it_cannot_delete_is_reported_and_the_bucket_is_kept():
    refused = SimpleNamespace(name="bundles/a.tar.gz", version_id="v1", code="AccessDenied", message="Access Denied.")
    client = FakeMinio([("bundles/a.tar.gz", "v1", False)], errors=[refused])
    count, errors = _module().empty_and_remove(client, "omega-hub-lane", delete_object=_target)
    assert count == 1
    assert errors == ["bundles/a.tar.gz (version v1): AccessDenied Access Denied."]
    assert not client.bucket_removed


def test_a_bucket_that_will_not_go_is_reported():
    client = FakeMinio([], bucket_error=RuntimeError("BucketNotEmpty"))
    _, errors = _module().empty_and_remove(client, "omega-hub-lane", delete_object=_target)
    assert errors == ["remove_bucket: RuntimeError: BucketNotEmpty"]


def test_dev_down_runs_the_teardown_and_fails_when_the_bucket_stays():
    script = DEV_DOWN.read_text()
    line = next(row for row in script.splitlines() if "teardown_bucket.py" in row and not row.startswith("#"))
    assert "2>/dev/null" not in line and "|| true" not in line
    assert "bucket_rc=$?" in line
    assert 'exit 1; }' in script.splitlines()[-1]
