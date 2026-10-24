"""Repository-owned Docker context policies must exclude private updater state.

Dockerfile-specific ignore files REPLACE, rather than extend, the context's
.dockerignore. Keep the private rule last so a public-source negation cannot
undo it. Real Docker COPY/image-layer checks live in test_update_e2e_standalone.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
PRIVATE_RULE = '**/.futureagi-updates/'
# bin/lib/build-local.{sh,ps1}, bin/e2e, Compose build contexts and release/base
# workflows. The E2E backend overlay is the one Dockerfile outside its context.
CONTEXTS = ('.', 'futureagi', 'frontend', 'agentcc-gateway', 'fi-collector',
            'futureagi/model_serving', 'futureagi/code-executor', 'deploy/standalone',
            'deploy/tests/update_e2e/fakeapp')
CONTEXT_OVERRIDES = {'e2e/stack/backend/Dockerfile': '.'}


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
