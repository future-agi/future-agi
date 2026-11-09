"""The section 17.1 evidence helper (``webhooks.tests.evidence``): what a row's test writes is what a reviewer reads."""

from __future__ import annotations

import json

from webhooks.tests import evidence


def test_a_second_write_keeps_what_the_test_wrote_before(tmp_path, monkeypatch):
    """A test that writes its measurements and then its checks (WT-08, WT-09) keeps both: the second write adds its
    keys to the test's entry, a key written again takes the new value, and another test's entry is untouched."""
    monkeypatch.setattr(evidence, "DIR", tmp_path)
    monkeypatch.setenv("OMEGA_EXEC_SOURCE_HEAD", "abc123")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "t.py::test_one (call)")
    evidence.write("WT-99", gaps_s=[1.0, 2.0], within_row_windows=[True, True], n=1)
    evidence.write("WT-99", gap_checks=[{"attempt": 2}], n=2)
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "t.py::test_two (call)")
    evidence.write("WT-99", other=True)

    doc = json.loads((tmp_path / "WT-99.json").read_text())
    one = doc["tests"]["t.py::test_one"]
    assert one["gaps_s"] == [1.0, 2.0] and one["within_row_windows"] == [True, True]
    assert one["gap_checks"] == [{"attempt": 2}] and one["n"] == 2
    assert "written_at" in one
    assert doc["tests"]["t.py::test_two"]["other"] is True
    assert (doc["row"], doc["head"]) == ("WT-99", "abc123")
