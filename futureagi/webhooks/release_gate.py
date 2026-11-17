"""DESIGN.md section 18's release gate, in code: "the Webhooks section stays absent until WT-01 to WT-21 and WT-23
pass", with WT-09's amended text and the new row WT-09b.

``SECTION_ROWS`` are the gate's rows of section 17.2. ``OPEN_ROWS`` are the ones whose named tests have not yet
passed in a recorded run. A row leaves ``OPEN_ROWS`` in the commit that records that run, or a recorded deviation,
and with ``OPEN_ROWS`` empty the gate is
passed: the section shows wherever the dispatcher may send and the webhooks keys are set.

While any row is open, ``integrations.services.destinations.webhooks_availability`` answers ``release_gate``: the
section is absent, never "coming soon", on every install, self-hosted ones that set ``WEBHOOKS_ENCRYPTION_KEYS``
included. The one exception is the section 17 test plan itself: ``FI_WEBHOOKS_TEST_PLAN=true`` shows the section so
its rows (the e2e flows of WT-23 among them) can run before the gate is passed. It is ignored on hosted settings, as
``FI_WEBHOOKS_TIME_SCALE`` is (WT-34). With the gate passed the switch changes nothing.

No Django import here.
"""

from __future__ import annotations

import os

SECTION_ROWS: tuple[str, ...] = (
    "WT-01",
    "WT-02",
    "WT-03",
    "WT-04",
    "WT-05",
    "WT-05b",
    "WT-05c",
    "WT-06",
    "WT-06b",
    "WT-07",
    "WT-08",
    "WT-08b",
    "WT-09",
    "WT-09b",
    "WT-10",
    "WT-11",
    "WT-12",
    "WT-13",
    "WT-13b",
    "WT-14",
    "WT-15",
    "WT-15b",
    "WT-15c",
    "WT-16",
    "WT-16b",
    "WT-17",
    "WT-18",
    "WT-18b",
    "WT-19",
    "WT-20",
    "WT-20b",
    "WT-21",
    "WT-21b",
    "WT-21c",
    "WT-23",
)

OPEN_ROWS: tuple[str, ...] = ()
"""Gate rows whose named tests have not yet passed in a recorded run (or that have no recorded deviation). Empty:
every gate row has passed. WT-09 was the last one open; its text was amended (one refused event on an endpoint
whose other events succeed) and WT-09b was added (an endpoint answering 500 to everything is turned off by section
7.7's 24 h rule, its deliveries paused, never dropped), and both have passed."""


def in_test_plan(*, hosted: bool | None = None) -> bool:
    """``FI_WEBHOOKS_TEST_PLAN=true``: the section 17 test plan's stack. Ignored on hosted settings."""
    if os.environ.get("FI_WEBHOOKS_TEST_PLAN", "").strip().lower() not in (
        "1",
        "t",
        "true",
        "yes",
        "on",
    ):
        return False
    if hosted is None:
        from webhooks import config

        hosted = config.is_hosted()
    return not hosted


def section_held(*, hosted: bool | None = None) -> tuple[str, ...]:
    """The open gate rows that keep the section absent on this install; empty when it may show."""
    if not OPEN_ROWS or in_test_plan(hosted=hosted):
        return ()
    return OPEN_ROWS
