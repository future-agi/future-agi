"""WT-27's suite-wide half (DESIGN.md section 17): across the whole run, no log record holds ``whsec_`` or an entry of
``WEBHOOKS_ENCRYPTION_KEYS`` in force when it was made.

``log_sweep`` scans every record of the run; this test runs last and reads the result. It first plants records the
sweep must catch (a secret in the arguments, in an extra field and in an exception, and a key in an extra field), on a
logger that does not propagate and has only a ``NullHandler``. So a sweep that scans nothing, or misses a field,
cannot pass.
"""

from __future__ import annotations

import logging

from cryptography.fernet import Fernet

from webhooks.core.signing import new_secret
from webhooks.tests import evidence
from webhooks.tests.log_sweep import SWEEP


def test_wt27_no_secret_in_any_log_record_of_the_run(request, monkeypatch):
    items = request.session.items
    assert (
        items[-1] is request.node
    )  # moved last: every other test of the run has started
    assert SWEEP.started >= len(items)

    planted = logging.getLogger("webhooks.tests.planted")
    planted.propagate = False
    planted.addHandler(logging.NullHandler())
    planted.setLevel(logging.INFO)
    secret = new_secret()
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("WEBHOOKS_ENCRYPTION_KEYS", key)
    before = len(SWEEP.hits)
    planted.warning("rotated %s", secret)
    planted.warning("rotated", extra={"secret_value": {"new": [secret]}})
    try:
        raise ValueError(secret)
    except ValueError:
        planted.exception("rotate failed")
    planted.info("keys", extra={"keys_value": key})
    caught = SWEEP.hits[before:]
    del SWEEP.hits[before:]
    assert [(name, where) for _test, name, where in caught] == [
        ("webhooks.tests.planted", "message:whsec_,args:whsec_"),
        ("webhooks.tests.planted", "extra.secret_value:whsec_"),
        ("webhooks.tests.planted", "exc_info:whsec_"),
        ("webhooks.tests.planted", "extra.keys_value:encryption_key"),
    ]

    evidence.write(
        "WT-27",
        suite_wide_log_sweep={
            "log_records_scanned": SWEEP.records,
            "tests_run": SWEEP.started,
            "planted_records_caught": len(caught),
            "records_with_a_secret": len(SWEEP.hits),
            "scan_errors": SWEEP.errors,
        },
    )
    assert SWEEP.errors == 0
    assert SWEEP.records > len(items)  # the run logged; the sweep saw it
    assert SWEEP.hits == [], "\n".join(
        f"{test} | logger {name} | {where}" for test, name, where in SWEEP.hits
    )
