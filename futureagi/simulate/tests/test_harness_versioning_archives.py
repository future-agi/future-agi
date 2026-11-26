import io
import os
import tarfile

import pytest

from simulate.services.harness_versioning import archives, diffs
from simulate.services.hosted_harness import HostedHarnessError

AUTHORING = {
    "contract.json": b'{"agent": "support"}',
    "sub_goals.json": b'{"sub_goals": []}',
    "simulator_prompt.md": b"Be a caller.",
    "schema.sql": b"create table orders(id int);",
    "world.sqlite": b"SQLite format 3\x00",
    "handlers/lookup.py": b"def handle(db):\n    return {}\n",
    "generic-harness/source-model.json": b"{}",
    "generic-harness/world-ir.json": b"{}",
    "harness-events.jsonl": b"{}\n",
    "scenarios/refund/scenario.json": b'{"name": "refund"}',
    "scenarios/refund/checks/refunded.py": b"def check(w, c):\n    return None\n",
    "scenarios.json": b'[{"name": "refund"}]',
    "coverage.json": b"{}",
    "proofs.json": b"{}",
    "environment-bundle/manifest.json": b'{"files": []}',
    "generic-harness/certified-bundle/manifest.json": b"{}",
    "generic-harness/certification.json": b"{}",
    "generic-harness/repair-patch-1.json": b"{}",
    "generic-harness/runtime-plan-repair-1.json": b"{}",
    "generic-harness/setup-checks-passed.json": b"[]",
    "runtime-validation.json": b'{"status": "passed"}',
    "cost.json": b'{"usd": 1}',
    ".futureagi-conversation.json": b"{}",
    ".agent-cache/sessions/1.key": b"private",
    "handlers/__pycache__/lookup.cpython-311.pyc": b"\x00",
}


def _tar(entries, *, kind=None):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as archive:
        for name, data in entries.items():
            member = tarfile.TarInfo(name)
            if kind is not None:
                member.type = kind
                member.linkname = "/etc/passwd"
                archive.addfile(member)
            else:
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
    return out.getvalue()


def _tar_with_folders(folders, *, files_per_folder):
    """A tar with a directory entry per folder, as ``tar`` writes a scenario tree."""
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as archive:
        root = tarfile.TarInfo("./")
        root.type = tarfile.DIRTYPE
        archive.addfile(root)
        for folder in range(folders):
            directory = tarfile.TarInfo(f"./scenarios/s{folder}")
            directory.type = tarfile.DIRTYPE
            archive.addfile(directory)
            for index in range(files_per_folder):
                member = tarfile.TarInfo(f"./scenarios/s{folder}/f{index}.py")
                member.size = 1
                archive.addfile(member, io.BytesIO(b"x"))
    return out.getvalue()


class TestReadArchive:
    def test_reads_every_regular_file(self):
        assert archives.read_archive(_tar({"./a/b.txt": b"x", "c": b"y"})) == {
            "a/b.txt": b"x",
            "c": b"y",
        }

    @pytest.mark.parametrize("name", ["../evil.py", "a/../../evil.py", "/etc/evil"])
    def test_refuses_a_path_outside_the_archive(self, name):
        with pytest.raises(HostedHarnessError) as error:
            archives.read_archive(_tar({name: b"x"}))

        assert error.value.code == "archive_member_invalid"
        assert error.value.status_code == 422

    @pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE])
    def test_refuses_links(self, kind):
        with pytest.raises(HostedHarnessError) as error:
            archives.read_archive(_tar({"link": b""}, kind=kind))

        assert error.value.code == "archive_member_invalid"

    def test_refuses_too_many_files(self, monkeypatch):
        monkeypatch.setattr(archives, "MAX_ARCHIVE_FILES", 2)

        with pytest.raises(HostedHarnessError) as error:
            archives.read_archive(_tar({"a": b"", "b": b"", "c": b""}))

        assert (error.value.code, error.value.status_code) == ("archive_too_large", 413)

    def test_refuses_too_many_entries(self, monkeypatch):
        monkeypatch.setattr(archives, "MAX_ARCHIVE_ENTRIES", 2)

        with pytest.raises(HostedHarnessError) as error:
            archives.read_archive(_tar_with_folders(3, files_per_folder=0))

        assert error.value.code == "archive_too_large"

    def test_directories_do_not_count_as_files(self, monkeypatch):
        monkeypatch.setattr(archives, "MAX_ARCHIVE_FILES", 4)

        files = archives.read_archive(_tar_with_folders(4, files_per_folder=1))

        assert len(files) == 4

    def test_a_suite_past_ten_thousand_files_is_read(self):
        body = _tar_with_folders(500, files_per_folder=22)

        files = archives.read_archive(body)

        assert len(files) == 11_000

    def test_the_caps_cover_the_largest_suite_a_job_allows(self):
        from simulate.models.hosted_harness import MAX_SCENARIOS_PER_JOB

        # Production holds about 22 files and 50 KB per scenario.
        assert archives.MAX_ARCHIVE_FILES >= MAX_SCENARIOS_PER_JOB * 22 * 1.5
        assert archives.MAX_ARCHIVE_INFLATED_BYTES >= MAX_SCENARIOS_PER_JOB * 50_000 * 2

    def test_refuses_too_many_inflated_bytes(self, monkeypatch):
        monkeypatch.setattr(archives, "MAX_ARCHIVE_INFLATED_BYTES", 10)

        with pytest.raises(HostedHarnessError) as error:
            archives.read_archive(_tar({"a": b"x" * 6, "b": b"x" * 6}))

        assert error.value.status_code == 413

    def test_refuses_bytes_that_are_not_an_archive(self):
        with pytest.raises(HostedHarnessError) as error:
            archives.read_archive(b"not a tarball")

        assert error.value.code == "archive_invalid"


class TestRealTarCounts:
    def test_a_real_tar_counts_only_its_files(self, tmp_path, monkeypatch):
        import subprocess

        for index in range(3):
            folder = tmp_path / "scenarios" / f"s{index}" / "checks"
            folder.mkdir(parents=True)
            (folder / "c.py").write_text("x")
        body = subprocess.run(
            ["tar", "-czf", "-", "."],
            cwd=tmp_path,
            capture_output=True,
            check=True,
            env={**os.environ, "COPYFILE_DISABLE": "1"},
        ).stdout
        monkeypatch.setattr(archives, "MAX_ARCHIVE_FILES", 3)

        files = archives.read_archive(body)

        assert sorted(files) == [
            f"scenarios/s{index}/checks/c.py" for index in range(3)
        ]


class TestRealTarOutput:
    def test_reads_what_tar_writes_for_a_whole_folder(self, tmp_path):
        import subprocess

        (tmp_path / "scenarios" / "refund").mkdir(parents=True)
        (tmp_path / "contract.json").write_bytes(b"{}")
        (tmp_path / "scenarios" / "refund" / "scenario.json").write_bytes(b"{}")
        body = subprocess.run(
            ["tar", "-czf", "-", "."],
            cwd=tmp_path,
            capture_output=True,
            check=True,
            env={**os.environ, "COPYFILE_DISABLE": "1"},
        ).stdout

        assert archives.read_archive(body) == {
            "contract.json": b"{}",
            "scenarios/refund/scenario.json": b"{}",
        }

    def test_refuses_the_same_file_twice(self):
        with pytest.raises(HostedHarnessError) as error:
            archives.read_archive(_tar({"a.json": b"1", "./a.json": b"2"}))

        assert error.value.code == "archive_member_invalid"

    def test_refuses_a_parent_reference_in_a_folder_entry(self):
        out = io.BytesIO()
        with tarfile.open(fileobj=out, mode="w:gz") as archive:
            folder = tarfile.TarInfo("a/../../up")
            folder.type = tarfile.DIRTYPE
            archive.addfile(folder)

        with pytest.raises(HostedHarnessError):
            archives.read_archive(out.getvalue())

    def test_build_logs_and_the_writer_journal_are_dropped(self):
        split = archives.split_authoring(
            {
                "harness-events.jsonl": b"{}",
                "written.jsonl": b"{}",
                "generic-harness/source-data-review-early-error.txt": b"error",
                "provider-import-profile.json": b"{}",
                "contract.json": b"{}",
            }
        )

        assert split.environment == {"contract.json": b"{}"}


class TestPackArchive:
    def test_is_deterministic_whatever_the_insertion_order(self):
        forward = archives.pack_archive({"b": b"2", "a/x": b"1"})
        backward = archives.pack_archive({"a/x": b"1", "b": b"2"})

        assert forward == backward

    def test_round_trips_through_read(self):
        files = {"a/b.json": b"{}", "c.sql": b"select 1;"}

        assert archives.read_archive(archives.pack_archive(files)) == files

    def test_has_no_timestamps_or_owners(self):
        body = archives.pack_archive({"a": b"1"})

        assert body[:2] == b"\x1f\x8b" and body[4:8] == b"\x00\x00\x00\x00"
        with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as archive:
            member = archive.getmember("a")
        assert (member.mtime, member.uid, member.gid, member.mode) == (0, 0, 0, 0o644)


class TestSplitAndCompose:
    def test_split_sorts_each_file_into_its_piece(self):
        split = archives.split_authoring(AUTHORING)

        assert set(split.environment) == {
            "contract.json",
            "sub_goals.json",
            "simulator_prompt.md",
            "schema.sql",
            "world.sqlite",
            "handlers/lookup.py",
            "generic-harness/source-model.json",
            "generic-harness/world-ir.json",
        }
        assert set(split.suite) == {
            "scenarios/refund/scenario.json",
            "scenarios/refund/checks/refunded.py",
            "scenarios.json",
            "coverage.json",
            "proofs.json",
        }
        assert set(split.sealed) == {
            "environment-bundle/manifest.json",
            "generic-harness/certified-bundle/manifest.json",
            "generic-harness/certification.json",
            "generic-harness/repair-patch-1.json",
            "generic-harness/runtime-plan-repair-1.json",
            "generic-harness/setup-checks-passed.json",
            "runtime-validation.json",
        }

    def test_split_never_keeps_spend_caches_or_hidden_session_files(self):
        split = archives.split_authoring(AUTHORING)
        kept = {**split.environment, **split.suite, **split.sealed}

        assert "cost.json" not in kept
        assert not any(path.startswith(".") for path in kept)
        assert not any("__pycache__" in path for path in kept)

    def test_legacy_scenario_folder_belongs_to_the_suite(self):
        split = archives.split_authoring({"scenario/a/scenario.json": b"{}"})

        assert set(split.suite) == {"scenario/a/scenario.json"}

    def test_split_then_compose_rebuilds_the_tree(self):
        split = archives.split_authoring(AUTHORING)
        rebuilt = archives.compose(split.environment, split.suite, split.sealed)

        assert rebuilt == {
            path: data
            for path, data in AUTHORING.items()
            if path != "cost.json"
            and path != "harness-events.jsonl"
            and not path.startswith(".")
            and "__pycache__" not in path
        }

    def test_a_combined_environment_never_leaks_its_scenarios(self):
        combined = dict(AUTHORING)
        suite = {
            "scenarios/other/scenario.json": b'{"name": "other"}',
            "scenarios.json": b'[{"name": "other"}]',
        }

        tree = archives.compose(combined, suite)

        assert "scenarios/refund/scenario.json" not in tree
        assert tree["scenarios.json"] == suite["scenarios.json"]
        assert "environment-bundle/manifest.json" not in tree
        assert tree["contract.json"] == AUTHORING["contract.json"]

    def test_a_combined_suite_contributes_only_its_scenarios(self):
        environment = {"contract.json": b'{"agent": "new"}'}

        tree = archives.compose(environment, AUTHORING)

        assert tree["contract.json"] == b'{"agent": "new"}'
        assert "schema.sql" not in tree
        assert "scenarios/refund/scenario.json" in tree

    def test_sealed_files_come_only_from_the_sealed_side(self):
        tree = archives.compose(
            {"contract.json": b"{}"},
            {"scenarios.json": b"[]"},
            {"environment-bundle/manifest.json": b"{}", "contract.json": b"bad"},
        )

        assert tree == {
            "contract.json": b"{}",
            "scenarios.json": b"[]",
            "environment-bundle/manifest.json": b"{}",
        }

    def test_keys_are_content_addressed_per_environment(self):
        body = archives.pack_archive({"a": b"1"})
        digest = archives.sha256_hex(body)

        assert archives.environment_key("org", "job", body) == (
            f"harness-environments/org/job/{digest}.tar.gz"
        )
        assert archives.suite_key("org", "job", body).startswith(
            "harness-suites/org/job/"
        )
        assert archives.pair_key("org", "job", body).startswith("harness-authoring/")
        assert archives.sealed_key("org", "job", body).startswith("harness-sealed/")


class TestContractDiff:
    def test_reports_tools_added_removed_and_changed(self):
        before = {
            "tools": [
                {"name": "lookup", "args": ["id"]},
                {"name": "refund", "args": ["id"]},
            ]
        }
        after = {
            "tools": [
                {"name": "lookup", "args": ["id", "email"]},
                {"name": "cancel", "args": ["id"]},
            ]
        }

        tools = diffs.contract_diff(before, after)["tools"]

        assert tools == {
            "added": ["cancel"],
            "removed": ["refund"],
            "changed": ["lookup"],
        }

    def test_reports_rules_use_cases_and_modality(self):
        before = {
            "hard_constraints": ["verify identity"],
            "use_cases": ["refunds"],
            "modality": "voice",
        }
        after = {
            "hard_constraints": ["verify identity", "never refund twice"],
            "real_use_cases": ["refunds", "cancellations"],
            "modality": "chat",
        }

        diff = diffs.contract_diff(before, after)

        assert diff["rules"] == {"added": ["never refund twice"], "removed": []}
        assert diff["use_cases"] == {"added": ["cancellations"], "removed": []}
        assert diff["modality"] == {"from": "voice", "to": "chat"}

    def test_ignores_chosen_evals_and_amendments(self):
        diff = diffs.contract_diff(
            {"chosen_evals": ["a"], "amendments": ["x"]},
            {"chosen_evals": ["b"], "amendments": ["y"]},
        )

        assert diff["fields"] == []

    def test_a_first_version_has_everything_added(self):
        diff = diffs.contract_diff(None, {"tools": [{"name": "lookup"}]})

        assert diff["tools"]["added"] == ["lookup"]


class TestSuiteAndAgentDiffs:
    def test_suite_diff_names_keys_and_changed_fields(self):
        before = [
            {"scenario_key": "a", "instruction": "x", "persona": {"age": 30}},
            {"scenario_key": "b", "instruction": "y"},
        ]
        after = [
            {"scenario_key": "a", "instruction": "x", "persona": {"age": 70}},
            {"name": "c", "instruction": "z"},
        ]

        diff = diffs.suite_diff(before, after)

        assert diff["added"] == ["c"]
        assert diff["removed"] == [{"key": "b", "reason": "removed by manual edit"}]
        assert diff["changed"] == {"a": ["persona"]}

    def test_suite_diff_ignores_identity_fields(self):
        diff = diffs.suite_diff(
            [{"scenario_key": "a", "scenario_id": "1"}],
            [{"scenario_key": "a", "scenario_id": "2"}],
        )

        assert diff["changed"] == {}

    def test_agent_diff_names_variables_never_values(self):
        diff = diffs.agent_diff(
            {
                "source": "git:a",
                "prompt_hash": "p",
                "credential_fingerprints": {"K": "1"},
            },
            {
                "source": "git:b",
                "prompt_hash": "p",
                "credential_fingerprints": {"K": "2", "N": "3"},
            },
        )

        assert diff["source"] is True and diff["prompt"] is False
        assert diff["variables"] == {"added": ["N"], "removed": [], "changed": ["K"]}

    def test_summaries_are_short_and_say_when_nothing_changed(self):
        environment = diffs.environment_diff(
            previous_contract={"tools": []},
            contract={"tools": [{"name": "refund"}]},
            previous_sub_goals={"a": "1"},
            sub_goals={"a": "2"},
            previous_files={"schema.sql": "1"},
            files={"schema.sql": "1"},
        )

        assert diffs.summarize_environment(None) == "First version"
        assert diffs.summarize_environment(environment) == (
            "1 tool added, 1 sub-goal changed"
        )
        assert diffs.summarize_suite({"added": [], "removed": [], "changed": {}}) == (
            "No scenario change"
        )
