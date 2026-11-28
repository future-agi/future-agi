"""Run on the disposable VM: python -m unittest discover -s scripts/tests -p j1_inventory_test.py."""

import importlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "futureagi"))
manifest_digest = importlib.import_module(
    "model_hub.services.skills_hub.packages"
).manifest_digest
inventory = importlib.import_module(
    "model_hub.services.skills_hub.r3.local_inventory"
).inventory


class LocalInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.library = self.root / "private"
        self.library.mkdir()
        self.agents = self.root / "AGENTS.md"
        self.agents.write_bytes(b"# Trial\nUse the selected policies.\n")

    def package(self, name, count=1):
        folder = self.library / name
        folder.mkdir()
        (folder / "SKILL.md").write_bytes(
            f"---\nname: {name}\ndescription: Use when reviewing a private policy\n---\n\nExact café text.\r\n".encode()
        )
        for index in range(count - 1):
            (folder / f"ref-{index}.md").write_text("Reference\n")
        return folder

    def run_inventory(self, **kwargs):
        return inventory(
            folders=[self.library],
            exchanges=[],
            agents=self.agents,
            name="trial",
            description="Private trial",
            **kwargs,
        )

    def test_whole_library_and_explicit_subset(self):
        for i in range(15):
            self.package(f"policy-{i}", 2)
        (self.library / "policy-14" / "binary.bin").write_bytes(b"\x00\xff")
        report = self.run_inventory()
        self.assertEqual(report["source_member_count"], 15)
        self.assertFalse(report["whole_collection_supported"])
        self.assertEqual(report["compiled_closure"]["error"], "unsupported_members")
        selected = [m["key"] for m in report["members"] if not m["blockers"]]
        self.assertEqual(len(selected), 14)
        subset = self.run_inventory(selected=selected)
        self.assertTrue(subset["compiled_closure"]["fits"])
        self.assertEqual(subset["compiled_closure"]["file_count"], 30)
        self.assertGreater(subset["compiled_closure"]["descriptor_bytes"], 0)
        self.assertEqual(subset["excluded"], ["folder-0/policy-14"])
        self.assertFalse(subset["whole_collection_supported"])
        self.assertIn(".futureagi/context.json", subset["compiled_closure"]["paths"])
        self.assertNotIn("Exact café", json.dumps(subset))

    def test_unselected_run_proposes_reviewed_supported_subset(self):
        for i in range(15):
            self.package(f"policy-{i}", 2)
        (self.library / "policy-14" / "binary.bin").write_bytes(b"\x00\xff")
        report = self.run_inventory()
        # The whole-collection verdict is unchanged; the subset is only proposed.
        self.assertFalse(report["compiled_closure"]["fits"])
        self.assertEqual(report["excluded"], [])
        proposed = report["proposed_supported_subset"]
        self.assertEqual(proposed["excluded"], ["folder-0/policy-14"])
        self.assertEqual(len(proposed["selected"]), 14)
        explicit = self.run_inventory(selected=proposed["selected"])
        self.assertEqual(proposed["compiled_closure"], explicit["compiled_closure"])
        self.assertNotIn("proposed_supported_subset", explicit)
        clean = Path(self.temp.name) / "clean"
        clean.mkdir()
        self.library = clean
        self.package("only", 1)
        self.assertNotIn("proposed_supported_subset", self.run_inventory())

    def test_packet_carries_exact_inventoried_bytes_and_identities(self):
        from model_hub.services.skills_hub.packages import parse_upload

        self.package("policy", 2)
        (self.library / "policy" / "binary.bin").write_bytes(b"\x00\xff")
        self.package("clean", 1)
        (self.library / "notes.txt").write_text("unowned\n")
        exchange = self.root / "exchange.json"
        files = [
            {
                "path": "SKILL.md",
                "content": "---\nname: refunds\ndescription: Use when refunding\n---\n\nBody\n",
            }
        ]
        exchange.write_text(
            json.dumps(
                {
                    "format": "langfuse-skills-v1",
                    "origin": "https://lf.example",
                    "project": "p",
                    "skills": [
                        {
                            "resource": "r1",
                            "revision": "3",
                            "digest": manifest_digest(files),
                            "files": files,
                        }
                    ],
                }
            )
        )
        capture = {}
        report = inventory(
            folders=[self.library],
            exchanges=[exchange],
            agents=self.agents,
            name="trial",
            description="Private trial",
            local_source={
                "origin": "local:synthetic",
                "project": "library",
                "revision": "v1",
            },
            capture=capture,
        )
        self.assertEqual(
            capture["context"],
            {
                "name": "trial",
                "description": "Private trial",
                "agents_md": self.agents.read_text(),
            },
        )
        (folder,) = [s for s in capture["sources"] if s["kind"] == "folder"]
        (langfuse,) = [s for s in capture["sources"] if s["kind"] == "langfuse"]
        self.assertEqual(langfuse["export"]["skills"][0]["files"], files)
        # Every regular file, including the binary and unowned ones, as uploaded.
        uploads = [(f"private/{item}", raw) for item, raw in folder["entries"]]
        on_disk = sorted(
            str(p.relative_to(self.root))
            for p in self.library.rglob("*")
            if p.is_file()
        )
        self.assertEqual(sorted(path for path, _ in uploads), on_disk)
        self.assertIn(("private/policy/binary.bin", b"\x00\xff"), uploads)
        parsed = parse_upload(uploads)
        clean = next(b for b in parsed["bundles"] if b["root"].endswith("clean"))
        self.assertEqual(
            folder["source_identities"][clean["key"]]["digest"],
            manifest_digest(clean["files"]),
        )
        # Blocked members never receive an importable identity.
        self.assertEqual(
            folder["source_identities"],
            report["folder_upload_source_identities"],
        )
        self.assertEqual(len(folder["source_identities"]), 1)

    def test_compiled_aggregate_not_individual_packages(self):
        for i in range(15):
            self.package(f"policy-{i}", 14)
        report = self.run_inventory()
        self.assertTrue(all(not m["blockers"] for m in report["members"]))
        self.assertFalse(report["compiled_closure"]["fits"])
        self.assertEqual(
            report["compiled_closure"]["error"], "context_closure_too_large"
        )
        self.assertEqual(report["compiled_closure"]["file_count"], 212)

    def test_descriptor_counts_at_file_boundary(self):
        self.package("policy", 199)
        report = self.run_inventory()
        # 199 source files + AGENTS.md + descriptor = 201, not a valid 200-file job.
        self.assertFalse(report["compiled_closure"]["fits"])
        self.assertEqual(report["compiled_closure"]["file_count"], 201)

    def test_bytes_include_descriptor_and_unicode(self):
        for i in range(5):
            folder = self.package(f"policy-{i}")
            (folder / "reference.md").write_bytes(("é" * (210 * 1024)).encode())
        report = self.run_inventory()
        self.assertTrue(all(not m["blockers"] for m in report["members"]))
        self.assertFalse(report["compiled_closure"]["fits"])
        self.assertGreater(report["compiled_closure"]["total_bytes"], 2 * 1024 * 1024)

    def test_prefix_path_limits_apply_after_mount(self):
        folder = self.package("policy")
        nested = folder / "a/b/c/d/e/f"
        nested.mkdir(parents=True)
        (nested / "g.md").write_text("Reference\n")
        report = self.run_inventory()
        self.assertFalse(report["compiled_closure"]["fits"])
        self.assertTrue(
            any(
                d["code"] == "path_invalid"
                for d in report["compiled_closure"]["diagnostics"]
            )
        )

    def test_symlink_and_unowned_files_are_not_hidden(self):
        folder = self.package("policy")
        secret = self.root / "outside"
        secret.write_text("do not read this secret")
        (folder / "reference.md").symlink_to(secret)
        (self.library / "unassigned.md").write_text("Unowned\n")
        report = self.run_inventory()
        self.assertFalse(report["whole_collection_supported"])
        self.assertTrue(report["members"][0]["blockers"])
        self.assertEqual(len(report["unowned_or_rejected"]), 2)
        self.assertNotIn("do not read", json.dumps(report))

    def test_symlink_ancestor_refused(self):
        self.package("policy")
        alias = self.root / "alias"
        alias.symlink_to(self.library, target_is_directory=True)
        with self.assertRaises(OSError):
            inventory(
                folders=[alias],
                exchanges=[],
                agents=self.agents,
                name="trial",
                description="Trial",
            )

    def test_nested_root_and_host_control_are_blocked(self):
        folder = self.package("policy")
        (folder / "CLAUDE.md").write_text("Host control\n")
        (folder / "nested").mkdir()
        (folder / "nested/SKILL.md").write_text(
            "---\nname: nested\ndescription: Nested policy\n---\nNested\n"
        )
        report = self.run_inventory()
        self.assertEqual(report["source_member_count"], 2)
        self.assertFalse(report["compiled_closure"]["fits"])
        parent = next(m for m in report["members"] if m["name"] == "policy")
        self.assertTrue(parent["blockers"])
        self.assertTrue(any(f["host_control"] for f in parent["inventory"]))

    def test_root_limit_precedes_member_decoding(self):
        for index in range(51):
            self.package(f"policy-{index}")
        with (
            mock.patch(
                "model_hub.services.skills_hub.r3.local_inventory._member",
                side_effect=AssertionError("Over-limit input must not be decoded"),
            ),
            self.assertRaisesRegex(ValueError, "50 roots"),
        ):
            self.run_inventory()

    def test_nested_expansion_is_bounded_before_redecoding(self):
        folder = self.package("policy")
        nested = folder / "nested"
        nested.mkdir()
        (nested / "SKILL.md").write_text(
            "---\nname: nested\ndescription: Use for nested policy\n---\nPolicy\n"
        )
        total = sum(path.stat().st_size for path in folder.rglob("*") if path.is_file())
        with (
            mock.patch(
                "model_hub.services.skills_hub.r3.local_inventory.MAX_INPUT_BYTES",
                total + 1,
            ),
            self.assertRaisesRegex(ValueError, "Nested collection expansion"),
        ):
            self.run_inventory()

    def test_link_limit(self):
        for i in range(21):
            self.package(f"policy-{i}")
        self.assertEqual(
            self.run_inventory()["compiled_closure"]["error"],
            "invalid_dependency_selector",
        )

    def test_exchange_and_folder_are_both_accounted(self):
        self.package("local")
        files = [
            {
                "path": "SKILL.md",
                "content": "---\nname: exported\ndescription: Exported policy\n---\nPolicy\n",
            }
        ]
        export = {
            "format": "langfuse-skills-v1",
            "origin": "fixture.invalid",
            "project": "synthetic",
            "skills": [
                {
                    "resource": "source-one",
                    "revision": "v1",
                    "digest": manifest_digest(files),
                    "files": files,
                }
            ],
        }
        path = self.root / "export.json"
        path.write_text(json.dumps(export))
        report = inventory(
            folders=[self.library],
            exchanges=[path],
            agents=self.agents,
            name="trial",
            description="Trial",
        )
        self.assertEqual(report["source_member_count"], 2)
        self.assertTrue(report["compiled_closure"]["fits"])
        self.assertEqual(report["members"][1]["source"]["revision"], "v1")
        export["skills"][0]["files"][0]["content"] += "changed"
        path.write_text(json.dumps(export))
        with self.assertRaisesRegex(ValueError, "digest"):
            inventory(
                folders=[],
                exchanges=[path],
                agents=self.agents,
                name="trial",
                description="Trial",
            )

    def test_folder_source_keys_match_public_upload_parser(self):
        from model_hub.services.skills_hub.packages import parse_upload

        folder = self.package("policy", 2)
        report = self.run_inventory(
            local_source={
                "origin": "local:synthetic",
                "project": "library",
                "revision": "v1",
            }
        )
        uploads = [
            ("private/policy/" + p.name, p.read_bytes())
            for p in sorted(folder.iterdir())
        ]
        parsed = parse_upload(uploads)
        bundle = parsed["bundles"][0]
        source = report["folder_upload_source_identities"][bundle["key"]]
        self.assertEqual(source["digest"], manifest_digest(bundle["files"]))
        self.assertEqual(source["resource"], "private/policy")
        # Frontmatter rename does not re-key immutable source resources.
        skill = folder / "SKILL.md"
        skill.write_bytes(skill.read_bytes().replace(b"name: policy", b"name: renamed"))
        updated = self.run_inventory(
            local_source={
                "origin": "local:synthetic",
                "project": "library",
                "revision": "v2",
            }
        )
        self.assertEqual(
            set(updated["folder_upload_source_identities"]), {bundle["key"]}
        )
        self.assertNotEqual(
            source["digest"],
            updated["folder_upload_source_identities"][bundle["key"]]["digest"],
        )

    def test_duplicate_mount_names_refused(self):
        first = self.package("first")
        second = self.package("second")
        (second / "SKILL.md").write_bytes((first / "SKILL.md").read_bytes())
        self.assertFalse(self.run_inventory()["compiled_closure"]["fits"])

    def write_exchange(
        self,
        filename,
        name,
        *,
        resource="retained-policy",
        revision="v1",
        origin="fixture.invalid",
        project="synthetic",
    ):
        files = [
            {
                "path": "SKILL.md",
                "content": f"---\nname: {name}\ndescription: Use for retained policy\n---\nPolicy\n",
            }
        ]
        path = self.root / filename
        path.write_text(
            json.dumps(
                {
                    "format": "langfuse-skills-v1",
                    "origin": origin,
                    "project": project,
                    "skills": [
                        {
                            "resource": resource,
                            "revision": revision,
                            "digest": manifest_digest(files),
                            "files": files,
                        }
                    ],
                }
            )
        )
        return path

    def test_repeated_identity_across_exchanges_refused_before_subset(self):
        first = self.write_exchange("first.json", "first")
        # Different revision/name does not create a different migration identity.
        second = self.write_exchange("second.json", "second", revision="v2")
        for selected in (None, ["exchange-0/0"]):
            with (
                self.subTest(selected=selected),
                self.assertRaisesRegex(
                    ValueError, "Duplicate retained source identity"
                ),
            ):
                inventory(
                    folders=[],
                    exchanges=[first, second],
                    agents=self.agents,
                    name="trial",
                    description="Trial",
                    selected=selected,
                )

    def test_exchange_and_folder_cannot_claim_one_source_identity(self):
        self.package("local")
        exchange = self.write_exchange(
            "source.json", "remote", resource="private/local"
        )
        with self.assertRaisesRegex(ValueError, "Duplicate retained source identity"):
            inventory(
                folders=[self.library],
                exchanges=[exchange],
                agents=self.agents,
                name="trial",
                description="Trial",
                local_source={
                    "origin": "fixture.invalid",
                    "project": "synthetic",
                    "revision": "v1",
                },
            )

    def test_same_resource_in_distinct_projects_and_origins_is_not_duplicate(self):
        first = self.write_exchange("first.json", "first")
        second = self.write_exchange("second.json", "second", project="another-project")
        third = self.write_exchange("third.json", "third", origin="another.invalid")
        report = inventory(
            folders=[],
            exchanges=[first, second, third],
            agents=self.agents,
            name="trial",
            description="Trial",
        )
        self.assertEqual(report["source_member_count"], 3)
        self.assertTrue(report["compiled_closure"]["fits"])

    def test_cli_isolated_no_credentials_no_source_mutations(self):
        self.package("policy")
        before = (self.library / "policy/SKILL.md").read_bytes()
        # An input directory's module must never be imported by the CLI.
        (self.library / "json.py").write_text(
            "raise RuntimeError('untrusted module executed')"
        )
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                str(ROOT / "scripts/skills_inventory.py"),
                "--folder",
                str(self.library),
                "--agents",
                str(self.agents),
                "--name",
                "trial",
                "--description",
                "Trial",
            ],
            cwd=self.library,
            env={"PATH": os.environ["PATH"]},
            capture_output=True,
            text=True,
            check=True,
        )
        report = json.loads(result.stdout)
        self.assertEqual(report["network_requests"], 0)
        self.assertTrue(report["complete_inventory"])
        self.assertEqual(before, (self.library / "policy/SKILL.md").read_bytes())
        self.assertNotIn("DJANGO_SETTINGS_MODULE", os.environ)

    def test_cli_packet_is_private_exclusive_and_bound_to_report(self):
        import base64
        import hashlib

        self.package("policy", 2)
        packet = self.root / "packet.json"
        command = [
            sys.executable,
            "-I",
            str(ROOT / "scripts/skills_inventory.py"),
            "--folder",
            str(self.library),
            "--agents",
            str(self.agents),
            "--name",
            "trial",
            "--description",
            "Trial",
            "--source-origin",
            "local:synthetic",
            "--source-project",
            "library",
            "--source-revision",
            "v1",
            "--packet",
            str(packet),
        ]
        result = subprocess.run(
            command,
            cwd=self.root,
            env={"PATH": os.environ["PATH"]},
            capture_output=True,
            text=True,
            check=True,
        )
        report = json.loads(result.stdout)
        summary = report.pop("migration_packet")
        raw = packet.read_bytes()
        self.assertEqual(summary["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(packet.stat().st_mode & 0o777, 0o600)
        body = json.loads(raw)
        self.assertEqual(body["kind"], "futureagi-migration-packet")
        self.assertEqual(
            body["inventory_sha256"],
            hashlib.sha256(
                json.dumps(report, ensure_ascii=True, sort_keys=True).encode()
            ).hexdigest(),
        )
        (folder,) = body["sources"]
        self.assertEqual(
            sorted(f["path"] for f in folder["files"]),
            ["private/policy/SKILL.md", "private/policy/ref-0.md"],
        )
        skill = next(
            f for f in folder["files"] if f["path"] == "private/policy/SKILL.md"
        )
        self.assertEqual(
            base64.b64decode(skill["content_base64"]),
            (self.library / "policy/SKILL.md").read_bytes(),
        )
        # Never overwrite an existing packet; no partial report either.
        again = subprocess.run(
            command,
            cwd=self.root,
            env={"PATH": os.environ["PATH"]},
            capture_output=True,
            text=True,
        )
        self.assertEqual(again.returncode, 1)
        self.assertFalse(json.loads(again.stdout)["complete_inventory"])
        self.assertEqual(packet.read_bytes(), raw)


if __name__ == "__main__":
    unittest.main()
