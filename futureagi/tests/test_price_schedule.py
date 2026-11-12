"""The dated price file the backend bills from stays a true copy of the gateway's.

The gateway prices requests from agentcc-gateway/internal/modeldb/price_schedule.json;
the backend image holds only futureagi/, so the backend reads its own copy. These
tests fail when the copy drifts, when backend CI would not run on a change to the
gateway's file, when a bad copy would load quietly, and when a catalogue row of
these models is not dated or disagrees with the price billed before the first date.

No database and no services: files are read and parsed.
"""

from __future__ import annotations

import json
import re
import runpy
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from agentic_eval.core_evals.run_prompt import price_schedule
from agentic_eval.core_evals.run_prompt.available_models import AVAILABLE_MODELS
from agentic_eval.core_evals.run_prompt.price_schedule import (
    PRICE_SCHEDULE,
    PRICE_SCHEDULE_PATH,
    PriceScheduleError,
    load_price_schedule,
)

ROOT = Path(__file__).resolve().parents[2]
GATEWAY_SCHEDULE = (
    ROOT / "agentcc-gateway" / "internal" / "modeldb" / "price_schedule.json"
)
BACKEND_CI = ROOT / ".github" / "workflows" / "backend-ci.yml"
DATED_MODEL = re.compile(r"^(gemini/|vertex_ai/)?gemini-3\.[678]-flash$")
RATE_NAMES = (
    "input_per_1M_tokens",
    "output_per_1M_tokens",
    "cached_input_per_1M_tokens",
)


def _row(**changes):
    row = {
        "from": "2027-01-01",
        "input_cost_per_token": 1.5e-6,
        "output_cost_per_token": 7.5e-6,
        "cache_read_input_token_cost": 1.5e-7,
        "input_cost_per_token_batches": 7.5e-7,
        "output_cost_per_token_batches": 3.75e-6,
    }
    row.update(changes)
    return row


def _one_row_file(**changes):
    return json.dumps({"gemini-3.7-flash": [_row(**changes)]})


def _catalogue_names():
    return [
        model["model_name"]
        for model in AVAILABLE_MODELS
        if isinstance(model.get("model_name"), str)
    ]


def test_the_python_copy_equals_the_gateway_file():
    assert GATEWAY_SCHEDULE.is_file(), (
        f"{GATEWAY_SCHEDULE} is missing. Run the backend tests from a full checkout "
        "of the repository: this test compares the backend's copy with it."
    )
    gateway = json.loads(GATEWAY_SCHEDULE.read_text(encoding="utf-8"))
    copy = json.loads(PRICE_SCHEDULE_PATH.read_text(encoding="utf-8"))
    assert copy == gateway, (
        f"{PRICE_SCHEDULE_PATH} differs from {GATEWAY_SCHEDULE}; copy the gateway's "
        "file over it so both bill the same dated prices."
    )


def test_every_row_starts_at_midnight_utc():
    """Each row starts at 00:00:00 UTC of its date, whatever the machine's zone."""
    raw = json.loads(PRICE_SCHEDULE_PATH.read_text(encoding="utf-8"))
    for key, rows in PRICE_SCHEDULE.items():
        for (start, _), row in zip(rows, raw[key], strict=True):
            year, month, day = (int(part) for part in row["from"].split("-"))
            assert start.tzinfo is UTC, f"{key} {row['from']}: read in {start.tzinfo!r}"
            assert start == datetime(year, month, day, tzinfo=UTC), (
                f"{key} {row['from']}: starts at {start.isoformat()}"
            )


def test_the_backend_ci_runs_when_the_gateway_schedule_changes():
    workflow = yaml.safe_load(BACKEND_CI.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["changes"]["steps"]
    filters = next(step for step in steps if step.get("id") == "filter")["with"]
    patterns = yaml.safe_load(filters["filters"])["backend"]
    assert "agentcc-gateway/internal/modeldb/price_schedule.json" in patterns


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(None, id="missing"),
        pytest.param("{", id="not-json"),
        pytest.param("[]", id="not-an-object"),
        pytest.param("{}", id="empty-object"),
        pytest.param(_one_row_file(**{"from": "2027-1-1"}), id="from-not-padded"),
        pytest.param(_one_row_file(**{"from": "20270101"}), id="from-no-dashes"),
        pytest.param(
            _one_row_file(**{"from": "2027-01-01T00:00:00Z"}), id="from-with-time"
        ),
        pytest.param(_one_row_file(input_cost_per_token=True), id="price-bool"),
        pytest.param(_one_row_file(output_cost_per_token="7.5e-7"), id="price-string"),
    ],
)
def test_the_loader_refuses_a_bad_file(tmp_path, content):
    path = tmp_path / "s.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")

    with pytest.raises(PriceScheduleError, match=r"^The dated price file "):
        load_price_schedule(path)


def test_importing_without_the_copy_fails_loudly(tmp_path):
    module = tmp_path / "price_schedule.py"
    shutil.copyfile(price_schedule.__file__, module)

    with pytest.raises(RuntimeError, match="is missing") as missing:
        runpy.run_path(str(module))
    assert missing.type.__name__ == "PriceScheduleError"

    (tmp_path / "price_schedule.json").write_text("{", encoding="utf-8")
    with pytest.raises(RuntimeError, match="not valid JSON") as malformed:
        runpy.run_path(str(module))
    assert malformed.type.__name__ == "PriceScheduleError"


def test_the_pricing_helper_loads_the_schedule_at_import():
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, agentic_eval.core_evals.fi_utils.token_count_helper; "
            "assert 'agentic_eval.core_evals.run_prompt.price_schedule' in sys.modules",
        ],
        cwd=ROOT / "futureagi",
        check=True,
    )


def test_static_catalogue_rows_equal_the_first_dated_row():
    checked = set()
    for model in AVAILABLE_MODELS:
        name = model.get("model_name")
        if not isinstance(name, str) or name not in PRICE_SCHEDULE:
            continue
        _, first_rates = PRICE_SCHEDULE[name][0]
        static = {rate: model["pricing"].get(rate) for rate in RATE_NAMES}
        assert static == pytest.approx(first_rates, rel=0, abs=1e-12), name
        checked.add(name)

    assert {
        "gemini/gemini-3.6-flash",
        "gemini/gemini-3.7-flash",
        "gemini/gemini-3.8-flash",
    } <= checked


def test_every_catalogue_row_of_these_models_is_dated():
    undated = [
        name
        for name in _catalogue_names()
        if DATED_MODEL.match(name) and name not in PRICE_SCHEDULE
    ]
    assert undated == []


@pytest.mark.requires_ee
def test_the_enterprise_vertex_rows_are_dated():
    names = set(_catalogue_names())
    for name in ("vertex_ai/gemini-3.6-flash", "vertex_ai/gemini-3.7-flash"):
        assert name in names
        assert name in PRICE_SCHEDULE
