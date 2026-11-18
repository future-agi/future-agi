"""The quarantine file is the day-one-green ratchet: entries must be owned,
dated, and expire within the cap so the backlog cannot silently rot."""

import datetime
import json
from pathlib import Path

import conftest
from conftest import _load_quarantine_entries

QUARANTINE = Path(__file__).parent.parent / ".test_quarantine.json"
REQUIRED_KEYS = {"id", "reason", "owner", "added", "expires", "mode"}
MAX_QUARANTINE_DAYS = 45


def _entries():
    data = json.loads(QUARANTINE.read_text())
    assert data["version"] == 1
    return data["entries"]


def test_entries_have_required_fields_and_valid_modes():
    seen = set()
    for e in _entries():
        missing = REQUIRED_KEYS - e.keys()
        assert not missing, f"{e.get('id', '<no id>')} missing {missing}"
        assert e["mode"] in ("run", "skip")
        assert e["id"] not in seen, f"duplicate entry {e['id']}"
        seen.add(e["id"])


def test_entries_are_test_scoped_and_owned():
    for e in _entries():
        assert "::" in e["id"], (
            f"{e['id']} quarantines a whole file or directory; "
            "entries must name a single test (path::test_name)"
        )
        owner = e["owner"].strip()
        assert owner and owner != "unassigned", (
            f"{e['id']} has no owner; quarantine needs someone accountable for it"
        )
        issue = e.get("issue")
        assert isinstance(issue, str) and issue.strip(), (
            f"{e['id']} has no tracking issue"
        )


def test_scoped_and_owned_assertions_pass_on_compliant_entries(tmp_path, monkeypatch):
    """The ratchet above only proves entries are non-compliant if it also passes
    on compliant ones."""
    compliant = {
        "id": "some/module.py::TestThing::test_thing",
        "reason": "flaky under parallel CH access",
        "owner": "atharva",
        "issue": "TH-1234",
        "added": "2026-08-05",
        "expires": "2026-09-19",
        "mode": "run",
    }
    path = tmp_path / ".test_quarantine.json"
    path.write_text(json.dumps({"version": 1, "entries": [compliant]}))
    monkeypatch.setitem(globals(), "QUARANTINE", path)

    test_entries_are_test_scoped_and_owned()


def test_expiry_window_is_capped():
    for e in _entries():
        added = datetime.date.fromisoformat(e["added"])
        expires = datetime.date.fromisoformat(e["expires"])
        assert added <= expires, e["id"]
        assert (expires - added).days <= MAX_QUARANTINE_DAYS, (
            f"{e['id']} quarantined for more than {MAX_QUARANTINE_DAYS} days"
        )


def test_loader_drops_malformed_entries_without_raising(tmp_path, monkeypatch):
    """A hand-edited entry missing a key must not reach the marker code, which
    subscripts entries directly — a raise there is an INTERNALERROR that runs
    zero tests and hides the validity tests that would explain it."""
    valid = {
        "id": "some/module.py::test_thing",
        "reason": "r",
        "owner": "o",
        "issue": "",
        "added": "2026-08-05",
        "expires": "9999-12-31",
        "mode": "run",
    }
    malformed = {k: v for k, v in valid.items() if k != "reason"}
    malformed["id"] = "some/module.py::test_other"

    path = tmp_path / ".test_quarantine.json"
    path.write_text(json.dumps({"version": 1, "entries": [valid, malformed]}))
    monkeypatch.setattr(conftest, "_QUARANTINE_PATH", path)

    assert _load_quarantine_entries() == [valid]
