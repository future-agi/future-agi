"""Repository-owned Docker context policies must exclude private updater state.

Dockerfile-specific ignore files REPLACE, rather than extend, the context's
.dockerignore. Keep the private rule last so a public-source negation cannot
undo it. Real Docker COPY/image-layer checks live in test_update_e2e_standalone.
"""
from pathlib import Path
import re
import unittest
import subprocess
import sys
import tempfile
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from deploy.tests.update_e2e.build_contexts import export_probe_image  # noqa: E402

PRIVATE_RULE = '**/.futureagi-updates/'
# bin/lib/build-local.{sh,ps1}, bin/e2e, Compose build contexts and release/base
# workflows. The E2E backend overlay is the one Dockerfile outside its context.
CONTEXTS = ('.', 'futureagi', 'frontend', 'agentcc-gateway', 'fi-collector',
            'futureagi/model_serving', 'futureagi/code-executor', 'deploy/standalone',
            'deploy/tests/update_e2e/fakeapp')
CONTEXT_OVERRIDES = {'e2e/stack/backend/Dockerfile': '.'}


class ProbeExportTests(unittest.TestCase):
    def export(self, *, failing=None, missing=False):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        archive = root/'image.tar'
        calls = []
        def run(words, **kwargs):
            calls.append(words)
            self.assertIn(kwargs['timeout'], (30, 120))
            kind = 'build' if words[:2] == ['docker', 'buildx'] else words[1]
            if kind == 'save' and not missing and failing != kind:
                archive.write_bytes(b'synthetic archive (command-only regression)')
            return subprocess.CompletedProcess(words, 1 if failing == kind else 0, '', 'injected' if failing == kind else '')
        with mock.patch('deploy.tests.update_e2e.build_contexts.subprocess.run', side_effect=run):
            if failing or missing:
                with self.assertRaises(AssertionError):
                    export_probe_image(['docker', 'buildx'], [], root/'Dockerfile', root, archive, root/'log')
            else:
                exported = export_probe_image(['docker', 'buildx'], [], root/'Dockerfile', root, archive, root/'log')
                self.assertEqual(exported['image_tag'], calls[0][calls[0].index('--tag') + 1])
        return calls

    def test_load_save_and_exact_tag_cleanup_use_no_tar_exporter_or_global_mutation(self):
        calls = self.export()
        self.assertEqual(len(calls), 3)
        build, save, cleanup = calls
        tag = build[build.index('--tag') + 1]
        self.assertRegex(tag, r'^fagi-upd-e2e-context-[0-9a-f]{32}:probe$')
        self.assertIn('--load', build)
        self.assertNotIn('--output', build)
        self.assertEqual(save[:3], ['docker', 'save', '--output'])
        self.assertEqual(save[-1], tag)
        self.assertEqual(cleanup, ['docker', 'image', 'rm', tag])
        self.assertIn('com.futureagi.update-e2e.context=' + tag, build)
        again = self.export()[0]
        self.assertNotEqual(again[again.index('--tag') + 1], tag)

    def test_build_save_missing_archive_and_cleanup_failures_are_never_silent(self):
        for failing in ('build', 'save', 'image', None):
            with self.subTest(failing=failing):
                calls = self.export(failing=failing, missing=failing is None)
                tag = calls[0][calls[0].index('--tag') + 1]
                self.assertEqual(calls[-1], ['docker', 'image', 'rm', tag])
                if failing == 'build':
                    self.assertEqual(len(calls), 2)


class BuildContextPolicyTests(unittest.TestCase):
    def test_every_dockerfile_has_a_guarded_effective_ignore_policy(self):
        dockerfiles = sorted(p for p in ROOT.rglob('Dockerfile*') if p.is_file() and
                             not p.name.endswith('.dockerignore'))
        self.assertTrue(dockerfiles)
        for dockerfile in dockerfiles:
            rel = dockerfile.relative_to(ROOT).as_posix()
            context = CONTEXT_OVERRIDES.get(rel, dockerfile.parent.relative_to(ROOT).as_posix())
            with self.subTest(dockerfile=rel, context=context):
                self.assertIn(context, CONTEXTS, 'inventory the new repository build context')
                specific = dockerfile.with_name(dockerfile.name + '.dockerignore')
                policy = specific if specific.exists() else ROOT / context / '.dockerignore'
                self.assertTrue(policy.is_file(), str(policy))
                self.assert_guarded(policy)

    def assert_guarded(self, policy):
        self.assertTrue(policy.is_file(), f'missing build-context policy: {policy}')
        rules = [line.strip() for line in policy.read_text().splitlines()
                 if line.strip() and not line.lstrip().startswith('#')]
        self.assertTrue(rules, str(policy))
        self.assertEqual(rules[-1], PRIVATE_RULE,
                         f'{policy}: recursive updater-state exclusion must be the final rule; '
                         'later negations can expose retained secrets')

    def test_all_ignore_policies_end_with_private_exclusion(self):
        for policy in sorted(ROOT.rglob('*.dockerignore')):
            with self.subTest(policy=str(policy.relative_to(ROOT))):
                self.assert_guarded(policy)

    def test_literal_compose_and_workflow_contexts_are_inventoried(self):
        paths = list(ROOT.glob('docker-compose*.yml')) + list((ROOT / '.github/workflows').glob('*.yml'))
        for path in paths:
            for context in re.findall(r'^\s*context:\s*[\'"]?([^\s,\'"}]+)', path.read_text(), re.M):
                if context.startswith(('$', '{')):  # matrix inputs or external operator-selected ALK checkout
                    continue
                context = context.removeprefix('./')
                with self.subTest(path=str(path.relative_to(ROOT)), context=context):
                    self.assertIn(context, CONTEXTS)
                    self.assert_guarded(ROOT / context / '.dockerignore')

    def test_ci_runs_on_ignore_and_dockerfile_changes(self):
        workflow = (ROOT / '.github/workflows/update-ci.yml').read_text()
        for pattern in ('"**/.dockerignore"', '"**/*.dockerignore"', '"**/Dockerfile*"'):
            self.assertEqual(workflow.count(pattern), 2, pattern)
