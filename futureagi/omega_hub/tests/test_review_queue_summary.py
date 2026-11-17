"""Review queue rows carry the entry's one-line summary (GET /v1/review/queue).

The summary is the submitted manifest's tagline as one line of plain text: tags and the
characters a tagline may not hold are removed, every run of whitespace becomes one
space, and a longer one is cut at a word to at most 160 characters, ending with an
ellipsis. A manifest without a tagline, or a submission whose checks never ran (it has
no manifest yet), gives none (null).
"""

import json
from datetime import timedelta

import pytest
from django.utils import timezone

from omega_hub.models import CheckJob
from omega_hub.tests.helpers import (
    checker_client,
    entry_files,
    run_checks,
    submit,
    token_client,
)

pytestmark = pytest.mark.django_db

QUEUE = "/omega-hub/v1/review/queue"
ELLIPSIS = "\N{HORIZONTAL ELLIPSIS}"
RLO = "\N{RIGHT-TO-LEFT OVERRIDE}"
ZWSP = "\N{ZERO WIDTH SPACE}"
NOT_PLAIN = set("<>`[]\r\n\t")


def row_in_queue(world, sub_id, status="in_review"):
    """The submission's row in the queue as maintainer B reads it."""
    response = world.b.get(QUEUE, {"status": status})
    assert response.status_code == 200, response.content
    row = next(item for item in response.json()["results"] if item["id"] == sub_id)
    assert "summary" in row, sorted(row)
    return row


def in_review(world, manifest=None):
    """Submit first-agent with `manifest` merged into its omega.hub.json (a None value
    drops that key), pass its checks, and return its queue row."""
    files = entry_files("first-agent", manifest=manifest)
    created = submit(
        token_client(world.token_a),
        "future-agi",
        files,
        name="first-agent",
        version="1.0.0",
    )
    assert created.status_code == 201, created.content
    _, _, result = run_checks(files)
    assert result["submission"] == "in_review", result
    return row_in_queue(world, created.json()["id"])


def test_a_queue_row_carries_the_manifest_tagline_as_its_summary(world):
    tagline = json.loads(entry_files("first-agent")["omega.hub.json"])["tagline"]
    row = in_review(world)
    assert row["ref"] == "future-agi/first-agent"
    assert row["summary"] == tagline


@pytest.mark.parametrize(
    "tagline",
    [None, "", " \n\t ", ["Answers", "questions"], 42, "<p> </p>"],
    ids=["missing", "empty", "only-whitespace", "a-list", "a-number", "only-tags"],
)
def test_a_manifest_without_a_tagline_gives_no_summary(world, tagline):
    assert in_review(world, {"tagline": tagline})["summary"] is None


def test_a_submission_whose_checks_never_ran_has_no_summary(world):
    files = entry_files("first-agent")
    created = submit(
        token_client(world.token_a),
        "future-agi",
        files,
        name="first-agent",
        version="1.0.0",
    )
    checker = checker_client()
    for _ in range(3):
        job = checker.post("/omega-hub/internal/checker/claim").json()
        CheckJob.objects.filter(pk=job["job"]).update(
            lease_expires_at=timezone.now() - timedelta(minutes=1)
        )
    assert checker.post("/omega-hub/internal/checker/claim").status_code == 204
    row = row_in_queue(world, created.json()["id"], status="check_error")
    assert row["status"] == "check_error"
    assert row["summary"] is None


@pytest.mark.parametrize(
    ("tagline", "expected"),
    [
        ("word " * 100, " ".join(["word"] * 32) + ELLIPSIS),
        ("x" * 400, "x" * 159 + ELLIPSIS),
        ("y" * 160, "y" * 160),
    ],
    ids=["many-words", "one-long-word", "exactly-160"],
)
def test_a_long_tagline_is_cut_at_a_word_to_160_characters(world, tagline, expected):
    summary = in_review(world, {"tagline": tagline})["summary"]
    assert summary == expected
    assert len(summary) <= 160


@pytest.mark.parametrize(
    ("tagline", "expected"),
    [
        (
            '<b>Answers</b> questions <img src=x onerror="alert(1)">about '
            '<a href="https://example.com">your data</a>',
            "Answers questions about your data",
        ),
        (
            "Runs `npm test` in a [sandbox] and <reports> back",
            "Runs npm test in a sandbox and back",
        ),
        (
            f"Plans the week\r\nthen{RLO} books it,\tall in{ZWSP} one go",
            "Plans the week then books it, all in one go",
        ),
    ],
    ids=["html-tags", "markup-characters", "line-breaks-and-controls"],
)
def test_a_summary_is_one_line_of_plain_text(world, tagline, expected):
    summary = in_review(world, {"tagline": tagline})["summary"]
    assert summary == expected
    assert not NOT_PLAIN & set(summary)
