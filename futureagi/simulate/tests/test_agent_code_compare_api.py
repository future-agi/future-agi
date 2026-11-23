from unittest.mock import patch

import pytest
from rest_framework import status

from simulate.models import HarnessAgentVersionRuntime
from simulate.services import agent_code_compare
from simulate.services.agent_code_compare import (
    AUTO_LOAD_LINES,
    DIFF_BUDGET_SECONDS,
    MAX_COMPARE_FILES,
    MAX_DIFF_LINES,
    MAX_PATCH_BATCH,
)
from simulate.services.harness_agent_versions import create_agent_version
from simulate.tests.test_agent_code_api import (
    FIRST,
    SECOND,
    _stored,
    _url,
    _version,
)
from simulate.tests.test_agent_code_api import agent as agent  # noqa: F401
from simulate.tests.test_agent_code_api import versioning_on as versioning_on  # noqa: F401
from simulate.tests.test_code_archives import storage as storage  # noqa: F401
from simulate.tests.test_harness_connected_versions import _source

pytestmark = pytest.mark.django_db


def _pair(agent, before, after, *, subfolder="agents/support"):
    _stored(agent, "c", before)
    _stored(agent, "d", after)
    older = _version(agent, commit="c", subfolder=subfolder)
    newer = _version(agent, commit="d", parent=older, subfolder=subfolder)
    return older, newer


def _compare(client, agent, version, against):
    response = client.get(_url(agent, version, "compare"), {"against": str(against.id)})
    assert response.status_code == status.HTTP_200_OK, response.content
    return response.json()


def _patches(client, agent, version, against, paths):
    return client.get(
        _url(agent, version, "compare/patches"),
        {"against": str(against.id), "path": paths},
    )


def test_the_summary_lists_each_changed_file_with_its_line_counts(
    auth_client,
    storage,
    agent,  # noqa: F811
):
    older, newer = _pair(agent, FIRST, SECOND)

    body = _compare(auth_client, agent, newer, older)

    assert body["version"] == str(newer.id)
    assert body["against"] == str(older.id)
    assert body["same_code"] is False
    assert body["truncated"] is False
    assert body["total_files"] == 3
    assert (body["additions"], body["deletions"]) == (2, 2)
    assert {one["path"]: one for one in body["files"]} == {
        "agents/support/agent.py": {
            "path": "agents/support/agent.py",
            "previous_path": None,
            "status": "modified",
            "kind": "text",
            "additions": 1,
            "deletions": 1,
            "collapsed": False,
        },
        "agents/support/prompts/system.md": {
            "path": "agents/support/prompts/system.md",
            "previous_path": None,
            "status": "removed",
            "kind": "text",
            "additions": 0,
            "deletions": 1,
            "collapsed": False,
        },
        "agents/support/tools.py": {
            "path": "agents/support/tools.py",
            "previous_path": None,
            "status": "added",
            "kind": "text",
            "additions": 1,
            "deletions": 0,
            "collapsed": False,
        },
    }


def test_the_older_version_is_always_the_left_side(
    auth_client,
    storage,
    agent,  # noqa: F811
):
    older, newer = _pair(agent, FIRST, SECOND)

    body = _compare(auth_client, agent, older, newer)

    assert body["version"] == str(newer.id)
    assert body["against"] == str(older.id)
    assert {one["path"]: one["status"] for one in body["files"]}[
        "agents/support/tools.py"
    ] == "added"


def test_a_moved_file_with_the_same_content_is_a_rename(
    auth_client,
    storage,
    agent,  # noqa: F811
):
    after = {**FIRST, "agents/support/prompts/main.md": "Be kind.\n"}
    del after["agents/support/prompts/system.md"]
    older, newer = _pair(agent, FIRST, after)

    body = _compare(auth_client, agent, newer, older)

    assert body["files"] == [
        {
            "path": "agents/support/prompts/main.md",
            "previous_path": "agents/support/prompts/system.md",
            "status": "renamed",
            "kind": "text",
            "additions": 0,
            "deletions": 0,
            "collapsed": False,
        }
    ]


def test_binary_and_oversized_files_carry_their_kind_and_no_counts(
    auth_client,
    storage,
    agent,  # noqa: F811
):
    after = {
        **FIRST,
        "agents/support/model.bin": b"\x00\x09changed",
        "agents/support/big.txt": "y" * (len(FIRST["agents/support/big.txt"])),
    }
    older, newer = _pair(agent, FIRST, after)

    files = {
        one["path"]: one for one in _compare(auth_client, agent, newer, older)["files"]
    }

    assert files["agents/support/model.bin"]["kind"] == "binary"
    assert files["agents/support/big.txt"]["kind"] == "too_large"
    assert files["agents/support/model.bin"]["additions"] is None
    patches = _patches(
        auth_client,
        agent,
        newer,
        older,
        ["agents/support/model.bin", "agents/support/big.txt"],
    ).json()["files"]
    assert [(one["kind"], one["hunks"]) for one in patches] == [
        ("binary", []),
        ("too_large", []),
    ]


def test_a_big_diff_is_collapsed_and_a_huge_one_is_too_large(
    auth_client,
    storage,
    agent,  # noqa: F811
):
    before = {
        "agents/support/medium.py": "",
        "agents/support/huge.py": "",
    }
    after = {
        "agents/support/medium.py": "x = 1\n" * (AUTO_LOAD_LINES + 1),
        "agents/support/huge.py": "x = 1\n" * (MAX_DIFF_LINES + 1),
    }
    older, newer = _pair(agent, before, after)

    files = {
        one["path"]: one for one in _compare(auth_client, agent, newer, older)["files"]
    }

    assert files["agents/support/medium.py"]["collapsed"] is True
    assert files["agents/support/medium.py"]["kind"] == "text"
    assert files["agents/support/medium.py"]["additions"] == AUTO_LOAD_LINES + 1
    assert files["agents/support/huge.py"]["kind"] == "too_large"
    assert files["agents/support/huge.py"]["additions"] is None
    patches = _patches(
        auth_client,
        agent,
        newer,
        older,
        ["agents/support/medium.py", "agents/support/huge.py"],
    ).json()["files"]
    assert len(patches[0]["hunks"][0]["lines"]) == AUTO_LOAD_LINES + 1
    assert (patches[1]["kind"], patches[1]["hunks"]) == ("too_large", [])


def test_a_file_past_the_line_cap_is_too_large_without_being_diffed(
    auth_client,
    storage,
    agent,  # noqa: F811
):
    repeated = "x = 1\n" * (MAX_DIFF_LINES + 1)
    older, newer = _pair(
        agent,
        {"agents/support/huge.py": repeated},
        {"agents/support/huge.py": repeated + "y = 2\n"},
    )

    with patch.object(
        agent_code_compare.difflib,
        "SequenceMatcher",
        wraps=agent_code_compare.difflib.SequenceMatcher,
    ) as matcher:
        [row] = _compare(auth_client, agent, newer, older)["files"]
        [one] = _patches(
            auth_client, agent, newer, older, ["agents/support/huge.py"]
        ).json()["files"]

    assert (row["kind"], row["additions"], row["deletions"]) == (
        "too_large",
        None,
        None,
    )
    assert (one["kind"], one["hunks"]) == ("too_large", [])
    matcher.assert_not_called()


def _past_the_budget_after_the_first_file():
    return patch.object(
        agent_code_compare.time,
        "monotonic",
        side_effect=[0.0] + [DIFF_BUDGET_SECONDS + 1] * 10,
    )


def test_files_past_the_time_budget_wait_for_load_and_the_summary_is_not_kept(
    auth_client,
    storage,
    agent,  # noqa: F811
):
    older, newer = _pair(
        agent,
        {"agents/support/a.py": "a = 1\n", "agents/support/b.py": "b = 1\n"},
        {"agents/support/a.py": "a = 2\n", "agents/support/b.py": "b = 2\n"},
    )

    with _past_the_budget_after_the_first_file():
        first = {
            one["path"]: one
            for one in _compare(auth_client, agent, newer, older)["files"]
        }
    with patch.object(
        agent_code_compare.difflib,
        "SequenceMatcher",
        wraps=agent_code_compare.difflib.SequenceMatcher,
    ) as matcher:
        second = {
            one["path"]: one
            for one in _compare(auth_client, agent, newer, older)["files"]
        }

    assert (
        first["agents/support/a.py"]["additions"],
        first["agents/support/a.py"]["collapsed"],
    ) == (1, False)
    assert first["agents/support/b.py"] == {
        "path": "agents/support/b.py",
        "previous_path": None,
        "status": "modified",
        "kind": "text",
        "additions": None,
        "deletions": None,
        "collapsed": True,
    }
    assert [second[path]["additions"] for path in sorted(second)] == [1, 1]
    assert matcher.call_count == 1


def test_a_patch_batch_past_the_time_budget_stops_diffing(
    auth_client,
    storage,
    agent,  # noqa: F811
):
    older, newer = _pair(
        agent,
        # Contents differ from the sibling budget test: patches are cached by blob,
        # so a shared pair could be served from that test's cache entry.
        {"agents/support/a.py": "a = 3\n", "agents/support/b.py": "b = 3\n"},
        {"agents/support/a.py": "a = 4\n", "agents/support/b.py": "b = 4\n"},
    )

    with _past_the_budget_after_the_first_file():
        files = _patches(
            auth_client,
            agent,
            newer,
            older,
            ["agents/support/a.py", "agents/support/b.py"],
        ).json()["files"]

    assert files[0]["kind"] == "text" and files[0]["hunks"]
    assert (files[1]["kind"], files[1]["hunks"]) == ("too_large", [])


def test_past_the_file_cap_the_summary_says_it_is_truncated(
    auth_client,
    storage,
    agent,  # noqa: F811
):
    after = {
        f"agents/support/many/f{index:04}.py": f"N = {index}\n"
        for index in range(MAX_COMPARE_FILES + 5)
    }
    older, newer = _pair(agent, FIRST, {**FIRST, **after})

    body = _compare(auth_client, agent, newer, older)

    assert body["truncated"] is True
    assert body["total_files"] == MAX_COMPARE_FILES + 5
    assert len(body["files"]) == MAX_COMPARE_FILES
    assert body["additions"] == MAX_COMPARE_FILES


def test_patches_are_hunks_of_changed_lines_with_context(
    auth_client,
    storage,
    agent,  # noqa: F811
):
    lines = [f"line {n}\n" for n in range(1, 21)]
    changed = list(lines)
    changed[9] = "line ten\n"
    older, newer = _pair(
        agent,
        {"agents/support/a.py": "".join(lines), "agents/support/gone.py": "x\n"},
        {"agents/support/a.py": "".join(changed)},
    )

    files = _patches(
        auth_client,
        agent,
        newer,
        older,
        ["agents/support/a.py", "agents/support/gone.py", "agents/support/same.py"],
    ).json()["files"]

    assert files[0]["hunks"] == [
        {
            "old_start": 7,
            "old_lines": 7,
            "new_start": 7,
            "new_lines": 7,
            "lines": [
                " line 7",
                " line 8",
                " line 9",
                "-line 10",
                "+line ten",
                " line 11",
                " line 12",
                " line 13",
            ],
        }
    ]
    assert files[1]["hunks"] == [
        {
            "old_start": 1,
            "old_lines": 1,
            "new_start": 0,
            "new_lines": 0,
            "lines": ["-x"],
        }
    ]
    assert files[2] == {
        "path": "agents/support/same.py",
        "kind": None,
        "unchanged": True,
        "hunks": [],
    }


def test_patches_after_the_summary_are_served_without_reading_the_code(
    auth_client,
    storage,
    agent,  # noqa: F811
):
    older, newer = _pair(agent, FIRST, SECOND)
    _compare(auth_client, agent, newer, older)

    with patch.object(
        agent_code_compare, "_texts", wraps=agent_code_compare._texts
    ) as read:
        response = _patches(
            auth_client, agent, newer, older, ["agents/support/agent.py"]
        )
        _compare(auth_client, agent, newer, older)

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["files"][0]["hunks"]
    read.assert_not_called()


def test_a_batch_past_the_limit_or_outside_the_code_is_refused(
    auth_client,
    storage,
    agent,  # noqa: F811
):
    older, newer = _pair(agent, FIRST, SECOND)

    too_many = _patches(
        auth_client,
        agent,
        newer,
        older,
        [f"agents/support/f{n}.py" for n in range(MAX_PATCH_BATCH + 1)],
    )
    outside = _patches(auth_client, agent, newer, older, ["../etc/passwd"])

    assert too_many.status_code == status.HTTP_400_BAD_REQUEST
    assert outside.status_code == status.HTTP_400_BAD_REQUEST


def test_versions_on_the_same_code_say_whether_only_env_values_changed(
    auth_client,
    storage,
    agent,  # noqa: F811
):
    _stored(agent, "c", FIRST)
    older = _version(agent, commit="c")
    newer = create_agent_version(
        agent,
        parent=older,
        env=None,
        source={**_source("c"), "subfolder": "agents/support"},
        prompt="You help, briefly.",
        agent_payload={"connector": "auto", "config": {}},
        trigger=HarnessAgentVersionRuntime.Trigger.CREDENTIALS,
    )
    assert newer.id != older.id
    rows = HarnessAgentVersionRuntime.no_workspace_objects
    rows.filter(agent_version=older).update(credential_set_hash="sha256:old")
    rows.filter(agent_version=newer).update(credential_set_hash="sha256:new")

    body = _compare(auth_client, agent, newer, older)

    assert (body["same_code"], body["env_changed"], body["files"]) == (
        True,
        True,
        [],
    )
    rows.filter(agent_version=newer).update(credential_set_hash="sha256:old")
    body = _compare(auth_client, agent, newer, older)
    assert (body["same_code"], body["env_changed"]) == (True, False)


def test_a_first_version_has_nothing_to_compare_with(
    auth_client,
    storage,
    agent,  # noqa: F811
):
    _stored(agent, "c", FIRST)
    only = _version(agent, commit="c")

    summary = auth_client.get(_url(agent, only, "compare")).json()
    patches = auth_client.get(
        _url(agent, only, "compare/patches"), {"path": ["README.md"]}
    )

    assert summary["unavailable"] == "no_previous_version"
    assert summary["files"] == []
    assert patches.status_code == status.HTTP_404_NOT_FOUND


def test_warming_a_version_computes_its_summary_against_the_previous_one(
    storage,
    agent,  # noqa: F811
):
    older, newer = _pair(agent, FIRST, SECOND)
    agent_code_compare.warm_compare(newer)

    with patch.object(
        agent_code_compare, "_texts", wraps=agent_code_compare._texts
    ) as read:
        body = agent_code_compare.compare(newer, older)

    read.assert_not_called()
    assert body["total_files"] == 3
