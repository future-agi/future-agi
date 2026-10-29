"""Cross-lane regression checks; synthetic source is never qualification."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from deploy.tests.test_update_safety_gate import gate, registry, reviewed

REGISTRY = registry()


class StaticConstructionRegressionTests(unittest.TestCase):
    def requires_exact_source_review(self, source, suffix='py'):
        # A change that introduces a ClickHouse schema or mutation statement
        # needs a declaration bound to its exact before/after blob hashes.
        # Exercise these constructions through that public API, rather than
        # calling parser internals.
        with tempfile.TemporaryDirectory(prefix='integration-source-review-') as name:
            root = Path(name)
            path = 'product/schema.' + suffix
            target = root/path
            target.parent.mkdir()
            target.write_text(source)
            previous = b'# previous registered schema source\n'
            sources = {'schema_version': 1, 'sources': [
                {'pattern': 'product/**', 'reason': 'Schema source changes require exact review.'}], 'exclusions': []}
            reviews = {'schema_version': 1, 'changes': []}
            def base_bytes(root, base, name):
                return None if name == 'deploy/update/clickhouse-sources.json' else previous
            with mock.patch.object(gate, 'base_bytes', side_effect=base_bytes):
                with self.assertRaisesRegex(gate.GateError, 'require human-reviewed declarations'):
                    gate.check_clickhouse_changes(root, [('M', path)], 'fixture-base', sources, reviews, REGISTRY)
                declaration = reviewed([{'table': 'clickhouse:spans', 'risk_class': 'clickhouse_change'}])
                reviews['changes'] = [{'path': path, 'before_sha256': gate.content_sha(previous),
                    'after_sha256': gate.content_sha(target.read_bytes()), 'declaration': declaration}]
                risks = gate.check_clickhouse_changes(root, [('M', path)], 'fixture-base', sources, reviews, REGISTRY)
                self.assertIn('clickhouse:spans', risks[0]['tables'])
                target.write_text(source+'\n# another change\n')
                with self.assertRaisesRegex(gate.GateError, 'exact base-to-head change'):
                    gate.check_clickhouse_changes(root, [('M', path)], 'fixture-base', sources, reviews, REGISTRY)

    def test_mutated_python_lists_cannot_hide_clickhouse_rewrites(self):
        for mutation in ("parts.append('MODIFY COLUMN latency_ms Int64;')",
                         "parts.extend(['MODIFY COLUMN', 'latency_ms Int64;'])",
                         "parts.insert(2, 'MODIFY COLUMN latency_ms Int64;')",
                         "parts += ['MODIFY COLUMN latency_ms Int64;']",
                         "parts[1] = 'spans MODIFY COLUMN latency_ms Int64;'"):
            for alias in ('parts', 'alias'):
                source = "parts = ['ALTER TABLE', 'spans']\nalias = parts\n" + mutation.replace('parts', alias) + "\nclient.command(' '.join(parts))\n"
                with self.subTest(mutation=mutation, alias=alias):
                    self.requires_exact_source_review(source)

    def passes_without_review(self, source, suffix='py'):
        with tempfile.TemporaryDirectory(prefix='integration-source-review-') as name:
            root = Path(name)
            path = 'product/schema.' + suffix
            target = root/path
            target.parent.mkdir()
            target.write_text(source)
            sources = {'schema_version': 1, 'sources': [
                {'pattern': 'product/**', 'reason': 'Schema source changes require exact review.'}], 'exclusions': []}
            reviews = {'schema_version': 1, 'changes': []}
            def base_bytes(root, base, name):
                return None if name == 'deploy/update/clickhouse-sources.json' else b'package p\n'
            with mock.patch.object(gate, 'base_bytes', side_effect=base_bytes):
                self.assertEqual(gate.check_clickhouse_changes(root, [('M', path)], 'fixture-base', sources,
                                                               reviews, REGISTRY), [])

    def test_go_constant_concatenation_keeps_executable_sql_together(self):
        for separator in (' + ', ' +\n', ' + /* explanation */\n'):
            source = 'package p\nfunc f() { command("ALTER TABLE spans "' + separator + '"MODIFY COLUMN latency_ms Int64;") }'
            with self.subTest(separator=separator):
                self.requires_exact_source_review(source, 'go')
        # Amendment A9 (user decision): review is triggered by schema or
        # mutation statements, not by every edit of a registered file, so a
        # statement that only appears in a comment is not executable DDL.
        self.passes_without_review('package p\n// "ALTER TABLE spans MODIFY COLUMN x Int64;"\n', 'go')

    def test_logging_secret_generator_text_is_not_executable_generation(self):
        with tempfile.TemporaryDirectory(prefix='integration-generator-') as name:
            root = Path(name)
            path = root/'generator.sh'
            generators = {'schema_version': 1, 'generators': [{'variables': ['NEW_TOKEN'],
                'path': 'generator.sh', 'language': 'shell', 'symbol': 'gen_secret',
                'entrypoint': 'write_secrets', 'availability': 'every_install',
                'reason': 'Explicit absent-secret generation definition.'}]}
            for source in ('printf "%s\\n" "fill_secret NEW_TOKEN gen_secret"',
                           'echo fill_secret NEW_TOKEN gen_secret', '# fill_secret NEW_TOKEN gen_secret',
                           'if true; then fill_secret NEW_TOKEN gen_secret; fi'):
                path.write_text(source+'\nwrite_secrets() { :; }\n')
                with self.subTest(source=source), self.assertRaises(gate.GateError):
                    gate.check_generators(root, generators)
            path.write_text('gen_secret() { printf secret; }\nwrite_secrets() { :; }\n')
            self.assertIn('NEW_TOKEN', gate.check_generators(root, generators))


class RestoreReceiptBoundaryTests(unittest.TestCase):
    def test_restore_receipt_uses_validated_instant_in_UTC_not_observer_local_zone(self):
        from datetime import timedelta, timezone
        from deploy.tests.test_update_automation_boundaries import RuntimeComponentWorld
        from deploy.update.readiness import NotReady
        world = RuntimeComponentWorld(self, fail_target=True)
        original = world.lifecycle._observation
        def zoned(*args):
            value = original(*args)
            value.facts.now = value.facts.now.astimezone(timezone(timedelta(hours=5, minutes=30)))
            return value
        world.lifecycle._observation = zoned
        try:
            with self.assertRaises(NotReady):
                world.run()
            self.assertEqual(world.engine.automatic_outcome, 'restored')
            restored = [r for r in world.case.state.history(world.case.inst.identity()) if r['kind'] == 'restore']
            self.assertEqual(len(restored), 1)
            self.assertEqual(restored[0]['finished_at'], world.now.strftime('%Y-%m-%dT%H:%M:%SZ'))
        finally:
            world.close()


if __name__ == '__main__':
    unittest.main()
