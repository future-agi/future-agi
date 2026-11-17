"""Daemon-free developer-gate contract and bad/safe fixtures (stdlib only)."""
import ast
import copy
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("update_safety_gate", ROOT / "scripts/check_update_safety.py")
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def registry():
    return {"schema_version": 1, "tables": [
        {"database": db, "table": table, "reason": "Traffic-proportional fact history", "source": "fixture"}
        for db, table in (("postgres", "hot_events"), ("clickhouse", "spans"))]}


def reviewed(risks):
    return {"schema_version": 1, "risk_classes": sorted({r["risk_class"] for r in risks}),
            "tables": sorted({r["table"] for r in risks}),
            "mitigation": "Backfill in batches of 500 with a bounded transaction and a resumable cursor.",
            "review": "https://github.com/future-agi/future-agi/pull/123#discussion_r456",
            "reviewer": "fixture-reviewer", "large_volume_rehearsal": True}


class LayoutTests(unittest.TestCase):
    def setUp(self):
        self.contract = gate.document(ROOT / "deploy/update/automatic-layout.json")
        self.model = {"services": {}, "volumes": copy.deepcopy(self.contract["volumes"])}
        for name, row in self.contract["services"].items():
            self.model["services"][name] = {"profiles": row["profiles"], "restart": row["restart"], "volumes": copy.deepcopy(row["mounts"])}
            if row.get("tmpfs"):
                # Compose renders each tmpfs entry as "target[:options]".
                self.model["services"][name]["tmpfs"] = [target + ":rw,nosuid,nodev" for target in row["tmpfs"]]

    def fails(self):
        with self.assertRaisesRegex(gate.GateError, "update .*automatic-layout|update docker-compose"):
            gate.check_layout(self.model, self.contract)

    def test_current_layout_passes(self):
        gate.check_layout(self.model, self.contract)

    def test_new_service_fails(self):
        self.model["services"]["writer"] = {"restart": "unless-stopped"}
        self.fails()

    def test_reviewed_new_service_still_needs_storage_coverage(self):
        self.model["services"]["writer"] = {"restart": "no"}
        self.contract["services"]["writer"] = {"profiles": [], "restart": "no", "mounts": []}
        self.fails()
        self.contract["service_storage"]["writer"] = {"volumes": [], "ephemeral": True, "reason": "Stateless fixture service"}
        gate.check_layout(self.model, self.contract)

    def test_new_named_volume_fails(self):
        self.model["volumes"]["unbacked"] = {}
        self.fails()

    def test_new_bind_fails(self):
        self.model["services"]["app"]["volumes"].append({"type": "bind", "source": "./new", "target": "/unbacked"})
        self.fails()

    def test_bind_propagation_change_fails(self):
        mount = next(m for m in self.model["services"]["app"]["volumes"] if m["type"] == "bind")
        mount["bind"] = {"propagation": "shared"}
        self.fails()

    def test_compose_default_bind_variants_pass(self):
        for options in ({}, {"create_host_path": True}):
            for service in self.model["services"].values():
                for mount in service["volumes"]:
                    if mount["type"] == "bind":
                        mount["bind"] = options
            gate.check_layout(self.model, self.contract)

    def test_new_profile_fails(self):
        self.model["services"]["app"]["profiles"] = ["new"]
        self.fails()

    def test_unsafe_restarts_fail(self):
        for restart in ("always", "on-failure", "on-failure:3"):
            with self.subTest(restart=restart):
                self.model["services"]["app"]["restart"] = restart
                self.fails()

    def test_no_restart_is_supported_when_reviewed(self):
        self.model["services"]["app"]["restart"] = "no"
        self.contract["services"]["app"]["restart"] = "no"
        gate.check_layout(self.model, self.contract)

    def test_new_volume_requires_coverage_even_after_contract_update(self):
        self.model["volumes"]["new"] = {}
        self.contract["volumes"]["new"] = {}
        self.fails()

    def test_missing_logical_domain_fails(self):
        self.contract["backup_coverage"]["app-data"]["domains"] = ["object_store"]
        self.fails()

    def test_missing_bind_coverage_fails(self):
        self.contract["bind_coverage"] = {}
        self.fails()

    def test_indirect_mount_and_swarm_restart_fail(self):
        for field, value in (("volumes_from", ["other"]), ("tmpfs", ["/data"]),
                             ("deploy", {"restart_policy": {"condition": "any"}})):
            with self.subTest(field=field):
                self.model["services"]["app"][field] = value
                self.fails()
                del self.model["services"]["app"][field]

    def test_external_volume_fails(self):
        self.model["volumes"]["app-data"] = {"external": True}
        self.fails()

    def test_code_executor_runs_by_default_with_reviewed_tmpfs_scratch(self):
        self.assertEqual(self.contract["services"]["code-executor"],
                         {"profiles": [], "restart": "unless-stopped", "mounts": [], "tmpfs": ["/sandbox/scripts", "/tmp"]})
        self.assertEqual(self.contract["service_storage"]["code-executor"]["ephemeral"], True)
        gate.check_layout(self.model, self.contract)

    def test_tmpfs_options_do_not_change_the_reviewed_target(self):
        for entries in (["/tmp", "/sandbox/scripts"], ["/tmp:size=64m", "/sandbox/scripts:rw,noexec,uid=65534"]):
            with self.subTest(entries=entries):
                self.model["services"]["code-executor"]["tmpfs"] = entries
                gate.check_layout(self.model, self.contract)

    def test_every_tmpfs_target_must_be_named(self):
        service = self.model["services"]["code-executor"]
        for entries in (["/tmp"], ["/tmp", "/sandbox/scripts", "/scratch"], ["/tmp", "/sandbox/other"],
                        ["/tmp", "/tmp:size=1m", "/sandbox/scripts"]):
            with self.subTest(entries=entries):
                service["tmpfs"] = entries
                self.fails()
        del service["tmpfs"]
        self.fails()
        self.model["services"]["app"]["tmpfs"] = ["/scratch"]
        self.fails()

    def test_tmpfs_target_cannot_shadow_a_mount(self):
        self.model["services"]["app"]["tmpfs"] = ["/data"]
        self.contract["services"]["app"]["tmpfs"] = ["/data"]
        self.fails()

    def test_hub_services_and_volumes_are_named_as_not_covered(self):
        unsupported = self.contract["automatic_unsupported"]
        self.assertTrue({"hub-checker", "hub-runner"} <= set(unsupported["services"]))
        hub_volumes = {"hub-runner-ca", "hub-runner-client", "hub-runner-data", "hub-runner-server"}
        self.assertEqual(set(unsupported["volumes"]), hub_volumes)
        self.assertFalse(hub_volumes & set(self.contract["backup_coverage"]))
        for name in ("hub-checker", "hub-runner"):
            self.assertEqual(self.contract["services"][name]["profiles"], ["hub"])
        gate.check_layout(self.model, self.contract)

    def test_not_covered_entries_need_a_reason_and_a_known_name(self):
        for kind, name in (("services", "hub-runner"), ("volumes", "hub-runner-data")):
            with self.subTest(kind=kind):
                original = self.contract["automatic_unsupported"][kind][name]
                self.contract["automatic_unsupported"][kind][name] = "  "
                self.fails()
                self.contract["automatic_unsupported"][kind][name] = original
        self.contract["automatic_unsupported"]["services"]["writer"] = "Not defined by Compose"
        self.fails()
        del self.contract["automatic_unsupported"]["services"]["writer"]
        del self.contract["automatic_unsupported"]
        self.fails()

    def test_a_not_covered_volume_takes_no_backup_and_no_covered_service_mounts_it(self):
        self.contract["backup_coverage"]["hub-runner-data"] = copy.deepcopy(self.contract["backup_coverage"]["app-data"])
        self.fails()
        del self.contract["backup_coverage"]["hub-runner-data"]
        mount = {"type": "volume", "source": "hub-runner-data", "target": "/runner", "read_only": False}
        self.model["services"]["app"]["volumes"].append(copy.deepcopy(mount))
        self.contract["services"]["app"]["mounts"].append(copy.deepcopy(mount))
        self.contract["services"]["app"]["mounts"].sort(key=lambda m: m["target"])
        self.contract["service_storage"]["app"]["volumes"].append("hub-runner-data")
        self.fails()

    def test_an_unnamed_volume_still_needs_coverage(self):
        del self.contract["automatic_unsupported"]["volumes"]["hub-runner-data"]
        self.fails()


class EnvironmentTests(unittest.TestCase):
    def reference(self, default='"optional empty"', generated=False):
        return ('schema = 1\n[[part]]\n' + ('role = "generated"\n' if generated else '') +
                '[[part.section]]\n[[part.section.row]]\nkeys = ["TOKEN"]\nsetups = ["S"]\ndefault = ' + default + '\n')

    def test_required_variable_fails(self):
        for compose in ("value: ${TOKEN}", "value: ${TOKEN:?required}", "value: $TOKEN", "value: ${TOKEN:+present}"):
            with self.subTest(compose=compose), self.assertRaisesRegex(gate.GateError, "TOKEN.*env-generators"):
                gate.check_env(compose, self.reference(), set())

    def test_explicit_default_passes(self):
        for compose in ("value: ${TOKEN:-}", "value: ${TOKEN-default}"):
            gate.check_env(compose, self.reference(), set())

    def test_explicit_registry_not_generated_section_supplies_generator(self):
        gate.check_env("value: ${TOKEN}", self.reference(), {"TOKEN"})
        with self.assertRaises(gate.GateError):
            gate.check_env("value: ${TOKEN}", self.reference(generated=True), set())
        with self.assertRaises(gate.GateError):
            gate.check_env("value: ${TOKEN}", self.reference(), "fill_secret TOKEN gen_secret")

    def test_documented_required_with_compose_default_is_supported(self):
        gate.check_env("value: ${TOKEN:-}", self.reference(default='"required"'), set())
        with self.assertRaisesRegex(gate.GateError, "required Standalone TOKEN"):
            gate.check_env("services: {}", self.reference(default='"required"'), set())

    def test_setup_specific_required_default_fails(self):
        for default in ('{ S = "required", D = "optional empty" }', '{ "S D" = "no default", H = "optional empty" }',
                        "{ 'S' = 'required', D = 'optional empty' }"):
            with self.subTest(default=default), self.assertRaisesRegex(gate.GateError, "required Standalone TOKEN"):
                gate.check_env("services: {}", self.reference(default=default), set())

    def test_commented_toml_headers_do_not_hide_required_rows(self):
        for header in ('[[part.section.row]] # New setting', '[[ part . section . row ]] # supported TOML',
                       '[[part.section.row]] # comment with "quotes"'):
            reference = self.reference(default='{ S = "required", D = "optional empty" }').replace('[[part.section.row]]', header)
            with self.subTest(header=header), self.assertRaisesRegex(gate.GateError, 'required Standalone TOKEN'):
                gate.check_env('services: {}', reference, set())

    def test_toml_comments_preserve_generators_and_literal_hashes(self):
        reference = self.reference(default='{ S = "required" }', generated=True)
        reference = reference.replace('[[part]]', '[[part]] # credentials').replace('keys = ["TOKEN"]', 'keys = ["TOKEN"] # comment')
        gate.check_env('value: ${TOKEN}', reference, {"TOKEN"})
        gate.check_env('services: {}', self.reference(default='"optional # not required"'), set())

    def test_unsupported_toml_headers_refuse_instead_of_dropping_rows(self):
        for header in ('[["part".section.row]]', '[[part.section.row]] garbage'):
            with self.subTest(header=header), self.assertRaisesRegex(gate.GateError, 'unsupported table header'):
                gate.check_env('services: {}', self.reference().replace('[[part.section.row]]', header), set())

    def test_setup_specific_optional_default_and_generator_pass(self):
        gate.check_env("services: {}", self.reference(default='{ S = "optional empty", D = "required" }'), set())
        gate.check_env("value: ${TOKEN}", self.reference(default='{ "S D" = "required" }'), {"TOKEN"})

    def test_setup_specific_defaults_are_validated_not_guessed(self):
        for default in ('{ D = "optional empty" }', '{ S = "", "S D" = "required" }', '{ S = "", S = "required" }',
                        '{ S = false }', '{ S = "required" garbage }'):
            with self.subTest(default=default), self.assertRaises(gate.GateError):
                gate.check_env("services: {}", self.reference(default=default), set())

    def test_nested_default_checks_inner_variable(self):
        with self.assertRaisesRegex(gate.GateError, "UNKNOWN"):
            gate.check_env("value: ${TOKEN:-${UNKNOWN}}", self.reference(), set())

    def test_escaped_dollars_and_comments_pass(self):
        gate.check_env("# value: ${UNKNOWN}\nvalue: $$UNKNOWN", self.reference(), set())

    def test_current_reference_and_compose_pass(self):
        generators = gate.check_generators(ROOT, gate.document(ROOT / 'deploy/update/env-generators.json'))
        gate.check_env((ROOT / "docker-compose.yml").read_text(), (ROOT / "deploy/env-reference.toml").read_text(), generators)


class GeneratorRegistryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='generator-definition-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / 'generator.sh'
        self.registry = {"schema_version": 1, "generators": [{"variables": ["TOKEN"], "path": "generator.sh",
            "language": "shell", "symbol": "gen_token", "entrypoint": "write_secrets", "availability": "every_install",
            "reason": "Generates an absent token without changing retained credentials."}]}
        self.valid = 'gen_token() {\n printf token\n}\nwrite_secrets() {\n :\n}\n'
        self.path.write_text(self.valid)

    def test_exact_definitions_exist(self):
        self.assertEqual(gate.check_generators(self.root, self.registry), {"TOKEN"})

    def test_source_log_text_and_calls_are_not_definitions(self):
        variants = ['echo "gen_token() {"\n', "printf '%s' 'gen_token() {'\n", '# gen_token() {\n',
                    'gen_token\n', 'printf "message\ngen_token() {\n}\n"\n',
                    "cat <<'CODE'\ngen_token() {\n :\n}\nCODE\n"]
        for text in variants:
            self.path.write_text(text + 'write_secrets() { :; }\n')
            with self.subTest(text=text), self.assertRaises(gate.GateError):
                gate.check_generators(self.root, self.registry)

    def test_deleted_generator_or_entrypoint_fails(self):
        for name in ('gen_token', 'write_secrets'):
            self.path.write_text(self.valid.replace(name + '()', 'wrong()'))
            with self.subTest(name=name), self.assertRaisesRegex(gate.GateError, name):
                gate.check_generators(self.root, self.registry)

    def test_closed_registry_and_required_fields(self):
        for key in self.registry['generators'][0]:
            value = copy.deepcopy(self.registry)
            del value['generators'][0][key]
            with self.subTest(missing=key), self.assertRaises(gate.GateError):
                gate.check_generators(self.root, value)
        for value in ({**self.registry, 'unknown': True}, {**self.registry, 'schema_version': True}):
            with self.subTest(value=value), self.assertRaises(gate.GateError):
                gate.check_generators(self.root, value)

    def test_symlinked_generator_parent_cannot_supply_a_definition(self):
        directory = self.root / 'actual'
        directory.mkdir()
        (directory / 'generator.sh').write_text(self.valid)
        (self.root / 'linked').symlink_to(directory, target_is_directory=True)
        self.registry['generators'][0]['path'] = 'linked/generator.sh'
        with self.assertRaisesRegex(gate.GateError, 'missing/indirect'):
            gate.check_generators(self.root, self.registry)

    def test_python_log_string_is_not_definition(self):
        self.path = self.root / 'generator.py'
        row = self.registry['generators'][0]
        row.update(path='generator.py', language='python')
        self.path.write_text('def gen_token():\n return "token"\ndef write_secrets():\n pass\n')
        self.assertEqual(gate.check_generators(self.root, self.registry), {'TOKEN'})
        self.path.write_text('message="def gen_token():"\ndef write_secrets():\n pass\n')
        with self.assertRaises(gate.GateError):
            gate.check_generators(self.root, self.registry)

    def test_duplicate_variable_unknown_language_and_symlink_fail(self):
        for key, value in (('variables', ['TOKEN', 'TOKEN']), ('language', 'go'), ('path', '../escape.sh')):
            registry = copy.deepcopy(self.registry)
            registry['generators'][0][key] = value
            with self.subTest(key=key), self.assertRaises(gate.GateError):
                gate.check_generators(self.root, registry)
        target = self.root / 'target.sh'
        target.write_text(self.valid)
        self.path.unlink()
        self.path.symlink_to(target)
        with self.assertRaises(gate.GateError):
            gate.check_generators(self.root, self.registry)


class ClickHouseRegistryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='schema-review-fixture-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.sources = {"schema_version": 1, "sources": [{"pattern": "product/**", "reason": "Product schema sources require review on every change."}], "exclusions": []}
        self.reviews = {"schema_version": 1, "changes": []}
        self.before = b'# previous schema runner\n'
        self.path = 'product/schema.py'

    def write(self, source, path=None):
        path = path or self.path
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source)
        return target

    def check(self, status='M', old_sources=None):
        from unittest.mock import patch
        def blob(root, base, path):
            if path == 'deploy/update/clickhouse-sources.json':
                return json.dumps(old_sources).encode() if old_sources else None
            return self.before
        with patch.object(gate, 'base_bytes', side_effect=blob):
            return gate.check_clickhouse_changes(self.root, [(status, self.path)], 'fixture-base', self.sources, self.reviews, registry())

    def review(self):
        value = reviewed([{'table': 'clickhouse:spans', 'risk_class': 'clickhouse_change'}])
        target = self.root / self.path
        self.reviews['changes'] = [{'path': self.path, 'before_sha256': gate.content_sha(self.before),
            'after_sha256': gate.content_sha(target.read_bytes() if target.exists() else None), 'declaration': value}]

    def test_literal_schema_and_mutation_statements_require_review_in_all_languages(self):
        statements = ['ALTER TABLE spans ADD COLUMN x String', 'CREATE TABLE spans (x UInt64)',
                      'CREATE MATERIALIZED VIEW v AS SELECT * FROM spans', 'DROP TABLE spans',
                      'RENAME TABLE spans TO old_spans', 'TRUNCATE TABLE spans', 'TRUNCATE spans',
                      'OPTIMIZE TABLE spans FINAL', 'ALTER TABLE spans UPDATE x=1 WHERE 1',
                      'ALTER TABLE spans DELETE WHERE 1', 'DELETE FROM spans WHERE 1']
        for suffix in ('schema.py', 'schema.go', 'schema.sql', 'schema.sh', 'start'):
            self.path = 'product/' + suffix
            for statement in statements:
                text = statement if suffix.endswith('.sql') else 'query="' + statement + '"\n'
                if suffix == 'start':
                    text = '#!/bin/sh\n' + text
                self.write(text)
                self.reviews['changes'] = []
                with self.subTest(path=self.path, statement=statement), self.assertRaisesRegex(gate.GateError, 'human-reviewed declarations'):
                    self.check()
                self.review()
                self.assertEqual(self.check()[0]['tables'], ['clickhouse:spans'])

    def test_truncate_without_table_with_modifiers_requires_review(self):
        statements = ('TRUNCATE spans SYNC', 'TRUNCATE spans ON CLUSTER analytics',
                      'TRUNCATE IF EXISTS default.spans ON CLUSTER analytics SYNC',
                      'TRUNCATE `default`.`spans` SYNC')
        for suffix in ('py', 'go', 'sql', 'sh', 'bash'):
            self.path = 'product/schema.' + suffix
            for statement in statements:
                text = statement + ';\n' if suffix == 'sql' else 'query="' + statement + '"\n'
                self.write(text)
                self.reviews['changes'] = []
                with self.subTest(path=self.path, statement=statement):
                    self.assertEqual(gate.check_source_coverage(self.root, self.sources, [self.path]), [self.path])
                    with self.assertRaisesRegex(gate.GateError, 'human-reviewed declarations'):
                        self.check()
                    self.review()
                    self.assertEqual(self.check()[0]['tables'], ['clickhouse:spans'])

    def test_bash_suffix_and_shebang_sources_require_registration_and_review(self):
        variants = (('schema.sh', ''), ('schema.bash', ''), ('start', '#!/bin/bash\n'),
                    ('.bootstrap', '#!/usr/bin/env bash\n'), ('schema.command', '#!/bin/bash -eu\n'),
                    ('schema.run', '#!/usr/bin/env -S bash -eu\n'))
        for name, shebang in variants:
            self.path = 'product/' + name
            self.write(shebang + 'clickhouse-client --query "DROP TABLE spans"\n')
            self.reviews['changes'] = []
            with self.subTest(path=self.path):
                self.assertEqual(gate.check_source_coverage(self.root, self.sources, [self.path]), [self.path])
                unregistered = {**self.sources, 'sources': []}
                with self.assertRaisesRegex(gate.GateError, 'unregistered schema/mutation'):
                    gate.check_source_coverage(self.root, unregistered, [self.path])
                with self.assertRaisesRegex(gate.GateError, 'human-reviewed declarations'):
                    self.check()
                self.review()
                self.assertEqual(len(self.check()), 1)

    def test_removing_bash_shebang_or_file_still_reviews_the_old_statement(self):
        self.path = 'product/schema.command'
        self.before = b'#!/usr/bin/env bash\nclickhouse-client --query "DROP TABLE spans"\n'
        target = self.write('Documentation only, no shell entrypoint now.\n')
        for status in ('M', 'D'):
            if status == 'D':
                target.unlink()
            self.reviews['changes'] = []
            with self.subTest(status=status):
                with self.assertRaisesRegex(gate.GateError, 'human-reviewed declarations'):
                    self.check(status)
                self.review()
                self.assertEqual(len(self.check(status)), 1)

    def test_readonly_bash_sources_do_not_require_review(self):
        for name, shebang in (('schema.bash', ''), ('start', '#!/bin/bash\n'),
                              ('schema.command', '#!/usr/bin/env bash\n')):
            self.path = 'product/' + name
            self.write(shebang + 'clickhouse-client --query "SELECT count() FROM spans"\n')
            with self.subTest(path=self.path):
                self.assertEqual(gate.check_source_coverage(self.root, self.sources, [self.path]), [])
                self.assertEqual(self.check(), [])

    def test_client_calls_readonly_builders_and_registration_alone_do_not_trigger_review(self):
        for text in ('# comment only\n', 'client.command(query)', 'conn.Exec(ctx, query)',
                     'ClickHouseClientSingleton().client.execute("SELECT * FROM spans")',
                     'import clickhouse_connect\nquery = "SELECT * FROM spans"', 'var q = "SELECT count() FROM spans"'):
            self.write(text)
            self.assertEqual(gate.check_source_coverage(self.root, self.sources, [self.path]), [])
            self.assertEqual(self.check(), [])

    def test_obfuscated_sql_is_explicitly_outside_the_keyword_scan_threat_model(self):
        self.write('parts=["AL", "TER", " TABLE spans"]; client.command("".join(parts))')
        self.assertEqual(self.check(), [])

    def test_new_and_deleted_files_need_exact_hash_review(self):
        self.before = None
        self.write('CREATE TABLE spans (id UInt64)')
        with self.assertRaises(gate.GateError):
            self.check('A')
        self.review()
        self.assertEqual(len(self.check('A')), 1)
        self.before = (self.root / self.path).read_bytes()
        (self.root / self.path).unlink()
        with self.assertRaises(gate.GateError):
            self.check('D')
        self.review()
        self.assertEqual(len(self.check('D')), 1)

    def test_review_cannot_be_reused_after_any_edit_or_base_change(self):
        self.write('ALTER TABLE spans ADD COLUMN x String;')
        self.review()
        self.write('ALTER TABLE spans ADD COLUMN x String; -- comment')
        with self.assertRaisesRegex(gate.GateError, 'exact base-to-head change'):
            self.check()
        self.write('ALTER TABLE spans ADD COLUMN x String;')
        self.before = b'# different base'
        with self.assertRaises(gate.GateError):
            self.check()

    def test_removing_source_registration_cannot_bypass_previous_registry(self):
        old = copy.deepcopy(self.sources)
        self.sources['sources'] = []
        self.write('DELETE FROM spans WHERE id=1')
        with self.assertRaisesRegex(gate.GateError, 'human-reviewed declarations'):
            self.check(old_sources=old)

    def test_deleted_or_obfuscated_unregistered_old_marker_still_requires_review(self):
        self.sources['sources'] = []
        self.before = b'ALTER TABLE spans MODIFY COLUMN x String'
        self.write('# removed all DDL markers')
        with self.assertRaises(gate.GateError):
            self.check()

    def test_new_discovery_paths_require_registration(self):
        self.sources['sources'] = []
        for path, source in (('new/schema.py', 'client.execute("DELETE FROM spans WHERE 1")'),
                             ('new/schema.go', 'conn.Exec(ctx, "OPTIMIZE TABLE spans FINAL")'),
                             ('new/schema.sql', 'CREATE MATERIALIZED VIEW v AS SELECT * FROM spans'),
                             ('new/start', '#!/bin/sh\nclickhouse-client -q "DROP TABLE spans"'),
                             ('new/schema.sh', 'ALTER TABLE spans ADD COLUMN x String')):
            self.write(source, path)
            with self.subTest(path=path), self.assertRaisesRegex(gate.GateError, 'unregistered schema/mutation'):
                gate.check_source_coverage(self.root, self.sources, [path])

    def test_test_like_production_filenames_never_get_an_implicit_exemption(self):
        for path in ('product/test_executor.py', 'product/executor_test.py', 'product/executor_test.go'):
            self.path = path
            self.write('query="DELETE FROM spans WHERE 1"')
            self.assertEqual(gate.check_source_coverage(self.root, self.sources, [path]), [path])
            with self.assertRaisesRegex(gate.GateError, 'human-reviewed declarations'):
                self.check()

    def test_only_concrete_test_directories_or_individually_reviewed_files_can_be_excluded(self):
        for pattern in ('**/test_*.py', '**/*_test.go', '**/tests/**', 'product/test_executor.py'):
            bad = copy.deepcopy(self.sources)
            bad['exclusions'] = [{'pattern': pattern, 'reason': 'A name is not evidence that this is a test.', 'sha256': None}]
            with self.subTest(pattern=pattern), self.assertRaises(gate.GateError):
                gate.source_registry(bad)
        path = 'product/tests/fixture.py'
        self.write('query="DROP TABLE spans"', path)
        self.sources['exclusions'] = [{'pattern': 'product/tests/**', 'reason': 'Verified fixture-only directory for this product.', 'sha256': None}]
        self.assertEqual(gate.check_source_coverage(self.root, self.sources, [path]), [])

    def test_historical_review_cost_reads_tag_blobs_and_uses_the_statement_trigger(self):
        from unittest.mock import patch
        blobs = {('v1.54.0', 'product/client.py'): b'query="SELECT 1"',
                 ('v1.55.0', 'product/client.py'): b'client.command("SELECT 2")',
                 ('v1.54.0', 'product/schema.sql'): b'CREATE TABLE spans (x UInt64)',
                 ('v1.55.0', 'product/schema.sql'): b'ALTER TABLE spans ADD COLUMN x UInt64'}
        with patch.object(gate, 'base_bytes', side_effect=lambda root, tag, path: blobs[(tag, path)]):
            candidates = gate.clickhouse_review_candidates(self.root,
                [('M', 'product/client.py'), ('M', 'product/schema.sql')], 'v1.54.0', self.sources, head='v1.55.0')
        self.assertEqual([row['path'] for row in candidates], ['product/schema.sql'])
        self.assertEqual(candidates[0]['after_sha256'], gate.content_sha(blobs[('v1.55.0', 'product/schema.sql')]))

    def test_whole_line_comments_and_text_truncation_prose_are_not_statements(self):
        for text in ('# DELETE FROM store\n', '// Create quota enforcement plugin\n', '-- ALTER TABLE spans\n',
                     'def truncate():\n    """Truncate a long display string."""\n    pass\n'):
            with self.subTest(text=text):
                self.assertEqual(gate.schema_statements(text.encode()), [])

    def test_extensionless_binary_and_non_source_assets_are_not_shell_sources(self):
        binary = self.root / 'product/native-helper'
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_bytes(b'\x7fELF\xff\x00ALTER TABLE spans')
        self.write('ALTER TABLE spans', 'product/notes.md')
        self.assertEqual(gate.check_source_coverage(self.root, self.sources,
                         ['product/native-helper', 'product/notes.md']), [])

    def test_missing_review_error_lists_exact_blobs_and_human_fields(self):
        self.write('client.execute("DELETE FROM spans WHERE 1")')
        with self.assertRaises(gate.GateError) as failure:
            self.check()
        for field in ('before_sha256', 'after_sha256', 'mitigation', 'actual reviewer', 'PR discussion', 'clickhouse:'):
            self.assertIn(field, str(failure.exception))

    def test_exclusion_is_blob_pinned_and_does_not_exempt_changed_source(self):
        self.sources['sources'] = []
        target = self.write('docker.command("inspect")')
        self.sources['exclusions'] = [{'pattern': self.path, 'reason': 'Docker-only commands are not ClickHouse schema operations.', 'sha256': gate.content_sha(target.read_bytes())}]
        gate.check_source_coverage(self.root, self.sources, [self.path])
        target.write_text('client.command("ALTER TABLE spans MODIFY COLUMN x String")')
        with self.assertRaises(gate.GateError):
            gate.check_source_coverage(self.root, self.sources, [self.path])

    def test_indirect_or_broken_source_symlinks_fail_closed(self):
        target = self.root / self.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(self.root / 'missing.py')
        with self.assertRaisesRegex(gate.GateError, 'indirect source'):
            gate.check_source_coverage(self.root, self.sources, [self.path])
        directory = self.root / 'tooling'
        directory.mkdir()
        (self.root / 'tool-link').symlink_to(directory, target_is_directory=True)
        self.assertEqual(gate.check_source_coverage(self.root, self.sources, ['tool-link']), [])

    def test_registry_closed_schema_and_malformed_review_refuse(self):
        self.write('SELECT 1;')
        self.review()
        for key in self.reviews['changes'][0]:
            broken = copy.deepcopy(self.reviews)
            del broken['changes'][0][key]
            original = self.reviews
            self.reviews = broken
            try:
                with self.subTest(missing=key), self.assertRaises(gate.GateError):
                    self.check()
            finally:
                self.reviews = original
        self.reviews['changes'][0]['declaration']['extra'] = True
        with self.assertRaises(gate.GateError):
            self.check()
        with self.assertRaises(gate.GateError):
            gate.source_registry({**self.sources, 'unknown': True})

    def test_repository_inventory_covers_literal_statements_not_readonly_client_sources(self):
        sources = gate.document(ROOT / 'deploy/update/clickhouse-sources.json')
        covered = gate.check_source_coverage(ROOT, sources)
        for path in ('futureagi/tracer/services/clickhouse/schema.py',
                     'futureagi/tracer/services/clickhouse/v2/schema/002_spans_v2.sql',
                     'futureagi/scripts/property_catalog_oss/bootstrap_clickhouse.sh', 'deploy/standalone/bin/bootstrap.py'):
            self.assertIn(path, covered)
        for path in ('futureagi/simulate/services/harness_versioning/assembly.py',
                     'futureagi/simulate/services/hosted_harness_gateway.py', 'deploy/update/probes/standalone.py',
                     'fi-collector/pkg/chwriter/writer.go'):
            self.assertNotIn(path, covered)

    def test_production_harness_modules_are_classified_by_statements_not_path_exclusions(self):
        sources = gate.document(ROOT / 'deploy/update/clickhouse-sources.json')
        for path in ('futureagi/simulate/services/harness_versioning/assembly.py',
                     'futureagi/simulate/services/hosted_harness_gateway.py', 'futureagi/simulate/services/test_executor.py'):
            content = (ROOT / path).read_bytes()
            self.assertFalse(gate.excluded_source(path, sources['exclusions'], content))
            self.assertEqual(gate.schema_statements(content), [])

    def test_experiment_create_view_docstring_needs_no_registration_or_review(self):
        # Exact isolated excerpt from _create_eval_metrics_inline's docstring.
        source = ('def _create_eval_metrics_inline(resolved=None):\n'
                  '    """Create UserEvalMetric records inline during experiment creation/update.\n'
                  '\n'
                  '        resolved: Optional ``[(template, pin)]`` from ``resolve_eval_entries``\n'
                  '            (the create view resolves before writing); resolved here if absent.\n'
                  '            The template must be one the organization can use, and the pin is\n'
                  '            stored on ``pinned_version`` (the runners read it).\n'
                  '    """\n'
                  '    return resolved\n')
        self.path = 'futureagi/model_hub/views/experiments.py'
        self.sources['sources'] = []
        self.before = source.encode()
        self.write(source + '# Unrelated routing edit.\n')
        self.assertEqual(gate.check_source_coverage(self.root, self.sources, [self.path]), [])
        self.assertEqual(self.check(), [])

    def test_python_comments_and_docstrings_are_ignored_in_historical_blobs(self):
        from unittest.mock import patch
        before = b'"""CREATE VIEW documentation."""\nvalue = 1 # DROP TABLE prose\n'
        after = b'async def build():\n    """ALTER TABLE documentation."""\n    pass\n'
        with patch.object(gate, 'base_bytes', side_effect=[before, after]):
            self.assertEqual(gate.clickhouse_review_candidates(
                self.root, [('M', self.path)], 'fixture-base', self.sources, head='fixture-head'), [])

    def test_python_sql_keeps_registration_review_hashes_and_original_lines(self):
        from unittest.mock import patch
        source = ('"""CREATE VIEW documentation."""\n'
                  '# DROP TABLE documentation\n'
                  'query = """\n'
                  'CREATE VIEW spans_view AS SELECT * FROM spans\n'
                  '"""\n'
                  'client.command("CREATE VIEW other_view AS SELECT 1") # ALTER TABLE prose\n')
        self.write(source)
        with patch.object(gate, 'base_bytes', return_value=self.before):
            candidates = gate.clickhouse_review_candidates(
                self.root, [('M', self.path)], 'fixture-base', self.sources)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]['before_statements'], [])
        self.assertEqual(candidates[0]['after_statements'], [
            {'keyword': 'CREATE VIEW', 'line': 4}, {'keyword': 'CREATE VIEW', 'line': 6}])
        self.assertEqual(candidates[0]['before_sha256'], gate.content_sha(self.before))
        self.assertEqual(candidates[0]['after_sha256'], gate.content_sha(source.encode()))
        with self.assertRaisesRegex(gate.GateError, 'unregistered schema/mutation'):
            gate.check_source_coverage(self.root, {**self.sources, 'sources': []}, [self.path])
        self.assertEqual(gate.check_source_coverage(self.root, self.sources, [self.path]), [self.path])
        with self.assertRaisesRegex(gate.GateError, 'human-reviewed declarations'):
            self.check()
        self.review()
        self.assertEqual(self.check()[0]['tables'], ['clickhouse:spans'])
        self.write(source + '# Comment-only edits still invalidate reviewed hashes.\n')
        with self.assertRaisesRegex(gate.GateError, 'exact base-to-head change'):
            self.check()

    def test_python_old_sql_still_needs_review_after_docstring_replacement_or_deletion(self):
        self.before = b'"""CREATE VIEW documentation."""\nquery = "CREATE VIEW spans_view AS SELECT 1"\n'
        target = self.write('"""CREATE VIEW is only documentation now."""\n')
        for status in ('M', 'D'):
            if status == 'D':
                target.unlink()
            self.reviews['changes'] = []
            with self.subTest(status=status):
                with self.assertRaisesRegex(gate.GateError, 'human-reviewed declarations') as failure:
                    self.check(status)
                self.assertIn('"before_statements": [{"keyword": "CREATE VIEW", "line": 2}]',
                              str(failure.exception))
                self.assertIn('"after_statements": []', str(failure.exception))
                self.review()
                self.assertEqual(len(self.check(status)), 1)


class PythonSchemaStatementsTests(unittest.TestCase):
    def scan(self, source):
        return gate.schema_statements(source.encode('utf-8'), path='product/schema.py')

    def test_module_class_function_and_async_docstrings_are_ignored(self):
        for source in ('"""CREATE VIEW documentation.\nALTER TABLE prose."""\n',
                       'class Schema:\n    """CREATE VIEW documentation."""\n    pass\n',
                       'def build():\n    """CREATE VIEW documentation."""\n    pass\n',
                       'async def build():\n    """CREATE VIEW documentation."""\n    pass\n',
                       'class Schema:\n    def build(self):\n        r"""CREATE VIEW documentation."""\n',
                       '@decorator\nasync def build():\n    u"CREATE VIEW documentation."\n'):
            with self.subTest(source=source):
                self.assertEqual(self.scan(source), [])

    def test_parenthesized_and_implicitly_concatenated_docstrings_are_ignored(self):
        source = ('("CREATE VIEW documentation. " # DROP TABLE prose\n'
                  ' "ALTER TABLE documentation.")\n'
                  'def build():\n'
                  '    ("CREATE VIEW documentation. "\n'
                  '     "DELETE FROM documentation.")\n')
        self.assertEqual(self.scan(source), [])

    def test_strings_outside_true_docstring_positions_remain_discoverable(self):
        variants = (
            ('query = """CREATE VIEW spans_view AS SELECT 1"""\n', 1),
            ('client.command("""CREATE VIEW spans_view AS SELECT 1""")\n', 1),
            ('def build():\n    return """CREATE VIEW spans_view AS SELECT 1"""\n', 2),
            ('"""Module documentation."""\n"""CREATE VIEW spans_view AS SELECT 1"""\n', 2),
            ('def build():\n    pass\n    """CREATE VIEW spans_view AS SELECT 1"""\n', 3),
            ('class Schema:\n    pass\n    """CREATE VIEW spans_view AS SELECT 1"""\n', 3),
            ('if True:\n    """CREATE VIEW spans_view AS SELECT 1"""\n', 2),
            ('f"CREATE VIEW spans_view AS SELECT 1"\n', 1),
            ('b"CREATE VIEW spans_view AS SELECT 1"\n', 1),
        )
        for source, line in variants:
            with self.subTest(source=source):
                self.assertEqual(self.scan(source), [{'keyword': 'CREATE VIEW', 'line': line}])

    def test_hash_and_other_comment_markers_inside_strings_are_preserved(self):
        source = ('# CREATE VIEW documentation\n'
                  'query = """# CREATE VIEW first_view AS SELECT 1\n'
                  '// CREATE VIEW second_view AS SELECT 1\n'
                  '-- CREATE VIEW third_view AS SELECT 1\n'
                  '"""\n'
                  'client.command("# CREATE VIEW fourth_view AS SELECT 1") # DROP TABLE prose\n')
        self.assertEqual(self.scan(source), [
            {'keyword': 'CREATE VIEW', 'line': line} for line in (2, 3, 4, 6)])

    def test_inline_and_whole_line_python_comments_are_ignored(self):
        source = ('# CREATE VIEW documentation\n'
                  'label = "café" # DROP TABLE documentation\n'
                  'client.command("CREATE VIEW spans_view AS SELECT 1") # ALTER TABLE prose\n')
        self.assertEqual(self.scan(source), [{'keyword': 'CREATE VIEW', 'line': 3}])

    def test_non_ascii_before_docstrings_and_comments_preserves_adjacent_sql(self):
        source = ('class Café: "CREATE VIEW documentation"; query = "CREATE VIEW café AS SELECT 1"\n'
                  'def café(): "ALTER TABLE documentation"; return "CREATE VIEW v AS SELECT 1" # DROP TABLE prose\n'
                  'async def café_async(): "DELETE FROM documentation"; return "CREATE VIEW v AS SELECT 1"\n')
        self.assertEqual(self.scan(source), [
            {'keyword': 'CREATE VIEW', 'line': line} for line in (1, 2, 3)])

    def test_unicode_line_separator_inside_string_does_not_shift_source_lines(self):
        source = ('label = "before after" # DROP TABLE documentation\n'
                  'class Café: "CREATE VIEW documentation"; query = "CREATE VIEW v AS SELECT 1"\n')
        self.assertEqual(self.scan(source), [{'keyword': 'CREATE VIEW', 'line': 2}])

    def test_python_newline_variants_preserve_diagnostic_lines(self):
        source = ('"""CREATE VIEW documentation.\nSecond documentation line."""\n'
                  '# DROP TABLE documentation\n'
                  'query = "CREATE VIEW spans_view AS SELECT 1"\n')
        for newline in ('\n', '\r\n', '\r'):
            with self.subTest(newline=repr(newline)):
                self.assertEqual(self.scan(source.replace('\n', newline)),
                                 [{'keyword': 'CREATE VIEW', 'line': 4}])

    def test_invalid_python_retains_discovery_without_partial_masking(self):
        variants = (
            ('def broken(:\n    """CREATE VIEW documentation."""\n', 2),
            ('query = """\n# CREATE VIEW spans_view AS SELECT 1\n', 2),
            ('"""CREATE VIEW documentation."""\nquery = (\n', 1),
            ('CREATE VIEW spans_view AS SELECT 1\n', 1),
        )
        for source, line in variants:
            with self.subTest(source=source):
                self.assertEqual(self.scan(source), [{'keyword': 'CREATE VIEW', 'line': line}])

    def test_legacy_and_non_python_calls_keep_existing_keyword_behavior(self):
        source = b'"""CREATE VIEW spans_view AS SELECT 1"""\n# ALTER TABLE ignored\nvalue = 1 # DROP TABLE kept\n'
        expected = [{'keyword': 'CREATE VIEW', 'line': 1}, {'keyword': 'DROP TABLE', 'line': 3}]
        self.assertEqual(gate.schema_statements(source), expected)
        for path in (None, 'schema.sql', 'schema.go', 'schema.sh', 'schema.bash', 'schema', 'schema.command'):
            with self.subTest(path=path):
                self.assertEqual(gate.schema_statements(source, path=path), expected)
        self.assertEqual(gate.schema_statements(None), [])
        self.assertEqual(gate.schema_statements(None, path='schema.py'), [])

    def test_shell_shebang_is_not_treated_as_a_python_docstring(self):
        source = b'#!/bin/sh\n"CREATE VIEW spans_view AS SELECT 1"\n'
        for path in ('schema.py', 'schema.sh', 'schema'):
            with self.subTest(path=path):
                self.assertEqual(gate.schema_statements(source, path=path),
                                 [{'keyword': 'CREATE VIEW', 'line': 2}])

    def test_inspected_python_is_not_executed(self):
        source = ('raise AssertionError("inspected source must not execute")\n'
                  'client.command("CREATE VIEW spans_view AS SELECT 1")\n')
        self.assertEqual(self.scan(source), [{'keyword': 'CREATE VIEW', 'line': 2}])


class InspectedNonClickHousePinsTests(unittest.TestCase):
    """Exact inspected blobs, not filename or SQL-dialect exemptions.

    These pins cover the benchmark fixtures and the dev blobs in its CI merge.
    Their before blobs were also inspected: two additions, and error_codes.py
    previously contained the same display text. A matching after pin skips the
    whole change, so a pin must never be added without that before-blob review.
    """

    pins = {
        'fi-collector/pkg/ingestbench/bench_test.go':
            ('2863c35e9934d5fe3cf3f0403fff2fac077a821ca5f94ad40fc43cc63bb445b0', 'DROP TABLE', 51),
        'fi-collector/pkg/ingestbench/integration_test.go':
            ('3cbb292f8fdadcd6fa35039c97953ebe204a40d1035a642c95405f1438058ae9', 'CREATE TABLE', 159),
        'futureagi/tfc/utils/error_codes.py':
            ('8cc24ba5517e415a6f5ae416ad31378277c1948fc77ec9bc91c7eb575f297e9b', 'CREATE USER', 356),
    }

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='gate-inspected-pins-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.sources = gate.source_registry(gate.document(ROOT / 'deploy/update/clickhouse-sources.json'))
        self.reviews = {'schema_version': 1, 'changes': []}

    def check(self, path, before, after, sources=None):
        from unittest import mock
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if after is None:
            target.unlink(missing_ok=True)
        else:
            target.write_bytes(after)
        status = 'D' if after is None else 'A' if before is None else 'M'
        with mock.patch.object(gate, 'base_bytes', return_value=before):
            return gate.check_clickhouse_changes(
                self.root, [(status, path)], 'inspected-base',
                self.sources if sources is None else sources, self.reviews, registry())

    def test_each_exact_pin_is_scoped_to_its_inspected_blob_and_path(self):
        for path, (digest, keyword, line) in self.pins.items():
            with self.subTest(path=path):
                blob = (ROOT / path).read_bytes()
                entries = [row for row in self.sources['exclusions'] if row['pattern'] == path]
                self.assertEqual(len(entries), 1)
                self.assertEqual(entries[0]['sha256'], digest)
                self.assertEqual(gate.content_sha(blob), digest)
                # The conservative alarm remains intact; only the registry pin applies.
                self.assertEqual(gate.schema_statements(blob), [{'keyword': keyword, 'line': line}])
                self.assertTrue(gate.excluded_source(path, self.sources['exclusions'], blob))
                self.assertEqual(self.check(path, None, blob), [])
                self.assertEqual(gate.check_source_coverage(self.root, self.sources, [path]), [])
                other = str(Path(path).with_name('unpinned_' + Path(path).name))
                self.assertFalse(gate.excluded_source(other, self.sources['exclusions'], blob))
                with self.assertRaisesRegex(gate.GateError, 'human-reviewed declarations'):
                    self.check(other, None, blob)

    def test_removing_each_pin_restores_the_original_alarm(self):
        for path in self.pins:
            with self.subTest(path=path):
                sources = copy.deepcopy(self.sources)
                sources['exclusions'] = [row for row in sources['exclusions'] if row['pattern'] != path]
                with self.assertRaisesRegex(gate.GateError, 'human-reviewed declarations'):
                    self.check(path, None, (ROOT / path).read_bytes(), sources)

    @staticmethod
    def mutation(path):
        if path.endswith('.go'):
            return b'\nfunc unreviewed(client interface{ Exec(string) error }) { _ = client.Exec("DROP TABLE spans") }\n'
        return b'\nclient.command("DELETE FROM spans WHERE 1")\n'

    def test_comment_and_executable_edits_invalidate_every_pin(self):
        for path in self.pins:
            before = (ROOT / path).read_bytes()
            comment = b'\n// changed fixture\n' if path.endswith('.go') else b'\n# changed fixture\n'
            for edit in (comment, self.mutation(path)):
                with self.subTest(path=path, edit=edit):
                    after = before + edit
                    self.assertFalse(gate.excluded_source(path, self.sources['exclusions'], after))
                    with self.assertRaisesRegex(gate.GateError, 'human-reviewed declarations') as failure:
                        self.check(path, before, after)
                    self.assertIn(gate.content_sha(before), str(failure.exception))
                    self.assertIn(gate.content_sha(after), str(failure.exception))
                    self.assertEqual(gate.check_source_coverage(self.root, self.sources, [path]), [path])

    def test_unpinned_old_mutations_cannot_disappear_by_deletion_or_replacement(self):
        for path in self.pins:
            before = (ROOT / path).read_bytes() + self.mutation(path)
            replacement = b'package ingestbench\n' if path.endswith('.go') else b'value = 1\n'
            for after in (None, replacement):
                with self.subTest(path=path, deleted=after is None):
                    with self.assertRaisesRegex(gate.GateError, 'human-reviewed declarations') as failure:
                        self.check(path, before, after)
                    self.assertIn(gate.content_sha(before), str(failure.exception))
                    self.assertIn('before_statements', str(failure.exception))

    def test_production_clickhouse_mutations_still_need_review(self):
        path = 'futureagi/tracer/services/clickhouse/unreviewed.py'
        for statement in ('DROP TABLE spans', 'ALTER TABLE spans DELETE WHERE 1',
                          'DELETE FROM spans WHERE 1', 'TRUNCATE TABLE spans'):
            with self.subTest(statement=statement):
                blob = ('client.command(' + repr(statement) + ')\n').encode()
                self.assertFalse(gate.excluded_source(path, self.sources['exclusions'], blob))
                with self.assertRaisesRegex(gate.GateError, 'human-reviewed declarations'):
                    self.check(path, None, blob)

    def test_gateway_non_test_go_file_gaining_or_losing_drop_table_still_needs_review(self):
        # The gateway's Go test files are cleared one exact blob at a time; its
        # production Go files never are.
        path = 'agentcc-gateway/internal/server/unreviewed_store.go'
        plain = b'package server\n'
        changed = plain + self.mutation(path)
        self.assertFalse(gate.excluded_source(path, self.sources['exclusions'], changed))
        for before, after in ((plain, changed), (changed, plain)):
            with self.subTest(gains=after is changed):
                with self.assertRaisesRegex(gate.GateError, 'human-reviewed declarations'):
                    self.check(path, before, after)

    def test_pinned_gateway_test_blob_clears_a_base_blob_that_held_the_text(self):
        # v1.58.0's toolguard_test.go held a DROP TABLE sample; the line's blob
        # holds none and is pinned. The pin clears that change, and only for
        # this exact blob.
        path = 'agentcc-gateway/internal/guardrails/mcpsec/toolguard_test.go'
        pinned = (ROOT / path).read_bytes()
        self.assertEqual(gate.schema_statements(pinned), [])
        before = pinned + self.mutation(path)
        self.assertTrue(gate.excluded_source(path, self.sources['exclusions'], pinned))
        self.assertEqual(self.check(path, before, pinned), [])
        edited = pinned + b'\n// changed fixture\n'
        self.assertFalse(gate.excluded_source(path, self.sources['exclusions'], edited))
        with self.assertRaisesRegex(gate.GateError, 'human-reviewed declarations'):
            self.check(path, before, edited)


class StaticEntryPointTests(unittest.TestCase):
    def setUp(self):
        import shutil
        self.directory = tempfile.TemporaryDirectory(prefix='gate-entrypoint-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        for relative in ('deploy/update/high-volume-tables.json', 'deploy/update/automatic-layout.json',
                         'deploy/update/clickhouse-sources.json', 'deploy/update/clickhouse-change-reviews.json',
                         'deploy/update/env-generators.json', 'deploy/update/migration-apps.json',
                         'deploy/env-reference.toml', 'docker-compose.yml', 'bin/lib/secrets.sh'):
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)
        self.write_edit_reviews()
        contract = gate.document(self.root / 'deploy/update/automatic-layout.json')
        self.model = self.root / 'model.json'
        self.model.write_text(json.dumps({'volumes': contract['volumes'], 'services': {
            name: dict({'profiles': row['profiles'], 'restart': row['restart'], 'volumes': row['mounts']},
                       **({'tmpfs': row['tmpfs']} if row.get('tmpfs') else {}))
            for name, row in contract['services'].items()}}))

    def run_changed_source(self, path, source, status='M', before=None, flags=('--static',)):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if source is not None:
            target.write_text(source)
        elif target.exists():
            target.unlink()
        return self.run_changes([(status, path)], {path: before}, flags)

    def run_changes(self, changes, before, flags=('--static',)):
        import contextlib
        import io
        from unittest.mock import patch
        out, err = io.StringIO(), io.StringIO()
        def blob(root, baseline, path):
            self.assertEqual(root, self.root)
            self.assertEqual(baseline, 'merge-base')
            return before.get(path)
        with patch.object(gate, 'changed_paths', return_value=changes), patch.object(gate, 'base_commit', return_value='merge-base'), \
                patch.object(gate, 'base_bytes', side_effect=blob), \
                patch.object(gate, 'repository_paths', return_value=[p for _, p in changes]), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = gate.main(['--root', str(self.root), *flags, '--base', 'fixture', '--compose-model', str(self.model), '--json'])
        return code, out.getvalue(), err.getvalue()

    migration = 'futureagi/tracer/migrations/existing.py'
    migration_source = ('from django.db import migrations\n\n'
                        'def forwards(apps, schema_editor):\n    pass\n\n'
                        'class Migration(migrations.Migration):\n'
                        '    dependencies = []\n'
                        '    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]\n')

    def migration_declaration(self):
        return 'UPDATE_SAFETY = ' + repr(reviewed([{'table': 'postgres:tracer_trace', 'risk_class': 'data_migration'}])) + '\n'

    def test_declaration_only_addition_passes_static_gate(self):
        for declaration in (self.migration_declaration(), self.migration_declaration().replace('UPDATE_SAFETY =', 'UPDATE_SAFETY: dict =')):
            with self.subTest(declaration=declaration):
                code, stdout, err = self.run_changed_source(self.migration, declaration + self.migration_source,
                                                          before=self.migration_source.encode())
                self.assertEqual(code, 0, err)
                self.assertEqual(json.loads(stdout)['new_migrations'], 1)

    def test_declaration_only_addition_is_validated_like_a_new_migration(self):
        invalid = (('UPDATE_SAFETY = {}\n', 'expected exactly'),
                   ('UPDATE_SAFETY = dict()\n', 'literal dict'),
                   ('UPDATE_SAFETY = {"schema_version": 1, "schema_version": 1}\n', 'literal keys'),
                   (self.migration_declaration().replace("'data_migration'", "'unknown_risk'"), 'invalid risk_classes'),
                   (self.migration_declaration().replace("'large_volume_rehearsal': True", "'large_volume_rehearsal': False"), 'mandatory'))
        for declaration, message in invalid:
            for status in ('A', 'M'):
                with self.subTest(declaration=declaration, status=status):
                    code, _, err = self.run_changed_source(self.migration, declaration + self.migration_source,
                                                          status, self.migration_source.encode() if status == 'M' else None)
                    self.assertEqual(code, 1, err)
                    self.assertNotIn('histories are immutable', err)
                    self.assertIn(message, err)

    def test_declaration_plus_other_ast_edit_is_immutable(self):
        edited = (self.migration_source.replace('operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]', 'operations = []'),
                  self.migration_source.replace('    pass', '    return None'),
                  '"New module docstring"\n' + self.migration_source)
        for source in edited:
            with self.subTest(source=source):
                code, _, err = self.run_changed_source(self.migration, self.migration_declaration() + source,
                                                      before=self.migration_source.encode())
                self.assertEqual(code, 1, err)
                self.assertIn('existing migration changed/deleted: deployed histories are immutable', err)

    def test_changing_an_existing_declaration_is_immutable(self):
        before = self.migration_declaration() + self.migration_source
        after = before.replace("'fixture-reviewer'", "'another-reviewer'")
        code, _, err = self.run_changed_source(self.migration, after, before=before.encode())
        self.assertEqual(code, 1, err)
        self.assertIn('histories are immutable', err)

    def test_removing_an_existing_declaration_is_immutable(self):
        before = self.migration_declaration() + self.migration_source
        code, _, err = self.run_changed_source(self.migration, self.migration_source, before=before.encode())
        self.assertEqual(code, 1, err)
        self.assertIn('histories are immutable', err)

    def test_deleting_an_existing_migration_is_immutable(self):
        for before in (self.migration_source, self.migration_declaration() + self.migration_source):
            with self.subTest(before=before):
                code, _, err = self.run_changed_source(self.migration, None, 'D', before.encode())
                self.assertEqual(code, 1, err)
                self.assertIn('histories are immutable', err)

    def write_edit_reviews(self, *rows):
        target = self.root / 'deploy/update/migration-edit-reviews.json'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({'schema_version': 1, 'reviews': list(rows)}))

    def edit_review(self, before, after, path=None, reason='Uses the migration connection; no schema operation changed.'):
        return {'path': path or self.migration, 'before_sha256': [gate.content_sha(before.encode())],
                'after_sha256': gate.content_sha(after.encode()), 'reason': reason}

    @property
    def aliased(self):
        return self.migration_source.replace(
            '    pass', '    apps.get_model("tracer", "Trace").objects.using(schema_editor.connection.alias).count()')

    def test_reviewed_edit_of_an_existing_migration_passes_on_an_exact_match(self):
        self.write_edit_reviews(self.edit_review(self.migration_source, self.aliased))
        code, stdout, err = self.run_changed_source(self.migration, self.aliased, before=self.migration_source.encode())
        self.assertEqual(code, 0, err)
        # An applied history reviewed byte for byte, not a new migration.
        self.assertEqual(json.loads(stdout)['new_migrations'], 0)

    def test_reviewed_edit_accepts_any_listed_base_version(self):
        other_base = self.migration_source.replace('dependencies = []', 'dependencies = [("tracer", "0001_initial")]')
        review = self.edit_review(self.migration_source, self.aliased)
        review['before_sha256'].append(gate.content_sha(other_base.encode()))
        self.write_edit_reviews(review)
        code, _, err = self.run_changed_source(self.migration, self.aliased, before=other_base.encode())
        self.assertEqual(code, 0, err)

    def test_reviewed_edit_refuses_any_other_head_bytes(self):
        self.write_edit_reviews(self.edit_review(self.migration_source, self.aliased))
        for after in (self.aliased + '\n', self.aliased.replace('count()', 'exists()')):
            with self.subTest(after=after):
                code, _, err = self.run_changed_source(self.migration, after, before=self.migration_source.encode())
                self.assertEqual(code, 1, err)
                self.assertIn('histories are immutable', err)

    def test_reviewed_edit_refuses_an_unlisted_base_version(self):
        self.write_edit_reviews(self.edit_review(self.migration_source, self.aliased))
        other_base = self.migration_source.replace('dependencies = []', 'dependencies = [("tracer", "0001_initial")]')
        code, _, err = self.run_changed_source(self.migration, self.aliased, before=other_base.encode())
        self.assertEqual(code, 1, err)
        self.assertIn('histories are immutable', err)

    def test_review_of_another_path_does_not_cover_this_one(self):
        self.write_edit_reviews(self.edit_review(self.migration_source, self.aliased,
                                                 path='futureagi/tracer/migrations/other.py'))
        code, _, err = self.run_changed_source(self.migration, self.aliased, before=self.migration_source.encode())
        self.assertEqual(code, 1, err)
        self.assertIn('histories are immutable', err)

    def test_review_never_covers_a_deleted_migration(self):
        self.write_edit_reviews(self.edit_review(self.migration_source, self.aliased))
        code, _, err = self.run_changed_source(self.migration, None, 'D', self.migration_source.encode())
        self.assertEqual(code, 1, err)
        self.assertIn('histories are immutable', err)

    def test_review_file_is_closed_and_every_entry_names_a_reason(self):
        review = self.edit_review(self.migration_source, self.aliased)
        invalid = (({**review, 'reason': '  '}, 'needs a reason'),
                   ({**review, 'before_sha256': []}, 'before_sha256'),
                   ({**review, 'before_sha256': ['not-a-sha']}, 'before_sha256'),
                   ({**review, 'after_sha256': 'not-a-sha'}, 'after_sha256'),
                   ({**review, 'path': 'futureagi/tracer/models.py'}, 'migration path'),
                   ({**review, 'reviewer': 'someone'}, 'expected exactly'))
        for row, message in invalid:
            with self.subTest(message=message, row=row):
                self.write_edit_reviews(row)
                code, _, err = self.run_changed_source(self.migration, self.aliased, before=self.migration_source.encode())
                self.assertEqual(code, 1, err)
                self.assertIn(message, err)
        self.write_edit_reviews(review, review)
        code, _, err = self.run_changed_source(self.migration, self.aliased, before=self.migration_source.encode())
        self.assertEqual(code, 1, err)
        self.assertIn('migration path', err)

    helper_source = 'def seed(apps, using):\n    return None\n'

    def test_new_migration_helper_module_is_not_a_migration(self):
        from unittest.mock import patch
        for helper in ('futureagi/agentcc/migrations/_frozen_helpers.py',
                       'futureagi/agentcc/migrations/~scratch.py'):
            seen = []
            def gate_paths(root, paths, mapping, registry):
                seen.extend(paths)
                return []
            with self.subTest(helper=helper), patch.object(gate, 'django_gate', side_effect=gate_paths):
                code, stdout, err = self.run_changed_source(helper, self.helper_source, 'A', None, ('--static', '--django'))
                self.assertEqual(code, 0, err)
                self.assertEqual(json.loads(stdout)['new_migrations'], 0)
                self.assertNotIn(helper, seen)

    def test_existing_migration_helper_stays_immutable(self):
        helper = 'futureagi/agentcc/migrations/_frozen_helpers.py'
        # An added UPDATE_SAFETY assignment is no exemption for a helper: it is imported, never loaded as a migration.
        for status, source in (('M', self.helper_source.replace('None', '0')), ('D', None),
                               ('M', self.migration_declaration() + self.helper_source)):
            with self.subTest(status=status, source=source):
                code, _, err = self.run_changed_source(helper, source, status, self.helper_source.encode())
                self.assertEqual(code, 1, err)
                self.assertIn('histories are immutable', err)

    def test_renaming_an_existing_migration_is_immutable(self):
        renamed = self.migration.replace('existing.py', 'renamed.py')
        target = self.root / renamed
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.migration_declaration() + self.migration_source)
        # changed_paths uses --no-renames, so a rename is a deletion plus addition.
        for changes in ([('A', renamed), ('D', self.migration)], [('R100', self.migration)]):
            with self.subTest(changes=changes):
                code, _, err = self.run_changes(changes, {self.migration: self.migration_source.encode()})
                self.assertEqual(code, 1, err)
                self.assertIn('histories are immutable', err)

    def test_modified_migration_needs_exactly_one_new_module_declaration(self):
        declarations = ('', self.migration_declaration() * 2, 'if True:\n    ' + self.migration_declaration(),
                        'UPDATE_SAFETY = {\n')
        for declaration in declarations:
            with self.subTest(declaration=declaration):
                code, _, err = self.run_changed_source(self.migration, declaration + self.migration_source,
                                                      before=self.migration_source.encode())
                self.assertEqual(code, 1, err)
                self.assertIn('histories are immutable', err)

    def test_declaration_addition_reaches_the_same_django_risk_gate(self):
        from unittest.mock import patch
        source = self.migration_declaration() + self.migration_source
        for flags in (('--django',), ('--static', '--django')):
            for status in ('A', 'M'):
                for failure in (None, gate.GateError('declaration does not cover detected tables/classes')):
                    with self.subTest(flags=flags, status=status, failure=failure), \
                            patch.object(gate, 'django_gate', return_value=[], side_effect=failure) as check:
                        code, _, err = self.run_changed_source(self.migration, source, status,
                                                              self.migration_source.encode() if status == 'M' else None, flags)
                        self.assertEqual(code, int(failure is not None), err)
                        check.assert_called_once_with(self.root, [self.migration],
                                                      gate.document(self.root / 'deploy/update/migration-apps.json'),
                                                      gate.document(self.root / 'deploy/update/high-volume-tables.json'))
                        if failure:
                            self.assertIn(str(failure), err)

    def test_registered_source_without_statements_does_not_need_a_declaration(self):
        code, stdout, err = self.run_changed_source('deploy/standalone/bin/bootstrap.py', '# documentation only')
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(stdout)['large_volume_rehearsal_required'], [])

    def test_unregistered_new_sql_shell_python_and_go_statement_sources_fail(self):
        for path, text in (('new/schema.py', 'client.execute("DELETE FROM spans WHERE 1")'),
                           ('new/schema.go', 'conn.Exec(ctx, "OPTIMIZE TABLE spans FINAL")'),
                           ('new/start', '#!/bin/sh\nclickhouse-client -q "DROP TABLE spans"'),
                           ('new/schema.sql', 'ALTER TABLE spans MODIFY COLUMN x String;')):
            with self.subTest(path=path):
                code, _, err = self.run_changed_source(path, text, 'A')
                self.assertEqual(code, 1, err)
                self.assertIn('unregistered schema/mutation', err)

    def test_truncate_modifiers_and_bash_sources_reach_static_review_gate(self):
        for path, text in (('scripts/schema.sql', 'TRUNCATE spans SYNC;'),
                           ('scripts/schema.py', 'query="TRUNCATE spans ON CLUSTER analytics"'),
                           ('scripts/schema.bash', 'clickhouse-client --query "DROP TABLE spans"'),
                           ('scripts/schema.command', '#!/usr/bin/env bash\nclickhouse-client --query "DROP TABLE spans"')):
            with self.subTest(path=path):
                code, _, err = self.run_changed_source(path, text, 'A')
                self.assertEqual(code, 1, err)
                self.assertIn('human-reviewed declarations', err)

    def test_commented_required_row_reaches_real_static_gate(self):
        reference = (ROOT / 'deploy/env-reference.toml').read_text()
        reference += '\n[[part]]\n[[part.section]]\n[[part.section.row]] # New setting\nkeys=["NEW_REQUIRED_TOKEN"]\nsetups=["S", "D"]\ndefault={S="required", D="optional empty"}\n'
        code, _, err = self.run_changed_source('deploy/env-reference.toml', reference)
        self.assertEqual(code, 1)
        self.assertIn('required Standalone NEW_REQUIRED_TOKEN', err)

    def test_existing_migration_modifications_and_deletions_fail(self):
        for status in ('M', 'D'):
            with self.subTest(status=status):
                code, _, err = self.run_changed_source('futureagi/tracer/migrations/old.py', '', status)
                self.assertEqual(code, 1)
                self.assertIn('histories are immutable', err)

    def test_verified_test_directory_is_not_a_product_schema_change(self):
        code, stdout, err = self.run_changed_source('deploy/tests/fixture.py', 'client.command("DROP TABLE spans")')
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(stdout)['large_volume_rehearsal_required'], [])


class MigrationEditReviewFileTests(unittest.TestCase):
    def test_every_reviewed_edit_matches_the_tree_and_names_a_reason(self):
        reviews = gate.migration_edit_reviews(gate.document(ROOT / 'deploy/update/migration-edit-reviews.json'))
        self.assertTrue(reviews)
        for path, row in reviews.items():
            with self.subTest(path=path):
                # A later edit of a reviewed migration needs its own review.
                self.assertEqual(gate.content_sha((ROOT / path).read_bytes()), row['after_sha256'])
                self.assertNotIn(row['after_sha256'], row['before_sha256'])


class WorkflowWiringTests(unittest.TestCase):
    def test_update_safety_filter_covers_every_scanned_source_change(self):
        import fnmatch
        import re
        text = (ROOT / ".github/workflows/backend-ci.yml").read_text()
        filters = text.split("          filters: |\n", 1)[1].split("      - id: always", 1)[0]
        groups, current = {}, None
        for line in filters.splitlines():
            match = re.fullmatch(r"            (\w+):", line)
            if match:
                current = match[1]
                groups[current] = []
            match = re.fullmatch(r"              - '([^']+)'", line)
            if match:
                groups[current].append(match[1])
        job = text.split("  update-safety:\n", 1)[1].split("\n  plan:", 1)[0]
        condition = next(line.strip()[4:] for line in job.splitlines() if line.strip().startswith("if: "))
        names = re.findall(r"needs\.changes\.outputs\.(\w+) == 'true'", condition)
        self.assertTrue(names, condition)
        self.assertEqual(condition, " || ".join("needs.changes.outputs." + name + " == 'true'" for name in names))
        self.assertIn("update_safety", names)
        outputs = text.split("    outputs:\n", 1)[1].split("    steps:", 1)[0]
        for name in names:
            self.assertIn("steps.filter.outputs." + name, outputs)

        # This path is outside the enumerated backend/bootstrap roots. Its
        # extensionless Bash shebang and DDL make it a real gate-scanned source.
        path = "docs/rebuild-catalog"
        content = b'#!/usr/bin/env bash\nclickhouse-client --query "DROP TABLE spans"\n'
        self.assertTrue(gate.source_blob(path, content))
        self.assertTrue(gate.schema_statements(content))
        # paths-filter uses picomatch, where '*.go' does not cross directories,
        # unlike fnmatch below; the gate finds shell sources by shebang, which no
        # path pattern can express. Only the catch-all is a sound trigger.
        self.assertEqual(groups["update_safety"], ["**"])
        self.assertTrue(any(fnmatch.fnmatchcase(path, pattern) for pattern in groups["update_safety"]),
                        path + " skips the required update-safety job")

        for path in ("fi-collector/pkg/server/server.go", "agentcc-gateway/cmd/agentcc/main.go",
                     "agentcc-gateway/internal/server/server.go", "new/schema.sql", "new/bootstrap.sh", "new/module.py",
                     "deploy/new/start", "bin/new-start", "scripts/new-start"):
            with self.subTest(path=path):
                self.assertTrue(any(fnmatch.fnmatchcase(path, pattern) for name in names for pattern in groups[name]),
                                path + " skips the required update-safety job")
        self.assertIn("        update-safety,", text.split("  backend-tests:\n", 1)[1])

    def test_backend_gate_is_required_and_uses_oss_settings(self):
        text = (ROOT / ".github/workflows/backend-ci.yml").read_text()
        job = text.split("  update-safety:\n", 1)[1].split("\n  plan:", 1)[0]
        self.assertIn("DJANGO_SETTINGS_MODULE: tfc.settings.settings", job)
        self.assertIn('EE_LICENSE_KEY: ""', job)
        self.assertIn("makemigrations --check --dry-run", job)
        self.assertIn("--static --django", job)
        self.assertIn("--calibrate", job)
        self.assertIn("uv sync --frozen --no-dev", job)
        self.assertIn("python -m unittest discover -s tests -p test_update_safety_gate_django.py", job)
        self.assertNotIn("python -I -m unittest", job)
        required = text.split("  backend-tests:\n", 1)[1]
        self.assertIn("        update-safety,", required)
        self.assertIn("scripts/check_update_safety.py", text.split("  plan:", 1)[0])

    def test_update_static_gate_has_backend_and_bootstrap_triggers(self):
        text = (ROOT / ".github/workflows/update-ci.yml").read_text()
        triggers = text.split("permissions:", 1)[0]
        for path in ('"futureagi/**"', '"bin/lib/**"', '"scripts/check_update_safety.py"'):
            self.assertEqual(triggers.count(path), 2)
        job = text.split("  safety:\n", 1)[1].split("\n  unit:", 1)[0]
        self.assertIn("--static --base", job)
        self.assertNotIn("up -d", job)


class GitComparisonTests(unittest.TestCase):
    def test_pr_base_includes_merge_base_diff_and_untracked_migrations(self):
        from unittest.mock import patch
        with patch.object(gate, "git", side_effect=["base\n", "merge-base\n",
                "A\tfutureagi/example/migrations/feature.py\n", "futureagi/example/migrations/untracked.py\n"]) as command:
            paths = gate.changed_paths(ROOT, "pr-base")
        self.assertEqual(paths, [("A", "futureagi/example/migrations/feature.py"),
                                 ("A", "futureagi/example/migrations/untracked.py")])
        self.assertEqual(command.call_args_list[1].args[1:], ("merge-base", "pr-base", "HEAD"))
        self.assertIn("merge-base", command.call_args_list[2].args)


class DeclarationTests(unittest.TestCase):
    def setUp(self):
        self.risks = [{"table": "postgres:hot_events", "risk_class": "data_migration"}]
        self.value = reviewed(self.risks)

    def test_reviewed_literal_passes(self):
        source = "UPDATE_SAFETY = " + repr(self.value)
        gate.validate_declaration(gate.declaration(source, "migration.py"), self.risks, registry(), "migration.py")

    def test_missing_or_bad_declaration_fails(self):
        for value in (None, {}, {**self.value, "large_volume_rehearsal": False},
                      {**self.value, "tables": ["postgres:*"]}, {**self.value, "risk_classes": ["index"]},
                      {**self.value, "review": "approved"}, {**self.value, "mitigation": "TODO"},
                      {**self.value, "ignored": True}):
            with self.subTest(value=value), self.assertRaisesRegex(gate.GateError, "update migration.py"):
                gate.validate_declaration(value, self.risks, registry(), "migration.py")

    def test_declaration_is_never_executed(self):
        with self.assertRaises(gate.GateError):
            gate.declaration('UPDATE_SAFETY = dict(schema_version=1)', "migration.py")

    def test_small_unregistered_table_is_reviewable_not_implicitly_safe(self):
        value = {**self.value, 'tables': ['postgres:small_config']}
        gate.validate_declaration(value, [{'risk_class': 'data_migration'}], registry(), 'migration.py')

    def test_duplicate_keys_and_non_dict_literal_declarations_fail(self):
        for source in ('UPDATE_SAFETY = None', 'UPDATE_SAFETY = []',
                       'UPDATE_SAFETY = {"schema_version": 1, "schema_version": 1}',
                       'UPDATE_SAFETY = {}\nUPDATE_SAFETY = {}',
                       'class Migration:\n    UPDATE_SAFETY = {}',
                       'if True:\n    UPDATE_SAFETY = {}'):
            with self.subTest(source=source), self.assertRaises(gate.GateError):
                gate.declaration(source, 'migration.py')

    def test_all_declaration_fields_are_required_and_typed(self):
        for key in self.value:
            bad = self.value.copy()
            del bad[key]
            with self.subTest(key=key), self.assertRaises(gate.GateError):
                gate.validate_declaration(bad, self.risks, registry(), 'migration.py')
        for key, value in (('reviewer', ''), ('reviewer', 'not a login'), ('reviewer', 'login-'), ('reviewer', 'two--hyphens'), ('schema_version', True),
                           ('risk_classes', ['data_migration', 'data_migration']), ('tables', ['postgres:hot_events'] * 2),
                           ('large_volume_rehearsal', 1), ('tables', ['unknown:table'])):
            with self.subTest(key=key, value=value), self.assertRaises(gate.GateError):
                gate.validate_declaration({**self.value, key: value}, self.risks, registry(), 'migration.py')

    def test_duplicate_and_unknown_registry_fail(self):
        duplicate = registry()
        duplicate["tables"].append(copy.deepcopy(duplicate["tables"][0]))
        with self.assertRaises(gate.GateError):
            gate.registry_tables(duplicate, "postgres")

    def test_real_registry_has_explicit_labels(self):
        rows = gate.document(ROOT / "deploy/update/migration-apps.json")["apps"]
        labels = {r["module"]: r["label"] for r in rows}
        self.assertEqual(labels["ee.licensing"], "ee_licensing")
        self.assertEqual(labels["agentcc"], "prism")
        self.assertEqual(labels["ee.usage"], "usage")

    def test_blocked_historical_calibration_cannot_report_cli_success(self):
        import contextlib
        import io
        from unittest.mock import patch
        result = {'status': 'blocked', 'qualification': False, 'releases': [{'real_django_loader': False}]}
        output = io.StringIO()
        with patch.object(gate, 'calibrate_releases', return_value=result), contextlib.redirect_stdout(output):
            self.assertEqual(gate.main(['--calibrate', '--json']), 1)
        self.assertEqual(json.loads(output.getvalue()), result)

    def test_missing_base_fails_cli(self):
        process = subprocess.run([sys.executable, "-I", str(ROOT / "scripts/check_update_safety.py")], capture_output=True, text=True)
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("explicit --base", process.stderr)

    def test_migration_filename_not_limited_to_numbers(self):
        self.assertTrue(gate.migration_path("futureagi/new/migrations/feature_name.py"))
        self.assertFalse(gate.migration_path("futureagi/new/migrations/__init__.py"))


class DeclaredDataMigrationTests(unittest.TestCase):
    """The pending-migration table-scope probe's own rule on real data migrations.

    The probe in scripts/rehearse_update_compatibility.py takes the tables of a
    RunPython or RunSQL step only from one module-level UPDATE_SAFETY literal and
    refuses a migration that has none. Its declared() runs here unchanged.
    """

    migrations = ROOT / "futureagi/model_hub/migrations"
    reference = (
        ROOT
        / "futureagi/tracer/migrations"
        / "0132_seed_alert_issue_occurrences_high_water.py"
    )
    tables = {
        "0125_backfill_score_value_updated_at.py": [
            "clickhouse:model_hub_score",
            "postgres:model_hub_score",
        ],
        "0127_experimentrun_ordinal_datasetbaseline.py": [
            "postgres:model_hub_dataset_version",
            "postgres:model_hub_experimentrun",
        ],
        "0143_row_activity.py": [
            "postgres:model_hub_cell",
            "postgres:model_hub_column",
            "postgres:model_hub_row",
            "postgres:model_hub_rowactivity",
        ],
    }

    @classmethod
    def setUpClass(cls):
        script = ast.parse(
            (ROOT / "scripts/rehearse_update_compatibility.py").read_text()
        )
        probe = next(
            ast.literal_eval(node.value)
            for node in script.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "PENDING_TABLE_SCOPE"
                for target in node.targets
            )
        )
        wanted = {"IDENT", "resolved", "declared"}
        body = [
            node
            for node in ast.parse(probe).body
            if (isinstance(node, ast.FunctionDef) and node.name in wanted)
            or (
                isinstance(node, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id in wanted
                    for target in node.targets
                )
            )
        ]
        cls.env = {
            "ast": ast,
            "re": re,
            "Path": Path,
            # declared() reads the source file of the migration's class.
            "inspect": SimpleNamespace(getsourcefile=lambda klass: klass.path),
            "schemas": {"postgres": "public", "clickhouse": "default"},
        }
        exec(
            compile(ast.Module(body=body, type_ignores=[]), "<scope-probe>", "exec"),
            cls.env,
        )

    def declared(self, path):
        migration = type("Migration", (), {"path": str(path)})()
        return self.env["declared"](migration)

    def test_probe_refuses_a_data_migration_without_a_declaration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "0001_backfill.py"
            path.write_text(
                "from django.db import migrations\n\n\n"
                "class Migration(migrations.Migration):\n"
                "    operations = [migrations.RunPython(migrations.RunPython.noop)]\n"
            )
            with self.assertRaisesRegex(
                AssertionError, "opaque_operation_table_scope_unavailable"
            ):
                self.declared(path)

    def test_probe_reads_each_data_step_table_scope(self):
        for name, tables in self.tables.items():
            with self.subTest(migration=name):
                try:
                    scope = self.declared(self.migrations / name)
                except AssertionError as exc:
                    self.fail(f"{name}: {exc}")
                schemas = self.env["schemas"]
                expected = set()
                for table in tables:
                    engine, table_name = table.split(":", 1)
                    expected.add((engine, schemas[engine] + "." + table_name))
                self.assertEqual(scope, expected)

    def test_declarations_keep_the_reviewed_shape(self):
        reference = gate.declaration(self.reference.read_text(), self.reference)
        for name, tables in self.tables.items():
            with self.subTest(migration=name):
                path = self.migrations / name
                value = gate.declaration(path.read_text(), path)
                self.assertIsNotNone(value, f"{name}: no UPDATE_SAFETY declaration")
                self.assertEqual(set(value), set(reference))
                self.assertEqual(value["schema_version"], 1)
                self.assertEqual(value["risk_classes"], ["data_migration"])
                self.assertEqual(value["tables"], tables)
                self.assertTrue(30 <= len(value["mitigation"].strip()) <= 2000)
                self.assertIs(value["large_volume_rehearsal"], True)


class DeclaredConnectorCredentialMigrationTests(DeclaredDataMigrationTests):
    """The same rule on ee/falcon_ai 0010, which rewrites connector credentials."""

    migrations = ROOT / "futureagi/ee/falcon_ai/migrations"
    tables = {
        "0010_encrypt_connector_credentials.py": [
            "postgres:falcon_ai_mcpconnector",
        ],
    }


class DeclaredLedgerMigrationTests(DeclaredDataMigrationTests):
    """The same rule on ee/usage 0032 and 0033, which seed the ledger buckets and backfill top-up PaymentIntents."""

    migrations = ROOT / "futureagi/ee/usage/migrations"
    tables = {
        "0032_ledger_opening_buckets.py": [
            "postgres:usage_managedcreditaccount",
            "postgres:usage_managedcreditentry",
        ],
        "0033_backfill_topup_payment_intent.py": ["postgres:usage_managedcreditentry"],
    }


if __name__ == "__main__":
    unittest.main()
