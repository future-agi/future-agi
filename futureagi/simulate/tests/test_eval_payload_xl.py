"""Source-level guard: every eval_outputs write in xl.py must use
``build_simulate_eval_payload``."""

from __future__ import annotations

import re
from pathlib import Path

XL_PY = (
    Path(__file__).resolve().parents[1] / "temporal" / "activities" / "xl.py"
).read_text()


# ── writer-site count guard ──────────────────────────────────────────────


def test_every_eval_outputs_write_uses_canonical_builder():
    lines = XL_PY.splitlines()
    offending = []
    found = 0
    for idx, line in enumerate(lines):
        if not re.search(r"call_execution\.eval_outputs\[[^\]]+\]\s*=", line):
            continue
        found += 1
        window = "\n".join(lines[idx : idx + 6])
        if "build_simulate_eval_payload(" not in window:
            offending.append(f"line {idx + 1}: {line.strip()}")
    assert found, (
        "No eval_outputs writes found in xl.py. Either the writer sites moved "
        "or the locator regex needs an update."
    )
    assert not offending, (
        "eval_outputs assignments not going through build_simulate_eval_payload:\n  "
        + "\n  ".join(offending)
    )


def test_canonical_builder_called_at_each_writer_site():
    calls = re.findall(r"build_simulate_eval_payload\(", XL_PY)
    assert len(calls) == 4, (
        f"Expected 4 build_simulate_eval_payload call sites in xl.py "
        f"(success / mapping-error / exception / no-transcript), found {len(calls)}"
    )
