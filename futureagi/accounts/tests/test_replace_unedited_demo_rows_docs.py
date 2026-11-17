"""INSTALLATION.md tells an upgrading operator how to run replace_unedited_demo_rows.

The command rewrites the rows of an earlier seeded demo dataset that nobody
has changed (accounts/management/commands/replace_unedited_demo_rows.py). The
startup guard admits its bare dry run in a running service and its real run in
a one-off operator process; test_replace_unedited_demo_rows_command.py runs
both exactly as shown (test_a_bare_dry_run_runs_in_an_app_process and
test_the_documented_operator_run_runs_the_command). The page must show both,
and no other form. Standard library only: no Django, services or network.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DOC = ROOT / "INSTALLATION.md"
RUN = "manage.py replace_unedited_demo_rows"
OPERATOR_VARIABLES = (
    "SERVICE_TYPE=bootstrap",
    "STARTUP_DB_MUTATION_MODE=operator",
    "NO_STARTUP_DB_MUTATIONS=false",
)


def _text() -> str:
    return DOC.read_text(encoding="utf-8")


def _invocations() -> list[str]:
    """Every shown command line that runs the command: fenced lines (a backslash
    continuation joined to its next line, comment lines skipped) and inline code."""
    found = []
    fenced = False
    pending = ""
    for line in _text().splitlines():
        if line.strip().startswith("```"):
            fenced, pending = not fenced, ""
            continue
        if fenced:
            if line.rstrip().endswith("\\"):
                pending += line.rstrip()[:-1] + " "
                continue
            command = (pending + line).split(" #")[0].strip()
            pending = ""
            if RUN in command and not command.startswith("#"):
                found.append(command)
        else:
            found += [span for span in re.findall(r"`([^`]+)`", line) if RUN in span]
    return found


def _arguments(invocation: str) -> str:
    return invocation.split(RUN, 1)[1].strip()


def test_installation_shows_how_to_run_the_command():
    assert _invocations(), "INSTALLATION.md shows no way to run replace_unedited_demo_rows"


def test_the_dry_run_is_shown_without_the_operator_variables():
    dry_runs = [i for i in _invocations() if _arguments(i) == "--dry-run"]
    assert dry_runs, "INSTALLATION.md shows no dry run of replace_unedited_demo_rows"
    for invocation in dry_runs:
        assert not any(v in invocation for v in OPERATOR_VARIABLES), invocation


def test_the_real_run_is_shown_as_a_one_off_operator_process():
    real_runs = [i for i in _invocations() if _arguments(i) == ""]
    assert real_runs, "INSTALLATION.md shows no real run of replace_unedited_demo_rows"
    for invocation in real_runs:
        missing = [v for v in OPERATOR_VARIABLES if v not in invocation]
        assert not missing, (invocation, missing)


def test_no_other_form_is_shown():
    # The guard refuses every other form in a running service, for example
    # --dry-run together with --organization.
    for invocation in _invocations():
        assert _arguments(invocation) in {"", "--dry-run"}, invocation


def test_the_page_says_a_second_run_changes_nothing():
    assert re.search(r"running\s+it\s+again\s+changes\s+nothing", _text(), re.IGNORECASE)
