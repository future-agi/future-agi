"""Pick cards carry a few plain lines of the entry's recorded demo (GET /v1/picks).

The lines are the last ones the latest approved version's recording printed, with escape
sequences, control characters and the shell prompt removed, credentials hidden and each
line capped. A version with no recording, or one that does not parse, shows none.
"""

import json

import pytest

from omega_hub.services import storage
from omega_hub.tests.flows import publish
from omega_hub.tests.helpers import FIXTURES, entry_files

pytestmark = pytest.mark.django_db

PICKS = "/omega-hub/v1/picks"
HEADER = {"version": 2, "width": 100, "height": 6}
LISBON = [
    "answer: In Lisbon today: sunny, 24 degrees.",
    'tool get_weather ok=true -> {"city":"Lisbon","forecast":"sunny, 24 degrees"}',
]
ELLIPSIS = "\N{HORIZONTAL ELLIPSIS}"
CHECK = "\N{HEAVY CHECK MARK}"
RLO = "\N{RIGHT-TO-LEFT OVERRIDE}"
PDF = "\N{POP DIRECTIONAL FORMATTING}"
ZWSP = "\N{ZERO WIDTH SPACE}"
# Credential-shaped values, built from parts so no whole value sits in the source.
AWS_KEY_ID = "AK" + "IA" + "Q7" * 8
GITHUB_TOKEN = "gh" + "p_" + "aB3" * 12
SLACK_TOKEN = "xo" + "xb-" + "1234567890-" + "aBcDeFgHiJ"
SHORT_SK_KEY = "sk" + "-" + "aB3dE5" * 4
PROJECT_KEY = "sk" + "-proj-" + "Zx9" * 16

ODD_OUTPUT = {
    "control-characters": (
        [
            "ding\x07 done\tok\r\n",
            "typo\x08\x08\x08\x08fixed\r\n",
            "50%\r75%\rall files saved\r\n",
            f"left{RLO}right{PDF}, zero{ZWSP}width, nul\x00\ud800 and c1\x96 gone\r\n",
        ],
        [
            "ding done ok",
            "fixed",
            "all files saved",
            "leftright, zerowidth, nul and c1 gone",
        ],
    ),
    "escape-sequences": (
        [
            f"\x1b]0;demo\x07\x1b[1;32m{CHECK}\x1b[0m \x1b[4mbuilt\x1b[24m in 2.1s\r\n",
            "\x1b]8;;https://example.com/a\x1b\\open the report\x1b]8;;\x1b\\\r\n",
            "\x1b[2K\x1b[1G\x1b[?25lprogress done\x1b[?25h\r\n",
            "\x9b31mred via c1\x9b0m and \x1b(Bcharset\x1b=\r\n",
        ],
        [
            f"{CHECK} built in 2.1s",
            "open the report",
            "progress done",
            "red via c1 and charset",
        ],
    ),
    "long-lines": (
        ["short line\r\n", "word " * 2000 + "\r\n", "x" * 10000 + "\r\n"],
        ["short line", " ".join(["word"] * 24) + ELLIPSIS, "x" * 119 + ELLIPSIS],
    ),
    "credentials": (
        [
            f"uploading with {AWS_KEY_ID} to the bucket\r\n",
            f"github answered {GITHUB_TOKEN} is valid\r\n",
            f"slack {SLACK_TOKEN} and short {SHORT_SK_KEY} keys\r\n",
            "checked " * 13 + PROJECT_KEY + " done\r\n",
        ],
        [
            "uploading with [hidden: aws-access-key-id] to the bucket",
            "github answered [hidden: github-token] is valid",
            "slack [hidden: slack-token] and short [hidden: provider-key] keys",
            " ".join(["checked"] * 13) + ELLIPSIS,
        ],
    ),
}

V1_RECORDING = {
    "version": 1,
    "width": 80,
    "height": 24,
    "stdout": [[0.1, "answer: sunny\r\n"], [0.2, "done\r\n"]],
}


def recording(*chunks, header=HEADER, events=None) -> bytes:
    """An asciicast v2 recording whose output events print `chunks` in order."""
    rows = events if events is not None else [[0.1, "o", chunk] for chunk in chunks]
    return ("\n".join(json.dumps(row) for row in [header, *rows]) + "\n").encode()


NO_EXCERPT = {
    "plain-text": b"answer: sunny\r\ntool get_weather ok\r\n",
    "asciicast-v1": json.dumps(V1_RECORDING).encode(),
    "event-not-json": recording("answer: sunny\r\n", "done\r\n") + b"[0.3, o, done\n",
    "header-without-size": recording(
        "answer: sunny\r\n", "done\r\n", header={"version": 2}
    ),
    "one-line-only": recording("answer: sunny\r\n"),
}


def fixture_recording(name):
    header, *events = (FIXTURES / name / "demo.cast").read_text().splitlines()
    return json.loads(header), [json.loads(event) for event in events]


def pick_with(world, cast=None, *, name="first-agent", drop=()):
    """Publish `name` with `cast` as its demo.cast (the fixture's own when None),
    make it a pick this week, and return the published version."""
    extra = {"demo.cast": cast} if cast is not None else None
    entry = publish(world, name, files=entry_files(name, extra=extra, drop=drop))
    picked = world.b.post(
        "/omega-hub/v1/review/picks",
        {"entry": f"future-agi/{name}", "why": "Worth a look this week."},
        format="json",
    )
    assert picked.status_code == 201, picked.content
    return entry.latest_version


def demo_lines(client, name="first-agent"):
    response = client.get(PICKS)
    assert response.status_code == 200, response.content
    item = next(item for item in response.json() if item["ref"] == f"future-agi/{name}")
    assert "demoExcerpt" in item, sorted(item)
    return item["demoExcerpt"]


def unreachable(key):
    raise ConnectionError("storage is unreachable")


def test_a_pick_shows_the_last_lines_of_its_recorded_demo(world):
    pick_with(world, name="engineering-team")
    assert demo_lines(world.c, "engineering-team") == [
        "run 2: CI truncate: pass",
        "run 2: CI readingTime: pass",
        "run 2: CI all helpers together: pass",
        "run 2: feature verified: true; model calls: 1 (only the unfinished helper)",
    ]
    card = world.c.get("/omega-hub/v1/entries").json()["results"][0]
    assert card["ref"] == "future-agi/engineering-team"
    assert "demoExcerpt" not in card


def test_the_prompt_the_typed_input_and_colour_codes_never_reach_the_card(world):
    header, events = fixture_recording("first-agent")
    coloured = [[at, kind, f"\x1b[32m{text}\x1b[0m"] for at, kind, text in events]
    prompt = "\x1b[1;32m$\x1b[0m "
    cast = recording(
        header=header,
        events=[
            [0.0, "o", prompt + "omega run future-agi/first-agent\r\n"],
            [0.0, "i", "omega run future-agi/first-agent\r"],
            *coloured,
            [0.4, "o", prompt],
        ],
    )
    pick_with(world, cast)
    assert demo_lines(world.c) == LISBON


def test_a_pick_without_a_recording_has_no_excerpt(world):
    version = pick_with(world, drop=("demo.cast",))
    assert not version.cast_key
    assert demo_lines(world.c) is None


@pytest.mark.parametrize("cast", list(NO_EXCERPT.values()), ids=list(NO_EXCERPT))
def test_a_recording_that_does_not_parse_or_prints_one_line_has_no_excerpt(world, cast):
    pick_with(world, cast)
    assert demo_lines(world.c) is None


@pytest.mark.parametrize(
    ("chunks", "expected"), list(ODD_OUTPUT.values()), ids=list(ODD_OUTPUT)
)
def test_odd_output_reaches_the_card_as_plain_capped_lines(world, chunks, expected):
    pick_with(world, recording(*chunks))
    lines = demo_lines(world.c)
    assert lines == expected
    assert all(len(line) <= 120 for line in lines)


def test_a_new_version_brings_its_own_excerpt(world):
    pick_with(world)
    assert demo_lines(world.c) == LISBON
    porto = recording("answer: In Porto today: rain.\r\n", "tool get_weather ok\r\n")
    manifest = {"version": "1.0.1"}
    files = entry_files("first-agent", manifest=manifest, extra={"demo.cast": porto})
    publish(world, "first-agent", files=files)
    assert demo_lines(world.c) == [
        "answer: In Porto today: rain.",
        "tool get_weather ok",
    ]


def test_the_recording_is_read_once_and_then_served_from_the_cache(world, monkeypatch):
    pick_with(world)
    reads = []
    read_bytes = storage.read_bytes

    def counting(key):
        if key.startswith("casts/"):
            reads.append(key)
        return read_bytes(key)

    monkeypatch.setattr(storage, "read_bytes", counting)
    assert demo_lines(world.c) == LISBON
    assert demo_lines(world.c) == LISBON
    assert len(reads) == 1


@pytest.mark.parametrize("failure", ["missing-object", "storage-down"])
def test_a_storage_failure_gives_no_excerpt_and_the_picks_still_load(
    world, monkeypatch, failure
):
    version = pick_with(world)
    if failure == "missing-object":
        storage.get_store().delete(version.cast_key)
    else:
        monkeypatch.setattr(storage, "read_bytes", unreachable)
    assert demo_lines(world.c) is None
