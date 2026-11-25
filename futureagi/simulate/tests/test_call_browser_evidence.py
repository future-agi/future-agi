"""What a trial's browser did, as run detail shows it: the screenshots come from the trial's own world_state file,
named by digest in its sealed record, never from a scenario key; each picture is the same job's stored screenshot
whose digest a row names. Every state is said, and a list that could not be read is never shown as empty."""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from simulate.models import HostedHarnessArtifact
from simulate.selectors import browser_evidence as be
from simulate.services.hosted_harness import create_hosted_job
from simulate.services.hosted_harness_ingestion import TRIAL_STATE_KEY
from simulate.tests.test_harness_versioning_backfill import (  # noqa: F401 - fixture
    storage as storage,
)
from simulate.tests.test_hosted_harness_channels import _payload

# World_state files as the kit writes them, made by its own code: a trial with no world door, and one with a door.
KIT_FILES = json.loads(
    (Path(__file__).parent / "browser_world_state_files.json").read_text()
)

BROWSER_SCHEMA = "futureagi.browser-world-state.v1"
DOOR_SCHEMA = "futureagi.world-state.v1"


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _shot(digest: str | None, url: str = "https://shop.example.com/cart", **extra):
    return {
        "file": "1.jpg",
        "when": "load",
        "url": url,
        "tab": 1,
        "at": 12.5,
        "bytes": 100,
        **({"artifact_id": f"sha256:{digest}"} if digest else {}),
        **extra,
    }


def _browser(*shots, **extra):
    return {
        "state": "used",
        "complete": True,
        "rows": [],
        "blocked_navigations": [],
        "blocked_page_requests": [],
        "screenshots": list(shots),
        "screenshots_capped": False,
        **extra,
    }


def _no_door_file(browser, **extra):
    return {
        "schema": BROWSER_SCHEMA,
        "why": "no_world_door",
        "recorded": False,
        "not_held": ["tables", "action_log"],
        **({"browser": browser} if browser is not None else {}),
        "blocked": [],
        **extra,
    }


class _Storage:
    """The object store as the page reads it: the bytes of each stored key, given a few at a time as a network read
    gives them, and every key read."""

    def __init__(self, piece: int = 7):
        self.objects: dict[str, bytes] = {}
        self.read: list[str] = []
        self.piece = piece

    def get_object(self, bucket, key):
        self.read.append(key)
        data = self.objects[key]
        position = 0

        def read(amount=None):
            nonlocal position
            size = min(self.piece, amount if amount is not None else len(data))
            chunk = data[position : position + size]
            position += len(chunk)
            return chunk

        response = MagicMock()
        response.read.side_effect = read
        return response


@pytest.fixture
def store():
    kept = _Storage()
    with (
        patch.object(be, "get_storage_client", return_value=kept),
        patch.object(
            be,
            "get_object_url",
            side_effect=lambda bucket, key: f"http://storage/{key}",
        ),
    ):
        yield kept


@pytest.fixture
def job(organization):
    job, _ = create_hosted_job(
        organization, _payload(), idempotency_key=f"browser-{uuid.uuid4()}"
    )
    job.artifact_level = "traces-and-recordings"
    job.save(update_fields=["artifact_level"])
    return job


def _stored(
    job,
    digest,
    kind="screenshot",
    size=100,
    scenario_key="trial-1",
    content_type="image/jpeg",
):
    return HostedHarnessArtifact.no_workspace_objects.create(
        job=job,
        sha256=digest,
        kind=kind,
        size=size,
        content_type=content_type,
        object_key=f"alk-harness/{job.organization_id}/{job.id}/{digest}",
        scenario_key=scenario_key,
    )


def _file(job, store, body, *, scenario_key="trial-1", size=None, raw=None):
    """A trial's world_state file, stored for the job; its digest as the sealed record names it."""
    data = raw if raw is not None else json.dumps(body).encode()
    digest = hashlib.sha256(data).hexdigest()
    row = _stored(
        job,
        digest,
        kind="world_state",
        size=size or len(data),
        scenario_key=scenario_key,
    )
    store.objects[row.object_key] = data
    return f"sha256:{digest}"


def _call(
    job,
    end_state,
    *,
    organization_id=None,
    workspace_id="same",
    recorded=False,
    job_id=None,
):
    state = {"state_changes": []}
    if recorded is False:
        state.update({"recorded": False, "why": "no_world_door"})
    else:
        state["door"] = True
    if end_state:
        state["end_state_artifact"] = end_state
    run_test = SimpleNamespace(
        organization_id=organization_id or job.organization_id,
        workspace_id=job.workspace_id if workspace_id == "same" else workspace_id,
    )
    return SimpleNamespace(
        id=uuid.uuid4(),
        call_metadata={
            TRIAL_STATE_KEY: state,
            "hosted_harness_receipt": {"job_id": str(job_id or job.id)},
        },
        test_execution=SimpleNamespace(run_test=run_test),
    )


@pytest.mark.django_db
def test_a_trial_is_of_a_browser_environment_when_its_job_has_sites_at_its_pin(job):
    assert be.browser_environment(_call(job, None)) is False
    job.payload = {**job.payload, "browser": {"allowed_sites": ["shop.example.com"]}}
    job.save(update_fields=["payload"])
    assert be.browser_environment(_call(job, None)) is True
    # Only the job that sent the trial's receipt, in the run's organisation and workspace.
    assert (
        be.browser_environment(_call(job, None, organization_id=uuid.uuid4())) is False
    )
    assert be.browser_environment(_call(job, None, workspace_id=uuid.uuid4())) is False
    assert be.browser_environment(_call(job, None, job_id=uuid.uuid4())) is False
    no_receipt = _call(job, None)
    no_receipt.call_metadata.pop("hosted_harness_receipt")
    assert be.browser_environment(no_receipt) is False
    # Read through the one function for a job's sites, with the job's pinned version.
    with patch.object(be, "browser_sites_at_pin", return_value=[]) as read:
        assert be.browser_environment(_call(job, None)) is False
    (payload, version), _ = read.call_args
    assert payload == job.payload and version is None


@pytest.mark.django_db
def test_a_browser_environment_whose_stored_list_is_refused_is_still_one(job):
    # A list the save would refuse still names a browser environment: its entry shows and says what was kept.
    job.payload = {**job.payload, "browser": {"allowed_sites": ["not a host"]}}
    job.save(update_fields=["payload"])
    assert be.browser_environment(_call(job, None)) is True


@pytest.mark.django_db
def test_screenshots_are_the_file_s_rows_in_order_each_with_its_stored_picture(
    job, store
):
    seen, lost = _digest("cart"), _digest("lost")
    _stored(job, seen)
    end = _file(
        job,
        store,
        _no_door_file(
            _browser(
                _shot(seen), _shot(lost, url="http://shop.example.com/pay", when="end")
            )
        ),
    )

    shown = be.browser_evidence(_call(job, end))

    assert shown["state"] == "listed"
    first, second = shown["screenshots"]
    assert first == {
        "when": "load",
        "url": "https://shop.example.com/cart",
        "link": True,
        "tab": 1,
        "at": 12.5,
        "kept": True,
        "picture_url": f"http://storage/alk-harness/{job.organization_id}/{job.id}/{seen}",
    }
    assert (second["kept"], second["picture_url"], second["when"]) == (
        False,
        None,
        "end",
    )
    assert shown["more"] == 0
    assert shown["capped"] is False
    assert shown["level_keeps_none"] is False


@pytest.mark.django_db
def test_a_picture_two_trials_took_alike_shows_in_both(job, store):
    same = _digest("start page")
    # Stored once, under the first trial's scenario key.
    _stored(job, same, scenario_key="trial-1")
    first = _file(
        job, store, _no_door_file(_browser(_shot(same))), scenario_key="trial-1"
    )
    second = _file(
        job,
        store,
        _no_door_file(_browser(_shot(same), _shot(None))),
        scenario_key="trial-2",
    )

    for end in (first, second):
        [picture, *_] = be.browser_evidence(_call(job, end))["screenshots"]
        assert picture["kept"] is True


@pytest.mark.django_db
def test_a_digest_another_job_holds_or_this_job_holds_as_another_kind_is_not_shown(
    job, store, organization
):
    other_job, _ = create_hosted_job(
        organization, _payload(), idempotency_key=f"other-{uuid.uuid4()}"
    )
    elsewhere, trace = _digest("other job"), _digest("a trace")
    _stored(other_job, elsewhere)
    _stored(job, trace, kind="tool_trace")
    end = _file(job, store, _no_door_file(_browser(_shot(elsewhere), _shot(trace))))

    shown = be.browser_evidence(_call(job, end))

    assert [one["kept"] for one in shown["screenshots"]] == [False, False]


@pytest.mark.django_db
def test_an_id_is_used_only_when_it_is_sixty_four_hex_characters(job, store):
    good = _digest("good")
    _stored(job, good)
    rows = [
        _shot(None, artifact_id=good.upper()),
        _shot(None, artifact_id=f"sha256:{good}0"),
        _shot(None, artifact_id={"id": good}),
        _shot(good),
    ]
    end = _file(job, store, _no_door_file(_browser(*rows)))

    shown = be.browser_evidence(_call(job, end))

    assert [one["kept"] for one in shown["screenshots"]] == [False, False, False, True]


@pytest.mark.django_db
def test_only_the_file_the_sealed_record_names_is_read_and_only_in_the_run_s_organisation_and_workspace(
    job, store
):
    end = _file(job, store, _no_door_file(_browser()))

    assert be.browser_evidence(_call(job, None))["state"] == "not_kept"
    assert (
        be.browser_evidence(_call(job, f"sha256:{_digest('never stored')}"))["state"]
        == "not_kept"
    )
    for other in (
        _call(job, end, organization_id=uuid.uuid4()),
        _call(job, end, workspace_id=uuid.uuid4()),
        _call(job, end, job_id=uuid.uuid4()),
    ):
        assert be.browser_evidence(other)["state"] == "not_kept"
    assert be.browser_evidence(_call(job, end))["state"] == "listed"


@pytest.mark.django_db
def test_a_file_too_large_is_never_read(job, store):
    end = _file(job, store, _no_door_file(_browser()), size=be.FILE_BYTES + 1)

    assert be.browser_evidence(_call(job, end))["state"] == "unreadable"
    assert store.read == []


@pytest.mark.django_db
@pytest.mark.parametrize(
    "raw",
    [
        b"not json",
        json.dumps({"schema": "someone.else.v1", "browser": _browser()}).encode(),
        json.dumps(["a", "list"]).encode(),
        json.dumps(_no_door_file({"state": "used", "complete": False})).encode(),
        json.dumps(_no_door_file({**_browser(), "screenshots": "many"})).encode(),
        b"[" * 100_000,
    ],
    ids=[
        "not-json",
        "unknown-schema",
        "not-an-object",
        "no-screenshot-list",
        "screenshots-not-a-list",
        "too-deep",
    ],
)
def test_a_file_that_cannot_be_read_is_said_never_shown_as_empty(job, store, raw):
    end = _file(job, store, None, raw=raw)

    shown = be.browser_evidence(_call(job, end))

    assert shown["state"] == "unreadable"
    assert shown["screenshots"] == []


@pytest.mark.django_db
def test_a_file_with_no_browser_says_no_browser_was_started(job, store):
    for browser in (None, {"state": "not_started"}):
        end = _file(job, store, _no_door_file(browser, why=str(browser)))
        assert be.browser_evidence(_call(job, end))["state"] == "no_browser"


@pytest.mark.django_db
def test_a_browser_the_agent_never_used_or_that_took_no_screenshot_lists_none(
    job, store
):
    for browser in ({"state": "not_used"}, _browser()):
        end = _file(job, store, _no_door_file(browser, why=str(browser)))
        shown = be.browser_evidence(_call(job, end))
        assert (shown["state"], shown["screenshots"]) == ("listed", [])


@pytest.mark.django_db
def test_the_cap_the_file_reached_and_the_rows_past_the_page_s_cap_are_said(job, store):
    rows = [
        _shot(None, url=f"https://shop.example.com/{n}")
        for n in range(be.SCREENSHOT_ROWS + 3)
    ]
    end = _file(job, store, _no_door_file(_browser(*rows, screenshots_capped=True)))

    shown = be.browser_evidence(_call(job, end))

    assert len(shown["screenshots"]) == be.SCREENSHOT_ROWS
    assert shown["more"] == 3
    assert shown["capped"] is True
    assert be.SCREENSHOT_ROWS >= 20  # the kit keeps at most 20 for a trial


@pytest.mark.django_db
def test_a_run_whose_level_keeps_no_screenshots_says_so(job, store):
    job.artifact_level = "traces"
    job.save(update_fields=["artifact_level"])
    end = _file(job, store, _no_door_file(_browser(_shot(_digest("x")))))

    shown = be.browser_evidence(_call(job, end))

    assert shown["level_keeps_none"] is True
    assert shown["screenshots"][0]["kept"] is False


@pytest.mark.django_db
def test_a_row_s_address_and_words_are_text_and_only_http_or_https_is_a_link(
    job, store
):
    rows = [
        _shot(None, url="javascript:alert(1)"),
        _shot(None, url="file:///etc/passwd"),
        _shot(None, url="HTTPS://shop.example.com/"),
        _shot(None, url={"not": "text"}, when=["load"], tab="one", at="soon"),
        _shot(None, url="https://shop.example.com/" + "a" * 5000),
    ]
    end = _file(job, store, _no_door_file(_browser(*rows)))

    js, local, upper, odd, long = be.browser_evidence(_call(job, end))["screenshots"]

    assert (js["url"], js["link"]) == ("javascript:alert(1)", False)
    assert local["link"] is False
    assert upper["link"] is True
    assert (odd["url"], odd["when"], odd["tab"], odd["at"], odd["link"]) == (
        "",
        "",
        None,
        None,
        False,
    )
    assert len(long["url"]) == be.TEXT
    # Nothing of a row is passed through whole.
    assert set(js) == {"when", "url", "link", "tab", "at", "kept", "picture_url"}


@pytest.mark.django_db
def test_a_picture_stored_as_something_other_than_an_image_is_not_shown(job, store):
    odd = _digest("odd")
    _stored(job, odd, content_type="text/html")
    end = _file(job, store, _no_door_file(_browser(_shot(odd))))

    assert be.browser_evidence(_call(job, end))["screenshots"][0]["kept"] is False


@pytest.mark.django_db
def test_a_door_world_s_file_is_read_whatever_it_says_of_its_records(job, store):
    shot = _digest("door shot")
    _stored(job, shot)
    end = _file(
        job,
        store,
        {
            "schema": DOOR_SCHEMA,
            "tables": {"orders": []},
            "action_log": [],
            "browser": _browser(_shot(shot)),
        },
    )

    shown = be.browser_evidence(_call(job, end, recorded=True))

    assert shown["state"] == "listed"
    assert shown["screenshots"][0]["kept"] is True


@pytest.mark.django_db
def test_a_trial_with_no_door_takes_its_blocked_list_from_the_file_and_a_door_trial_from_its_record(
    job, store
):
    blocked = [
        {
            "host": "www.example.com",
            "via": "browser",
            "what": "navigation",
            "url": "https://www.example.com/a?x=1",
            "at": 3,
        },
        {
            "host": "www.example.com",
            "via": "browser",
            "what": "navigation",
            "url": "https://www.example.com/b",
            "at": 4,
        },
        {
            "host": "cdn.other.test",
            "via": "browser",
            "what": "page_request",
            "count": 6,
        },
        {"host": "fonts.other.test", "method": "CONNECT", "via": "browser", "count": 3},
        {"truncated": True},
    ]
    end = _file(job, store, _no_door_file(_browser(), blocked=blocked))

    no_door = be.browser_evidence(_call(job, end))
    door = be.browser_evidence(_call(job, end, recorded=True))

    assert no_door["blocked_source"] == "file"
    # Shaped as the trial's own blocked list is: no address is served, each page attempt counts one.
    assert no_door["blocked"] == [
        {"host": "www.example.com", "via": "browser", "what": "navigation", "count": 1},
        {"host": "www.example.com", "via": "browser", "what": "navigation", "count": 1},
        {
            "host": "cdn.other.test",
            "via": "browser",
            "what": "page_request",
            "count": 6,
        },
        {"host": "fonts.other.test", "method": "CONNECT", "via": "browser", "count": 3},
    ]
    assert no_door["blocked_truncated"] is True
    # A door trial's lists are its record's, shown with its changes: never from both.
    assert door["blocked_source"] == "record"
    assert "blocked" not in door


@pytest.mark.django_db
def test_a_blocked_list_the_file_could_not_read_is_said(job, store):
    unread = _file(
        job, store, _no_door_file(_browser(), blocked_unreadable=True, why="x")
    )
    rows = [
        {"host": f"h{n}.test", "via": "browser", "what": "page_request", "count": 1}
        for n in range(be.BLOCKED_ROWS + 2)
    ]
    many = _file(job, store, _no_door_file(_browser(), blocked=rows, why="y"))
    missing = _file(job, store, {**_no_door_file(_browser(), why="z"), "blocked": None})

    assert be.browser_evidence(_call(job, unread))["blocked_source"] == "unreadable"
    assert be.browser_evidence(_call(job, missing))["blocked_source"] == "unreadable"
    shown = be.browser_evidence(_call(job, many))
    assert (len(shown["blocked"]), shown["blocked_truncated"]) == (
        be.BLOCKED_ROWS,
        True,
    )
    # With no file to read, a no-door trial's list is not known.
    assert be.browser_evidence(_call(job, None))["blocked_source"] == "unreadable"


@pytest.mark.django_db
def test_the_trial_s_browser_is_read_through_its_call_only_in_its_workspace(
    user, workspace, storage, settings, auth_client
):
    from accounts.models import Organization, Workspace
    from simulate.models import CallExecution, RunTest
    from simulate.services.scoring_status import locked_call_metadata_update
    from simulate.tests.test_harness_versioning_picks import _create, _environment

    settings.HARNESS_VERSIONING_ENABLED = True
    settings.HARNESS_BUNDLE_CERTIFY_ENABLED = False
    run = _create(_environment(user, workspace, "browser-scope", storage), key="s")
    call = CallExecution.objects.filter(test_execution_id=run.test_execution_id).first()
    locked_call_metadata_update(
        call.id,
        lambda metadata: (
            metadata.update(
                {TRIAL_STATE_KEY: {"recorded": False, "why": "no_world_door"}}
            )
            or True
        ),
    )
    url = f"/simulate/v3/call-executions/{call.id}/browser/"
    run_tests = RunTest.no_workspace_objects.filter(id=call.test_execution.run_test_id)

    # Call detail says whether the trial's environment has a browser; this one has none.
    detail = auth_client.get(f"/simulate/v3/call-executions/{call.id}/")
    assert detail.status_code == 200, detail.content
    assert detail.json()["browser_environment"] is False

    allowed = auth_client.get(url)
    assert allowed.status_code == 200, allowed.content
    assert allowed.json()["state"] == "not_kept"

    other_workspace = Workspace.no_workspace_objects.create(
        name="Other browser workspace",
        organization=workspace.organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )
    run_tests.update(workspace=other_workspace)
    assert auth_client.get(url).status_code == 404

    run_tests.update(
        workspace=workspace,
        organization=Organization.objects.create(name="Other browser organization"),
    )
    assert auth_client.get(url).status_code == 404


@pytest.mark.django_db
def test_the_kit_s_own_files_read_as_listed_with_their_pictures_and_refusals(
    job, store
):
    for text in KIT_FILES["shot_bytes"].values():
        _stored(job, hashlib.sha256(text.encode()).hexdigest())
    no_door = _file(job, store, KIT_FILES["no_door"])
    door = _file(job, store, KIT_FILES["door"])

    without = be.browser_evidence(_call(job, no_door))
    with_door = be.browser_evidence(_call(job, door, recorded=True))

    for shown in (without, with_door):
        assert shown["state"] == "listed"
        assert [
            (one["when"], one["url"], one["kept"]) for one in shown["screenshots"]
        ] == [
            ("load", "https://shop.example.com/", True),
            ("end", "https://shop.example.com/cart", True),
        ]
    assert without["blocked_source"] == "file"
    assert without["blocked"] == [
        {"host": "www.example.com", "via": "browser", "what": "navigation", "count": 1},
        {
            "host": "cdn.other.test",
            "via": "browser",
            "what": "page_request",
            "count": 1,
        },
    ]
    assert with_door["blocked_source"] == "record"


@pytest.mark.django_db
def test_a_file_storage_cannot_give_is_said_unreadable(job, store):
    end = _file(job, store, _no_door_file(_browser()))

    with patch.object(store, "get_object", side_effect=OSError("storage is down")):
        shown = be.browser_evidence(_call(job, end))

    assert shown["state"] == "unreadable"
    assert shown["blocked_source"] == "unreadable"


@pytest.mark.django_db
def test_every_field_of_a_listed_answer_is_served_as_read(job, store):
    from simulate.serializers.call_detail_v3 import CallBrowserEvidenceSerializer

    _stored(job, hashlib.sha256(KIT_FILES["shot_bytes"]["load"].encode()).hexdigest())
    body = KIT_FILES["no_door"]
    end = _file(job, store, {**body, "browser": {**body["browser"], "complete": False}})
    shown = be.browser_evidence(_call(job, end))

    served = json.loads(json.dumps(CallBrowserEvidenceSerializer(shown).data))

    assert shown["complete"] is False
    assert served == json.loads(json.dumps(shown))
